#!/usr/bin/env python3
"""Verification engine CLI (stdlib only).

Usage (common flags go after the subcommand: --repo PATH, --json,
--state-dir PATH):

    verify_engine.py status
    verify_engine.py select [--base REF] [--head REF]
    verify_engine.py checks [--base REF] [--dry-run]
    verify_engine.py receipt show [--branch B]
    verify_engine.py receipt record-generic --summary TEXT
    verify_engine.py receipt acceptance --pr N --attachments K
    verify_engine.py receipt exempt --pr N --reason TEXT
    verify_engine.py receipt ci (--from-gh [--pr N] | --evidence FILE)
                                [--pending-declared] [--exempt --reason TEXT]
    verify_engine.py pr register (--from-gh [--pr N] | --evidence FILE | --none [--branch B])
    verify_engine.py pr show [--branch B]
    verify_engine.py candidate list
    verify_engine.py candidate add --slug S --pattern TEXT --occurrence LINE...
                                   [--trigger-paths GLOBS] [--trigger-added REGEX] [--scope S]
    verify_engine.py init-profile [--name NAME]
    verify_engine.py lint

The knowledge source is resolved by kb_source.py; receipts, the PR registry
and every validity rule live in verify_receipts.py. Profile and candidate
text is printed inside the data fence (verify_profile.fence).

Exit codes: FAIL, rejected or red CI evidence, lint errors and an existing
init-profile target are 1; usage errors are 2; everything else is 0,
including NO-KB, NO-PROFILE, PROFILE-STALE, PROFILE-INVALID, UNVERIFIABLE.
The schema and the reason codes are in docs/knowledge-source-contract.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Optional, Sequence

import kb_source
import verify_checks as vc
import verify_ci as vci
import verify_git as vg
import verify_kb as vk
import verify_profile as vp
import verify_receipts as vr
import verify_select as vs

EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2
RESULT_WRITTEN, RESULT_REJECTED = vci.WRITTEN, vci.REJECTED
R_EVIDENCE_UNAVAILABLE = "evidence-unavailable"
R_PROFILE_PRESENT = "profile-present"
R_ACCEPTANCE_EXISTS = "acceptance-exists"
FETCH_HINT = "; if it is a remote ref that has not been fetched yet, run `git fetch` first"
KIND_EVIDENCE, KIND_EXEMPT = "evidence-comment", "exempt"
CANDIDATE_OK = frozenset({"created", "appended", "unchanged"})
METHODS_GENERIC = ("generic", "none")


class UsageError(Exception):
    """Bad arguments; exit 2."""


class JsonArgumentParser(argparse.ArgumentParser):
    """argparse that also prints a JSON error object when --json was given.

    `json_requested` is set by main() from the argv actually being parsed
    (not sys.argv), so programmatic calls get the same behavior as the CLI.
    """

    json_requested = False

    def error(self, message: str) -> None:  # type: ignore[override]
        if JsonArgumentParser.json_requested:
            print(json.dumps({"result": "error", "reason": "usage", "message": message}))
        self.print_usage(sys.stderr)
        sys.stderr.write(f"{self.prog}: error: {message}\n")
        sys.exit(EXIT_USAGE)


# --- output helpers -----------------------------------------------------------------

def emit(args: argparse.Namespace, data: dict, lines: Sequence[str]) -> None:
    if args.json:
        print(json.dumps(data, ensure_ascii=False, sort_keys=True))
    else:
        print("\n".join(lines))


def _context(args: argparse.Namespace) -> tuple:
    info = vr.repo_info(args.repo)
    return info, vp.load_context(info.key)


def _require_root(info: vr.RepoInfo) -> str:
    if not info.root:
        raise UsageError(f"{info.path} is not a git repository")
    return info.root


def _status_line(ctx: vp.KbContext) -> str:
    if ctx.status == kb_source.LABEL_NO_KB:
        return f"NO-KB ({ctx.home_reason})"
    if ctx.status == kb_source.LABEL_MATCH:
        return f"MATCH {ctx.profile.filename}"
    if ctx.status == kb_source.LABEL_AMBIGUOUS:
        return f"AMBIGUOUS {' '.join(ctx.profiles)}"
    return f"NO-PROFILE ({ctx.profile_reason})"


# --- status / select --------------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> int:
    _, ctx = _context(args)
    data = {"status": ctx.status, "profile": ctx.profile.filename if ctx.profile else None,
            "reason": ctx.home_reason or ctx.profile_reason}
    emit(args, data, [_status_line(ctx)])
    return EXIT_OK


def _fenced_item(sel: vs.Selected) -> list:
    reasons = "\n".join(f"{r['trigger']}: {r['file']} ({r['pattern']})" for r in sel.reasons)
    body = (f"{sel.item.title}\nCheck: {sel.item.fields.get('Check', '')}\n"
            f"Evidence: {sel.item.fields.get('Evidence', '')}\n{reasons}")
    return [f"- {sel.item.id} [{sel.item.source}]", vp.fence(body)]


def _ui_line(ui: vr.UiJudgement) -> str:
    if ui.is_ui is None:
        return f"ui: unknown ({ui.reason})"
    return f"ui: {'yes' if ui.is_ui else 'no'} ({len(ui.files)} files)"


def cmd_select(args: argparse.Namespace) -> int:
    info, ctx = _context(args)
    root = _require_root(info)
    base = _checked_base(args, root, ctx)
    if not vg.safe_ref(args.head) or not vg.resolve_commit(root, args.head):
        raise UsageError(f"--head must be a plain git ref that resolves to a commit{FETCH_HINT}")
    diff = vs.load_diff(root, base, args.head)
    selected = vs.select(ctx, root, diff)
    ui = vr.ui_changes(root, ctx.profile, diff.base, diff.head)
    data = {"status": ctx.status, "profile": ctx.profile.filename if ctx.profile else None,
            "base": diff.base, "head": diff.head, "items": vs.selected_json(selected),
            "ui": {"is_ui": ui.is_ui, "files": list(ui.files), "reason": ui.reason}}
    lines = [_status_line(ctx), f"base {diff.base} head {diff.head}: {len(selected)} item(s)",
             _ui_line(ui)]
    notes = vp.section(ctx.profile.doc, vp.SECTION_NOTES) if ctx.profile else None
    if notes and notes.text.strip():
        lines += ["Verifier notes:", vp.fence(notes.text)]
    for sel in selected:
        lines += _fenced_item(sel)
    emit(args, data, lines)
    return EXIT_OK


# --- checks -----------------------------------------------------------------------------

def _checks_lines(data: dict, run: vc.ChecksRun) -> list:
    lines = [f"verdict: {data['verdict']}"]
    for outcome in run.outcomes:
        lines.append(f"  {outcome.id} {outcome.status} exit={outcome.exit} "
                     f"{' '.join(outcome.argv)}")
    lines += [f"  stale: {r}" for r in run.stale] + [f"  invalid: {r}" for r in run.invalid]
    lines += [f"receipt: {data['receipt']}"] if data["receipt"] else []
    lines += [f"notice: {n}" for n in data["notices"]]
    return lines


def _checks_notices(dirty_before: bool, dirty: bool, receipt: Optional[dict],
                    custom_base: bool, root: str, profile: Optional[vp.Profile]) -> list:
    notices = [vc.DIRTY_NOTICE] if dirty_before else []
    if dirty and not dirty_before:
        notices.append(vc.CHANGED_DURING_NOTICE)
    if custom_base and receipt is not None and not vr.when_skip_trusted(receipt, root, profile):
        notices.append(vc.NON_DEFAULT_BASE_NOTICE)
    return notices


def _untracked_notice(created: Sequence[str]) -> list:
    if not created:
        return []
    shown = ", ".join(created[:vc.NEW_UNTRACKED_SHOWN])
    more = len(created) - vc.NEW_UNTRACKED_SHOWN
    paths = shown + (f" 等，另有 {more} 筆" if more > 0 else "")
    return [vc.NEW_UNTRACKED_NOTICE.format(paths=paths)]


def _checked_base(args: argparse.Namespace, root: str, ctx: vp.KbContext) -> str:
    """The diff base for checks/select; an explicit --base must resolve."""
    base = args.base or vp.default_base(ctx.profile)
    if not vg.safe_ref(base):
        raise UsageError("--base must be a plain git ref")
    if args.base and not (vg.resolve_commit(root, base)
                          or vg.resolve_commit(root, f"origin/{base}")):
        raise UsageError(f"--base {base} does not resolve to a commit"
                         f"{FETCH_HINT}")
    return base


def cmd_checks(args: argparse.Namespace) -> int:
    info, ctx = _context(args)
    root = _require_root(info)
    before = vc.work_state(root)
    head, tree = before.head, before.tree
    base = _checked_base(args, root, ctx)
    if not (head and tree):
        raise UsageError("HEAD must exist")
    diff = vs.load_diff(root, base)
    run = vc.evaluate(root, head, ctx, diff.changes, args.dry_run)
    selected = vs.select(ctx, root, diff)
    after = vc.work_state(root)
    dirty, created = vc.dirty_during(before, after), vc.new_untracked(before, after)
    path, receipt = None, None
    if not args.dry_run:
        receipt = vc.build_receipt(info, ctx, run, head=head, tree=tree, dirty=dirty,
                                   selected=selected, diff_base=diff.base)
        path = vr.write_receipt(root, vr.quick_receipt_name(tree), receipt)
    data = {"verdict": "DRY-RUN" if args.dry_run else run.verdict, "receipt": path,
            "checks": [o.as_json() for o in run.outcomes], "dirty": dirty,
            "stale_reasons": list(run.stale), "invalid_reasons": list(run.invalid),
            "argv": {o.id: list(o.argv) for o in run.outcomes},
            "new_untracked": list(created),
            "notices": _checks_notices(before.dirty, dirty, receipt, bool(args.base),
                                       root, ctx.profile) + _untracked_notice(created)}
    emit(args, data, _checks_lines(data, run))
    return EXIT_FAIL if data["verdict"] == vc.FAIL else EXIT_OK


# --- receipt subcommands --------------------------------------------------------------

def _receipt_result(args: argparse.Namespace, result: str, reason: str = "",
                    path: Optional[str] = None, conclusion: Optional[str] = None,
                    message: str = "", label: Optional[str] = None) -> int:
    data = {"result": result, "conclusion": conclusion, "reason": reason, "receipt": path,
            "message": message}
    shown = label or conclusion or "-"
    line = f"{result}: {shown} {reason} {path or ''} {message}".rstrip()
    emit(args, data, [line])
    failed = result == RESULT_REJECTED or conclusion == vci.RED
    return EXIT_FAIL if failed else EXIT_OK


def _branch(args: argparse.Namespace, root: str) -> str:
    branch = getattr(args, "branch", None) or vg.current_branch(root)
    if not branch:
        raise UsageError("cannot determine the branch; pass --branch")
    return branch


def _validity_json(validity: vr.Validity) -> dict:
    return {"valid": validity.valid, "reason": validity.reason, "receipt": validity.receipt}


def cmd_receipt_show(args: argparse.Namespace) -> int:
    info = vr.repo_info(args.repo)
    branch = _branch(args, _require_root(info))
    kw = {"state_dir": args.state_dir}
    data = {"branch": branch,
            "quick": _validity_json(vr.check_quick_receipt(info.root)),
            "ci": _validity_json(vr.check_ci_receipt(info.root, branch, **kw)),
            "acceptance": _validity_json(vr.check_acceptance_receipt(info.root, branch, **kw)),
            "exemption": _validity_json(vr.check_exemption(info.root, branch, **kw))}
    lines = [f"{name}: {'valid' if data[name]['valid'] else 'invalid'} {data[name]['reason']}"
             for name in ("quick", "ci", "acceptance", "exemption")]
    emit(args, data, [f"branch {branch}", *lines])
    return EXIT_OK


def cmd_receipt_generic(args: argparse.Namespace) -> int:
    info, ctx = _context(args)
    root = _require_root(info)
    if ctx.profile is not None:
        return _receipt_result(args, RESULT_REJECTED, R_PROFILE_PRESENT,
                               message="a profile matches this repo; run `checks` instead")
    head, tree = vg.resolve_commit(root, "HEAD"), vg.head_tree(root)
    if not (head and tree):
        raise UsageError("HEAD must exist")
    verdict = vc.NO_KB if ctx.home is None else vc.NO_PROFILE
    run = vc.ChecksRun(verdict, args.method, (), (), ())
    receipt = vc.build_receipt(info, ctx, run, head=head, tree=tree, dirty=vg.is_dirty(root),
                               selected=(), diff_base=None)
    receipt["summary"] = vp.clean_text(args.summary, max_chars=2000, max_lines=20)
    path = vr.write_receipt(root, vr.quick_receipt_name(tree), receipt)
    return _receipt_result(args, RESULT_WRITTEN, path=path, conclusion=verdict)


def _positive(value: str, flag: str, minimum: int = 1) -> int:
    if not (value.isascii() and value.isdigit()) or int(value) < minimum:
        raise UsageError(f"{flag} must be an integer >= {minimum}")
    return int(value)


def _write_acceptance(args: argparse.Namespace, kind: str, attachments: int,
                      reason: str) -> int:
    info = vr.repo_info(args.repo)
    root, pr = _require_root(info), _positive(args.pr, "--pr")
    entry = vr.find_entry_by_pr(info.key, pr, state_dir=args.state_dir) if info.key else None
    if entry is None:
        return _receipt_result(args, RESULT_REJECTED, vci.R_MISMATCH,
                               message="PR is not registered for this repo; run `pr register`")
    head = vg.branch_head(root, entry["branch"])
    if head is None:
        return _receipt_result(args, RESULT_REJECTED, vr.R_HEAD_UNRESOLVED,
                               message="the PR branch has no local ref")
    name = vr.acceptance_receipt_name(pr, head)
    existing = vr.read_receipt(root, name)
    if kind == KIND_EXEMPT and existing and existing.get("kind") == KIND_EVIDENCE:
        return _receipt_result(args, RESULT_REJECTED, R_ACCEPTANCE_EXISTS,
                               message="this head already has a real acceptance receipt; "
                                       "an exemption may not replace it")
    receipt = {"schema": vr.SCHEMA_ACCEPTANCE, "repo": info.key, "pr": pr, "head_sha": head,
               "kind": kind, "attachments": attachments, "reason": reason,
               "created": vr.now_iso()}
    path = vr.write_receipt(root, name, receipt)
    return _receipt_result(args, RESULT_WRITTEN, path=path, label=kind)


def cmd_receipt_acceptance(args: argparse.Namespace) -> int:
    return _write_acceptance(args, KIND_EVIDENCE,
                             _positive(args.attachments, "--attachments"), "")


def cmd_receipt_exempt(args: argparse.Namespace) -> int:
    if not args.reason.strip():
        raise UsageError("--reason must not be empty")
    return _write_acceptance(args, KIND_EXEMPT, 0, vp.clean_line(args.reason, 500))


def _load_evidence(args: argparse.Namespace, info: vr.RepoInfo) -> vci.Evidence:
    if args.from_gh:
        return vci.fetch_evidence_from_gh(info, args.pr)
    return vci.load_evidence_file(args.evidence)


def cmd_receipt_ci(args: argparse.Namespace) -> int:
    if args.exempt and not (args.reason or "").strip():
        raise UsageError("--exempt needs --reason")
    if args.reason and not args.exempt:
        raise UsageError("--reason is only valid with --exempt")
    info, ctx = _context(args)
    _require_root(info)
    try:
        evidence = _load_evidence(args, info)
    except vci.EvidenceError as exc:
        if args.from_gh:
            return _receipt_result(args, vci.UNVERIFIABLE, R_EVIDENCE_UNAVAILABLE, message=str(exc))
        return _receipt_result(args, RESULT_REJECTED, vci.R_INVALID, message=str(exc))
    reason = vp.clean_line(args.reason, 500) if args.exempt else None
    flags = vci.CiFlags(args.pending_declared, reason)
    out = vci.evaluate(info, evidence, ctx, flags, args.state_dir)
    return _receipt_result(args, out.result, out.reason, out.receipt, out.conclusion, out.message)


# --- pr subcommands -----------------------------------------------------------------------

def _register_source(args: argparse.Namespace, info: vr.RepoInfo) -> tuple:
    """(repo key, pr, state, branch) reported by the chosen source."""
    if args.none:
        return info.key, None, vr.STATE_NONE, _branch(args, info.root)
    if args.from_gh:
        view = vci.gh_pr_view(info.root, args.pr)
        repo, pr = vci.gh_repo_key(info.root), view["number"]
        state, branch = vci.PR_STATE_MAP.get(str(view.get("state")).upper()), view.get("headRefName")
    else:
        data = vci.load_evidence_file(args.evidence).data
        repo = kb_source.normalize_remote_or_key(str(data.get("repo")))
        pr, state, branch = data["pr"], data.get("pr_state"), data.get("pr_head_branch")
    reg_state = vr.STATE_OPEN if state == vr.STATE_OPEN else vr.STATE_CLOSED
    if state not in (vr.STATE_OPEN, vr.STATE_CLOSED, "merged"):
        raise vci.EvidenceError(f"unknown PR state {state!r}")
    return repo, pr, reg_state, branch


def cmd_pr_register(args: argparse.Namespace) -> int:
    info = vr.repo_info(args.repo)
    _require_root(info)
    if args.branch and not args.none:
        raise UsageError("--branch is only valid with --none")
    try:
        repo, pr, state, branch = _register_source(args, info)
    except vci.EvidenceError as exc:
        return _receipt_result(args, RESULT_REJECTED, vci.R_INVALID, message=str(exc))
    if repo != info.key or not isinstance(branch, str) or not branch:
        return _receipt_result(args, RESULT_REJECTED, vci.R_MISMATCH,
                               message="the PR does not belong to this repo or has no branch")
    entry = vr.register(info.root, branch, pr, state, state_dir=args.state_dir)
    data = {"result": "registered", "entry": entry}
    emit(args, data, [f"registered {entry['branch']}: pr={entry['pr']} state={entry['state']} "
                      f"head={entry['head_sha']}"])
    return EXIT_OK


def cmd_pr_show(args: argparse.Namespace) -> int:
    info = vr.repo_info(args.repo)
    branch = _branch(args, _require_root(info))
    entry = vr.read_registry_entry(info.root, branch, state_dir=args.state_dir)
    head = vr.resolve_head(info.root, branch, state_dir=args.state_dir)
    exemption = vr.check_exemption(info.root, branch, state_dir=args.state_dir)
    data = {"branch": branch, "entry": entry, "head": head._asdict(),
            "exemption": _validity_json(exemption)}
    summary = (f"pr={entry['pr']} state={entry['state']}" if entry else "not registered")
    emit(args, data, [f"branch {branch}: {summary}",
                      f"head {head.sha or '-'} ({head.source}) {head.reason}".rstrip()])
    return EXIT_OK


# --- KB subcommands -------------------------------------------------------------------------

def _kb_home() -> Optional[str]:
    return kb_source.resolve_kb_home().path


def cmd_candidate_list(args: argparse.Namespace) -> int:
    home = _kb_home()
    cands = vk.list_candidates(home) if home else ()
    data = {"candidates": [c._asdict() | {"reasons": list(c.reasons)} for c in cands]}
    lines = [f"{c.id} [{c.status}] promotable={c.promotable} {c.file}" for c in cands]
    for cand in cands:
        lines += [vp.fence("\n".join(cand.reasons))] if cand.reasons else []
    emit(args, data, lines or ["no candidates" if home else "NO-KB"])
    return EXIT_OK


def cmd_candidate_add(args: argparse.Namespace) -> int:
    home = _kb_home()
    if home is None:
        emit(args, {"result": "no-kb", "path": None}, ["NO-KB: nothing written"])
        return EXIT_OK
    out = vk.add_candidate(home, args.slug, {
        "pattern": args.pattern, "paths": args.trigger_paths or "",
        "added": args.trigger_added or "", "occurrences": args.occurrence,
        "scope": args.scope})
    emit(args, out._asdict(), [f"{out.result}: {out.path or ''} {out.message}".rstrip()])
    return EXIT_OK if out.result in CANDIDATE_OK else EXIT_FAIL


def cmd_init_profile(args: argparse.Namespace) -> int:
    info = vr.repo_info(args.repo)
    home = _kb_home()
    if home is None:
        emit(args, {"result": "no-kb", "path": None}, ["NO-KB: nothing written"])
        return EXIT_OK
    if not info.key:
        emit(args, {"result": "invalid", "path": None}, ["repo has no parseable origin remote"])
        return EXIT_FAIL
    out = vk.init_profile(home, info.key, args.name)
    emit(args, {"result": out.result, "path": out.path},
         [f"{out.result}: {out.path or ''} {out.message}".rstrip()])
    return EXIT_OK if out.result == "created" else EXIT_FAIL


def cmd_lint(args: argparse.Namespace) -> int:
    home = _kb_home()
    report = vk.lint_kb(home) if home else vk.LintReport((), ())
    data = {"errors": [f._asdict() for f in report.errors],
            "warnings": [f._asdict() for f in report.warnings]}
    lines = [] if home else ["NO-KB: nothing to lint"]
    lines += [f"ERROR {f.file}: {vp.clean_line(f.message)}" for f in report.errors]
    lines += [f"WARN  {f.file}: {vp.clean_line(f.message)}" for f in report.warnings]
    lines.append(f"{len(report.errors)} errors, {len(report.warnings)} warnings")
    emit(args, data, lines)
    return EXIT_FAIL if report.errors else EXIT_OK


# --- parser ------------------------------------------------------------------------------------

def _common_flags() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", default=".", help="target git repo (default: .)")
    common.add_argument("--json", action="store_true", help="print one JSON object")
    common.add_argument("--state-dir", default=None, help="local state directory")
    return common


def _add(sub, name: str, func, common, help_text: str) -> argparse.ArgumentParser:
    parser = sub.add_parser(name, parents=[common], help=help_text)
    parser.set_defaults(func=func)
    return parser


def _source_flags(parser: argparse.ArgumentParser, allow_none: bool) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--from-gh", action="store_true", help="read the PR through gh")
    group.add_argument("--evidence", help="verify-ci-evidence/1 JSON file")
    if allow_none:
        group.add_argument("--none", action="store_true", help="mark the branch as having no PR")
    parser.add_argument("--pr", help="PR number for --from-gh (digits only)")


def _build_receipt(sub, common) -> None:
    receipt = sub.add_parser("receipt", help="receipts").add_subparsers(dest="action", required=True)
    show = _add(receipt, "show", cmd_receipt_show, common, "validity of every receipt")
    show.add_argument("--branch")
    gen = _add(receipt, "record-generic", cmd_receipt_generic, common,
               "record generic steps when no profile applies")
    gen.add_argument("--summary", required=True)
    gen.add_argument("--method", choices=METHODS_GENERIC, default="generic")
    acc = _add(receipt, "acceptance", cmd_receipt_acceptance, common, "acceptance receipt")
    acc.add_argument("--pr", required=True)
    acc.add_argument("--attachments", required=True)
    exe = _add(receipt, "exempt", cmd_receipt_exempt, common, "acceptance exemption")
    exe.add_argument("--pr", required=True)
    exe.add_argument("--reason", required=True)
    ci = _add(receipt, "ci", cmd_receipt_ci, common, "CI receipt")
    _source_flags(ci, allow_none=False)
    ci.add_argument("--pending-declared", action="store_true")
    ci.add_argument("--exempt", action="store_true")
    ci.add_argument("--reason")


def _build_pr_and_kb(sub, common) -> None:
    pr = sub.add_parser("pr", help="PR registry").add_subparsers(dest="action", required=True)
    reg = _add(pr, "register", cmd_pr_register, common, "register the branch's PR")
    _source_flags(reg, allow_none=True)
    reg.add_argument("--branch")
    _add(pr, "show", cmd_pr_show, common, "show the registry entry").add_argument("--branch")
    cand = sub.add_parser("candidate", help="candidates").add_subparsers(dest="action", required=True)
    _add(cand, "list", cmd_candidate_list, common, "list candidates")
    add = _add(cand, "add", cmd_candidate_add, common, "write a new candidate file")
    add.add_argument("--slug", required=True)
    add.add_argument("--pattern", required=True)
    add.add_argument("--occurrence", action="append", required=True)
    add.add_argument("--trigger-paths")
    add.add_argument("--trigger-added")
    add.add_argument("--scope", default="common")
    _add(sub, "init-profile", cmd_init_profile, common, "write a profile skeleton").add_argument("--name")
    _add(sub, "lint", cmd_lint, common, "lint the KB verification/ directory")


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(description="Verification engine.")
    common = _common_flags()
    sub = parser.add_subparsers(dest="command", required=True, parser_class=JsonArgumentParser)
    _add(sub, "status", cmd_status, common, "which profile applies")
    sel = _add(sub, "select", cmd_select, common, "checklist items triggered by the diff")
    sel.add_argument("--base")
    sel.add_argument("--head", default="HEAD")
    chk = _add(sub, "checks", cmd_checks, common, "run quick checks and write a receipt")
    chk.add_argument("--base")
    chk.add_argument("--dry-run", action="store_true")
    _build_receipt(sub, common)
    _build_pr_and_kb(sub, common)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    JsonArgumentParser.json_requested = "--json" in argv
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except UsageError as exc:
        if args.json:
            print(json.dumps({"result": "error", "reason": "usage", "message": str(exc)}))
        sys.stderr.write(f"usage error: {exc}\n")
        return EXIT_USAGE
    except vr.StateError as exc:
        if args.json:
            print(json.dumps({"result": "error", "reason": "state-error", "message": str(exc)}))
        sys.stderr.write(f"state error: {exc}\n")
        return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
