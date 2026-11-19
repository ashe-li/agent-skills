#!/usr/bin/env python3
"""Pick the checklist items a diff triggers (stdlib only).

Matching rules come from docs/knowledge-source-contract.md (Checklist 項目):
changed files are taken from merge-base(base, head)..head; Trigger-paths
needs a matching file (and status, when Trigger-status is given); with
Trigger-added too, an added line in one of those files must match; with only
Trigger-added, every changed file's added lines are searched. Retired items
and items that fail lint are never selected. The builtin ENG-001 is selected
when the repo is the KB itself and the diff touches `verification/`.
"""
from __future__ import annotations

import os
import re
from typing import Mapping, NamedTuple, Optional, Sequence

import kb_source
import verify_git as vg
import verify_profile as vp

TRIGGER_PATHS, TRIGGER_ADDED, TRIGGER_BUILTIN = "Trigger-paths", "Trigger-added", "builtin"
BUILTIN_PATTERN = f"{kb_source.VERIFICATION_DIR}/**"


class Selected(NamedTuple):
    item: vp.ChecklistItem
    reasons: tuple


class Diff(NamedTuple):
    base: Optional[str]
    head: Optional[str]
    changes: tuple
    added: Mapping[str, tuple]


def load_diff(root: str, base_ref: str, head_ref: str = "HEAD") -> Diff:
    head = vg.resolve_commit(root, head_ref)
    base = vg.merge_base(root, base_ref, head) if head else None
    if base is None and head:
        base = vg.merge_base(root, f"origin/{base_ref}", head)
    if not (base and head):
        return Diff(base, head, (), {})
    return Diff(base, head, vg.changed_files(root, base, head), vg.added_lines(root, base, head))


def all_items(ctx: vp.KbContext) -> tuple:
    common = vp.checklist_items(ctx.common_doc, vp.COMMON_FILE)
    own = vp.checklist_items(ctx.profile.doc, ctx.profile.filename) if ctx.profile else ()
    return common + own


def _path_reasons(item: vp.ChecklistItem, changes: Sequence[vg.Change]) -> list:
    reasons = []
    for change in changes:
        pattern = vp.first_match(change.path, item.trigger_paths)
        if pattern is None:
            continue
        if item.trigger_status and change.status not in item.trigger_status:
            continue
        reasons.append({"trigger": TRIGGER_PATHS, "file": change.path,
                        "pattern": pattern, "status": change.status})
    return reasons


def _added_reasons(item: vp.ChecklistItem, files: Sequence[str],
                   added: Mapping[str, tuple]) -> list:
    regex = re.compile(item.trigger_added)
    return [{"trigger": TRIGGER_ADDED, "file": path, "pattern": item.trigger_added}
            for path in files if any(regex.search(line) for line in added.get(path, ()))]


def match_item(item: vp.ChecklistItem, diff: Diff) -> tuple:
    """Reasons the item is triggered; empty when it is not."""
    if item.status == vp.STATUS_RETIRED or vp.trigger_errors(item.trigger_paths, item.trigger_added):
        return ()
    if item.trigger_paths:
        path_hits = _path_reasons(item, diff.changes)
        if not path_hits or not item.trigger_added:
            return tuple(path_hits)
        files = list(dict.fromkeys(r["file"] for r in path_hits))
        added = _added_reasons(item, files, diff.added)
        return tuple(path_hits + added) if added else ()
    files = [c.path for c in diff.changes]
    return tuple(_added_reasons(item, files, diff.added))


def builtin_reasons(ctx: vp.KbContext, root: Optional[str], diff: Diff) -> tuple:
    if not (ctx.home and root):
        return ()
    if os.path.realpath(root) != os.path.realpath(ctx.home):
        return ()
    return tuple({"trigger": TRIGGER_BUILTIN, "file": c.path, "pattern": BUILTIN_PATTERN}
                 for c in diff.changes if vp.glob_match(c.path, BUILTIN_PATTERN))


def select(ctx: vp.KbContext, root: Optional[str], diff: Diff) -> tuple:
    chosen = [Selected(item, reasons) for item in all_items(ctx)
              for reasons in (match_item(item, diff),) if reasons]
    builtin = builtin_reasons(ctx, root, diff)
    if builtin:
        chosen.append(Selected(vp.builtin_item(), builtin))
    return tuple(chosen)


def selected_json(selected: Sequence[Selected]) -> list:
    return [{"id": s.item.id, "title": vp.clean_line(s.item.title), "source": s.item.source,
             "check": vp.clean_line(s.item.fields.get("Check", "")),
             "evidence": vp.clean_line(s.item.fields.get("Evidence", "")),
             "reasons": list(s.reasons)} for s in selected]
