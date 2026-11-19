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


from verify_gate_state import *  # noqa: F401,F403  (shared constants and helpers)
from verify_gate_state import RUN

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
    branch = reason_branch(outcome.branch)
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


def ui_change(root: str, profile, head: str) -> bool:
    """True when the branch (merge-base with the default base .. head) touches UI.

    When no merge-base can be found the branch is treated as a UI change, so
    the acceptance gate asks for evidence rather than silently passing. The
    decision itself is verify_receipts.ui_changes, shared with `select` and
    check_acceptance_receipt.
    """
    import verify_receipts as vr
    judgement = vr.branch_ui_changes(root, profile, head)
    return bool(judgement.is_ui) or judgement.reason == vr.R_UI_DIFF_UNAVAILABLE


# --- stop: per-link evaluation ----------------------------------------------------------

def _outcome(ok: bool, reason: str, link: Mapping, pr=None, head=None) -> Outcome:
    return Outcome(ok, reason, link.get("repo"), link.get("branch"), pr, head)


def _missing(reason: str, link: Mapping, pr, head) -> Outcome:
    """A failed receipt check; in a fallback worktree a missing receipt is not
    evidence of anything (it lived in the removed worktree), so it is let through."""
    import verify_receipts as vr
    if reason == vr.R_NO_RECEIPT and link.get("fallback"):
        return _outcome(True, R_RECEIPTS_UNAVAILABLE, link, pr, head)
    return _outcome(False, reason, link, pr, head)


def _ci_outcome(link: Mapping, profile, entry: Mapping, head: str) -> Outcome:
    import verify_profile as vp
    import verify_receipts as vr
    pr = entry.get("pr")
    if not vp.required_jobs(profile):
        return _outcome(True, R_CI_NOT_CONFIGURED, link, pr, head)
    validity = vr.check_ci_receipt(link["repo_path"], link["branch"])
    if not validity.valid:
        return _missing(validity.reason, link, pr, head)
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
        return _missing(validity.reason, link, pr, head)
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


def link_usable(link: Mapping) -> bool:
    """The recorded repo still exists at its path with the same remote."""
    import verify_receipts as vr
    return vr.repo_info(link["repo_path"]).key == link["repo"]


def alternate_root(ctx: Ctx, link: Mapping) -> Optional[str]:
    """Another existing worktree of the same repository as a removed one."""
    import verify_git as vg
    import verify_receipts as vr
    common = worktree_roots(ctx).get(link["repo_path"])
    if not isinstance(common, str) or not os.path.isdir(common):
        return None
    listing = vg.run_git(common, "worktree", "list", "--porcelain") or ""
    for line in listing.splitlines():
        path = line[len("worktree "):] if line.startswith("worktree ") else None
        if path and path != link["repo_path"] and os.path.isdir(path) \
                and vr.repo_info(path).key == link["repo"]:
            return path
    return None


def usable_links(ctx: Ctx, linked: tuple) -> tuple:
    """(links to evaluate, skipped outcomes); a removed worktree falls back to a sibling."""
    usable, skipped = [], []
    for link in linked:
        same = link_usable(link)
        root = link["repo_path"] if same else alternate_root(ctx, link)
        if root:
            usable.append({**link, "repo_path": root, "fallback": not same})
        else:
            skipped.append(_outcome(True, R_REPO_UNAVAILABLE, link))
    return tuple(usable), tuple(skipped)


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
    RUN["path"] = PATH_FULL
    usable, skipped = usable_links(ctx, linked)
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


def pre_targets(ctx: Ctx, invocations: tuple) -> tuple:
    """(root, key) of every distinct profiled repo the `gh pr create`s run in.

    An invocation whose directory cannot be resolved records `cwd-unresolved`,
    one in a repo without a profile records `no-profile`; neither hides the others.
    """
    import verify_receipts as vr
    targets: dict = {}
    for invocation in invocations:
        if invocation.cwd is None:
            record_early(ctx, (GATE_PRE_PR,), R_CWD_UNRESOLVED)
            continue
        info = vr.repo_info(invocation.cwd) if os.path.isdir(invocation.cwd) else None
        if info is None or not info.root or not info.key:
            continue
        if load_profile(info.key) is None:
            record_early(ctx, (GATE_PRE_PR,), R_NO_PROFILE)
            continue
        targets.setdefault(info.root, info.key)
    return tuple(targets.items())


def run_pre_tool(ctx: Ctx, data: Mapping, invocations: tuple) -> int:
    """Check every `gh pr create` of the command; any failing repo blocks."""
    targets = pre_targets(ctx, invocations)
    if not targets:
        return 0
    RUN["path"] = PATH_FULL
    outcomes = tuple(_quick_outcome(root, key) for root, key in targets)
    blocked = decide_gate(ctx, GATE_PRE_PR, outcomes)
    if blocked:
        sys.stderr.write(block_reason(tuple((GATE_PRE_PR, o) for o in blocked)) + "\n")
        sys.stderr.flush()
        return PRE_TOOL_BLOCK_EXIT
    return 0


def wanted_invocations(sub: str, data: Mapping, environ: Mapping[str, str]) -> tuple:
    """(invocations, gap): gap is R_UNPARSED for a pre-tool command in which no
    `gh pr create` was recognised (parseable or not) although the text mentions
    it outside quotes, comments and heredoc bodies."""
    import verify_gate_cmd as vcmd
    command = bash_command(data)
    if command is None:
        return (), None
    kinds = {SUB_PRE: (vcmd.KIND_PR_CREATE,),
             SUB_POST: (vcmd.KIND_PUSH, vcmd.KIND_PR_CREATE, vcmd.KIND_REGISTER)}[sub]
    found = vcmd.find_invocations(command, payload_cwd(data), environ.get("HOME") or None)
    wanted = tuple(i for i in found if i.kind in kinds)
    seen = any(i.kind in (vcmd.KIND_PR_CREATE, vcmd.KIND_PR_CREATE_NOOP) for i in found)
    gap = (R_UNPARSED if sub == SUB_PRE and not seen and vcmd.mentions_pr_create(command)
           else None)
    return wanted, gap


def dispatch(sub: Optional[str], raw: bytes, environ: Mapping[str, str], started: float) -> int:
    if sub not in SUB_GATES:
        return 0
    modes = gate_modes(environ)
    if all(modes[gate] == MODE_OFF for gate in SUB_GATES[sub]):
        return 0
    data = read_payload(raw)
    if data is None:
        return 0
    invocations, gap = ((), None) if sub == SUB_STOP else wanted_invocations(sub, data, environ)
    if sub != SUB_STOP and not invocations and not gap:
        return 0
    session = session_of(data)
    state = checked_state_root(environ) if session else None
    if state is None:
        return 0
    ctx = Ctx(session, state, modes, started)
    gates = tuple(g for g in SUB_GATES[sub] if modes[g] != MODE_OFF)
    RUN.update(ctx=ctx, path=PATH_EARLY, gates=(None,) if sub == SUB_POST else gates)
    if gap:
        record_early(ctx, gates, gap)
        return 0
    if sub == SUB_STOP:
        return run_stop(ctx)
    if sub == SUB_POST:
        from verify_gate_post import run_post_tool
        return run_post_tool(ctx, data, invocations)
    return run_pre_tool(ctx, data, invocations)


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
    except BaseException as exc:  # fail-open: includes the deadline and SystemExit
        record_failure(previous, isinstance(exc, _Deadline))
        previous = None
        return 0
    finally:
        try:
            _disarm(previous)
        except BaseException:
            pass


def record_failure(previous, timed_out: bool) -> None:
    """Best effort: log a fail-open allow so observe can count them; never raises."""
    try:
        _disarm(previous)
        ctx = RUN.get("ctx")
        if ctx is None:
            return
        reason = R_HOOK_TIMEOUT if timed_out else R_HOOK_ERROR
        for gate in RUN.get("gates") or ():
            append_record(ctx.state, make_record(ctx, gate=gate, decision=D_ALLOW,
                                                 reason=reason, path=RUN.get("path")))
    except BaseException:
        pass


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
