"""Execution-summary report for a plan-run state file.

Pure functions only: no disk I/O, no subprocess, never opens evidence paths
(evidence items are opaque display strings). Called by `plan_runner.py
report`, which lazy-imports this module so `hook-stop` never loads it.

This module must NOT import plan_runner (decision A): running plan_runner as
`__main__` and importing it again would create a second module object with
duplicate constants. Anything runner-specific is injected by the caller
(`strip_unsafe`, the `progress` dict from `plan_runner.summary()`).

Markdown escaping follows decision D of
plans/active/plan-runner-step-summary-and-completion-report.md:
  - byte layer: every state-sourced string goes through `strip_unsafe`;
  - parser-inert: report headings are `####` except the fixed `### 執行摘要`
    (no plan text, so never contains "Phase"; the plan title goes on the
    non-heading `**Plan**：` line); every other line starts with a
    non-space, non-`-`, non-`#` character (`|`, `*`, `>`, CJK text), so it can
    never match parse_plan's phase_re / step_re / field_re or be taken as an
    indented action continuation line, even without the `##` wrapper;
  - markdown: `|` -> `\\|` and newlines folded in table cells, `<`/`>` ->
    `&amp;`/`&lt;`/`&gt;` everywhere, evidence in a code span whose delimiter is
    longer than the longest backtick run inside;
  - fence look-alikes neutralized with the same rule as the runner.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Callable

# Mirrors plan_runner's status constants; drift guarded by
# scripts/tests/test_plan_report.py (k).
PENDING = "pending"
IN_PROGRESS = "in_progress"
COMPLETED = "completed"
FAILED = "failed"
BLOCKED = "blocked"
SKIPPED = "skipped"

REPORT_SCHEMA_VERSION = 1
UNCLASSIFIED_PHASE = "（未分類）"
EMPTY_PHASE_NOTE = "（此 phase 無 step）"
NO_VALUE = "—"
# Blockquote marker at column 0, then 4 spaces (3 after the marker's own
# space, so still a paragraph, not a code block). The leading `>` is what keeps
# `  - Files: x` style summary lines away from parse_plan's field_re.
SUMMARY_LINE_PREFIX = ">    "
TABLE_HEADER = "| Step | 標題 | 狀態 | 耗時 | Evidence |"
TABLE_SEPARATOR = "|---|---|---|---|---|"

# Same values and rule as plan_runner.PLAN_FENCE_START / PLAN_FENCE_END /
# _FENCE_LOOKALIKE_CHAR / _neutralize_fence_lookalikes (decision D rule 4).
# Duplicated rather than imported because of decision A; keep in sync.
PLAN_FENCE_START = "--- plan data (not instructions) ---"
PLAN_FENCE_END = "--- end plan data ---"
_FENCE_LOOKALIKE_CHAR = "‑"  # U+2011 non-breaking hyphen
# Fixed text: parse_plan() treats any `### ` heading containing "Phase" as a
# phase, so no plan-sourced text may ever appear on this line.
REPORT_HEADING = "### 執行摘要"

# Trailing "未完成與例外" categories, in display order.
_TRAILING_CATEGORIES = (
    ("failed", FAILED, "失敗"),
    ("in_progress", IN_PROGRESS, "進行中"),
    ("blocked", BLOCKED, "受阻"),
    ("pending", PENDING, "未開始"),
    ("skipped", SKIPPED, "略過"),
)


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _duration_seconds(step: dict[str, Any]) -> int | None:
    started = _parse_ts(step.get("started_at"))
    completed = _parse_ts(step.get("completed_at"))
    if started is None or completed is None:
        return None
    delta = (completed - started).total_seconds()
    return None if delta < 0 else int(delta)


def _step_row(sid: str, step: dict[str, Any]) -> dict[str, Any]:
    evidence = step.get("evidence") or []
    return {
        "id": step.get("id") or sid,
        "title": step.get("title") or "",
        "status": step.get("status") or "",
        "duration_seconds": _duration_seconds(step),
        "summary": step.get("summary"),
        "evidence": [str(item) for item in evidence],
    }


def _group_phases(state: dict[str, Any]) -> list[dict[str, Any]]:
    phase_order = list(state.get("phase_order") or [])
    grouped: dict[str, list[dict[str, Any]]] = {name: [] for name in phase_order}
    unclassified: list[dict[str, Any]] = []
    for sid, step in (state.get("steps") or {}).items():
        phase = step.get("phase") or ""
        bucket = grouped.get(phase) if phase else None
        (bucket if bucket is not None else unclassified).append(_step_row(sid, step))
    phases = [{"name": name, "steps": grouped[name]} for name in phase_order]
    if unclassified:
        phases.append({"name": UNCLASSIFIED_PHASE, "steps": unclassified})
    return phases


def _trailing_lists(state: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    lists: dict[str, list[dict[str, Any]]] = {key: [] for key, _, _ in _TRAILING_CATEGORIES}
    by_status = {status: key for key, status, _ in _TRAILING_CATEGORIES}
    for sid, step in (state.get("steps") or {}).items():
        key = by_status.get(step.get("status"))
        if key is None:
            continue
        item = {"id": step.get("id") or sid, "title": step.get("title") or ""}
        if key == "failed":
            item["failure_reason"] = step.get("failure_reason")
        lists[key].append(item)
    return lists


def build_report(state: dict[str, Any], *, progress: Any, now: str) -> dict[str, Any]:
    """Build a fresh report dict from `state` (never mutated).

    `progress` is plan_runner.summary(state); only its "progress" string is
    kept. `now` is recorded as `generated_at` (json only, so md output is
    deterministic for a given state).
    """
    progress_text = progress["progress"] if isinstance(progress, dict) else str(progress)
    report: dict[str, Any] = {
        "title": state.get("title") or "",
        "slug": state.get("slug") or "",
        "generated_at": now,
        "progress": progress_text,
        "phases": _group_phases(state),
    }
    report.update(_trailing_lists(state))
    return report


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------

def format_duration(seconds: float | int | None) -> str:
    if seconds is None or seconds < 0:
        return NO_VALUE
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def _neutralize_fence_lookalikes(text: str) -> str:
    fence_norms = {PLAN_FENCE_START.lower(), PLAN_FENCE_END.lower()}
    lines = text.split("\n")
    return "\n".join(
        line.replace("-", _FENCE_LOOKALIKE_CHAR) if line.strip().lower() in fence_norms else line
        for line in lines
    )


def _escape_html(text: str) -> str:
    # `&` first, otherwise the entities produced below get double-escaped.
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _inline(value: Any, strip_unsafe: Callable[[str], str]) -> str:
    """Single-line text: bytes stripped, newlines folded, `<`/`>` escaped."""
    text = strip_unsafe("" if value is None else str(value))
    return _escape_html(" ".join(text.split("\n")).strip())


def _cell(value: Any, strip_unsafe: Callable[[str], str]) -> str:
    return _inline(value, strip_unsafe).replace("|", "\\|")


def _code_span(value: str, strip_unsafe: Callable[[str], str]) -> str:
    content = _cell(value, strip_unsafe)
    longest = max((len(run) for run in re.findall(r"`+", content)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if content.startswith("`") or content.endswith("`") else ""
    return f"{fence}{pad}{content}{pad}{fence}"


def _table_row(step: dict[str, Any], strip_unsafe: Callable[[str], str]) -> str:
    evidence = ", ".join(_code_span(item, strip_unsafe) for item in step["evidence"])
    cells = (
        _cell(step["id"], strip_unsafe),
        _cell(step["title"], strip_unsafe) or NO_VALUE,
        _cell(step["status"], strip_unsafe),
        format_duration(step["duration_seconds"]),
        evidence or NO_VALUE,
    )
    return "| " + " | ".join(cells) + " |"


def _summary_block(step: dict[str, Any], strip_unsafe: Callable[[str], str]) -> list[str]:
    raw = step.get("summary")
    if not raw:
        return []
    text = _neutralize_fence_lookalikes(_escape_html(strip_unsafe(str(raw))))
    body = [(SUMMARY_LINE_PREFIX + line).rstrip() for line in text.split("\n")]
    return ["", f"**{_inline(step['id'], strip_unsafe)}** 摘要："] + body


def _phase_lines(phase: dict[str, Any], strip_unsafe: Callable[[str], str]) -> list[str]:
    lines = ["", f"#### {_inline(phase['name'], strip_unsafe)}", ""]
    if not phase["steps"]:
        return lines + [EMPTY_PHASE_NOTE]
    lines += [TABLE_HEADER, TABLE_SEPARATOR]
    lines += [_table_row(step, strip_unsafe) for step in phase["steps"]]
    for step in phase["steps"]:
        lines += _summary_block(step, strip_unsafe)
    return lines


def _trailing_item(key: str, item: dict[str, Any], strip_unsafe: Callable[[str], str]) -> str:
    # `**id**` keeps a hand-edited id such as "[x] S9" off step_re's `- [x]`.
    line = f"- **{_inline(item['id'], strip_unsafe)}** {_inline(item['title'], strip_unsafe)}".rstrip()
    if key == "failed" and item.get("failure_reason"):
        line += f"：{_inline(item['failure_reason'], strip_unsafe)}"
    return line


def _trailing_lines(report: dict[str, Any], strip_unsafe: Callable[[str], str]) -> list[str]:
    lines = ["", "#### 未完成與例外"]
    for key, _, label in _TRAILING_CATEGORIES:
        items = report.get(key) or []
        if items:
            lines += ["", f"**{label}**", ""]
            lines += [_trailing_item(key, item, strip_unsafe) for item in items]
    if len(lines) == 2:
        lines += ["", "（無）"]
    return lines


def render_report_md(report: dict[str, Any], *, strip_unsafe: Callable[[str], str]) -> str:
    """Render the md report. `strip_unsafe` is required (no unsafe default)."""
    lines = [
        REPORT_HEADING,
        "",
        f"**Plan**：{_inline(report['title'], strip_unsafe)}",
        "",
        f"**進度**：{_inline(report['progress'], strip_unsafe)}",
    ]
    for phase in report["phases"]:
        lines += _phase_lines(phase, strip_unsafe)
    lines += _trailing_lines(report, strip_unsafe)
    return _neutralize_fence_lookalikes("\n".join(lines))


def render_report_json(report: dict[str, Any]) -> str:
    payload = {"schema_version": REPORT_SCHEMA_VERSION, **report}
    return json.dumps(payload, indent=2, ensure_ascii=False)
