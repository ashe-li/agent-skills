#!/usr/bin/env python3
"""KB-side operations: lint, candidate list/add, init-profile.

Lint follows docs/knowledge-source-contract.md: files in `verification/`
that the contract does not define are errors (README.md and .gitkeep are
exempt); profiles, `_common.md` and candidates are validated field by field.
Candidate text is only read and linted, never executed.

The only writes the engine makes into a KB live here: a new candidate file
under `verification/_candidates/` and a new profile skeleton. Both are
created with O_EXCL and never overwrite an existing file.
"""
from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from typing import NamedTuple, Optional, Sequence

import kb_source
import verify_profile as vp

EXEMPT_NAMES = frozenset({"README.md", ".gitkeep"})
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
CANDIDATE_STATUSES = frozenset({"candidate", "promoted", "rejected"})
PROFILE_REQUIRED_FIELDS = ("Check", "Evidence", "Incident", "Added")
MAX_NOTES_LINES = 20
MAX_FLOW_NOTES = 200
MIN_DISTINCT_SOURCES = 2
OCCURRENCE_FIELDS = 4
SECTION_PATTERN, SECTION_TRIGGER, SECTION_OCCURRENCES = "Pattern", "Proposed trigger", "Occurrences"


class Finding(NamedTuple):
    file: str
    message: str


class LintReport(NamedTuple):
    errors: tuple
    warnings: tuple


class Candidate(NamedTuple):
    id: str
    file: str
    status: str
    promotable: bool
    reasons: tuple


class WriteResult(NamedTuple):
    result: str
    path: Optional[str]
    message: str


def _vdir(home: str) -> Path:
    return Path(home) / kb_source.VERIFICATION_DIR


def _read_text(home: str, path: Path) -> Optional[str]:
    raw = vp.read_kb_file(home, path)
    return None if raw is None else raw.decode("utf-8", errors="replace")


# --- checklist and profile lint ---------------------------------------------------

def item_errors(item: vp.ChecklistItem) -> list:
    errors = [] if vp.ITEM_ID_RE.match(item.id) else [f"{item.id}: ID must look like ABC-001"]
    errors += [f"{item.id}: {e}" for e in vp.trigger_errors(item.trigger_paths, item.trigger_added)]
    if item.trigger_status and not vp.TRIGGER_STATUS_RE.match(item.trigger_status):
        errors.append(f"{item.id}: Trigger-status may only combine A, M, D, R")
    errors += [f"{item.id}: {k} is required" for k in PROFILE_REQUIRED_FIELDS
               if not item.fields.get(k, "").strip()]
    if item.status not in (vp.STATUS_ACTIVE, vp.STATUS_RETIRED):
        errors.append(f"{item.id}: Status must be active or retired")
    return errors


def _frontmatter_errors(doc: vp.Doc, schema: str) -> list:
    if doc.fm_reason:
        return [f"frontmatter: {doc.fm_reason}"]
    found = doc.frontmatter.get("schema")
    return [] if found == schema else [f"schema must be {schema}, got {found!r}"]


def _profile_meta_errors(doc: vp.Doc, stem: str) -> list:
    errors = _frontmatter_errors(doc, vp.PROFILE_SCHEMA)
    name = doc.frontmatter.get("name")
    if name != stem or not NAME_RE.match(str(name)):
        errors.append("name must equal the file stem and use lowercase letters, digits and '-'")
    remotes = doc.frontmatter.get("remotes")
    if not isinstance(remotes, tuple) or not remotes:
        errors.append("remotes must be a non-empty list")
    else:
        errors += [f"remote {r!r} is not host/owner/repo" for r in remotes
                   if kb_source.normalize_remote_or_key(r) is None]
    return errors


def _profile_warnings(doc: vp.Doc) -> list:
    warnings = []
    if not vp.split_list(vp.section_field(doc, vp.SECTION_CI, "Required-jobs")):
        warnings.append("CI Required-jobs is empty; CI evidence can never be judged green")
    notes = vp.section(doc, vp.SECTION_NOTES)
    if notes and len(notes.text.strip("\n").splitlines()) > MAX_NOTES_LINES:
        warnings.append(f"Verifier notes exceed {MAX_NOTES_LINES} lines")
    flows = vp.section(doc, vp.SECTION_FLOWS)
    for item in flows.items if flows else ():
        if len(item.fields.get("Notes", "")) > MAX_FLOW_NOTES:
            warnings.append(f"{item.id}: Notes exceed {MAX_FLOW_NOTES} characters")
    return warnings


def lint_profile(path: Path, text: str) -> tuple:
    """(errors, warnings, checklist items) for one profile."""
    doc = vp.parse_doc(text)
    errors = _profile_meta_errors(doc, path.stem)
    sec = vp.section(doc, vp.SECTION_QUICK)
    for item in sec.items if sec else ():
        errors += list(vp.parse_quick_check(item).invalid)
    items = vp.checklist_items(doc, path.name)
    for item in items:
        errors += item_errors(item)
    return errors, _profile_warnings(doc), items


# --- directory walk -------------------------------------------------------------------

def _stray_entries(vdir: Path) -> list:
    stray = []
    for entry in sorted(vdir.iterdir()):
        name = entry.name
        if name in EXEMPT_NAMES:
            continue
        if name == vp.CANDIDATES_DIR and entry.is_dir() and not entry.is_symlink():
            stray += [c for c in sorted(entry.iterdir()) if c.name not in EXEMPT_NAMES
                      and not (c.name.endswith(".md") and c.is_file())]
        elif name == vp.COMMON_FILE or (name.endswith(".md") and not name.startswith("_")
                                        and entry.is_file()):
            continue
        else:
            stray.append(entry)
    return stray


def _duplicate_errors(items: Sequence[vp.ChecklistItem]) -> list:
    seen, errors = {vp.BUILTIN_ID: "builtin"}, []
    for item in items:
        if item.id in seen:
            errors.append(Finding(item.source, f"{item.id}: duplicate ID (also in {seen[item.id]})"))
        seen.setdefault(item.id, item.source)
    return errors


def _remote_errors(home: str, profiles: Sequence[Path]) -> list:
    owners, errors = {}, []
    for path in profiles:
        doc = vp.parse_doc(_read_text(home, path) or "")
        for remote in doc.frontmatter.get("remotes") or ():
            key = kb_source.normalize_remote_or_key(remote)
            if key and key in owners and owners[key] != path.name:
                errors.append(Finding(str(path), f"remote {key} also listed by {owners[key]}"))
            owners.setdefault(key, path.name)
    return errors


def _lint_files(home: str, vdir: Path) -> tuple:
    errors, warnings, items = [], [], []
    profiles = [p for p in sorted(vdir.glob("*.md"))
                if p.name not in EXEMPT_NAMES and not p.name.startswith("_")]
    for path in profiles:
        text = _read_text(home, path)
        if text is None:
            errors.append(Finding(str(path), "profile resolves outside the KB root; ignored"))
            continue
        errs, warns, own = lint_profile(path, text)
        errors += [Finding(str(path), e) for e in errs]
        warnings += [Finding(str(path), w) for w in warns]
        items += own
    return errors + _remote_errors(home, profiles), warnings, items


def _lint_common(home: str, vdir: Path) -> tuple:
    path = vdir / vp.COMMON_FILE
    text = _read_text(home, path) if path.exists() else None
    if text is None:
        return [], []
    doc = vp.parse_doc(text)
    errors = [Finding(str(path), e) for e in _frontmatter_errors(doc, vp.CHECKLIST_SCHEMA)]
    items = vp.checklist_items(doc, vp.COMMON_FILE)
    for item in items:
        errors += [Finding(str(path), e) for e in item_errors(item)]
    return errors, list(items)


def lint_kb(home: str) -> LintReport:
    vdir = _vdir(home)
    if not vdir.is_dir():
        return LintReport((Finding(str(vdir), "verification/ directory is missing"),), ())
    errors = [Finding(str(p), "file is not part of the knowledge-source contract")
              for p in _stray_entries(vdir)]
    common_errors, common_items = _lint_common(home, vdir)
    file_errors, warnings, profile_items = _lint_files(home, vdir)
    errors += common_errors + file_errors + _duplicate_errors(common_items + profile_items)
    for cand in list_candidates(home):
        errors += [Finding(cand.file, r) for r in cand.reasons if r.startswith("invalid:")]
    return LintReport(tuple(errors), tuple(warnings))


# --- candidates ----------------------------------------------------------------------

def _occurrence_sources(text: str) -> tuple:
    sources = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= OCCURRENCE_FIELDS and parts[2]:
            sources.append(parts[2])
    return tuple(sources)


def _candidate_invalid(doc: vp.Doc) -> list:
    errors = _frontmatter_errors(doc, vp.CANDIDATE_SCHEMA)
    if not str(doc.frontmatter.get("id") or "").strip():
        errors.append("id is missing")
    if doc.frontmatter.get("status") not in CANDIDATE_STATUSES:
        errors.append("status must be candidate, promoted or rejected")
    return [f"invalid: {e}" for e in errors]


def candidate_info(path: Path, text: str) -> Candidate:
    doc = vp.parse_doc(text)
    reasons = _candidate_invalid(doc)
    status = str(doc.frontmatter.get("status") or "")
    if status != "candidate":
        reasons.append(f"status is {status!r}, not candidate")
    trigger = vp.section(doc, SECTION_TRIGGER)
    fields = trigger.fields if trigger else {}
    reasons += [f"proposed trigger: {e}" for e in vp.trigger_errors(
        vp.split_list(fields.get("Trigger-paths")), fields.get("Trigger-added", ""))]
    occ = vp.section(doc, SECTION_OCCURRENCES)
    distinct = len(set(_occurrence_sources(occ.text if occ else "")))
    if distinct < MIN_DISTINCT_SOURCES:
        reasons.append(f"needs {MIN_DISTINCT_SOURCES} occurrences with distinct sources, has {distinct}")
    cid = vp.clean_line(str(doc.frontmatter.get("id") or path.stem))
    return Candidate(cid, str(path), vp.clean_line(status), not reasons, tuple(reasons))


def list_candidates(home: str) -> tuple:
    cdir = _vdir(home) / vp.CANDIDATES_DIR
    if not cdir.is_dir() or not vp.inside(home, cdir):
        return ()
    found = []
    for path in sorted(cdir.glob("*.md")):
        text = _read_text(home, path)
        if text is not None and path.name not in EXEMPT_NAMES:
            found.append(candidate_info(path, text))
    return tuple(found)


def _create_exclusive(path: Path, text: str) -> bool:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    return True


def _target_dir(home: str, *parts: str) -> Optional[Path]:
    """Create verification/<parts> one level at a time, refusing symlinks."""
    directory = Path(home)
    for part in (kb_source.VERIFICATION_DIR, *parts):
        directory = directory / part
        if directory.is_symlink() or not vp.inside(home, directory):
            return None
        directory.mkdir(exist_ok=True)
    return directory


def candidate_text(cid: str, scope: str, pattern: str, paths: str, added: str,
                   occurrences: Sequence[str], created: str) -> str:
    trigger = [f"- Trigger-paths: {vp.clean_line(paths)}"] if paths else []
    trigger += [f"- Trigger-added: {vp.clean_line(added)}"] if added else []
    lines = ["---", f"schema: {vp.CANDIDATE_SCHEMA}", f"id: {cid}", "status: candidate",
             f"proposed_scope: {scope}", f"created: {created}", "---", "",
             f"# {vp.clean_line(pattern, 120)}", "", f"## {SECTION_PATTERN}", "",
             vp.clean_text(pattern, max_chars=2000, max_lines=20), "", f"## {SECTION_TRIGGER}", "",
             *(trigger or ["(none yet)"]), "", f"## {SECTION_OCCURRENCES}", "",
             *(vp.clean_line(o) for o in occurrences), ""]
    return "\n".join(lines)


def add_candidate(home: str, slug: str, text_args: dict, today: Optional[date] = None) -> WriteResult:
    if not SLUG_RE.match(slug):
        return WriteResult("invalid", None, "slug must be lowercase letters, digits and '-'")
    scope = text_args.get("scope") or "common"
    if not NAME_RE.match(scope):
        return WriteResult("invalid", None, "scope must be 'common' or a profile name")
    directory = _target_dir(home, vp.CANDIDATES_DIR)
    if directory is None:
        return WriteResult("invalid", None, "_candidates resolves outside the KB root")
    created = (today or date.today()).isoformat()
    path = directory / f"{created}-{slug}.md"
    text = candidate_text(f"CAND-{slug.upper()}", scope, text_args.get("pattern", ""),
                          text_args.get("paths", ""), text_args.get("added", ""),
                          text_args.get("occurrences") or (), created)
    if not _create_exclusive(path, text):
        return WriteResult("exists", str(path), "candidate file already exists")
    return WriteResult("created", str(path), "")


# --- init-profile ----------------------------------------------------------------------

def default_profile_name(repo_key: str) -> str:
    stem = re.sub(r"[^a-z0-9-]+", "-", repo_key.rsplit("/", 1)[-1].lower()).strip("-")
    return stem or "repo"


def profile_skeleton(name: str, repo_key: str) -> str:
    return "\n".join([
        "---", f"schema: {vp.PROFILE_SCHEMA}", f"name: {name}", "remotes:", f"  - {repo_key}",
        f"default_base: {vp.DEFAULT_BASE}", "---", "", f"# {name} verification profile", "",
        f"## {vp.SECTION_QUICK}", "",
        "<!-- ### QC-001 — <title>, then '- Argv: <command>' (no shell metacharacters) -->", "",
        f"## {vp.SECTION_PATHS}", "", "<!-- - UI: <globs>   - Tests: <globs> -->", "",
        f"## {vp.SECTION_CI}", "", "<!-- - Workflow: <name>   - Required-jobs: <jobs> -->", "",
        f"## {vp.SECTION_CHECKLIST}", "", f"## {vp.SECTION_NOTES}", "", ""])


def init_profile(home: str, repo_key: str, name: Optional[str]) -> WriteResult:
    name = name or default_profile_name(repo_key)
    if not NAME_RE.match(name):
        return WriteResult("invalid", None, "name must use lowercase letters, digits and '-'")
    directory = _target_dir(home)
    if directory is None:
        return WriteResult("invalid", None, "verification/ resolves outside the KB root")
    path = directory / f"{name}.md"
    if path.exists() or path.is_symlink() or not _create_exclusive(path, profile_skeleton(name, repo_key)):
        return WriteResult("exists", str(path), "profile already exists; not overwritten")
    return WriteResult("created", str(path), "")
