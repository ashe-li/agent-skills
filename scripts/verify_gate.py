#!/usr/bin/env python3
"""Claude Code hook for the three verification gates (stdlib only).

Subcommands, each reading one hook JSON object from stdin:

    pre-tool   PreToolUse[Bash]   gate `pre_pr`: `gh pr create` needs a valid
                                  quick-check receipt (exit 2 + stderr blocks)
    post-tool  PostToolUse[Bash]  records session links, pushed heads and
                                  `link` events; never blocks, never prints
    stop       Stop               gates `ui_acceptance` and `ci_evidence` over
                                  every PR linked to this session; blocks by
                                  printing {"decision": "block", "reason": ...}

Every gate defaults to off. observe only appends to decisions.jsonl; enforce
blocks. Validity rules live in verify_receipts.py and are only called from
here. The hook never touches the network, never runs `gh`, never reads the
conversation log and never echoes command text or tool output.

Fail-open: any exception, the overall deadline, bad input, an unknown mode or
an unusable state directory ends with exit 0 and no output. The only non-zero
exit is the deliberate pre-tool block (2). The formats are documented in
docs/knowledge-source-contract.md and docs/hooks-setup.md.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import sys
import time
import warnings
from pathlib import Path
from typing import Mapping, NamedTuple, Optional

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

GATE_PRE_PR, GATE_UI, GATE_CI = "pre_pr", "ui_acceptance", "ci_evidence"
GATES = (GATE_PRE_PR, GATE_UI, GATE_CI)
STOP_GATES = (GATE_UI, GATE_CI)
MODE_OFF, MODE_OBSERVE, MODE_ENFORCE = "off", "observe", "enforce"
MODES = frozenset({MODE_OFF, MODE_OBSERVE, MODE_ENFORCE})
ENV_MASTER = "AGENT_SKILLS_VERIFY_GATE"
MODE_FILE_PARTS = ("agent-skills", "verify-gate")
MODE_FILE_MAX_BYTES = 4096

SUB_PRE, SUB_POST, SUB_STOP = "pre-tool", "post-tool", "stop"
STDIN_MAX_BYTES = 4 * 1024 * 1024
DEADLINE_SECONDS = 8
PRE_TOOL_BLOCK_EXIT = 2
MAX_BLOCKS_PER_GATE = 2
REASON_MAX_CHARS = 400
BRANCH_MAX_CHARS = 60
REASON_PREFIX = "[verify-gate]"

DECISIONS_FILE = "decisions.jsonl"
RECORD_KEYS = ("ts", "event", "session_id", "gate", "mode", "decision", "reason_code",
               "repo", "branch", "pr", "head_sha", "tree_sha", "path", "duration_ms")
EVENT_DECISION, EVENT_LINK = "decision", "link"
D_ALLOW, D_WOULD_BLOCK, D_BLOCK, D_RELEASED = "allow", "would_block", "block", "released"
PATH_EARLY, PATH_FULL = "early-exit", "full-check"

R_VERIFIED = "verified"
R_NO_LINKS = "no-links"
R_NO_PROFILE = "no-profile"
R_PROFILE_STALE = "profile-stale"
R_NOT_UI = "not-ui"
R_CI_RED = "ci-red"
R_CI_NOT_CONFIGURED = "ci-not-configured"
R_DECLARED = "declared-unverified"
R_REPO_UNAVAILABLE = "repo-unavailable"
VERDICT_PROFILE_STALE = "PROFILE-STALE"
CI_DECLARED = frozenset({"pending-declared", "exempt"})

SUB_GATES = {SUB_PRE: (GATE_PRE_PR,), SUB_POST: STOP_GATES, SUB_STOP: STOP_GATES}
PUSH_FAILURE_MARKERS = ("failed to push", "[rejected]", "[remote rejected]",
                        "has no upstream branch", "does not appear to be a git repository",
                        "could not read from remote repository", "authentication failed",
                        "permission denied")
GENERIC_FAILURE_MARKERS = ("error:", "fatal:", "traceback (most recent call last)", "usage:",
                           '"result": "error"', "could not", "must first push",
                           "no commits between")
HEAD_OPTIONS = frozenset({"--head", "-H"})
REPO_OPTIONS = frozenset({"--repo"})
BRANCH_OPTIONS = frozenset({"--branch"})
_PULL_URL = re.compile(r"/pull/(\d{1,9})(?!\d)")

ENGINE = "verify_engine.py"
_HINTS = {
    "pr-unregistered": f"{ENGINE} pr register --from-gh（沒有 PR 就加 --none）",
    "registration-stale": f"在該 branch 重跑 {ENGINE} pr register --from-gh",
    "head-unresolved": f"在該 branch 重跑 {ENGINE} pr register --from-gh",
    R_CI_RED: f"修好失敗的必要 job，CI 轉綠後重跑 {ENGINE} receipt ci --from-gh",
}
_GATE_HINTS = {
    GATE_PRE_PR: f"在最終 HEAD、乾淨工作樹上跑 {ENGINE} checks",
    GATE_UI: f"以 /pr-evidence-comment 驗收後跑 {ENGINE} receipt acceptance",
    GATE_CI: f"CI 跑完後跑 {ENGINE} receipt ci --from-gh",
}
_GATE_LABELS = {GATE_PRE_PR: "開 PR 前快檢", GATE_UI: "UI 驗收", GATE_CI: "CI 證據"}


class _Deadline(BaseException):
    """Raised by SIGALRM when the hook runs past DEADLINE_SECONDS."""


class ToolResult(NamedTuple):
    stdout: str
    stderr: str
    interrupted: bool


class Outcome(NamedTuple):
    ok: bool
    reason: str
    repo: Optional[str]
    branch: Optional[str]
    pr: Optional[int]
    head: Optional[str]
    tree: Optional[str] = None


class Ctx(NamedTuple):
    session_id: str
    state: Path
    modes: Mapping[str, str]
    started: float


# --- payload -------------------------------------------------------------------------

def read_payload(raw: bytes) -> Optional[dict]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def text_field(data: Mapping, key: str) -> Optional[str]:
    value = data.get(key)
    return value if isinstance(value, str) and value else None


def bash_command(data: Mapping) -> Optional[str]:
    if data.get("tool_name") != "Bash":
        return None
    tool_input = data.get("tool_input")
    return text_field(tool_input, "command") if isinstance(tool_input, dict) else None


def payload_cwd(data: Mapping) -> Optional[str]:
    cwd = text_field(data, "cwd")
    return cwd if cwd and os.path.isabs(cwd) and os.path.isdir(cwd) else None


def tool_result(data: Mapping) -> ToolResult:
    response = data.get("tool_response")
    if isinstance(response, str):
        return ToolResult(response, "", False)
    if not isinstance(response, dict):
        return ToolResult("", "", False)
    out, err = response.get("stdout"), response.get("stderr")
    return ToolResult(out if isinstance(out, str) else "", err if isinstance(err, str) else "",
                      response.get("interrupted") is True)


# --- modes -----------------------------------------------------------------------------

def _normalize_mode(value: str) -> str:
    mode = value.strip().lower()
    return mode if mode in MODES else MODE_OFF


def mode_file_path(environ: Mapping[str, str]) -> Optional[Path]:
    xdg = environ.get("XDG_CONFIG_HOME", "").strip()
    home = environ.get("HOME", "").strip()
    base = Path(xdg) if xdg else (Path(home) / ".config" if home else None)
    return base.joinpath(*MODE_FILE_PARTS) if base else None


def read_mode_file(environ: Mapping[str, str]) -> dict:
    """`<gate>=<mode>` per line; `#` comments and unknown gates are ignored."""
    path = mode_file_path(environ)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read(MODE_FILE_MAX_BYTES)
    except (OSError, TypeError):
        return {}
    found = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        gate, sep, mode = line.partition("=")
        if sep and gate.strip() in GATES:
            found[gate.strip()] = mode.strip()
    return found


def gate_modes(environ: Mapping[str, str]) -> dict:
    """Per-gate env, then the master env, then the mode file; else off."""
    from_file = None
    modes = {}
    for gate in GATES:
        value = environ.get(f"{ENV_MASTER}_{gate.upper()}", "").strip()
        value = value or environ.get(ENV_MASTER, "").strip()
        if not value:
            from_file = read_mode_file(environ) if from_file is None else from_file
            value = from_file.get(gate, "")
        modes[gate] = _normalize_mode(value)
    return modes


# --- state, records, block counts -------------------------------------------------------

def _within_home(path: Path, environ: Mapping[str, str]) -> bool:
    home = environ.get("HOME", "").strip()
    if not home or not os.path.isdir(home):
        return False
    real_home = os.path.realpath(home)
    return os.path.realpath(str(path)).startswith(real_home.rstrip(os.sep) + os.sep)


def checked_state_root(environ: Mapping[str, str]) -> Optional[Path]:
    """The state directory, created 0700, or None when it must not be used."""
    import verify_receipts as vr
    root = vr.state_root(None)
    if not _within_home(root, environ):
        return None
    vr.safe_dir(root, create=True, private=True)
    return root


def clean_branch(branch: Optional[str]) -> Optional[str]:
    if branch is None:
        return None
    cleaned = "".join(ch if ch.isprintable() and not ch.isspace() else "?" for ch in branch)
    return cleaned[:BRANCH_MAX_CHARS] + ("…" if len(cleaned) > BRANCH_MAX_CHARS else "")


def append_record(state: Path, record: Mapping) -> None:
    """Append one JSON line with a single O_APPEND write; never rewrites."""
    import verify_receipts as vr
    line = json.dumps({key: record.get(key) for key in RECORD_KEYS}, ensure_ascii=False)
    data = (line + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(state / DECISIONS_FILE, flags | getattr(os, "O_CLOEXEC", 0), vr.FILE_MODE)
    try:
        if os.write(fd, data) != len(data):
            raise OSError("short write to decisions.jsonl")
    finally:
        os.close(fd)


def make_record(ctx: Ctx, *, event: str = EVENT_DECISION, gate: Optional[str] = None,
                decision: Optional[str] = None, outcome: Optional[Outcome] = None,
                reason: Optional[str] = None, path: Optional[str] = None) -> dict:
    import verify_receipts as vr
    out = outcome or Outcome(True, reason or "", None, None, None, None)
    return {"ts": vr.now_iso(), "event": event, "session_id": ctx.session_id, "gate": gate,
            "mode": ctx.modes.get(gate) if gate else None, "decision": decision,
            "reason_code": reason if reason is not None else (out.reason or None),
            "repo": out.repo, "branch": clean_branch(out.branch),
            "pr": out.pr if isinstance(out.pr, int) else None, "head_sha": out.head,
            "tree_sha": out.tree, "path": path,
            "duration_ms": int((time.perf_counter() - ctx.started) * 1000)}


def record_early(ctx: Ctx, gates: tuple, reason: str) -> None:
    for gate in gates:
        append_record(ctx.state, make_record(ctx, gate=gate, decision=D_ALLOW, reason=reason,
                                             path=PATH_EARLY))


def blocks_so_far(ctx: Ctx, gate: str) -> int:
    import verify_receipts as vr
    session = vr.read_session(ctx.session_id, state_dir=str(ctx.state)) or {}
    value = (session.get("blocks") or {}).get(gate) if isinstance(session.get("blocks"), dict) else 0
    return value if isinstance(value, int) and value >= 0 else 0


def decide_gate(ctx: Ctx, gate: str, outcomes: tuple) -> tuple:
    """Record every outcome of one gate; return the outcomes that block now."""
    import verify_receipts as vr
    failing = tuple(o for o in outcomes if not o.ok)
    mode = ctx.modes[gate]
    verdict = D_WOULD_BLOCK if mode == MODE_OBSERVE else D_BLOCK
    if failing and mode == MODE_ENFORCE:
        if blocks_so_far(ctx, gate) >= MAX_BLOCKS_PER_GATE:
            verdict = D_RELEASED
        else:
            vr.bump_session_blocks(ctx.session_id, gate, state_dir=str(ctx.state))
    for outcome in outcomes:
        decision = D_ALLOW if outcome.ok else verdict
        append_record(ctx.state, make_record(ctx, gate=gate, decision=decision,
                                             outcome=outcome, path=PATH_FULL))
    return failing if verdict == D_BLOCK else ()


def _item_text(gate: str, outcome: Outcome) -> str:
    hint = _HINTS.get(outcome.reason, _GATE_HINTS[gate])
    pr = f"PR {outcome.pr}" if isinstance(outcome.pr, int) else "未註冊 PR"
    branch = clean_branch(outcome.branch) or "未知"
    return (f"{_GATE_LABELS[gate]}（{gate}）未通過：{outcome.reason}，branch {branch}，"
            f"{pr}。下一步：{hint}")


def block_reason(blocked: tuple) -> str:
    """Static template; only gate, reason code, branch, PR and a hint go in."""
    text = REASON_PREFIX
    for index, (gate, outcome) in enumerate(blocked):
        left = len(blocked) - index - 1
        tail = f"（另有 {left} 項）" if left else ""
        candidate = f"{text} {_item_text(gate, outcome)}"
        if len(candidate) + len(tail) <= REASON_MAX_CHARS:
            text = candidate
            continue
        more = f"（另有 {left + 1} 項）"
        text = candidate[:REASON_MAX_CHARS - len(tail)] + tail if index == 0 else text + more
        break
    return text[:REASON_MAX_CHARS]


# --- profile lookups (no git) -----------------------------------------------------------

def load_profile(key: Optional[str]):
    """The matched Profile for a normalized repo key, or None."""
    import verify_profile as vp
    if not key:
        return None
    return vp.load_context(key).profile


def ui_change(root: str, profile, head: str) -> bool:
    """True when the branch (merge-base with the default base .. head) touches UI.

    When no merge-base can be found the branch is treated as a UI change, so
    the acceptance gate asks for evidence rather than silently passing.
    """
    import verify_git as vg
    import verify_profile as vp
    if not vp.path_globs(profile, "UI"):
        return False
    base = vp.default_base(profile)
    merge = vg.merge_base(root, head, base) or vg.merge_base(root, head, f"origin/{base}")
    names = vg.diff_names(root, merge, head) if merge else None
    if names is None:
        return True
    return any(vp.is_ui_path(profile, name) for name in names)


# --- stop: per-link evaluation ----------------------------------------------------------

def _outcome(ok: bool, reason: str, link: Mapping, pr=None, head=None) -> Outcome:
    return Outcome(ok, reason, link.get("repo"), link.get("branch"), pr, head)


def _ci_outcome(link: Mapping, profile, entry: Mapping, head: str) -> Outcome:
    import verify_profile as vp
    import verify_receipts as vr
    pr = entry.get("pr")
    if not vp.required_jobs(profile):
        return _outcome(True, R_CI_NOT_CONFIGURED, link, pr, head)
    validity = vr.check_ci_receipt(link["repo_path"], link["branch"])
    if not validity.valid:
        return _outcome(False, validity.reason, link, pr, head)
    conclusion = validity.receipt.get("conclusion")
    if conclusion == "red":
        return _outcome(False, R_CI_RED, link, pr, head)
    reason = R_DECLARED if conclusion in CI_DECLARED else R_VERIFIED
    return _outcome(True, reason, link, pr, head)


def _ui_outcome(link: Mapping, entry: Mapping, head: str) -> Outcome:
    import verify_receipts as vr
    pr = entry.get("pr")
    validity = vr.check_acceptance_receipt(link["repo_path"], link["branch"])
    if not validity.valid:
        return _outcome(False, validity.reason, link, pr, head)
    exempt = validity.receipt.get("kind") == "exempt"
    return _outcome(True, R_DECLARED if exempt else R_VERIFIED, link, pr, head)


def evaluate_link(gate: str, link: Mapping, profile) -> Outcome:
    """One gate's verdict for one linked (repo, branch)."""
    import verify_receipts as vr
    repo, branch = link["repo_path"], link["branch"]
    entry = vr.read_registry_entry(repo, branch)
    head = vr.resolve_head(repo, branch).sha
    pr = entry.get("pr") if entry else None
    if gate == GATE_UI and head and not ui_change(repo, profile, head):
        return _outcome(True, R_NOT_UI, link, pr, head)
    state = entry.get("state") if entry else None
    if state in (None, vr.STATE_UNREGISTERED):
        return _outcome(False, vr.R_UNREGISTERED, link, pr, head)
    if state in (vr.STATE_CLOSED, vr.STATE_NONE):
        exemption = vr.check_exemption(repo, branch)
        return _outcome(exemption.valid, exemption.reason, link, pr, head)
    if head is None:
        return _outcome(False, vr.R_HEAD_UNRESOLVED, link, pr, None)
    if gate == GATE_CI:
        return _ci_outcome(link, profile, entry, head)
    return _ui_outcome(link, entry, head)


def session_links(ctx: Ctx) -> tuple:
    import verify_receipts as vr
    session = vr.read_session(ctx.session_id, state_dir=str(ctx.state)) or {}
    links = session.get("links") if isinstance(session.get("links"), list) else []
    return tuple(l for l in links if isinstance(l, dict) and isinstance(l.get("repo"), str)
                 and isinstance(l.get("repo_path"), str) and isinstance(l.get("branch"), str))


def link_usable(link: Mapping) -> bool:
    """The recorded repo still exists at its path with the same remote."""
    import verify_receipts as vr
    return vr.repo_info(link["repo_path"]).key == link["repo"]


def run_stop(ctx: Ctx) -> int:
    gates = tuple(g for g in STOP_GATES if ctx.modes[g] != MODE_OFF)
    links = session_links(ctx)
    if not links:
        record_early(ctx, gates, R_NO_LINKS)
        return 0
    profiles = {key: load_profile(key) for key in {l["repo"] for l in links}}
    linked = tuple(l for l in links if profiles[l["repo"]] is not None)
    if not linked:
        record_early(ctx, gates, R_NO_PROFILE)
        return 0
    if shutil.which("git") is None:
        return 0
    usable = tuple(l for l in linked if link_usable(l))
    skipped = tuple(_outcome(True, R_REPO_UNAVAILABLE, l) for l in linked if l not in usable)
    blocked = []
    for gate in gates:
        outcomes = tuple(evaluate_link(gate, l, profiles[l["repo"]]) for l in usable)
        blocked.extend((gate, o) for o in decide_gate(ctx, gate, outcomes + skipped))
    if blocked:
        payload = {"decision": "block", "reason": block_reason(tuple(blocked))}
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


# --- pre-tool -----------------------------------------------------------------------------

def _quick_outcome(root: str, key: str) -> Outcome:
    import verify_git as vg
    import verify_receipts as vr
    validity = vr.check_quick_receipt(root)
    receipt = validity.receipt or {}
    ok = validity.valid or (validity.reason == vr.R_REQUIRED
                            and receipt.get("verdict") == VERDICT_PROFILE_STALE)
    reason = R_VERIFIED if validity.valid else (R_PROFILE_STALE if ok else validity.reason)
    return Outcome(ok, reason, key, vg.current_branch(root), None,
                   vg.resolve_commit(root, "HEAD"), vg.head_tree(root))


def run_pre_tool(ctx: Ctx, data: Mapping, invocations: tuple) -> int:
    import verify_receipts as vr
    target = invocations[0].cwd
    info = vr.repo_info(target) if target and os.path.isdir(target) else None
    if info is None or not info.root or not info.key:
        return 0
    if load_profile(info.key) is None:
        record_early(ctx, (GATE_PRE_PR,), R_NO_PROFILE)
        return 0
    outcome = _quick_outcome(info.root, info.key)
    if decide_gate(ctx, GATE_PRE_PR, (outcome,)):
        sys.stderr.write(block_reason(((GATE_PRE_PR, outcome),)) + "\n")
        sys.stderr.flush()
        return PRE_TOOL_BLOCK_EXIT
    return 0


# --- post-tool ----------------------------------------------------------------------------

def push_succeeded(result: ToolResult) -> bool:
    """Not interrupted and no push-specific failure text; unsure counts as pushed."""
    if result.interrupted:
        return False
    text = f"{result.stderr}\n{result.stdout}".lower()
    return not any(marker in text for marker in PUSH_FAILURE_MARKERS)


def created_pr(result: ToolResult) -> tuple:
    """(succeeded, PR number or None) for a `gh pr create` result."""
    if result.interrupted:
        return False, None
    found = _PULL_URL.search(result.stdout)
    if found is None and "already exists" in result.stderr.lower():
        found = _PULL_URL.search(result.stderr)
    if found:
        return True, int(found.group(1))
    text = f"{result.stderr}\n{result.stdout}".lower()
    return not any(marker in text for marker in GENERIC_FAILURE_MARKERS), None


def registered(result: ToolResult) -> bool:
    if result.interrupted:
        return False
    text = f"{result.stderr}\n{result.stdout}".lower()
    if '"result": "registered"' in text:
        return True
    return not any(marker in text for marker in GENERIC_FAILURE_MARKERS)


def profiled_repo(directory: Optional[str]):
    """RepoInfo of a git repo that has exactly one matching profile, else None."""
    import verify_receipts as vr
    if not directory or not os.path.isdir(directory):
        return None
    info = vr.repo_info(directory)
    if not info.root or not info.key or load_profile(info.key) is None:
        return None
    return info


def link_branch(ctx: Ctx, info, branch: str, pr: Optional[int] = None) -> None:
    """Record (repo, branch) for this session, keeping a PR number seen earlier."""
    import verify_receipts as vr
    if pr is None:
        known = [l.get("pr") for l in session_links(ctx)
                 if l["repo"] == info.key and l["branch"] == branch]
        pr = known[0] if known and isinstance(known[0], int) else None
    vr.record_session_link(ctx.session_id, info.root, branch, state_dir=str(ctx.state), pr=pr)


def handle_push(ctx: Ctx, invocation, result: ToolResult) -> None:
    import verify_gate_cmd as vcmd
    import verify_git as vg
    import verify_receipts as vr
    target = vcmd.push_target(invocation.args)
    if target.kind == vcmd.PUSH_NONE or not push_succeeded(result):
        return
    info = profiled_repo(invocation.cwd)
    if info is None:
        return
    branch = target.branch if target.kind == vcmd.PUSH_BRANCH else None
    if target.kind == vcmd.PUSH_CURRENT:
        branch = vg.current_branch(info.root)
    if not branch:
        vr.mark_push_unresolved(info.root, state_dir=str(ctx.state))
        return
    vr.record_push(info.root, branch, state_dir=str(ctx.state))
    link_branch(ctx, info, branch)


def handle_pr_create(ctx: Ctx, invocation, result: ToolResult) -> None:
    import verify_gate_cmd as vcmd
    import verify_git as vg
    succeeded, pr = created_pr(result)
    info = profiled_repo(invocation.cwd) if succeeded else None
    if info is None:
        return
    head = vcmd.option_value(invocation.args, HEAD_OPTIONS)
    branch = head.split(":", 1)[-1] if head else vg.current_branch(info.root)
    if not branch:
        return
    link_branch(ctx, info, branch, pr)
    if pr is not None:
        outcome = Outcome(True, "", info.key, branch, pr, vg.branch_head(info.root, branch))
        append_record(ctx.state, make_record(ctx, event=EVENT_LINK, outcome=outcome))


def handle_register(ctx: Ctx, invocation, result: ToolResult) -> None:
    import verify_gate_cmd as vcmd
    import verify_git as vg
    import verify_receipts as vr
    if not registered(result):
        return
    repo = vcmd.option_value(invocation.args, REPO_OPTIONS) or "."
    base = invocation.cwd if invocation.cwd else None
    directory = repo if os.path.isabs(repo) else (os.path.join(base, repo) if base else None)
    info = profiled_repo(directory)
    if info is None:
        return
    branch = vcmd.option_value(invocation.args, BRANCH_OPTIONS) or vg.current_branch(info.root)
    if not branch:
        return
    entry = vr.read_registry_entry(info.root, branch, state_dir=str(ctx.state)) or {}
    link_branch(ctx, info, branch, entry.get("pr") if isinstance(entry.get("pr"), int) else None)


def run_post_tool(ctx: Ctx, data: Mapping, invocations: tuple) -> int:
    import verify_gate_cmd as vcmd
    handlers = {vcmd.KIND_PUSH: handle_push, vcmd.KIND_PR_CREATE: handle_pr_create,
                vcmd.KIND_REGISTER: handle_register}
    result = tool_result(data)
    for invocation in invocations:
        handlers[invocation.kind](ctx, invocation, result)
    return 0


# --- entry point ----------------------------------------------------------------------------

def session_of(data: Mapping) -> Optional[str]:
    import verify_receipts as vr
    raw = text_field(data, "session_id")
    try:
        return vr.sanitize_session_id(raw) if raw else None
    except ValueError:
        return None


def wanted_invocations(sub: str, data: Mapping, environ: Mapping[str, str]) -> tuple:
    import verify_gate_cmd as vcmd
    command = bash_command(data)
    if command is None:
        return ()
    kinds = {SUB_PRE: (vcmd.KIND_PR_CREATE,),
             SUB_POST: (vcmd.KIND_PUSH, vcmd.KIND_PR_CREATE, vcmd.KIND_REGISTER)}[sub]
    found = vcmd.find_invocations(command, payload_cwd(data), environ.get("HOME") or None)
    return tuple(i for i in found if i.kind in kinds)


def dispatch(sub: Optional[str], raw: bytes, environ: Mapping[str, str], started: float) -> int:
    if sub not in SUB_GATES:
        return 0
    modes = gate_modes(environ)
    if all(modes[gate] == MODE_OFF for gate in SUB_GATES[sub]):
        return 0
    data = read_payload(raw)
    if data is None:
        return 0
    invocations = () if sub == SUB_STOP else wanted_invocations(sub, data, environ)
    if sub != SUB_STOP and not invocations:
        return 0
    session = session_of(data)
    state = checked_state_root(environ) if session else None
    if state is None:
        return 0
    ctx = Ctx(session, state, modes, started)
    if sub == SUB_STOP:
        return run_stop(ctx)
    runner = run_pre_tool if sub == SUB_PRE else run_post_tool
    return runner(ctx, data, invocations)


def _expire(_signum, _frame) -> None:
    raise _Deadline()


def _arm_deadline():
    if not hasattr(signal, "setitimer"):
        return None
    previous = signal.signal(signal.SIGALRM, _expire)
    signal.setitimer(signal.ITIMER_REAL, DEADLINE_SECONDS)
    return previous


def _disarm(previous) -> None:
    if previous is not None:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def main(argv: list) -> int:
    """Run one hook event; every failure path returns 0 with nothing printed."""
    started, previous = time.perf_counter(), None
    try:
        warnings.simplefilter("ignore")
        previous = _arm_deadline()
        raw = sys.stdin.buffer.read(STDIN_MAX_BYTES + 1)
        if len(raw) > STDIN_MAX_BYTES or len(argv) != 1:
            return 0
        return dispatch(argv[0], raw, os.environ, started)
    except BaseException:  # fail-open: includes the deadline and SystemExit
        return 0
    finally:
        try:
            _disarm(previous)
        except BaseException:
            pass


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
