#!/usr/bin/env python3
"""Receipts and validity checks (stdlib only).

This module is the single implementation of "is this receipt still valid".
The engine (verify_engine.py) writes receipts through it and the gate hook
imports it to decide; neither keeps its own copy of the rules. The rules and
reason codes are documented in docs/knowledge-source-contract.md (sections
快檢 receipt, 驗收 receipt, CI receipt, PR 註冊表, head 決定順序, 免檢規則).

Local state lives in verify_state.py (state directory, atomic writes, repo
identity) and verify_registry.py (PR registry, push markers, session links,
head resolution). Every public name of those two modules is re-exported here,
so callers keep using `verify_receipts.<name>`.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import NamedTuple, Optional

import verify_git as vg
import verify_profile as vp
from verify_registry import (  # noqa: F401  (re-exported public API)
    BRANCH_MARKER_KEY, REGISTRY_DIR, REPOS_DIR, R_HEAD_UNRESOLVED, SCHEMA_REGISTRY,
    SCHEMA_REPO_STATE, SCHEMA_SESSION, SESSIONS_DIR, SOURCE_LOCAL, SOURCE_PUSHED,
    SOURCE_REG, SOURCE_UNRESOLVED, STATE_CLOSED, STATE_NONE, STATE_OPEN,
    STATE_UNREGISTERED, HeadResolution, bump_session_blocks, find_entry_by_pr,
    mark_push_unresolved, push_unresolved_at, read_registry_entry, read_session,
    record_push, record_session_link, register, registered_branches, registry_entries,
    resolve_head, sanitize_session_id, _read_entry)
from verify_state import (  # noqa: F401  (re-exported public API)
    DEFAULT_STATE_PARTS, DIR_MODE, ENV_STATE_DIR, FILE_MODE, HASH_CHARS, LOCK_NAME,
    RepoInfo, StateError, dir_lock, dump_json, now_iso, parse_time, read_json, repo_info, safe_dir,
    state_dir_path, state_lock, state_root, write_bytes_atomic)

ENGINE_VERSION = "1.0.0"
RECEIPTS_PARTS = (vg.VERIFICATION_DIRNAME, "receipts")

SCHEMA_QUICK = "verify-receipt/1"
SCHEMA_CI = "verify-ci-receipt/1"
SCHEMA_ACCEPTANCE = "verify-acceptance-receipt/1"

VERDICT_PASS, CHECK_PASS, CHECK_SKIPPED = "PASS", "PASS", "SKIPPED"
SKIPPED_BY_WHEN = "when"
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
R_REG_STALE = "registration-stale"
R_NOT_EXEMPT = "not-exempt"
R_UNREGISTERED = "pr-unregistered"
R_EXEMPT_CLOSED = "exempt-closed"
R_EXEMPT_NONE = "exempt-none"
R_UI_NO_PROFILE = "no-profile"
R_UI_NO_PATHS = "no-ui-paths"
R_UI_DIFF_UNAVAILABLE = "diff-unavailable"

_ACCEPTANCE_NAME = re.compile(r"^acceptance-pr-(\d+)-([0-9a-f]{40}(?:[0-9a-f]{24})?)\.json$")


class Validity(NamedTuple):
    valid: bool
    reason: str
    receipt: Optional[dict]


def _invalid(reason: str, receipt: Optional[dict] = None) -> Validity:
    return Validity(False, reason, receipt)


# --- repo identity and receipt files -------------------------------------------

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


# --- validity: quick receipt ---------------------------------------------------------

def default_base_merge_bases(root: str, profile: Optional[vp.Profile]) -> frozenset:
    """Merge-bases of HEAD with the local default_base and origin/<default_base>."""
    base = vp.default_base(profile)
    found = (vg.merge_base(root, "HEAD", base), vg.merge_base(root, "HEAD", f"origin/{base}"))
    return frozenset(f for f in found if f)


def when_skip_trusted(receipt: dict, root: str, profile: Optional[vp.Profile]) -> bool:
    """When-skips count only if When was computed against default_base.

    The diff base must be the merge-base of HEAD with the local default_base
    or with origin/<default_base> (a local default_base may lag behind
    origin). `checks --base <ref>` with any other ref, for example HEAD, can
    make the diff empty and skip a conditional check the change triggers.
    """
    diff_base = receipt.get("diff_base_sha")
    return vg.is_sha(diff_base) and diff_base in default_base_merge_bases(root, profile)


def _check_satisfied(check: object, when_ok: bool) -> bool:
    """A required check counts when it passed, or was skipped because When
    matched no changed file of the default-base diff; anything else does not."""
    if not isinstance(check, dict):
        return False
    if check.get("required") is not True or check.get("status") == CHECK_PASS:
        return True
    return (when_ok and check.get("status") == CHECK_SKIPPED
            and check.get("skipped_by") == SKIPPED_BY_WHEN)


def _required_passed(receipt: dict, root: str, profile: Optional[vp.Profile]) -> bool:
    checks = receipt.get("checks")
    if receipt.get("verdict") != VERDICT_PASS or not isinstance(checks, list):
        return False
    when_ok = when_skip_trusted(receipt, root, profile)
    return all(_check_satisfied(c, when_ok) for c in checks)


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
    if not _required_passed(receipt, info.root, ctx.profile):
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
