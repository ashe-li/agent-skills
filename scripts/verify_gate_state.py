#!/usr/bin/env python3
"""Shared pieces of the verify gate hook: constants, payload fields, modes,
the state directory, decision records, session links and the worktree map.

verify_gate.py (pre-tool, stop, entry point) and verify_gate_post.py
(post-tool) both use these. Engine modules are imported lazily inside the
functions so the mode-off path stays cheap. stdlib only.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Mapping, NamedTuple, Optional

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
R_CWD_UNRESOLVED = "cwd-unresolved"
R_UNPARSED = "unparsed"
R_HOOK_TIMEOUT, R_HOOK_ERROR = "hook-timeout", "hook-error"
R_RECEIPTS_UNAVAILABLE = "receipts-unavailable"
R_LINK_UNRESOLVED = "link-unresolved"
WORKTREES_FILE, WORKTREES_SCHEMA, WORKTREES_MAX = "gate-worktrees.json", "verify-gate-worktrees/1", 500
_REASON_BRANCH_UNSAFE = re.compile(r"[^A-Za-z0-9._/-]")
RUN: dict = {}  # context and phase of the running hook, for the fail-open record
VERDICT_PROFILE_STALE = "PROFILE-STALE"
CI_DECLARED = frozenset({"pending-declared", "exempt"})

SUB_GATES = {SUB_PRE: (GATE_PRE_PR,), SUB_POST: STOP_GATES, SUB_STOP: STOP_GATES}


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


def reason_branch(branch: Optional[str]) -> str:
    """Branch name for a block reason: only [A-Za-z0-9._/-], truncated."""
    if not branch:
        return "未知"
    cleaned = _REASON_BRANCH_UNSAFE.sub("_", branch)
    return cleaned[:BRANCH_MAX_CHARS] + ("..." if len(cleaned) > BRANCH_MAX_CHARS else "")


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


# --- profile lookups (no git) -----------------------------------------------------------

def load_profile(key: Optional[str]):
    """The matched Profile for a normalized repo key, or None."""
    import verify_profile as vp
    if not key:
        return None
    return vp.load_context(key).profile


def session_links(ctx: Ctx) -> tuple:
    import verify_receipts as vr
    session = vr.read_session(ctx.session_id, state_dir=str(ctx.state)) or {}
    links = session.get("links") if isinstance(session.get("links"), list) else []
    return tuple(l for l in links if isinstance(l, dict) and isinstance(l.get("repo"), str)
                 and isinstance(l.get("repo_path"), str) and isinstance(l.get("branch"), str))


def remember_worktree(ctx: Ctx, root: str) -> None:
    """Map a linked worktree to its git common dir, for when it is removed later."""
    import verify_git as vg
    import verify_receipts as vr
    common = vg.git_line(root, "rev-parse", "--git-common-dir")
    if not common:
        return
    common = os.path.normpath(os.path.join(root, common))
    try:
        with vr.state_lock(ctx.state):
            kept = dict(list(worktree_roots(ctx).items())[-(WORKTREES_MAX - 1):])
            kept.pop(root, None)
            vr.write_bytes_atomic(ctx.state / WORKTREES_FILE, vr.dump_json(
                {"schema": WORKTREES_SCHEMA, "roots": {**kept, root: common}}))
    except (vr.StateError, OSError):
        return  # the map is a fallback only; a broken one just means no fallback


def worktree_roots(ctx: Ctx) -> dict:
    """The worktree -> common dir map; {} when missing, a symlink or malformed."""
    import verify_receipts as vr
    data = vr.read_json(ctx.state / WORKTREES_FILE, strict=False) or {}
    roots = data.get("roots") if data.get("schema") == WORKTREES_SCHEMA else None
    if not isinstance(roots, dict):
        return {}
    return {k: v for k, v in roots.items() if isinstance(k, str) and isinstance(v, str)}


# --- entry point ----------------------------------------------------------------------------

def session_of(data: Mapping) -> Optional[str]:
    import verify_receipts as vr
    raw = text_field(data, "session_id")
    try:
        return vr.sanitize_session_id(raw) if raw else None
    except ValueError:
        return None
