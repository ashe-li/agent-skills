#!/usr/bin/env python3
"""Run a profile's Quick checks and build the quick-check receipt.

Only the matched profile's checks run, and only when its realpath is inside
the KB root (kb_source.match_profile and verify_profile.read_kb_file both
enforce that). Each check is `shlex.split` argv, `shell=False`, with its own
timeout, cwd at the repo root and stdin closed. Argv with shell
metacharacters is PROFILE-INVALID and never runs; a missing executable or
exit 127 is PROFILE-STALE, not FAIL. Logs go to
`.verification/<date>/verify-<short sha>/<id>.log`.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from datetime import date
from pathlib import Path
from typing import NamedTuple, Optional, Sequence

import verify_git as vg
import verify_profile as vp
import verify_receipts as vr
import verify_select as vs

PASS, FAIL, SKIPPED = "PASS", "FAIL", "SKIPPED"
STALE, INVALID = "PROFILE-STALE", "PROFILE-INVALID"
NO_KB, NO_PROFILE = "NO-KB", "NO-PROFILE"
METHOD_PROFILE, METHOD_GENERIC = "profile-quick-checks", "generic"
EXIT_NOT_FOUND = 127
SHORT_SHA = 7
DURATION_DIGITS = 3
DIRTY_NOTICE = "工作樹不乾淨，這張 receipt 不能當作開 PR 的憑證（快檢跑的是工作樹內容，不是 HEAD 的 tree）"
_LOG_NAME_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


class CheckOutcome(NamedTuple):
    id: str
    required: bool
    status: str
    exit: Optional[int]
    duration_s: float
    log: Optional[str]
    reason: str
    argv: tuple

    def as_json(self) -> dict:
        return {"id": self.id, "required": self.required, "status": self.status,
                "exit": self.exit, "duration_s": self.duration_s, "log": self.log}


class ChecksRun(NamedTuple):
    verdict: str
    method: str
    outcomes: tuple
    stale: tuple
    invalid: tuple


def _outcome(qc: vp.QuickCheck, status: str, reason: str = "", exit_code: Optional[int] = None,
             duration: float = 0.0, log: Optional[str] = None) -> CheckOutcome:
    return CheckOutcome(qc.id, qc.required, status, exit_code, round(duration, DURATION_DIGITS),
                        log, reason, qc.argv)


def _open_log(path: Path):
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    return os.fdopen(os.open(path, flags, vr.FILE_MODE), "wb")


def _execute(qc: vp.QuickCheck, root: str, log_path: Path, rel_log: str) -> CheckOutcome:
    start = time.monotonic()
    with _open_log(log_path) as log:
        try:
            proc = subprocess.run(list(qc.argv), cwd=root, shell=False, stdin=subprocess.DEVNULL,
                                  stdout=log, stderr=subprocess.STDOUT, timeout=qc.timeout)
        except (FileNotFoundError, PermissionError, NotADirectoryError) as exc:
            reason = f"{qc.id}: cannot start {qc.argv[0]!r} ({exc.strerror}); the profile is out of date"
            return _outcome(qc, STALE, reason, None, time.monotonic() - start, rel_log)
        except subprocess.TimeoutExpired:
            reason = f"{qc.id}: timed out after {qc.timeout}s"
            return _outcome(qc, FAIL, reason, None, time.monotonic() - start, rel_log)
    duration = time.monotonic() - start
    if proc.returncode == EXIT_NOT_FOUND:
        reason = f"{qc.id}: exit {EXIT_NOT_FOUND} (command not found); the profile is out of date"
        return _outcome(qc, STALE, reason, proc.returncode, duration, rel_log)
    status = PASS if proc.returncode == 0 else FAIL
    return _outcome(qc, status, "", proc.returncode, duration, rel_log)


def _pre_run(qc: vp.QuickCheck, root: str, changes: Sequence[vg.Change]) -> Optional[CheckOutcome]:
    """Outcome decided without running anything, or None when it should run."""
    if qc.invalid:
        return _outcome(qc, INVALID, "; ".join(qc.invalid))
    if qc.requires_path and not (Path(root) / qc.requires_path).exists():
        return _outcome(qc, STALE, f"{qc.id}: Requires-path {qc.requires_path} does not exist")
    if qc.when_paths is not None and not any(
            vp.first_match(c.path, qc.when_paths) for c in changes):
        return _outcome(qc, SKIPPED, f"{qc.id}: no changed file matches When")
    return None


def log_dir(root: str, head: str, today: Optional[date] = None) -> tuple:
    """(absolute dir, repo-relative dir) for this run's logs; creates it."""
    day = (today or date.today()).isoformat()
    parts = (vg.VERIFICATION_DIRNAME, day, f"verify-{head[:SHORT_SHA]}")
    directory = vr.safe_dir(Path(root), *parts, create=True)
    return directory, "/".join(parts)


def run_quick_checks(root: str, head: str, profile: vp.Profile, changes: Sequence[vg.Change],
                     dry_run: bool) -> tuple:
    outcomes, logs = [], None
    for qc in vp.quick_checks(profile):
        decided = _pre_run(qc, root, changes)
        if decided is None and dry_run:
            decided = _outcome(qc, SKIPPED, f"{qc.id}: dry run, not executed")
        if decided is None:
            logs = logs or log_dir(root, head)
            name = _LOG_NAME_UNSAFE.sub("_", qc.id) + ".log"
            decided = _execute(qc, root, logs[0] / name, f"{logs[1]}/{name}")
        outcomes.append(decided)
    return tuple(outcomes)


def path_stale_reasons(root: str, profile: vp.Profile) -> tuple:
    tracked = vg.ls_files(root)
    reasons = []
    for key in vp.PATH_KEYS:
        for pattern in vp.path_globs(profile, key):
            if not any(vp.glob_match(path, pattern) for path in tracked):
                reasons.append(f"Paths {key}: {pattern} matches no tracked file")
    return tuple(reasons)


def profile_invalid_reasons(profile: vp.Profile) -> tuple:
    schema = profile.doc.frontmatter.get("schema")
    if schema != vp.PROFILE_SCHEMA:
        return (f"{profile.filename}: schema must be {vp.PROFILE_SCHEMA}",)
    return ()


def _verdict(outcomes: Sequence[CheckOutcome], stale: Sequence[str], invalid: Sequence[str]) -> str:
    if any(o.status == FAIL and o.required for o in outcomes):
        return FAIL
    if invalid:
        return INVALID
    return STALE if stale else PASS


def evaluate(root: str, head: str, ctx: vp.KbContext, changes: Sequence[vg.Change],
             dry_run: bool) -> ChecksRun:
    if ctx.home is None:
        return ChecksRun(NO_KB, METHOD_GENERIC, (), (), ())
    if ctx.profile is None:
        return ChecksRun(NO_PROFILE, METHOD_GENERIC, (), (), ())
    profile_invalid = profile_invalid_reasons(ctx.profile)
    outcomes = () if profile_invalid else run_quick_checks(root, head, ctx.profile, changes, dry_run)
    stale = tuple(o.reason for o in outcomes if o.status == STALE) + path_stale_reasons(root, ctx.profile)
    invalid = profile_invalid + tuple(o.reason for o in outcomes if o.status == INVALID)
    return ChecksRun(_verdict(outcomes, stale, invalid), METHOD_PROFILE, outcomes, stale, invalid)


def build_receipt(info: vr.RepoInfo, ctx: vp.KbContext, run: ChecksRun, *, head: str,
                  tree: str, dirty: bool, selected: Sequence[vs.Selected],
                  diff_base: Optional[str]) -> dict:
    return {
        "schema": vr.SCHEMA_QUICK, "repo": info.key, "created": vr.now_iso(),
        "head_sha": head, "tree_sha": tree,
        "base_sha": vr.quick_base_sha(info.root, ctx.profile), "diff_base_sha": diff_base,
        "config_digest": vp.config_digest(ctx), "engine_version": vr.ENGINE_VERSION,
        "dirty": dirty, "method": run.method,
        "checks": [o.as_json() for o in run.outcomes],
        "selected_items": [s.item.id for s in selected],
        "verdict": run.verdict,
        "stale_reasons": list(run.stale), "invalid_reasons": list(run.invalid),
    }
