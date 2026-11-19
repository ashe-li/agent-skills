#!/usr/bin/env python3
"""What a `git push <args>` does to branches, for the verify gate hook (stdlib only).

`push_plan` turns the words after `git push` into a plan: nothing (dry run),
every tracked branch (`--all`), one RefPush per refspec, or unknowable.
`verify_gate_cmd` re-exports everything here. Nothing here runs a process.
"""
from __future__ import annotations

from typing import NamedTuple, Optional

PUSH_CURRENT, PUSH_BRANCH, PUSH_UNRESOLVED, PUSH_NONE = "current", "branch", "unresolved", "none"
PUSH_REFSPEC = "refspec"  # `<src>:<branch>`: the branch gets whatever <src> resolves to
PUSH_IGNORE, PUSH_ALL, PUSH_MULTI = "ignore", "all", "multi"
REF_CURRENT, REF_BRANCH, REF_REFSPEC = PUSH_CURRENT, PUSH_BRANCH, PUSH_REFSPEC
REF_IGNORE, REF_UNRESOLVED = PUSH_IGNORE, PUSH_UNRESOLVED
PLAN_NONE, PLAN_ALL, PLAN_REFS, PLAN_UNRESOLVED = "none", "all", "refs", "unresolved"
_PUSH_FLAGS = frozenset({"-u", "--set-upstream", "-f", "--force", "--force-with-lease",
                         "--no-force-with-lease", "--force-if-includes", "-q", "--quiet",
                         "-v", "--verbose", "--progress", "--no-progress", "--no-verify",
                         "--verify", "--atomic", "--no-atomic", "--porcelain",
                         "--follow-tags", "--no-follow-tags", "--signed", "--no-signed",
                         "-4", "-6", "--ipv4", "--ipv6", "--thin", "--no-thin"})
_PUSH_VALUE_OPTS = frozenset({"-o", "--push-option", "--repo", "--receive-pack", "--exec"})
_PUSH_VALUE_PREFIXES = ("--force-with-lease=", "--push-option=", "--repo=", "--receive-pack=",
                        "--exec=", "--signed=", "--recurse-submodules=")
_PUSH_DRY_RUN = frozenset({"-n", "--dry-run"})
_PUSH_ALL = frozenset({"--all", "--branches", "--mirror"})
_PUSH_DELETE = frozenset({"-d", "--delete"})
_PUSH_PLAN_FLAGS = _PUSH_DRY_RUN | _PUSH_ALL | _PUSH_DELETE | frozenset({"--tags", "--prune"})
_TAGS = "refs/tags/"
_HEADS = "refs/heads/"
_HEAD_RELATIVE = ("HEAD~", "HEAD^", "HEAD@", "@~", "@^", "@{")


class RefPush(NamedTuple):
    kind: str            # REF_CURRENT, REF_BRANCH, REF_REFSPEC, REF_IGNORE, REF_UNRESOLVED
    branch: Optional[str]
    src: Optional[str]   # REF_REFSPEC only: the source ref to resolve
    explicit: bool = False  # the destination was spelled `refs/heads/<name>`


class PushPlan(NamedTuple):
    kind: str            # PLAN_NONE, PLAN_ALL, PLAN_REFS, PLAN_UNRESOLVED
    refs: tuple          # RefPush per refspec (PLAN_REFS only)


class PushTarget(NamedTuple):
    kind: str  # PUSH_CURRENT, PUSH_BRANCH, PUSH_UNRESOLVED or PUSH_NONE (dry run)
    branch: Optional[str]


def _branch_name(ref: str) -> Optional[str]:
    if ref.startswith(_HEADS):
        ref = ref[len(_HEADS):]
    if not ref or ref.startswith(("refs/", "-")) or ref in ("HEAD", "@"):
        return None
    return ref


def _ref_push(spec: str) -> "RefPush":
    """What one refspec does to a branch; see the hooks document for the table."""
    spec = spec[1:] if spec.startswith("+") else spec
    if spec in ("HEAD", "@"):
        return RefPush(REF_CURRENT, None, None)
    src, sep, dst = spec.partition(":")
    target = dst if sep else spec
    if (sep and not src) or target.startswith(_TAGS):
        return RefPush(REF_IGNORE, None, None)  # a deletion or a tag
    name = _branch_name(target)
    if not name:
        return RefPush(REF_UNRESOLVED, None, None)
    explicit = target.startswith(_HEADS)
    if not sep or _branch_name(src) == name:
        return RefPush(REF_BRANCH, name, None, explicit)
    return RefPush(REF_REFSPEC, name, src, explicit)


def head_relative(src: Optional[str]) -> bool:
    """`HEAD`, `@`, `HEAD~2`, `@^` ...: a source whose meaning moves with HEAD."""
    return bool(src) and (src in ("HEAD", "@") or src.startswith(_HEAD_RELATIVE))


def _push_words(args: tuple) -> Optional[tuple]:
    """(positional words, flags) of `git push`; None when unknowable."""
    positional, flags, index = [], set(), 0
    while index < len(args):
        word, text = args[index], args[index].text
        if word.dynamic:
            return None
        if text in _PUSH_VALUE_OPTS:
            index += 2
            continue
        if text == "--":
            positional.extend(w.text for w in args[index + 1:])
            break
        if not text.startswith("-"):
            positional.append(text)
        elif text in _PUSH_PLAN_FLAGS or text in _PUSH_FLAGS:
            flags.add(text)
        elif not text.startswith(_PUSH_VALUE_PREFIXES):
            return None
        index += 1
    return None if any(w.dynamic for w in args) else (tuple(positional), frozenset(flags))


def push_plan(args: tuple) -> PushPlan:
    """How a `git push <args>` changes branches: none, all, per-refspec, or unknown."""
    parsed = _push_words(args)
    if parsed is None:
        return PushPlan(PLAN_UNRESOLVED, ())
    positional, flags = parsed
    if flags & _PUSH_DRY_RUN:
        return PushPlan(PLAN_NONE, ())
    if flags & _PUSH_ALL:
        return PushPlan(PLAN_ALL, ())
    if flags & _PUSH_DELETE:
        return PushPlan(PLAN_REFS, ())
    refspecs = positional[1:]
    if not refspecs:
        tags_only = "--tags" in flags
        return PushPlan(PLAN_REFS, () if tags_only else (RefPush(REF_CURRENT, None, None),))
    return PushPlan(PLAN_REFS, tuple(_ref_push(spec) for spec in refspecs))


def push_target(args: tuple) -> PushTarget:
    """One-line summary of push_plan, used by tests and the hooks document."""
    plan = push_plan(args)
    if plan.kind != PLAN_REFS:
        kind = {PLAN_NONE: PUSH_NONE, PLAN_ALL: PUSH_ALL}.get(plan.kind, PUSH_UNRESOLVED)
        return PushTarget(kind, None)
    if not plan.refs:
        return PushTarget(PUSH_IGNORE, None)
    if len(plan.refs) > 1:
        return PushTarget(PUSH_MULTI, None)
    return PushTarget(plan.refs[0].kind, plan.refs[0].branch)
