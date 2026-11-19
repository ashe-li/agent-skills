#!/usr/bin/env python3
"""PostToolUse handling of the verify gate hook (stdlib only).

Records session links, pushed heads, push-unresolved markers and `link`
events after a successful `git push`, `gh pr create` or
`verify_engine.py pr register`. Never blocks and never prints; failures
propagate to verify_gate.main, which fails open.
"""
from __future__ import annotations

import os
import re
from typing import Mapping, Optional

from verify_gate_state import *  # noqa: F401,F403  (shared constants and helpers)

_REGISTERED_PR = re.compile(r'(?m)^registered \S+: pr=(\d{1,9})\b|"pr":\s*(\d{1,9})\b')
_REGISTERED_TEXT = re.compile(r"^registered \S+: pr=\S+ state=\S+", re.M)
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


# --- post-tool ----------------------------------------------------------------------------

def push_succeeded(result: ToolResult) -> bool:
    """Not interrupted and no push-specific failure text; unsure counts as pushed."""
    if result.interrupted:
        return False
    text = f"{result.stderr}\n{result.stdout}".lower()
    return not any(marker in text for marker in PUSH_FAILURE_MARKERS)



def created_pr(result: ToolResult) -> tuple:
    """(succeeded, PR number or None) for a `gh pr create` result. Without a
    tool_response it counts as created with no PR number (the caller records that)."""
    if result.interrupted:
        return False, None
    if not result.available:
        return True, None
    found = _PULL_URL.search(result.stdout)
    if found is None and "already exists" in result.stderr.lower():
        found = _PULL_URL.search(result.stderr)
    if found:
        return True, int(found.group(1))
    text = f"{result.stderr}\n{result.stdout}".lower()
    return not any(marker in text for marker in GENERIC_FAILURE_MARKERS), None


def registered(result: ToolResult) -> bool:
    """Only the engine's own success output counts (JSON or text mode); without a
    tool_response it counts as registered (the caller records that)."""
    if result.interrupted:
        return False
    if not result.available:
        return True
    text = f"{result.stdout}\n{result.stderr}"
    return '"result": "registered"' in text or bool(_REGISTERED_TEXT.search(text))


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
    remember_worktree(ctx, info.root)


def _is_tag(root: str, name: Optional[str]) -> bool:
    """`name` resolves to a tag in this repo and not to a local branch."""
    import verify_git as vg
    if not name or name.startswith("refs/heads/"):
        return False
    short = name[len("refs/tags/"):] if name.startswith("refs/tags/") else name
    return (vg.resolve_commit(root, f"refs/heads/{short}") is None
            and vg.resolve_commit(root, f"refs/tags/{short}") is not None)


def push_all(ctx: Ctx, info) -> None:
    """`--all` / `--mirror`: record every tracked branch at its local ref; no new links."""
    import verify_git as vg
    import verify_receipts as vr
    state = str(ctx.state)
    linked = {l["branch"] for l in session_links(ctx) if l["repo"] == info.key}
    for branch in sorted(linked | set(vr.registered_branches(info.root, state_dir=state))):
        if vg.branch_head(info.root, branch):
            vr.record_push(info.root, branch, state_dir=state)


def apply_ref(ctx: Ctx, info, ref) -> None:
    """Record one refspec of a successful push."""
    import verify_gate_cmd as vcmd
    import verify_git as vg
    import verify_receipts as vr
    root, state = info.root, str(ctx.state)
    if ref.kind == vcmd.REF_IGNORE:
        return
    branch = vg.current_branch(root) if ref.kind == vcmd.REF_CURRENT else ref.branch
    if ref.kind == vcmd.REF_UNRESOLVED:  # destination elsewhere under refs/
        vr.mark_push_unresolved(root, state_dir=state)
        return
    if not branch:
        return  # detached HEAD: git refuses to push "the current branch"
    if not ref.explicit and _is_tag(root, branch):  # only the destination decides
        return
    sha = vg.resolve_commit(root, ref.src) if ref.kind == vcmd.REF_REFSPEC else None
    if (sha is None and ref.kind == vcmd.REF_REFSPEC) or not (sha or vg.branch_head(root, branch)):
        vr.mark_push_unresolved(root, branch=branch, state_dir=state)
    else:
        vr.record_push(root, branch, state_dir=state, head_sha=sha)
    link_branch(ctx, info, branch)


def record_post(ctx: Ctx, key: Optional[str], reason: str) -> None:
    """A PostToolUse decision record (gate null) that changes no state."""
    outcome = Outcome(True, reason, key, None, None, None)
    append_record(ctx.state, make_record(ctx, decision=D_ALLOW, outcome=outcome,
                                         path=PATH_FULL))


def _switched_after(invocation, plan) -> bool:
    """An implicit / HEAD push followed in the same command by checkout or switch:
    the branch read afterwards may not be the one that was pushed."""
    import verify_gate_cmd as vcmd
    return invocation.moved and any(
        r.kind == vcmd.REF_CURRENT or (r.kind == vcmd.REF_REFSPEC and vcmd.head_relative(r.src))
        for r in plan.refs)


def handle_push(ctx: Ctx, invocation, result: ToolResult, fallback: Optional[str]) -> None:
    """Record pushed heads. Only an unknowable target marks the whole repo; a push we
    cannot attribute after a branch switch is only recorded, never marked or linked."""
    import verify_gate_cmd as vcmd
    import verify_receipts as vr
    plan = vcmd.push_plan(invocation.args)
    if plan.kind == vcmd.PLAN_NONE or (result.available and not push_succeeded(result)):
        return
    info = profiled_repo(invocation.cwd if invocation.cwd else fallback)
    if info is None:
        return
    if not result.available:
        record_post(ctx, info.key, R_NO_TOOL_RESPONSE)
    if invocation.cwd is None or plan.kind == vcmd.PLAN_UNRESOLVED:
        vr.mark_push_unresolved(info.root, state_dir=str(ctx.state))
    elif _switched_after(invocation, plan):
        record_post(ctx, info.key, R_PUSH_UNRESOLVED)
    elif plan.kind == vcmd.PLAN_ALL:
        push_all(ctx, info)
    else:
        for ref in plan.refs:
            apply_ref(ctx, info, ref)


def link_unresolved(ctx: Ctx, directory: Optional[str], pr: Optional[int]) -> None:
    """A PR was created or registered but its repo or branch is unknown: record it."""
    import verify_receipts as vr
    key = vr.repo_info(directory).key if directory and os.path.isdir(directory) else None
    if key and load_profile(key) is None:
        return  # a known repo without a profile is out of scope, not a coverage gap
    outcome = Outcome(True, R_LINK_UNRESOLVED, key, None, pr, None)
    append_record(ctx.state, make_record(ctx, event=EVENT_LINK, outcome=outcome))


def handle_pr_create(ctx: Ctx, invocation, result: ToolResult,
                     slot: Optional[tuple] = None) -> None:
    """`slot` = (index, numbers) when several `gh pr create` share one output: the
    index-th number when the counts match, otherwise no PR number at all."""
    import verify_gate_cmd as vcmd
    import verify_git as vg
    succeeded, pr = created_pr(result)
    if slot is not None:
        index, numbers = slot
        pr = numbers[index] if succeeded and index < len(numbers) else None
    head = vcmd.option_value(invocation.args, HEAD_OPTIONS)
    if succeeded and (invocation.cwd is None or head == vcmd.DYNAMIC_VALUE):
        if pr is not None:
            link_unresolved(ctx, invocation.cwd, pr)
        return
    info = profiled_repo(invocation.cwd) if succeeded else None
    if info is None:
        return
    branch = head.split(":", 1)[-1] if head else vg.current_branch(info.root)
    if not branch:
        return
    link_branch(ctx, info, branch, pr)
    if not result.available:
        record_post(ctx, info.key, R_NO_TOOL_RESPONSE)
    if pr is not None:
        outcome = Outcome(True, "", info.key, branch, pr, vg.branch_head(info.root, branch))
        append_record(ctx.state, make_record(ctx, event=EVENT_LINK, outcome=outcome))


def registered_pr(result: ToolResult) -> Optional[int]:
    found = _REGISTERED_PR.search(f"{result.stdout}\n{result.stderr}")
    return int(next(g for g in found.groups() if g)) if found else None


def handle_register(ctx: Ctx, invocation, result: ToolResult) -> None:
    import verify_gate_cmd as vcmd
    import verify_git as vg
    import verify_receipts as vr
    if not registered(result):
        return
    repo = vcmd.option_value(invocation.args, REPO_OPTIONS) or "."
    wanted = vcmd.option_value(invocation.args, BRANCH_OPTIONS)
    base = invocation.cwd
    directory = repo if os.path.isabs(repo) else (os.path.join(base, repo) if base else None)
    if vcmd.DYNAMIC_VALUE in (repo, wanted) or directory is None:
        link_unresolved(ctx, None if repo == vcmd.DYNAMIC_VALUE else directory,
                        registered_pr(result))
        return
    info = profiled_repo(directory)
    if info is None:
        return
    branch = wanted or vg.current_branch(info.root)
    if not branch:
        return
    entry = vr.read_registry_entry(info.root, branch, state_dir=str(ctx.state)) or {}
    link_branch(ctx, info, branch, entry.get("pr") if isinstance(entry.get("pr"), int) else None)
    if not result.available:
        record_post(ctx, info.key, R_NO_TOOL_RESPONSE)


def run_post_tool(ctx: Ctx, data: Mapping, invocations: tuple) -> int:
    import verify_gate_cmd as vcmd
    result, fallback = tool_result(data), payload_cwd(data)
    creates = [i for i in invocations if i.kind == vcmd.KIND_PR_CREATE]
    numbers = pr_numbers(result, len(creates)) if len(creates) > 1 else None
    RUN["path"] = PATH_FULL
    for invocation in invocations:
        if invocation.kind == vcmd.KIND_PUSH:
            handle_push(ctx, invocation, result, fallback)
        elif invocation.kind == vcmd.KIND_PR_CREATE:
            slot = None if numbers is None else (creates.index(invocation), numbers)
            handle_pr_create(ctx, invocation, result, slot)
        else:
            handle_register(ctx, invocation, result)
    return 0


def pr_numbers(result: ToolResult, count: int) -> tuple:
    """PR numbers in stdout order when there is exactly one per `gh pr create`, else ()."""
    found = tuple(int(n) for n in _PULL_URL.findall(result.stdout))
    return found if len(found) == count else ()
