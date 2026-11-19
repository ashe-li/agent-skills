#!/usr/bin/env python3
"""Knowledge-source resolution for agent-skills (stdlib only).

agent-skills ships generic machinery only; each user keeps their own
verification knowledge in a knowledge base (KB) directory elsewhere. This
module answers two questions the rest of the machinery depends on:

1. Where is the KB root?
2. Which profile in `<kb>/verification/` belongs to the current git repo?

KB root resolution order (see docs/knowledge-source-contract.md):

1. Environment variable AGENT_SKILLS_KB_HOME (empty string = unset).
2. First line of `${XDG_CONFIG_HOME:-$HOME/.config}/agent-skills/kb-home`.
3. Neither set: NO-KB. This is a normal state, not an error.

A configured value that is not an existing directory is also NO-KB, with a
readable reason. An invalid higher-priority value never falls through to a
lower-priority one: a typo should be visible, not silently masked.

Profile matching is deterministic. A profile is `<kb>/verification/<name>.md`
whose frontmatter `remotes` list contains the repo's normalized `origin`
remote (`host/owner/repo`, lowercase). Zero matches is NO-PROFILE, exactly
one is MATCHED, more than one is AMBIGUOUS (listed, never guessed).

The frontmatter parser is deliberately restricted: scalars and string lists
(block `- item` or inline `[a, b]`) only, nothing from pyyaml. Nested
structures are ignored; unknown keys are kept. It never raises: a missing or
broken frontmatter yields an empty mapping plus a reason.

Usage:
    kb_source.py resolve
    kb_source.py match [--repo PATH]

Every outcome, including NO-KB, exits 0 so callers can degrade quietly.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, NamedTuple, Optional, Sequence, Union
from urllib.parse import urlsplit

ENV_KB_HOME = "AGENT_SKILLS_KB_HOME"
ENV_XDG = "XDG_CONFIG_HOME"
ENV_HOME = "HOME"
CONFIG_SUBDIR = ("agent-skills", "kb-home")
DEFAULT_CONFIG_DIR = ".config"
VERIFICATION_DIR = "verification"
PROFILE_SUFFIX = ".md"
SKIPPED_PROFILE_NAMES = frozenset({"README.md"})
SKIPPED_PROFILE_PREFIX = "_"
REMOTES_KEY = "remotes"

SOURCE_ENV = "env"
SOURCE_FILE = "file"
SOURCE_NONE = "none"

MATCHED = "matched"
NO_PROFILE = "no-profile"
AMBIGUOUS = "ambiguous"

LABEL_NO_KB = "NO-KB"
LABEL_MATCH = "MATCH"
LABEL_NO_PROFILE = "NO-PROFILE"
LABEL_AMBIGUOUS = "AMBIGUOUS"

GIT_TIMEOUT_SECONDS = 10
FRONTMATTER_FENCE = "---"
_KEY_LINE = re.compile(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$")
_LIST_ITEM = re.compile(r"^\s*-(?:\s+(.*))?$")
_SCP_REMOTE = re.compile(r"^(?:[^@/\s]+@)?([^:/\s]+):(?!//)(.+)$")
_BLOCK_SCALAR_MARKERS = frozenset({"|", ">", "|-", ">-", "|+", ">+"})

FrontmatterValue = Union[str, tuple]


class KbHome(NamedTuple):
    path: Optional[str]
    source: str
    reason: str


class Frontmatter(NamedTuple):
    data: Mapping[str, FrontmatterValue]
    reason: str


class ProfileMatch(NamedTuple):
    status: str
    profiles: tuple
    reason: str


# --- KB root resolution -----------------------------------------------------

def _none(reason: str) -> KbHome:
    return KbHome(None, SOURCE_NONE, reason)


def _check_dir(raw: str, source: str, label: str) -> KbHome:
    if not os.path.isdir(raw):
        return _none(f"{label} points to a missing or non-directory path: {raw}")
    return KbHome(os.path.abspath(raw), source, "")


def _config_file(environ: Mapping[str, str]) -> Optional[Path]:
    xdg = environ.get(ENV_XDG, "").strip()
    if xdg:
        base = Path(xdg)
    else:
        home = environ.get(ENV_HOME, "").strip()
        if not home:
            return None
        base = Path(home) / DEFAULT_CONFIG_DIR
    return base.joinpath(*CONFIG_SUBDIR)


def _first_line(path: Path) -> Optional[str]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.readline().strip()
    except OSError:
        return None


def resolve_kb_home(environ: Optional[Mapping[str, str]] = None) -> KbHome:
    """Return the KB root, or a KbHome with path=None and a reason."""
    env = os.environ if environ is None else environ
    from_env = env.get(ENV_KB_HOME, "").strip()
    if from_env:
        return _check_dir(from_env, SOURCE_ENV, ENV_KB_HOME)
    config = _config_file(env)
    from_file = _first_line(config) if config is not None else None
    if from_file:
        return _check_dir(from_file, SOURCE_FILE, f"kb-home file {config}")
    return _none(f"{ENV_KB_HOME} is not set and no kb-home file was found")


# --- remote URL normalization -----------------------------------------------

def _clean_repo_path(path: str) -> str:
    path = path.strip().strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    return path.strip("/")


def _split_remote(url: str) -> Optional[tuple]:
    """Return (host, path) from a URL-style or scp-style remote."""
    if "://" in url:
        try:
            parts = urlsplit(url)
            return (parts.hostname or ""), parts.path
        except ValueError:
            return None
    match = _SCP_REMOTE.match(url)
    if match:
        return match.group(1), match.group(2)
    return None


def normalize_remote(url: str) -> Optional[str]:
    """Normalize a git remote to lowercase `host/owner/repo`, or None.

    Drops credentials, port, a trailing slash and a `.git` suffix. Anything
    that cannot be read as host plus at least owner/repo returns None.
    """
    if not isinstance(url, str) or not url.strip():
        return None
    split = _split_remote(url.strip())
    if split is None:
        return None
    host, path = split
    segments = [s for s in _clean_repo_path(path).split("/") if s]
    if not host or len(segments) < 2:
        return None
    return "/".join([host, *segments]).lower()


def normalize_remote_or_key(value: str) -> Optional[str]:
    """Like normalize_remote, but also accepts an already normalized key.

    Profiles list `host/owner/repo` directly, so a value with no scheme and
    no scp colon is taken as that form when its first segment looks like a
    host (contains a dot) and at least owner/repo follow.
    """
    normalized = normalize_remote(value)
    if normalized is not None or not isinstance(value, str):
        return normalized
    candidate = value.strip()
    if ":" in candidate or any(c.isspace() for c in candidate):
        return None
    segments = [s for s in _clean_repo_path(candidate).split("/") if s]
    if len(segments) < 3 or "." not in segments[0]:
        return None
    return "/".join(segments).lower()


# --- restricted frontmatter parser ------------------------------------------

def _empty(reason: str) -> Frontmatter:
    return Frontmatter(MappingProxyType({}), reason)


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _inline_list(text: str) -> tuple:
    inner = text[1:-1]
    return tuple(i for i in (_unquote(p) for p in inner.split(",")) if i)


def _block_list(children: Sequence[str]) -> Optional[tuple]:
    items = []
    for child in children:
        match = _LIST_ITEM.match(child)
        if match is None:
            return None
        items.append(_unquote(match.group(1) or ""))
    return tuple(i for i in items if i)


def _group_entries(lines: Sequence[str]) -> list:
    """Group lines into (head, children); raises ValueError if malformed."""
    entries: list = []
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] in " \t":
            if not entries:
                raise ValueError("indented line before any key")
            entries[-1][1].append(line)
        else:
            entries.append((line.rstrip(), []))
    return entries


def _entry_value(rest: str, children: Sequence[str]) -> Optional[FrontmatterValue]:
    """Value for one key, or None when the shape is not supported."""
    if not rest:
        if not children:
            return ""
        return _block_list(children)
    if rest.startswith("[") and rest.endswith("]"):
        return _inline_list(rest)
    if rest.startswith("{") or rest in _BLOCK_SCALAR_MARKERS:
        return None
    return _unquote(rest)


def parse_frontmatter(text: str) -> Frontmatter:
    """Parse leading `---` frontmatter into scalars and string tuples."""
    if not isinstance(text, str):
        return _empty("input is not text")
    lines = text.splitlines()
    if not lines or lines[0].strip() != FRONTMATTER_FENCE:
        return _empty("no frontmatter")
    ends = [i for i in range(1, len(lines)) if lines[i].strip() == FRONTMATTER_FENCE]
    if not ends:
        return _empty("frontmatter is not closed")
    try:
        entries = _group_entries(lines[1 : ends[0]])
        data = _build_mapping(entries)
    except ValueError as exc:
        return _empty(f"malformed frontmatter: {exc}")
    return Frontmatter(MappingProxyType(data), "")


def _build_mapping(entries: Sequence[tuple]) -> dict:
    data: dict = {}
    for head, children in entries:
        match = _KEY_LINE.match(head)
        if match is None:
            raise ValueError(f"line is not 'key: value': {head[:40]!r}")
        value = _entry_value(match.group(2).strip(), children)
        if value is not None:
            data[match.group(1)] = value
    return data


# --- profile matching --------------------------------------------------------

def _is_profile_candidate(path: Path) -> bool:
    name = path.name
    return (name.endswith(PROFILE_SUFFIX)
            and not name.startswith(SKIPPED_PROFILE_PREFIX)
            and name not in SKIPPED_PROFILE_NAMES
            and path.is_file())


def _inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


def _profile_remotes(path: Path) -> frozenset:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return frozenset()
    value = parse_frontmatter(text).data.get(REMOTES_KEY)
    if not isinstance(value, tuple):
        return frozenset()
    return frozenset(n for n in (normalize_remote_or_key(v) for v in value) if n)


def match_profile(kb_home: str, remote: Optional[str]) -> ProfileMatch:
    """Find the profile whose `remotes` contains the given remote.

    `remote` may be a raw URL or an already normalized `host/owner/repo`.
    """
    key = normalize_remote_or_key(remote) if remote else None
    if key is None:
        return ProfileMatch(NO_PROFILE, (), "repo remote is unknown or unparseable")
    root = Path(kb_home)
    vdir = root / VERIFICATION_DIR
    if not vdir.is_dir():
        return ProfileMatch(NO_PROFILE, (), f"no {VERIFICATION_DIR}/ directory in KB")
    found = tuple(sorted(
        p.name for p in vdir.iterdir()
        if _is_profile_candidate(p) and _inside(root, p) and key in _profile_remotes(p)
    ))
    if len(found) == 1:
        return ProfileMatch(MATCHED, found, "")
    if found:
        return ProfileMatch(AMBIGUOUS, found, "more than one profile lists this remote")
    return ProfileMatch(NO_PROFILE, (), f"no profile lists remote {key}")


# --- git + CLI ----------------------------------------------------------------

def get_repo_remote(repo: str) -> Optional[str]:
    """Return `origin`'s URL for a repo, or None on any failure."""
    argv = ["git", "-C", repo, "remote", "get-url", "origin"]
    try:
        proc = subprocess.run(argv, shell=False, capture_output=True, text=True,
                              timeout=GIT_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError):
        return None
    url = proc.stdout.strip()
    return url if proc.returncode == 0 and url else None


def _cmd_resolve(_args: argparse.Namespace) -> int:
    home = resolve_kb_home()
    if home.path is None:
        print(f"{LABEL_NO_KB} ({home.reason})")
    else:
        print(f"KB {home.path} (source: {home.source})")
    return 0


def _cmd_match(args: argparse.Namespace) -> int:
    home = resolve_kb_home()
    if home.path is None:
        print(f"{LABEL_NO_KB} ({home.reason})")
        return 0
    result = match_profile(home.path, get_repo_remote(args.repo))
    if result.status == MATCHED:
        print(f"{LABEL_MATCH} {result.profiles[0]}")
    elif result.status == AMBIGUOUS:
        print(f"{LABEL_AMBIGUOUS} {' '.join(result.profiles)}")
    else:
        print(f"{LABEL_NO_PROFILE} ({result.reason})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Resolve the agent-skills KB source.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("resolve", help="print the resolved KB root").set_defaults(func=_cmd_resolve)
    match = sub.add_parser("match", help="match the repo's origin to a profile")
    match.add_argument("--repo", default=".", help="repo path (default: .)")
    match.set_defaults(func=_cmd_match)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
