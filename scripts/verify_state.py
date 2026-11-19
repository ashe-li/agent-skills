#!/usr/bin/env python3
"""Low-level local state helpers for the verification engine (stdlib only).

Time stamps, the state directory (0700, files 0600, symlinks refused with
StateError, atomic temp-file-then-os.replace writes, an advisory flock on
`<state>/.lock`) and repo identity. verify_registry.py and
verify_receipts.py build on these; callers keep using the names re-exported
by verify_receipts.
"""
from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, NamedTuple, Optional

import kb_source
import verify_git as vg

try:
    import fcntl
except ImportError:  # non-POSIX: run without the advisory lock
    fcntl = None

ENV_STATE_DIR = "AGENT_SKILLS_VERIFY_STATE_DIR"
DEFAULT_STATE_PARTS = (".claude", "verify-gate")
DIR_MODE, FILE_MODE = 0o700, 0o600
HASH_CHARS = 32
LOCK_NAME = ".lock"


class StateError(Exception):
    """The state directory or a state file cannot be used safely."""


class RepoInfo(NamedTuple):
    path: str
    root: Optional[str]
    key: Optional[str]


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


@contextlib.contextmanager
def dir_lock(directory: Path) -> Iterator[None]:
    """Exclusive advisory flock on a directory itself (no lock file is created).

    Serializes read-modify-write sequences on files inside `directory`, such
    as appending to a KB candidate or replacing an acceptance receipt.
    """
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(directory, flags)
    except OSError as exc:
        raise StateError(f"cannot lock {directory}: {exc.strerror}") from exc
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:HASH_CHARS]


def repo_info(repo: str) -> RepoInfo:
    root = vg.toplevel(str(repo))
    remote = kb_source.get_repo_remote(root) if root else None
    return RepoInfo(str(repo), root, kb_source.normalize_remote(remote) if remote else None)
