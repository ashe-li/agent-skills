#!/usr/bin/env python3
"""Read-only git helpers for the verification engine (stdlib only).

Every call is an argv list with `shell=False` and a timeout. Any git failure
(missing binary, not a repo, unknown ref, timeout) yields None or an empty
result instead of an exception, so callers can degrade.

Refs that come from the command line are checked with `safe_ref` before they
reach git, so a value such as `--output=x` can never be read as an option.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from typing import Mapping, NamedTuple, Optional, Sequence

GIT_TIMEOUT_SECONDS = 30
VERIFICATION_DIRNAME = ".verification"
NULL_TREE_TARGET = "/dev/null"
DIFF_PREFIXES = ("--src-prefix=a/", "--dst-prefix=b/")
_REFLOG_TIME = re.compile(r"@\{(\d+)\}$")
_SAFE_REF = re.compile(r"^[A-Za-z0-9._/@^~{}+-]+$")
_SHA = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")
STATUS_LETTERS = frozenset("AMDR")


class Change(NamedTuple):
    status: str
    path: str


def is_sha(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA.match(value))


def safe_ref(ref: Optional[str]) -> bool:
    """A ref is usable when it cannot be mistaken for an option."""
    return bool(ref) and not ref.startswith("-") and bool(_SAFE_REF.match(ref))


def _git(repo: str, *args: str) -> Optional[subprocess.CompletedProcess]:
    argv = ["git", "-c", "core.quotePath=false", "-C", repo, *args]
    try:
        return subprocess.run(argv, shell=False, capture_output=True, text=True,
                              timeout=GIT_TIMEOUT_SECONDS, errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None


def run_git(repo: str, *args: str) -> Optional[str]:
    proc = _git(repo, *args)
    return proc.stdout if proc is not None and proc.returncode == 0 else None


def is_ancestor(repo: str, ancestor: str, descendant: str) -> Optional[bool]:
    """True/False from `merge-base --is-ancestor`; None when git cannot tell
    (for example the ancestor object is not in this clone)."""
    if not (is_sha(ancestor) and is_sha(descendant)):
        return None
    proc = _git(repo, "merge-base", "--is-ancestor", ancestor, descendant)
    if proc is None or proc.returncode not in (0, 1):
        return None
    return proc.returncode == 0


def git_line(repo: str, *args: str) -> Optional[str]:
    out = run_git(repo, *args)
    if out is None:
        return None
    line = out.strip()
    return line or None


def toplevel(repo: str) -> Optional[str]:
    return git_line(repo, "rev-parse", "--show-toplevel")


def resolve_commit(repo: str, ref: str) -> Optional[str]:
    if not safe_ref(ref):
        return None
    return git_line(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")


def head_tree(repo: str) -> Optional[str]:
    return git_line(repo, "rev-parse", "--verify", "--quiet", "HEAD^{tree}")


def branch_head(repo: str, branch: str) -> Optional[str]:
    """SHA of `refs/heads/<branch>`, or None when the local ref is missing."""
    if not branch or "\x00" in branch or ".." in branch:
        return None
    return git_line(repo, "rev-parse", "--verify", "--quiet",
                    f"refs/heads/{branch}^{{commit}}")


def reflog_time(repo: str, branch: str) -> Optional[int]:
    """Unix time (seconds) of the newest reflog entry of `refs/heads/<branch>`."""
    if not branch or "\x00" in branch or ".." in branch:
        return None
    line = git_line(repo, "reflog", "show", "-1", "--format=%gd", "--date=unix",
                    f"refs/heads/{branch}", "--")
    match = _REFLOG_TIME.search(line or "")
    return int(match.group(1)) if match else None


def current_branch(repo: str) -> Optional[str]:
    return git_line(repo, "symbolic-ref", "--quiet", "--short", "HEAD")


def merge_base(repo: str, left: str, right: str) -> Optional[str]:
    a, b = resolve_commit(repo, left), resolve_commit(repo, right)
    if a is None or b is None:
        return None
    return git_line(repo, "merge-base", a, b)


def _outside_verification(path: str) -> bool:
    return path != VERIFICATION_DIRNAME and not path.startswith(VERIFICATION_DIRNAME + "/")


class StatusPaths(NamedTuple):
    tracked: tuple
    untracked: tuple


def status_paths(repo: str) -> Optional[StatusPaths]:
    """Changed tracked paths and untracked paths, excluding `.verification/`."""
    out = run_git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    if out is None:
        return None
    tokens = out.split("\x00")
    tracked, untracked, index = [], [], 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if len(token) < 4:
            continue
        if token[0] in "RC":
            index += 1  # the original path of a rename/copy follows
        (untracked if token[:2] == "??" else tracked).append(token[3:])
    keep = lambda paths: tuple(p for p in paths if _outside_verification(p))  # noqa: E731
    return StatusPaths(keep(tracked), keep(untracked))


def dirty_paths(repo: str) -> Optional[tuple]:
    """Changed or untracked paths, excluding `.verification/`; None on failure."""
    status = status_paths(repo)
    return None if status is None else status.tracked + status.untracked


def is_dirty(repo: str) -> bool:
    """True when the work tree has changes; a git failure counts as dirty."""
    paths = dirty_paths(repo)
    return paths is None or bool(paths)


def _status_letter(raw: str) -> str:
    letter = raw[:1]
    if letter == "C":
        return "A"
    if letter == "T":
        return "M"
    return letter if letter in STATUS_LETTERS else "M"


def changed_files(repo: str, base: str, head: str) -> tuple:
    """`Change(status, path)` for base..head; renames report the new path."""
    if not (is_sha(base) and is_sha(head)):
        return ()
    out = run_git(repo, "diff", *DIFF_PREFIXES, "--name-status", "-z", "-M",
                  "--no-ext-diff", base, head)
    if out is None:
        return ()
    tokens = [t for t in out.split("\x00")]
    changes, index = [], 0
    while index < len(tokens) and tokens[index]:
        raw = tokens[index]
        width = 2 if raw[:1] in "RC" else 1
        path = tokens[index + width] if index + width < len(tokens) else ""
        changes.append(Change(_status_letter(raw), path))
        index += width + 1
    return tuple(c for c in changes if c.path)


def _target_path(line: str) -> Optional[str]:
    target = line[4:].rstrip("\n")
    if target == NULL_TREE_TARGET or not target.startswith("b/"):
        return None
    return target[2:]


def _hunk_counts(line: str) -> tuple:
    match = _HUNK.match(line)
    if match is None:
        return 0, 0
    old = int(match.group(1)) if match.group(1) is not None else 1
    new = int(match.group(2)) if match.group(2) is not None else 1
    return old, new


def _collect_hunk(lines: Sequence[str], start: int, remaining: int, sink: list) -> int:
    """Consume a hunk body; append `+` lines to sink; return the next index."""
    index = start
    while remaining > 0 and index < len(lines):
        line = lines[index]
        index += 1
        if line.startswith("\\"):
            continue
        if line.startswith("+"):
            sink.append(line[1:])
        remaining -= 1
    return index


def added_lines(repo: str, base: str, head: str) -> Mapping[str, tuple]:
    """Map path -> added lines for base..head, parsed by hunk line counts."""
    if not (is_sha(base) and is_sha(head)):
        return {}
    out = run_git(repo, "diff", *DIFF_PREFIXES, "-U0", "-M", "--no-color",
                  "--no-ext-diff", base, head)
    if out is None:
        return {}
    lines, found = out.split("\n"), {}
    current, index = None, 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if line.startswith("diff --git "):
            current = None
        elif line.startswith("+++ ") and current is None:
            current = _target_path(line) or ""
        elif line.startswith("@@ ") and current:
            old, new = _hunk_counts(line)
            index = _collect_hunk(lines, index, old + new, found.setdefault(current, []))
    return {path: tuple(added) for path, added in found.items()}


def index_digest(repo: str) -> Optional[str]:
    """sha256 of the staged entries (`ls-files -s`), or None on failure."""
    out = run_git(repo, "ls-files", "-s", "-z")
    return None if out is None else hashlib.sha256(out.encode("utf-8", "replace")).hexdigest()


def ls_files(repo: str) -> tuple:
    out = run_git(repo, "ls-files", "-z")
    if out is None:
        return ()
    return tuple(p for p in out.split("\x00") if p)


def diff_names(repo: str, old: str, new: str) -> Optional[tuple]:
    """Paths that differ between two commits, or None if either is unknown."""
    if not (is_sha(old) and is_sha(new)):
        return None
    out = run_git(repo, "diff", *DIFF_PREFIXES, "--name-only", "-z", "--no-renames",
                  "--no-ext-diff", old, new)
    if out is None:
        return None
    return tuple(p for p in out.split("\x00") if p)
