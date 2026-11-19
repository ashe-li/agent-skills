#!/usr/bin/env python3
"""Receipts, the PR registry and validity checks (stdlib only).

This module is the single implementation of "is this receipt still valid".
The engine (verify_engine.py) writes receipts through it and the gate hook
imports it to decide; neither keeps its own copy of the rules. The rules and
reason codes are documented in docs/knowledge-source-contract.md (sections
快檢 receipt, 驗收 receipt, CI receipt, PR 註冊表, head 決定順序, 免檢規則).

Local state (registry, push markers, session links) lives in a state
directory: the `state_dir` argument, else $AGENT_SKILLS_VERIFY_STATE_DIR,
else ~/.claude/verify-gate/. Directories are 0700 and files 0600; a symlink
anywhere from the state directory down is refused with StateError, never
followed. Writes go to a temp file first and are moved in with os.replace.
Read-modify-write sequences hold an advisory flock on `<state>/.lock`.

Layout (an implementation detail; use the functions):
    registry/<sha256(repo)[:32]>/<sha256(branch)[:32]>.json
    repos/<sha256(repo)[:32]>.json      push-unresolved marker
    sessions/<sanitized session id>.json
"""
from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, NamedTuple, Optional

import kb_source
import verify_git as vg
import verify_profile as vp

try:
    import fcntl
except ImportError:  # non-POSIX: run without the advisory lock
    fcntl = None

ENGINE_VERSION = "1.0.0"
ENV_STATE_DIR = "AGENT_SKILLS_VERIFY_STATE_DIR"
DEFAULT_STATE_PARTS = (".claude", "verify-gate")
DIR_MODE, FILE_MODE = 0o700, 0o600
HASH_CHARS = 32
LOCK_NAME = ".lock"
REGISTRY_DIR, REPOS_DIR, SESSIONS_DIR = "registry", "repos", "sessions"
RECEIPTS_PARTS = (vg.VERIFICATION_DIRNAME, "receipts")
SESSION_ID_MAX = 128
_SESSION_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")

SCHEMA_QUICK = "verify-receipt/1"
SCHEMA_CI = "verify-ci-receipt/1"
SCHEMA_ACCEPTANCE = "verify-acceptance-receipt/1"
SCHEMA_REGISTRY = "verify-pr-registry/1"
SCHEMA_REPO_STATE = "verify-repo-state/1"
SCHEMA_SESSION = "verify-session/1"

STATE_OPEN, STATE_CLOSED, STATE_NONE, STATE_UNREGISTERED = "open", "closed", "none", "unregistered"
SOURCE_LOCAL, SOURCE_PUSHED, SOURCE_REG, SOURCE_UNRESOLVED = (
    "local-ref", "last-pushed", "registration", "unresolved")
VERDICT_PASS, CHECK_PASS = "PASS", "PASS"
CI_CONCLUSIONS = frozenset({"green", "red", "pending-declared", "exempt"})
ACCEPTANCE_KINDS = frozenset({"evidence-comment", "exempt"})

R_NO_RECEIPT = "no-receipt"
R_DIRTY_NOW = "dirty-now"
R_DIRTY_AT_VERIFY = "dirty-at-verify"
R_STALE_CONFIG = "stale-config"
R_STALE_BASE = "stale-base"
R_ENGINE = "engine-changed"
R_REQUIRED = "required-incomplete"
R_STALE_CI = "stale-ci-config"
R_UI_CHANGED = "ui-changed"
R_HEAD_UNRESOLVED = "head-unresolved"
R_REG_STALE = "registration-stale"
R_NOT_EXEMPT = "not-exempt"
R_UNREGISTERED = "pr-unregistered"
R_EXEMPT_CLOSED = "exempt-closed"
R_EXEMPT_NONE = "exempt-none"
R_UI_NO_PROFILE = "no-profile"
R_UI_NO_PATHS = "no-ui-paths"
R_UI_DIFF_UNAVAILABLE = "diff-unavailable"

_ACCEPTANCE_NAME = re.compile(r"^acceptance-pr-(\d+)-([0-9a-f]{40}(?:[0-9a-f]{24})?)\.json$")


class StateError(Exception):
    """The state directory or a state file cannot be used safely."""


class Validity(NamedTuple):
    valid: bool
    reason: str
    receipt: Optional[dict]


class HeadResolution(NamedTuple):
    sha: Optional[str]
    source: str
    reason: str


class RepoInfo(NamedTuple):
    path: str
    root: Optional[str]
    key: Optional[str]


def _invalid(reason: str, receipt: Optional[dict] = None) -> Validity:
    return Validity(False, reason, receipt)


# --- time -------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_time(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# --- safe filesystem ---------------------------------------------------------------

def state_root(state_dir: Optional[str] = None) -> Path:
    if state_dir:
        return Path(state_dir)
    env = os.environ.get(ENV_STATE_DIR, "").strip()
    if env:
        return Path(env)
    return Path.home().joinpath(*DEFAULT_STATE_PARTS)


def _check_dir(path: Path) -> bool:
    """True if path is a real directory, False if missing; StateError otherwise."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise StateError(f"cannot inspect {path}: {exc.strerror}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise StateError(f"refusing to use {path}: it is a symlink")
    if not stat.S_ISDIR(info.st_mode):
        raise StateError(f"refusing to use {path}: it is not a directory")
    return True


def _make_dir(path: Path) -> None:
    try:
        os.mkdir(path, DIR_MODE)
    except FileExistsError:
        _check_dir(path)
        return
    except OSError as exc:
        raise StateError(f"cannot create {path}: {exc.strerror}") from exc
    os.chmod(path, DIR_MODE)


def _tighten(path: Path) -> None:
    """Make an existing state directory we own private (0700)."""
    try:
        info = os.lstat(path)
        if info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) != DIR_MODE:
            os.chmod(path, DIR_MODE)
    except OSError as exc:
        raise StateError(f"cannot secure {path}: {exc.strerror}") from exc


def safe_dir(base: Path, *parts: str, create: bool, private: bool = False) -> Optional[Path]:
    """Walk base/parts refusing symlinks; create missing parts when asked.

    private=True (state directories) also tightens existing directories we
    own to 0700; repo directories such as `.verification/` are left as found.
    """
    if create:
        try:
            base.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StateError(f"cannot create {base.parent}: {exc.strerror}") from exc
    current = base
    for part in ("", *parts):
        current = current / part if part else current
        if not _check_dir(current):
            if not create:
                return None
            _make_dir(current)
        elif create and private:
            _tighten(current)
    return current


def state_dir_path(state_dir: Optional[str], *parts: str, create: bool) -> Optional[Path]:
    return safe_dir(state_root(state_dir), *parts, create=create, private=True)


def write_bytes_atomic(path: Path, data: bytes) -> None:
    if os.path.islink(path):
        raise StateError(f"refusing to write {path}: it is a symlink")
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, FILE_MODE)
        os.replace(tmp, path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise StateError(f"cannot write {path}: {exc.strerror}") from exc


def dump_json(data: dict) -> bytes:
    return (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def read_json(path: Path, *, strict: bool) -> Optional[dict]:
    """Read a JSON object without following a symlink at the last component."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    except OSError as exc:
        if strict and exc.errno in (errno.ELOOP, errno.EMLINK):
            raise StateError(f"refusing to read {path}: it is a symlink") from exc
        return None
    try:
        with os.fdopen(fd, "rb") as handle:
            data = json.loads(handle.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


@contextlib.contextmanager
def state_lock(root: Path) -> Iterator[None]:
    safe_dir(root, create=True, private=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(root / LOCK_NAME, flags, FILE_MODE)
    except OSError as exc:
        raise StateError(f"cannot open lock in {root}: {exc.strerror}") from exc
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:HASH_CHARS]


# --- repo identity and receipt files -------------------------------------------

def repo_info(repo: str) -> RepoInfo:
    root = vg.toplevel(str(repo))
    remote = kb_source.get_repo_remote(root) if root else None
    return RepoInfo(str(repo), root, kb_source.normalize_remote(remote) if remote else None)


def receipts_dir(root: str) -> Path:
    return Path(root).joinpath(*RECEIPTS_PARTS)


def quick_receipt_name(tree: str) -> str:
    return f"{tree}.json"


def ci_receipt_name(pr: int, head: str) -> str:
    return f"ci-pr-{int(pr)}-{head}.json"


def acceptance_receipt_name(pr: int, head: str) -> str:
    return f"acceptance-pr-{int(pr)}-{head}.json"


def write_receipt(root: str, name: str, data: dict) -> str:
    """Write a receipt under `<repo>/.verification/receipts/`; return its path."""
    directory = safe_dir(Path(root), *RECEIPTS_PARTS, create=True)
    path = directory / name
    write_bytes_atomic(path, dump_json(data))
    return str(path)


def read_receipt(root: str, name: str) -> Optional[dict]:
    return read_json(Path(root).joinpath(*RECEIPTS_PARTS, name), strict=False)


def quick_base_sha(root: str, profile: Optional[vp.Profile]) -> Optional[str]:
    """Merge-base of HEAD and the profile's default base (or origin/<base>)."""
    base = vp.default_base(profile)
    for ref in (base, f"origin/{base}"):
        found = vg.merge_base(root, "HEAD", ref)
        if found:
            return found
    return None


# --- registry ---------------------------------------------------------------------

def _entry_dir(state_dir: Optional[str], key: str, create: bool) -> Optional[Path]:
    return state_dir_path(state_dir, REGISTRY_DIR, _digest(key), create=create)


def _valid_entry(data: Optional[dict], key: str, branch: Optional[str]) -> bool:
    return (isinstance(data, dict) and data.get("schema") == SCHEMA_REGISTRY
            and data.get("repo") == key and (branch is None or data.get("branch") == branch))


def _read_entry(state_dir: Optional[str], key: str, branch: str) -> Optional[dict]:
    directory = _entry_dir(state_dir, key, create=False)
    if directory is None:
        return None
    data = read_json(directory / f"{_digest(branch)}.json", strict=True)
    return data if _valid_entry(data, key, branch) else None


def _write_entry(state_dir: Optional[str], entry: dict) -> dict:
    directory = _entry_dir(state_dir, entry["repo"], create=True)
    write_bytes_atomic(directory / f"{_digest(entry['branch'])}.json", dump_json(entry))
    return entry


def read_registry_entry(repo: str, branch: str, *, state_dir: Optional[str] = None
                        ) -> Optional[dict]:
    key = repo_info(repo).key
    return _read_entry(state_dir, key, branch) if key else None


def registry_entries(key: str, *, state_dir: Optional[str] = None) -> tuple:
    directory = _entry_dir(state_dir, key, create=False)
    if directory is None:
        return ()
    found = []
    for name in sorted(os.listdir(directory)):
        if name.endswith(".json") and not name.startswith("."):
            data = read_json(directory / name, strict=False)
            if _valid_entry(data, key, None):
                found.append(data)
    return tuple(found)


def find_entry_by_pr(key: str, pr: int, *, state_dir: Optional[str] = None) -> Optional[dict]:
    """The most recently updated entry of this repo whose current PR is `pr`."""
    matches = [e for e in registry_entries(key, state_dir=state_dir) if e.get("pr") == pr]
    matches.sort(key=lambda e: str(e.get("updated_at") or ""), reverse=True)
    return matches[0] if matches else None


def _new_entry(key: str, branch: str, now: str) -> dict:
    return {"schema": SCHEMA_REGISTRY, "repo": key, "branch": branch, "pr": None,
            "state": STATE_UNREGISTERED, "head_sha": None, "registered_at": None,
            "last_pushed_head": None, "updated_at": now, "history": []}


def _require_key(info: RepoInfo) -> str:
    if not info.key or not info.root:
        raise StateError(f"{info.path} is not a git repo with a parseable origin remote")
    return info.key


def register(repo: str, branch: str, pr: Optional[int], state: str, *,
             state_dir: Optional[str] = None, at: Optional[str] = None) -> dict:
    """Overwrite the branch's entry; a replaced PR number moves into history."""
    info = repo_info(repo)
    key, now = _require_key(info), at or now_iso()
    with state_lock(state_root(state_dir)):
        prev = _read_entry(state_dir, key, branch)
        history = list(prev.get("history") or []) if prev else []
        old_pr = prev.get("pr") if prev else None
        if old_pr is not None and old_pr != pr and old_pr not in history:
            history.append(old_pr)
        entry = {**_new_entry(key, branch, now), "pr": pr, "state": state,
                 "head_sha": vg.branch_head(info.root, branch), "registered_at": now,
                 "last_pushed_head": prev.get("last_pushed_head") if prev else None,
                 "history": history}
        return _write_entry(state_dir, entry)


def _repo_state_path(state_dir: Optional[str], key: str, create: bool) -> Optional[Path]:
    directory = state_dir_path(state_dir, REPOS_DIR, create=create)
    return None if directory is None else directory / f"{_digest(key)}.json"


def mark_push_unresolved(repo: str, *, state_dir: Optional[str] = None,
                         at: Optional[str] = None) -> None:
    """Record that a push happened whose branch could not be determined."""
    key = _require_key(repo_info(repo))
    with state_lock(state_root(state_dir)):
        path = _repo_state_path(state_dir, key, create=True)
        data = {"schema": SCHEMA_REPO_STATE, "repo": key, "push_unresolved_at": at or now_iso()}
        write_bytes_atomic(path, dump_json(data))


def push_unresolved_at(key: str, *, state_dir: Optional[str] = None) -> Optional[str]:
    path = _repo_state_path(state_dir, key, create=False)
    data = read_json(path, strict=True) if path else None
    if not data or data.get("repo") != key:
        return None
    value = data.get("push_unresolved_at")
    return value if parse_time(value) else None


def record_push(repo: str, branch: str, *, state_dir: Optional[str] = None,
                head_sha: Optional[str] = None, pushed_at: Optional[str] = None) -> dict:
    """Store `last_pushed_head` after a successful push.

    Without head_sha the branch's local ref is used; if that ref is missing
    too, the repo is marked push-unresolved and the current entry (or {}) is
    returned unchanged.
    """
    info = repo_info(repo)
    key = _require_key(info)
    sha = head_sha or vg.branch_head(info.root, branch)
    if not vg.is_sha(sha):
        if head_sha:
            raise ValueError("head_sha must be a full hexadecimal SHA")
        mark_push_unresolved(repo, state_dir=state_dir, at=pushed_at)
        return _read_entry(state_dir, key, branch) or {}
    when = pushed_at or now_iso()
    if parse_time(when) is None:
        raise ValueError("pushed_at must be an ISO 8601 time")
    with state_lock(state_root(state_dir)):
        entry = _read_entry(state_dir, key, branch) or _new_entry(key, branch, when)
        updated = {**entry, "last_pushed_head": {"sha": sha, "at": when}, "updated_at": now_iso()}
        return _write_entry(state_dir, updated)


# --- session links (written by the gate hook) --------------------------------------

def sanitize_session_id(session_id: str) -> str:
    cleaned = _SESSION_UNSAFE.sub("_", str(session_id or ""))[:SESSION_ID_MAX].lstrip(".")
    if not cleaned:
        raise ValueError("session id is empty after sanitizing")
    return cleaned


def _session_path(state_dir: Optional[str], session_id: str, create: bool) -> Optional[Path]:
    directory = state_dir_path(state_dir, SESSIONS_DIR, create=create)
    return None if directory is None else directory / f"{sanitize_session_id(session_id)}.json"


def read_session(session_id: str, *, state_dir: Optional[str] = None) -> Optional[dict]:
    path = _session_path(state_dir, session_id, create=False)
    data = read_json(path, strict=True) if path else None
    return data if data and data.get("schema") == SCHEMA_SESSION else None


def record_session_link(session_id: str, repo: str, branch: str, *,
                        state_dir: Optional[str] = None, pr: Optional[int] = None,
                        at: Optional[str] = None) -> dict:
    """Associate (repo, branch) with a session; one link per repo and branch."""
    info = repo_info(repo)
    key, when = _require_key(info), at or now_iso()
    with state_lock(state_root(state_dir)):
        path = _session_path(state_dir, session_id, create=True)
        data = read_json(path, strict=True) or {}
        links = [l for l in data.get("links") or []
                 if not (l.get("repo") == key and l.get("branch") == branch)]
        links.append({"repo": key, "repo_path": info.root, "branch": branch, "pr": pr, "at": when})
        session = {**data, "schema": SCHEMA_SESSION,
                   "session_id": sanitize_session_id(session_id), "links": links}
        write_bytes_atomic(path, dump_json(session))
        return session


def bump_session_blocks(session_id: str, gate: str, *, state_dir: Optional[str] = None) -> int:
    """Increment the session's `blocks[gate]` counter; return the new value.

    Written by the gate hook only. Links and unknown fields are preserved, and
    a session file that does not exist yet is created with no links.
    """
    with state_lock(state_root(state_dir)):
        path = _session_path(state_dir, session_id, create=True)
        data = read_json(path, strict=True) or {}
        blocks = data.get("blocks") if isinstance(data.get("blocks"), dict) else {}
        current = blocks.get(gate)
        value = (current if isinstance(current, int) and current >= 0 else 0) + 1
        session = {**data, "schema": SCHEMA_SESSION,
                   "session_id": sanitize_session_id(session_id),
                   "links": list(data.get("links") or []), "blocks": {**blocks, gate: value}}
        write_bytes_atomic(path, dump_json(session))
        return value


# --- head resolution ---------------------------------------------------------------

def _later_record(entry: Optional[dict]) -> Optional[tuple]:
    """(time, sha, source) of the later of last push and registration."""
    found = []
    if entry and vg.is_sha(entry.get("head_sha")) and parse_time(entry.get("registered_at")):
        found.append((parse_time(entry["registered_at"]), entry["head_sha"], SOURCE_REG))
    pushed = entry.get("last_pushed_head") if entry else None
    if isinstance(pushed, dict) and vg.is_sha(pushed.get("sha")) and parse_time(pushed.get("at")):
        found.append((parse_time(pushed["at"]), pushed["sha"], SOURCE_PUSHED))
    if not found:
        return None
    return max(found, key=lambda r: (r[0], r[2] == SOURCE_PUSHED))


def resolve_head(repo: str, branch: str, *, state_dir: Optional[str] = None) -> HeadResolution:
    """Local ref, else the later of last push and registration, else unresolved."""
    info = repo_info(repo)
    local = vg.branch_head(info.root, branch) if info.root else None
    if local:
        return HeadResolution(local, SOURCE_LOCAL, "")
    unresolved = HeadResolution(None, SOURCE_UNRESOLVED, R_HEAD_UNRESOLVED)
    if not info.key:
        return unresolved
    record = _later_record(_read_entry(state_dir, info.key, branch))
    if record is None:
        return unresolved
    marker = parse_time(push_unresolved_at(info.key, state_dir=state_dir))
    if marker is not None and marker >= record[0]:
        return unresolved
    return HeadResolution(record[1], record[2], "")


# --- validity: quick receipt ---------------------------------------------------------

def _required_passed(receipt: dict) -> bool:
    checks = receipt.get("checks")
    if receipt.get("verdict") != VERDICT_PASS or not isinstance(checks, list):
        return False
    return all(isinstance(c, dict) and (c.get("required") is not True or c.get("status") == CHECK_PASS)
               for c in checks)


def check_quick_receipt(repo: str, *, kb_home: Optional[str] = None) -> Validity:
    """Re-evaluate the quick-check receipt for the current HEAD tree."""
    info = repo_info(repo)
    tree = vg.head_tree(info.root) if info.root else None
    receipt = read_receipt(info.root, quick_receipt_name(tree)) if tree else None
    if (not receipt or receipt.get("schema") != SCHEMA_QUICK
            or receipt.get("tree_sha") != tree or receipt.get("repo") != info.key):
        return _invalid(R_NO_RECEIPT)
    if vg.is_dirty(info.root):
        return _invalid(R_DIRTY_NOW, receipt)
    if receipt.get("dirty") is not False:
        return _invalid(R_DIRTY_AT_VERIFY, receipt)
    ctx = vp.load_context(info.key, kb_home)
    if receipt.get("config_digest") != vp.config_digest(ctx):
        return _invalid(R_STALE_CONFIG, receipt)
    if receipt.get("base_sha") != quick_base_sha(info.root, ctx.profile):
        return _invalid(R_STALE_BASE, receipt)
    if receipt.get("engine_version") != ENGINE_VERSION:
        return _invalid(R_ENGINE, receipt)
    if not _required_passed(receipt):
        return _invalid(R_REQUIRED, receipt)
    return Validity(True, "", receipt)


# --- validity: PR-bound receipts ------------------------------------------------------

class _PrHead(NamedTuple):
    failure: Optional[Validity]
    info: Optional[RepoInfo]
    entry: Optional[dict]
    head: Optional[HeadResolution]


def _pr_head(repo: str, branch: str, state_dir: Optional[str]) -> _PrHead:
    """The branch's registered PR and its head, or the reason there is none."""
    info = repo_info(repo)
    entry = _read_entry(state_dir, info.key, branch) if info.key else None
    if not entry or not isinstance(entry.get("pr"), int) or not info.root:
        return _PrHead(_invalid(R_NO_RECEIPT), info, entry, None)
    head = resolve_head(repo, branch, state_dir=state_dir)
    if head.sha is None:
        return _PrHead(_invalid(R_HEAD_UNRESOLVED), info, entry, head)
    return _PrHead(None, info, entry, head)


def check_ci_receipt(repo: str, branch: str, *, kb_home: Optional[str] = None,
                     state_dir: Optional[str] = None) -> Validity:
    """CI receipt for the branch's current PR at its current head."""
    found = _pr_head(repo, branch, state_dir)
    if found.failure:
        return found.failure
    info, entry, head = found.info, found.entry, found.head
    receipt = read_receipt(info.root, ci_receipt_name(entry["pr"], head.sha))
    if (not receipt or receipt.get("schema") != SCHEMA_CI or receipt.get("repo") != info.key
            or receipt.get("pr") != entry["pr"] or receipt.get("head_sha") != head.sha
            or receipt.get("conclusion") not in CI_CONCLUSIONS):
        return _invalid(R_NO_RECEIPT)
    ctx = vp.load_context(info.key, kb_home)
    if receipt.get("ci_config_digest") != vp.ci_config_digest(ctx.profile):
        return _invalid(R_STALE_CI, receipt)
    return Validity(True, "", receipt)


def acceptance_receipts(root: str, key: str, pr: int) -> tuple:
    directory = receipts_dir(root)
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return ()
    found = []
    for name in names:
        match = _ACCEPTANCE_NAME.match(name)
        if not match or int(match.group(1)) != pr:
            continue
        data = read_json(directory / name, strict=False)
        if (data and data.get("schema") == SCHEMA_ACCEPTANCE and data.get("repo") == key
                and data.get("pr") == pr and data.get("head_sha") == match.group(2)
                and data.get("kind") in ACCEPTANCE_KINDS):
            found.append(data)
    found.sort(key=lambda d: str(d.get("created") or ""), reverse=True)
    return tuple(found)


class UiJudgement(NamedTuple):
    is_ui: Optional[bool]
    files: tuple
    reason: str


def ui_changes(root: str, profile: Optional[vp.Profile], old: Optional[str],
               new: Optional[str]) -> UiJudgement:
    """The single UI decision: which files in old..new match UI and not UI-exclude.

    Used by `select`, check_acceptance_receipt and the ui_acceptance gate.
    is_ui is None when there is no profile or the diff cannot be computed.
    """
    if profile is None:
        return UiJudgement(None, (), R_UI_NO_PROFILE)
    if not vp.path_globs(profile, "UI"):
        return UiJudgement(False, (), R_UI_NO_PATHS)
    names = () if old and old == new else (vg.diff_names(root, old, new) if old and new else None)
    if names is None:
        return UiJudgement(None, (), R_UI_DIFF_UNAVAILABLE)
    files = tuple(n for n in names if vp.is_ui_path(profile, n))
    return UiJudgement(bool(files), files, "")


def branch_ui_changes(root: str, profile: Optional[vp.Profile], head: str,
                      base: Optional[str] = None) -> UiJudgement:
    """ui_changes over merge-base(head, base or default_base)..head."""
    ref = base or vp.default_base(profile)
    merge = vg.merge_base(root, head, ref) or vg.merge_base(root, head, f"origin/{ref}")
    return ui_changes(root, profile, merge, head)


def _ui_untouched(root: str, profile: Optional[vp.Profile], old: str, new: str) -> Optional[bool]:
    judgement = ui_changes(root, profile, old, new)
    if judgement.reason == R_UI_NO_PROFILE:
        return True
    return None if judgement.is_ui is None else not judgement.is_ui


def check_acceptance_receipt(repo: str, branch: str, *, kb_home: Optional[str] = None,
                             state_dir: Optional[str] = None) -> Validity:
    """Acceptance receipt of the current PR with no UI change since it."""
    found = _pr_head(repo, branch, state_dir)
    if found.failure:
        return found.failure
    info, entry, head = found.info, found.entry, found.head
    receipts = acceptance_receipts(info.root, info.key, entry["pr"])
    if not receipts:
        return _invalid(R_NO_RECEIPT)
    profile = vp.load_context(info.key, kb_home).profile
    compared = None
    for receipt in receipts:
        untouched = _ui_untouched(info.root, profile, receipt["head_sha"], head.sha)
        if untouched:
            return Validity(True, "", receipt)
        compared = receipt if untouched is False else compared
    return _invalid(R_UI_CHANGED, compared) if compared else _invalid(R_NO_RECEIPT)


def check_exemption(repo: str, branch: str, *, state_dir: Optional[str] = None) -> Validity:
    """closed / none entries are exempt while the head equals the registered one."""
    info = repo_info(repo)
    entry = _read_entry(state_dir, info.key, branch) if info.key else None
    if not entry:
        return _invalid(R_UNREGISTERED)
    state = entry.get("state")
    if state not in (STATE_CLOSED, STATE_NONE):
        return _invalid(R_NOT_EXEMPT, entry)
    head = resolve_head(repo, branch, state_dir=state_dir)
    if head.sha is None:
        return _invalid(R_HEAD_UNRESOLVED, entry)
    if head.sha != entry.get("head_sha"):
        return _invalid(R_REG_STALE, entry)
    return Validity(True, R_EXEMPT_CLOSED if state == STATE_CLOSED else R_EXEMPT_NONE, entry)
