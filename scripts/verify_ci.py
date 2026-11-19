#!/usr/bin/env python3
"""CI evidence: fetch it read-only through `gh`, check it, write CI receipts.

Evidence format is `verify-ci-evidence/1` (docs/knowledge-source-contract.md,
CI 證據). The checks run in the contract's order and stop at the first
mismatch: repo and registered PR, PR open, head equality (PR head, every
run's head, the branch head), Required-jobs declared, evidence non-empty,
every required job present; then a completed required job that did not
succeed makes the conclusion red even while others are still running (so
--pending-declared can never cover a known failure), and only then must
every required job be completed. Only the highest
attempt of each run_id counts, and within one workflow a job from a newer
run_id supersedes the same job from older runs on the same head. Required
jobs are looked up across workflows, so jobs from several workflows combine;
a job outside Required-jobs never affects the conclusion. The registry entry
of the PR must be open (plan decision 4), else repo-or-pr-mismatch.

`gh` is only ever called with fixed read-only subcommands (repo view, pr
view, run list, run view), argv form, shell=False and a timeout. PR numbers
and run IDs must be plain digits; head SHAs must be hexadecimal.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import date
from pathlib import Path
from typing import NamedTuple, Optional, Sequence

import kb_source
import verify_git as vg
import verify_profile as vp
import verify_receipts as vr

EVIDENCE_SCHEMA = "verify-ci-evidence/1"
GH_TIMEOUT_SECONDS = 60
GH_RUN_LIMIT = "100"
PR_FIELDS = "number,state,headRefName,headRefOid,url"
REPO_FIELDS = "nameWithOwner,url"
RUN_LIST_FIELDS = "databaseId,attempt,headSha,workflowName,status,conclusion"
RUN_VIEW_FIELDS = "databaseId,attempt,headSha,workflowName,status,conclusion,jobs"
COMPLETED, SUCCESS = "completed", "success"
PR_STATE_MAP = {"OPEN": "open", "CLOSED": "closed", "MERGED": "merged"}

WRITTEN, REJECTED, UNVERIFIABLE = "written", "rejected", "unverifiable"
GREEN, RED, PENDING_DECLARED, EXEMPT = "green", "red", "pending-declared", "exempt"
R_MISMATCH = "repo-or-pr-mismatch"
R_NOT_OPEN = "pr-not-open"
R_HEAD = "head-mismatch"
R_NOT_CONFIGURED = "ci-not-configured"
R_EMPTY = "evidence-empty"
R_JOB_MISSING = "required-job-missing"
R_PENDING = "ci-pending"
R_INVALID = "evidence-invalid"


class EvidenceError(Exception):
    """Evidence could not be read, fetched or parsed."""


class Evidence(NamedTuple):
    data: dict
    path: str
    sha256: str


class CiOutcome(NamedTuple):
    result: str
    conclusion: Optional[str]
    reason: str
    receipt: Optional[str]
    message: str


class CiFlags(NamedTuple):
    pending_declared: bool
    exempt_reason: Optional[str]


def is_pr_number(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


# --- evidence sources ------------------------------------------------------------

def _validate(data: object) -> dict:
    if not isinstance(data, dict) or data.get("schema") != EVIDENCE_SCHEMA:
        raise EvidenceError(f"evidence must be a {EVIDENCE_SCHEMA} JSON object")
    if not is_pr_number(data.get("pr")) or not isinstance(data.get("runs"), list):
        raise EvidenceError("evidence needs an integer pr and a runs list")
    for run in data["runs"]:
        if not isinstance(run, dict) or not isinstance(run.get("jobs", []), list):
            raise EvidenceError("every run must be an object with a jobs list")
        if not all(isinstance(job, dict) for job in run.get("jobs") or []):
            raise EvidenceError("every job must be an object")
    return data


def load_evidence_file(path: str) -> Evidence:
    try:
        raw = Path(path).read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise EvidenceError(f"cannot read evidence file: {exc}") from exc
    return Evidence(_validate(data), str(Path(path).resolve()), hashlib.sha256(raw).hexdigest())


def _gh(root: str, *args: str):
    try:
        proc = subprocess.run(["gh", *args], cwd=root, shell=False, capture_output=True,
                              text=True, timeout=GH_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        raise EvidenceError(f"gh {args[0]} {args[1]} could not run: {exc}") from exc
    if proc.returncode != 0:
        raise EvidenceError(f"gh {args[0]} {args[1]} failed with exit {proc.returncode}")
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise EvidenceError(f"gh {args[0]} {args[1]} returned non-JSON output") from exc


def gh_pr_view(root: str, pr: Optional[str]) -> dict:
    if pr is not None and not (pr.isascii() and pr.isdigit() and int(pr) > 0):
        raise EvidenceError("--pr must be a positive integer")
    args = ("pr", "view", *((pr,) if pr else ()), "--json", PR_FIELDS)
    view = _gh(root, *args)
    if not isinstance(view, dict) or not is_pr_number(view.get("number")):
        raise EvidenceError("gh pr view returned no PR number")
    return view


def gh_repo_key(root: str) -> Optional[str]:
    view = _gh(root, "repo", "view", "--json", REPO_FIELDS)
    url = view.get("url") if isinstance(view, dict) else None
    return kb_source.normalize_remote(url) if isinstance(url, str) else None


def _gh_runs(root: str, head: str) -> list:
    if not vg.is_sha(head):
        raise EvidenceError("gh pr view returned a malformed head SHA")
    listed = _gh(root, "run", "list", "--commit", head, "--limit", GH_RUN_LIMIT,
                 "--json", RUN_LIST_FIELDS)
    runs = []
    for item in listed if isinstance(listed, list) else []:
        run_id = item.get("databaseId") if isinstance(item, dict) else None
        if not is_pr_number(run_id):
            raise EvidenceError("gh run list returned a malformed run ID")
        view = _gh(root, "run", "view", str(run_id), "--json", RUN_VIEW_FIELDS)
        runs.append({"run_id": run_id, "attempt": view.get("attempt"),
                     "workflow": view.get("workflowName"), "head_sha": view.get("headSha"),
                     "status": view.get("status"), "conclusion": view.get("conclusion"),
                     "jobs": [{"name": j.get("name"), "status": j.get("status"),
                               "conclusion": j.get("conclusion")}
                              for j in view.get("jobs") or [] if isinstance(j, dict)]})
    return runs


def fetch_evidence_from_gh(info: vr.RepoInfo, pr: Optional[str]) -> Evidence:
    """Build evidence with read-only gh calls and save it under .verification/."""
    view = gh_pr_view(info.root, pr)
    data = _validate({
        "schema": EVIDENCE_SCHEMA, "repo": gh_repo_key(info.root), "pr": view["number"],
        "pr_state": PR_STATE_MAP.get(str(view.get("state")).upper(), "unknown"),
        "pr_head_sha": view.get("headRefOid"), "pr_head_branch": view.get("headRefName"),
        "fetched_at": vr.now_iso(), "runs": _gh_runs(info.root, str(view.get("headRefOid"))),
    })
    raw = vr.dump_json(data)
    directory = vr.safe_dir(Path(info.root), vg.VERIFICATION_DIRNAME, date.today().isoformat(),
                            create=True)
    head = data["pr_head_sha"] if vg.is_sha(data["pr_head_sha"]) else "unknown"
    path = directory / f"ci-evidence-pr-{data['pr']}-{head}.json"
    vr.write_bytes_atomic(path, raw)
    return Evidence(data, str(path), hashlib.sha256(raw).hexdigest())


# --- evaluation -------------------------------------------------------------------

def latest_attempts(runs: Sequence[dict]) -> tuple:
    """Keep only the highest attempt per run_id, independent of list order."""
    best = {}
    for run in runs:
        attempt = run.get("attempt") if isinstance(run.get("attempt"), int) else 0
        key = run.get("run_id")
        if key not in best or attempt > best[key][0]:
            best[key] = (attempt, run)
    return tuple(run for _, run in best.values())


def _reject(reason: str, message: str = "") -> CiOutcome:
    return CiOutcome(REJECTED, None, reason, None, message)


def _unverifiable(reason: str, message: str = "") -> CiOutcome:
    return CiOutcome(UNVERIFIABLE, None, reason, None, message)


def _attribution(info: vr.RepoInfo, data: dict, state_dir: Optional[str]) -> tuple:
    """(entry, head) when the evidence belongs to this repo's open PR head."""
    entry = vr.find_entry_by_pr(info.key, data["pr"], state_dir=state_dir) if info.key else None
    branch = data.get("pr_head_branch")
    if (kb_source.normalize_remote_or_key(str(data.get("repo"))) != info.key or entry is None
            or (branch is not None and branch != entry["branch"])):
        return _reject(R_MISMATCH, "evidence is not for a PR registered in this repo"), None
    if entry.get("state") != vr.STATE_OPEN:
        return _reject(R_MISMATCH, "the registry entry for this PR is not open; re-run "
                                   "`pr register` so it reflects the PR's current state"), None
    if data.get("pr_state") != vr.STATE_OPEN:
        return _reject(R_NOT_OPEN, "the PR is closed or merged; re-run `pr register` "
                                   "to record that instead of writing a CI receipt"), None
    head = vr.resolve_head(info.path, entry["branch"], state_dir=state_dir)
    if head.sha is None:
        return _reject(vr.R_HEAD_UNRESOLVED, "cannot determine the branch head"), None
    run_heads = {run.get("head_sha") for run in data["runs"]}
    if data.get("pr_head_sha") != head.sha or run_heads - {head.sha}:
        return _reject(R_HEAD, "evidence is for a different head than the branch"), None
    return entry, head.sha


def adopted_jobs(runs: Sequence[dict]) -> dict:
    """job name -> jobs that count.

    `runs` already holds one attempt per run_id. Within a workflow, the run
    with the highest run_id that has a job of that name supersedes older
    runs of the same workflow on the same head (cancelled duplicates, earlier
    re-triggers). Every job of that name inside the adopted run counts, so
    two same-named jobs in one run must both succeed.
    """
    best = {}
    for run in runs:
        run_id = run.get("run_id") if isinstance(run.get("run_id"), int) else -1
        for job in run.get("jobs") or []:
            key = (run.get("workflow"), job.get("name"))
            best[key] = max(best.get(key, run_id), run_id)
    seen = {}
    for run in runs:
        run_id = run.get("run_id") if isinstance(run.get("run_id"), int) else -1
        for job in run.get("jobs") or []:
            if best[(run.get("workflow"), job.get("name"))] == run_id:
                seen.setdefault(job.get("name"), []).append(job)
    return seen


def _job_states(runs: Sequence[dict], required: Sequence[str]) -> tuple:
    """(missing jobs, pending jobs, failed jobs) over the adopted jobs."""
    seen = adopted_jobs(runs)
    missing = tuple(j for j in required if j not in seen)
    pending = tuple(j for j in required if j in seen
                    and any(x.get("status") != COMPLETED for x in seen[j]))
    failed = tuple(j for j in required if j in seen and any(
        x.get("status") == COMPLETED and x.get("conclusion") != SUCCESS for x in seen[j]))
    return missing, pending, failed


def _judge(data: dict, required: Sequence[str], flags: CiFlags) -> tuple:
    """(conclusion, CiOutcome-or-None) after attribution has passed."""
    if flags.exempt_reason:
        return EXEMPT, None
    if not required:
        if flags.pending_declared:
            return PENDING_DECLARED, None
        return None, _unverifiable(R_NOT_CONFIGURED, "the profile declares no Required-jobs")
    if not data["runs"] or any(not run.get("jobs") for run in data["runs"]):
        return None, _unverifiable(R_EMPTY, "the evidence has no run, or a run without jobs")
    missing, pending, failed = _job_states(latest_attempts(data["runs"]), required)
    if missing:
        return None, _reject(R_JOB_MISSING, "required jobs not in evidence: " + ", ".join(missing))
    if failed:
        return RED, None
    if pending:
        if flags.pending_declared:
            return PENDING_DECLARED, None
        return None, _reject(R_PENDING, "required jobs not finished: " + ", ".join(pending))
    return GREEN, None


def _receipt(info: vr.RepoInfo, evidence: Evidence, head: str, conclusion: str,
             required: Sequence[str], profile: Optional[vp.Profile], flags: CiFlags) -> dict:
    runs = [{k: run.get(k) for k in ("run_id", "attempt", "workflow", "head_sha", "status",
                                      "conclusion", "jobs")}
            for run in latest_attempts(evidence.data["runs"])]
    return {"schema": vr.SCHEMA_CI, "repo": info.key, "pr": evidence.data["pr"],
            "head_sha": head, "conclusion": conclusion, "reason": flags.exempt_reason or "",
            "required_jobs": list(required), "runs": runs,
            "evidence_path": evidence.path, "evidence_sha256": evidence.sha256,
            "ci_config_digest": vp.ci_config_digest(profile), "created": vr.now_iso()}


def evaluate(info: vr.RepoInfo, evidence: Evidence, ctx: vp.KbContext, flags: CiFlags,
             state_dir: Optional[str]) -> CiOutcome:
    entry, head = _attribution(info, evidence.data, state_dir)
    if isinstance(entry, CiOutcome):
        return entry
    required = vp.required_jobs(ctx.profile)
    conclusion, stop = _judge(evidence.data, required, flags)
    if stop is not None:
        return stop
    receipt = _receipt(info, evidence, head, conclusion, required, ctx.profile, flags)
    path = vr.write_receipt(info.root, vr.ci_receipt_name(evidence.data["pr"], head), receipt)
    return CiOutcome(WRITTEN, conclusion, "", path, "")
