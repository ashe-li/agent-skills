#!/usr/bin/env python3
"""Standalone structural quality gate for SKILL.md files.

Subset of skills-ecosystem-eval/src/learn_eval_bridge.py (structural mode).
Bridge version: 2026-03-15. Sync manually when bridge scoring logic changes.

Usage:
    python scripts/check_skill.py --files curation/SKILL.md update/SKILL.md --repo-dir .
    python scripts/check_skill.py --files pr/SKILL.md --repo-dir . --output results.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STOPWORDS = frozenset({
    "the", "and", "for", "that", "this", "with", "from", "are", "not",
    "use", "can", "will", "have", "when", "how", "you", "code", "file",
    "import", "return", "function", "class", "def", "const", "let", "var",
})

GATE_THRESHOLD_NEW = 2.5
GATE_THRESHOLD_MODIFIED = 2.0


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter and return (metadata, body)."""
    if not text.startswith("---"):
        return {}, text

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text

    try:
        metadata = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        metadata = {}

    body = parts[2].strip()
    return metadata, body


# ---------------------------------------------------------------------------
# Skill type detection
# ---------------------------------------------------------------------------

def detect_skill_type(metadata: dict, skill_path: Path) -> str:
    """Detect whether a skill is 'learned' or 'user-invocable'."""
    if "allowed-tools" in metadata or "argument-hint" in metadata:
        return "user-invocable"
    if metadata.get("user-invocable") is False or metadata.get("origin") == "auto-extracted":
        return "learned"
    if skill_path.name == "SKILL.md":
        return "user-invocable"
    return "learned"


# ---------------------------------------------------------------------------
# Structural check (System 3 Lite)
# ---------------------------------------------------------------------------

REQUIRED_SECTIONS = {"problem", "solution", "when to use", "when"}
BONUS_SECTIONS = {"example", "examples", "gotchas", "caveats", "context"}


def check_structural(
    skill_path: Path,
    repo_dir: Path,
) -> dict[str, Any]:
    """Run structural quality check on a single SKILL.md."""
    text = skill_path.read_text(encoding="utf-8")
    metadata, body = parse_frontmatter(text)
    skill_type = detect_skill_type(metadata, skill_path)
    line_count = len(text.splitlines())

    issues: list[str] = []

    # --- Frontmatter quality ---
    fm_score = 10
    if not metadata.get("name"):
        issues.append("missing name")
        fm_score -= 3
    if not metadata.get("description"):
        issues.append("missing description")
        fm_score -= 3
    elif len(metadata["description"]) < 20:
        issues.append("description too short")
        fm_score -= 1
    elif len(metadata["description"]) > 1024:
        issues.append("description too long")
        fm_score -= 2

    if skill_type == "learned":
        if metadata.get("user-invocable") is not False:
            issues.append("missing user-invocable: false")
            fm_score -= 1
    else:
        if "allowed-tools" not in metadata:
            issues.append("missing allowed-tools")
            fm_score -= 1

    # --- Description quality (for scope_fit dimension) ---
    description = metadata.get("description", "")
    desc_len = len(description)
    if desc_len == 0:
        desc_quality = 0
    elif desc_len < 30:
        desc_quality = 3
    elif desc_len < 80:
        desc_quality = 7
    elif desc_len <= 200:
        desc_quality = 10
    else:
        desc_quality = 8
    desc_lower = description.lower()
    if any(w in desc_lower for w in ["when", "if", "error", "issue", "problem", "fail"]):
        desc_quality = min(10, desc_quality + 1)

    # --- Section completeness ---
    if skill_type == "learned":
        sections_found: set[str] = set()
        for line in body.splitlines():
            stripped = line.strip().lower()
            if stripped.startswith("##"):
                heading = stripped.lstrip("#").strip()
                for req in REQUIRED_SECTIONS | BONUS_SECTIONS:
                    if req in heading:
                        sections_found.add(req)
        has_when = "when to use" in sections_found or "when" in sections_found
        required_met = ("problem" in sections_found) + ("solution" in sections_found) + has_when
        section_score = min(10, required_met * 3 + len(sections_found & BONUS_SECTIONS))
        if required_met < 3:
            issues.append(f"missing required sections ({3 - required_met} of 3)")
    else:
        step_count = sum(
            1 for line in body.splitlines()
            if re.match(r"^##\s+Step\s+\d", line, re.IGNORECASE)
        )
        if step_count >= 3:
            section_score = 10
        elif step_count == 2:
            section_score = 7
        elif step_count == 1:
            section_score = 4
        else:
            section_score = 0
            issues.append("no Step N sections found")

    # --- Code examples ---
    # Use raw ``` count for scoring (matches bridge), pairs only for reporting
    code_blocks_raw = body.count("```")
    code_blocks = code_blocks_raw // 2  # pairs, for reporting only

    if skill_type == "learned":
        code_score = min(10, code_blocks_raw * 3) if code_blocks_raw else 0
    else:
        if code_blocks_raw >= 2:  # at least one complete code block
            code_score = 10
        elif any(
            re.match(r"^\s*\|", line) for line in body.splitlines()
        ):
            code_score = 7
        else:
            code_score = 0

    if code_blocks_raw == 0 and code_score == 0:
        issues.append("no code examples")

    # --- Content density ---
    if skill_type == "learned":
        if line_count < 15:
            density_score = 3
            issues.append("very short content")
        elif line_count < 30:
            density_score = 6
        elif line_count <= 200:
            density_score = 10
        else:
            density_score = 8
    else:
        if line_count < 15:
            density_score = 3
            issues.append("very short content")
        elif line_count < 50:
            density_score = 6
        elif line_count <= 350:
            density_score = 10
        else:
            density_score = 8

    fm_score = max(0, fm_score)  # clamp to prevent negative

    structural_score = round(
        fm_score * 0.2 + section_score * 0.3 + code_score * 0.25 + density_score * 0.25,
        1,
    )

    # --- Redundancy (Jaccard overlap within repo) ---
    redundancy = _check_redundancy(skill_path, body, repo_dir, skill_type)

    return {
        "skill_path": str(skill_path),
        "skill_type": skill_type,
        "structural_score": structural_score,
        "fm_score": fm_score,
        "section_score": section_score,
        "code_score": code_score,
        "density_score": density_score,
        "description_quality": desc_quality,
        "line_count": line_count,
        "code_blocks": code_blocks,
        "issues": issues,
        "redundancy": redundancy,
    }


# ---------------------------------------------------------------------------
# Redundancy check
# ---------------------------------------------------------------------------

def _extract_keywords(text: str) -> set[str]:
    """Extract meaningful keywords from text."""
    words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", text.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 2}


def _check_redundancy(
    skill_path: Path,
    body: str,
    repo_dir: Path,
    skill_type: str,
) -> dict[str, Any]:
    """Jaccard keyword overlap check against sibling skills."""
    target_keywords = _extract_keywords(body)
    if not target_keywords:
        return {"redundancy_penalty": 0, "overlaps": []}

    # For user-invocable: compare against other SKILL.md in the repo
    # For learned: compare against ~/.claude/skills/learned/
    if skill_type == "user-invocable":
        search_dir = repo_dir
    else:
        search_dir = Path("~/.claude/skills/learned").expanduser()

    if not search_dir.exists():
        return {"redundancy_penalty": 0, "overlaps": []}

    overlaps: list[dict[str, Any]] = []
    skill_resolved = skill_path.resolve()

    glob_pattern = "SKILL.md" if skill_type == "user-invocable" else "*.md"
    for md_file in search_dir.rglob(glob_pattern):
        if md_file.resolve() == skill_resolved:
            continue
        try:
            other_content = md_file.read_text(encoding="utf-8")
        except OSError:
            continue
        other_keywords = _extract_keywords(other_content)
        if not other_keywords:
            continue

        intersection = target_keywords & other_keywords
        union = target_keywords | other_keywords
        jaccard = len(intersection) / len(union) if union else 0

        if jaccard >= 0.25:
            overlaps.append({
                "skill": md_file.stem if md_file.name != "SKILL.md" else md_file.parent.name,
                "jaccard": round(jaccard, 3),
                "shared_keywords": sorted(intersection)[:10],
            })

    overlaps.sort(key=lambda x: -x["jaccard"])
    max_jaccard = overlaps[0]["jaccard"] if overlaps else 0
    penalty = round(min(5, max_jaccard * 10), 1)

    return {
        "redundancy_penalty": penalty,
        "max_jaccard": max_jaccard,
        "overlaps": overlaps[:5],
    }


# ---------------------------------------------------------------------------
# Dimension mapping
# ---------------------------------------------------------------------------

def compute_dimensions(
    scores: dict,
    threshold: float = GATE_THRESHOLD_NEW,
) -> dict[str, Any]:
    """Map structural scores to 5-dimension learn-eval scores (1-5 scale)."""

    def clamp(val: float, lo: float = 1.0, hi: float = 5.0) -> float:
        return max(lo, min(hi, val))

    code_s = scores["code_score"]
    density_s = scores["density_score"]
    section_s = scores["section_score"]
    structural = scores["structural_score"]
    penalty = scores["redundancy"]["redundancy_penalty"]

    specificity = clamp(1 + (code_s / 10) * 2 + (density_s / 10) * 2)
    actionability = clamp(1 + structural * 0.4)  # structural-only fallback
    non_redundancy = clamp(5 - penalty)
    coverage = clamp(1 + (section_s / 10) * 2 + (density_s / 10) * 2)

    # scope_fit: compute from description quality heuristic (matches bridge)
    desc_quality = scores.get("description_quality", 0)
    scope_fit = clamp(1 + (desc_quality / 10) * 4)

    dimensions = {
        "specificity": round(specificity, 1),
        "actionability": round(actionability, 1),
        "scope_fit": round(scope_fit, 1),
        "non_redundancy": round(non_redundancy, 1),
        "coverage": round(coverage, 1),
    }

    all_pass = all(v >= threshold for v in dimensions.values())

    return {
        "dimensions": dimensions,
        "total_score": round(sum(dimensions.values()), 1),
        "gate_result": "PASS" if all_pass else "FAIL",
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Structural quality gate for SKILL.md files",
    )
    parser.add_argument(
        "--files", nargs="+", required=True,
        help="SKILL.md files to check",
    )
    parser.add_argument(
        "--repo-dir", type=Path, default=Path("."),
        help="Repository root for redundancy comparison",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Save JSON report to file",
    )
    parser.add_argument(
        "--new-files", nargs="*", default=[],
        help="Files that are newly added (use stricter threshold 2.5)",
    )
    args = parser.parse_args()

    new_file_set = {Path(f).resolve() for f in (args.new_files or [])}
    results: list[dict[str, Any]] = []
    has_failure = False

    for file_str in args.files:
        skill_path = Path(file_str)
        if not skill_path.exists():
            print(f"::warning file={file_str}::File not found, skipping")
            continue

        is_new = skill_path.resolve() in new_file_set
        threshold = GATE_THRESHOLD_NEW if is_new else GATE_THRESHOLD_MODIFIED

        scores = check_structural(skill_path, args.repo_dir)
        dims = compute_dimensions(scores, threshold=threshold)

        result = {**scores, "learn_eval": dims}
        results.append(result)

        gate = dims["gate_result"]
        if gate == "FAIL":
            has_failure = True
            failing = [
                f"{k}={v}" for k, v in dims["dimensions"].items()
                if v < threshold
            ]
            print(
                f"::error file={file_str}::"
                f"{', '.join(failing)} below threshold {threshold}"
            )
        else:
            print(f"::notice file={file_str}::PASS (total={dims['total_score']})")

    if args.output:
        args.output.write_text(
            json.dumps(results, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"Report saved: {args.output}", file=sys.stderr)

    sys.exit(1 if has_failure else 0)


if __name__ == "__main__":
    main()
