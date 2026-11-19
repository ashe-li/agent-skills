#!/usr/bin/env python3
"""PR registry, push markers, session links and head resolution (stdlib only).

Split out of verify_receipts.py, which re-exports every public name here;
callers (engine, gate hook, tests) keep using `verify_receipts.<name>`.
Rules: docs/knowledge-source-contract.md (PR 註冊表, head 決定順序).

Layout under the state directory (an implementation detail):
    registry/<sha256(repo)[:32]>/<sha256(branch)[:32]>.json
    repos/<sha256(repo)[:32]>.json      repo-level push-unresolved marker
    sessions/<sanitized session id>.json
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple, Optional

import verify_git as vg
from verify_state import (RepoInfo, StateError, _digest, dump_json, now_iso, parse_time,
                          read_json, repo_info, safe_dir, state_lock, state_root,
                          state_dir_path, write_bytes_atomic)

REGISTRY_DIR, REPOS_DIR, SESSIONS_DIR = "registry", "repos", "sessions"
SESSION_ID_MAX = 128
_SESSION_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")
SCHEMA_REGISTRY = "verify-pr-registry/1"
SCHEMA_REPO_STATE = "verify-repo-state/1"
SCHEMA_SESSION = "verify-session/1"
STATE_OPEN, STATE_CLOSED, STATE_NONE, STATE_UNREGISTERED = "open", "closed", "none", "unregistered"
SOURCE_LOCAL, SOURCE_PUSHED, SOURCE_REG, SOURCE_UNRESOLVED = (
    "local-ref", "last-pushed", "registration", "unresolved")
R_HEAD_UNRESOLVED = "head-unresolved"
BRANCH_MARKER_KEY = "push_unresolved_at"


class HeadResolution(NamedTuple):
    sha: Optional[str]
    source: str
    reason: str


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


def mark_push_unresolved(repo: str, *, branch: Optional[str] = None,
                         state_dir: Optional[str] = None, at: Optional[str] = None) -> None:
    """Record a push whose pushed head could not be determined.

    With `branch` the marker goes into that branch's registry entry (an
    unregistered entry is created when there is none) and only that branch
    becomes unresolved. Without it the marker is repo-level and applies to
    every branch of the repo. A later resolvable push or `pr register` of a
    branch clears its own marker and outdates the repo-level one for it.
    """
    key, when = _require_key(repo_info(repo)), at or now_iso()
    if parse_time(when) is None:
        raise ValueError("at must be an ISO 8601 time")
    with state_lock(state_root(state_dir)):
        if branch is None:
            path = _repo_state_path(state_dir, key, create=True)
            data = {"schema": SCHEMA_REPO_STATE, "repo": key, "push_unresolved_at": when}
            write_bytes_atomic(path, dump_json(data))
            return
        entry = _read_entry(state_dir, key, branch) or _new_entry(key, branch, when)
        _write_entry(state_dir, {**entry, BRANCH_MARKER_KEY: when, "updated_at": now_iso()})


def registered_branches(repo: str, *, state_dir: Optional[str] = None) -> tuple:
    """Names of the branches of this repo that have a registry entry (sorted)."""
    key = repo_info(repo).key
    if not key:
        return ()
    return tuple(sorted({e["branch"] for e in registry_entries(key, state_dir=state_dir)
                         if isinstance(e.get("branch"), str)}))


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

    With head_sha that SHA is recorded as is and the local ref is not read.
    Without it the branch's local ref is used; if that ref is missing too,
    the branch is marked push-unresolved and its entry (or {}) is returned.
    A recorded push clears the branch's own push-unresolved marker.
    """
    info = repo_info(repo)
    key = _require_key(info)
    sha = head_sha or vg.branch_head(info.root, branch)
    if not vg.is_sha(sha):
        if head_sha:
            raise ValueError("head_sha must be a full hexadecimal SHA")
        mark_push_unresolved(repo, branch=branch, state_dir=state_dir, at=pushed_at)
        return _read_entry(state_dir, key, branch) or {}
    when = pushed_at or now_iso()
    if parse_time(when) is None:
        raise ValueError("pushed_at must be an ISO 8601 time")
    with state_lock(state_root(state_dir)):
        entry = _read_entry(state_dir, key, branch) or _new_entry(key, branch, when)
        updated = {k: v for k, v in entry.items() if k != BRANCH_MARKER_KEY}
        updated.update({"last_pushed_head": {"sha": sha, "at": when}, "updated_at": now_iso()})
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


_UNRESOLVED = HeadResolution(None, SOURCE_UNRESOLVED, R_HEAD_UNRESOLVED)


def _epoch(value: Optional[datetime]) -> Optional[float]:
    return value.timestamp() if value is not None else None


def _local_or_pushed(root: str, branch: str, local: str, entry: Optional[dict]) -> HeadResolution:
    """The local ref, or the last pushed SHA when the remote has content the ref lacks.

    The pushed SHA being an ancestor of the ref means the ref is just ahead
    (not pushed yet): local. Otherwise either the remote got content the ref
    lacks, or the ref rewrote history (amend, rebase) after the push; the
    ref's latest reflog time decides, compared by whole seconds (the reflog
    has no finer resolution): the ref changed in a later second than the push
    means local; the push in a later second means the pushed SHA. The same
    second, a push time in the future (clock skew), no reflog, or no ancestry
    answer (object missing): unresolved.
    """
    local_head = HeadResolution(local, SOURCE_LOCAL, "")
    pushed = entry.get("last_pushed_head") if entry else None
    sha = pushed.get("sha") if isinstance(pushed, dict) else None
    if not vg.is_sha(sha) or sha == local:
        return local_head
    behind = vg.is_ancestor(root, sha, local)
    if behind is None:
        return _UNRESOLVED
    if behind:
        return local_head
    changed, pushed_at = vg.reflog_time(root, branch), _epoch(parse_time(pushed.get("at")))
    if changed is None or pushed_at is None or pushed_at > _epoch(datetime.now(timezone.utc)):
        return _UNRESOLVED
    pushed_second = int(pushed_at)
    if changed == pushed_second:
        return _UNRESOLVED
    return local_head if changed > pushed_second else HeadResolution(sha, SOURCE_PUSHED, "")


def _markers(key: str, entry: Optional[dict], state_dir: Optional[str]) -> tuple:
    found = (parse_time(push_unresolved_at(key, state_dir=state_dir)),
             parse_time(entry.get(BRANCH_MARKER_KEY)) if entry else None)
    return tuple(m for m in found if m is not None)


def resolve_head(repo: str, branch: str, *, state_dir: Optional[str] = None) -> HeadResolution:
    """Decide the branch head (contract: head 決定順序).

    1. A push-unresolved marker (the branch's own, or the repo-level one) is
       later than every record of the branch (registration, last push):
       unresolved, local ref or not.
    2. Local ref exists: _local_or_pushed.
    3. No local ref: the later of last push and registration.
    """
    info = repo_info(repo)
    entry = _read_entry(state_dir, info.key, branch) if info.key else None
    times = _record_times(entry)
    markers = _markers(info.key, entry, state_dir) if info.key else ()
    if any(all(marker > t for t in times) for marker in markers):
        return _UNRESOLVED
    local = vg.branch_head(info.root, branch) if info.root else None
    if local:
        return _local_or_pushed(info.root, branch, local, entry)
    record = _later_record(entry)
    return _UNRESOLVED if record is None else HeadResolution(record[1], record[2], "")


def _record_times(entry: Optional[dict]) -> tuple:
    if not entry:
        return ()
    pushed = entry.get("last_pushed_head")
    found = (parse_time(entry.get("registered_at")),
             parse_time(pushed.get("at")) if isinstance(pushed, dict) else None)
    return tuple(t for t in found if t is not None)
