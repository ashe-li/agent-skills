"""Red-phase tests for the `report` subcommand and `scripts/plan_report.py`
(S2.1 of plans/active/plan-runner-step-summary-and-completion-report.md).

Target interface (not yet implemented — this file is the RED half of the
red/green cycle; S2.2 implements it):
    - New module `scripts/plan_report.py`. It must NOT `import plan_runner`
      (decision A) — it defines its own copy of the six status constants
      (PENDING/IN_PROGRESS/COMPLETED/FAILED/BLOCKED/SKIPPED), guarded here
      by a drift test (k) against `plan_runner`'s own constants.
    - `build_report(state, *, progress, now) -> dict`: pure, does not
      mutate `state`. `progress` is the dict `plan_runner.summary(state)`
      returns (`{"total", "by_status", "progress": "N/M", "all_done"}`);
      the report's own `report["progress"]` must equal `progress["progress"]`.
      `now` is a caller-supplied timestamp (opaque to these tests beyond
      "must be accepted and must not crash the call" — see the assumptions
      note at the bottom of this docstring).
    - `render_report_md(report, *, strip_unsafe) -> str`: `strip_unsafe` is
      a required keyword-only callable (no default), injected by the caller
      as `plan_runner._strip_unsafe_bytes`.
    - `render_report_json(report) -> str`: plain `json.dumps`-shaped output,
      `json.loads`-able, carrying `schema_version: 1`.
    - `format_duration(seconds: float | int | None) -> str`: `45s` / `2m49s`
      / `1h02m`; `None` or negative -> `"—"`.
    - `plan_runner.cmd_report(Namespace(plan, format, output, force))`:
      lazy-imports `plan_report` inside the function body (so `hook-stop`
      never loads it — see test (l)); never calls `save_state`.

Conventions follow scripts/tests/test_plan_runner_summary.py (CLI helpers
that call `pr.cmd_*(Namespace(...))` in-process so coverage tooling sees the
lines run) and scripts/tests/test_plan_run_hook.py's HookStopCliTests (faked
$HOME, subprocess for the one genuinely-external-process assertion). This
file is self-contained: it does not import from sibling test modules, and it
does NOT `import plan_report` at module scope — every test that needs it
imports lazily (in `setUp` or the test body itself) via `_import_plan_report()`
below, so a missing/broken module surfaces as that test's ERROR rather than
failing collection of the whole file.

Run: cd <worktree> && python3 -m unittest scripts.tests.test_plan_report -v

Interface assumptions this file bakes in (S2.2 must conform, or this file
needs a matching follow-up edit — flag either in review, don't silently
diverge):
    - `build_report()`'s per-step dict (inside `phases[i]["steps"]`) has
      exactly: `id`, `title`, `status`, `duration_seconds`, `summary`,
      `evidence`. Missing `summary`/`evidence` keys on the input step read
      as `None` / `[]` respectively (old-state compatibility).
    - `report["phases"]` is a list of `{"name": <phase or "（未分類）">,
      "steps": [...]}` in `phase_order` order, with the unclassified group
      (steps whose phase is falsy or absent from `phase_order`) appended
      LAST regardless of where such steps sit in `state["steps"]`.
    - `report["failed"]` items carry `failure_reason`; `report["skipped"]`,
      `report["pending"]`, `report["blocked"]`, `report["in_progress"]` are
      lists of `{"id", "title"}`. All five are keyed by exactly those names.
    - `report["progress"]` is the plain `"N/M"` string (not the whole
      `summary()` dict).
    - md layout: a line-for-line contract is asserted only where the plan
      text pins one verbatim (`### 執行摘要`, `**Plan**：<title>`, `**進度**：N/M`,
      `#### <phase>`, the 5-column table header, `（此 phase 無 step）`,
      `（未分類）`, `#### 未完成與例外`, the three duration strings, and the
      em dash fallback). Everything else (exact summary-block label text,
      backtick-fencing algorithm for evidence containing backticks) is
      asserted only as an invariant (row count, escaping happened, output
      still round-trips through `parse_plan`), not as an exact string, so
      S2.2 keeps some rendering latitude.
    - `now` is accepted as a required kwarg but this file does not pin what
      it does to the output beyond "must not raise" — the plan's S2.1(c)
      ties duration purely to `started_at`/`completed_at`, so `now` reads as
      report metadata (e.g. a generated-at stamp), not a live-elapsed input.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import plan_runner as pr  # noqa: E402

PLAN_REPORT_PATH = SCRIPTS_DIR / "plan_report.py"


def _import_plan_report():
    """Lazy import so a missing/broken module errors just the calling test."""
    import plan_report  # noqa: PLC0415
    return plan_report


# ---------------------------------------------------------------------------
# Pure-function fixtures — hand-built state/step dicts, no disk I/O. Mirror
# the shape plan_runner.init_state()/transition_step() produce.
# ---------------------------------------------------------------------------

BASE_TIME = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _iso(offset_seconds: float = 0.0, base: datetime = BASE_TIME) -> str:
    return (base + timedelta(seconds=offset_seconds)).isoformat()


def _step(*, status="pending", phase="P1", title="Do thing", deps=None, **overrides) -> dict:
    step = {
        "id": None,
        "title": title,
        "phase": phase,
        "deps": deps or [],
        "agent": None,
        "skill": None,
        "command": None,
        "files": None,
        "action": "do the thing",
        "risk": None,
        "status": status,
        "task_id": None,
        "started_at": None,
        "completed_at": None,
        "failure_reason": None,
        "summary": None,
        "evidence": [],
    }
    step.update(overrides)
    return step


def _state(steps: dict, *, phase_order=None, title="Test Plan", slug="test-plan") -> dict:
    for sid, s in steps.items():
        if s.get("id") is None:
            s["id"] = sid
    return {
        "plan_path": "/irrelevant/plan.md",
        "slug": slug,
        "title": title,
        "phase_order": phase_order if phase_order is not None else ["P1"],
        "parent_task_id": None,
        "created_at": _iso(),
        "updated_at": _iso(),
        "steps": steps,
    }


def _phase_by_name(report: dict, name: str) -> dict:
    for p in report["phases"]:
        if p["name"] == name:
            return p
    raise AssertionError(f"phase {name!r} not found in {[p['name'] for p in report['phases']]}")


def _step_row(phase: dict, sid: str) -> dict:
    for s in phase["steps"]:
        if s["id"] == sid:
            return s
    raise AssertionError(f"step {sid!r} not found in phase {phase['name']!r}")


# ---------------------------------------------------------------------------
# (a) Grouping by phase_order, intra-phase order preserved from `state`.
# ---------------------------------------------------------------------------

class BuildReportGroupingTests(unittest.TestCase):
    def setUp(self):
        self.plan_report = _import_plan_report()

    def test_groups_by_phase_order_preserving_intragroup_state_order(self):
        # Deliberately inserted out of phase_order's declared order, and with
        # phase-1 steps interleaved with phase-2 steps in `steps` — grouping
        # must follow phase_order, and within a group must follow each
        # step's relative position in `state["steps"]`.
        steps = {
            "S3": _step(phase="Phase 2: Build", title="Third"),
            "S1": _step(phase="Phase 1: Setup", title="First"),
            "S2": _step(phase="Phase 1: Setup", title="Second"),
            "S4": _step(phase="Phase 2: Build", title="Fourth"),
        }
        state = _state(steps, phase_order=["Phase 1: Setup", "Phase 2: Build"])
        progress = pr.summary(state)
        report = self.plan_report.build_report(state, progress=progress, now=_iso())

        names = [p["name"] for p in report["phases"]]
        self.assertEqual(names, ["Phase 1: Setup", "Phase 2: Build"])

        phase1_ids = [s["id"] for s in _phase_by_name(report, "Phase 1: Setup")["steps"]]
        phase2_ids = [s["id"] for s in _phase_by_name(report, "Phase 2: Build")["steps"]]
        self.assertEqual(phase1_ids, ["S1", "S2"])
        self.assertEqual(phase2_ids, ["S3", "S4"])

    def test_build_report_does_not_mutate_input_state(self):
        steps = {"S1": _step(status="completed", started_at=_iso(), completed_at=_iso(45))}
        state = _state(steps)
        before = json.loads(json.dumps(state))
        self.plan_report.build_report(state, progress=pr.summary(state), now=_iso())
        self.assertEqual(state, before)


# ---------------------------------------------------------------------------
# (b) Empty phase (declared in phase_order, no steps) and unclassified
# steps (phase is "" or not in phase_order) grouped last as "（未分類）".
# ---------------------------------------------------------------------------

class EmptyAndUnclassifiedPhaseTests(unittest.TestCase):
    def setUp(self):
        self.plan_report = _import_plan_report()

    def test_phase_declared_but_empty_yields_empty_steps_list_and_md_placeholder(self):
        steps = {"S1": _step(phase="Phase 1: Setup")}
        state = _state(
            steps, phase_order=["Phase 1: Setup", "Phase 2: Empty"],
        )
        report = self.plan_report.build_report(state, progress=pr.summary(state), now=_iso())
        empty_phase = _phase_by_name(report, "Phase 2: Empty")
        self.assertEqual(empty_phase["steps"], [])

        md = self.plan_report.render_report_md(report, strip_unsafe=pr._strip_unsafe_bytes)
        self.assertIn("#### Phase 2: Empty", md)
        # The placeholder must appear after that phase's own heading, not
        # merely anywhere in the document.
        after_heading = md.split("#### Phase 2: Empty", 1)[1]
        self.assertIn("（此 phase 無 step）", after_heading.split("#### ", 1)[0])

    def test_blank_or_unknown_phase_grouped_last_as_unclassified(self):
        steps = {
            "S1": _step(phase="Phase 1: Setup"),
            "S2": _step(phase=""),
            "S3": _step(phase="Some Phase Never Declared"),
        }
        state = _state(steps, phase_order=["Phase 1: Setup"])
        report = self.plan_report.build_report(state, progress=pr.summary(state), now=_iso())

        names = [p["name"] for p in report["phases"]]
        self.assertEqual(names[-1], "（未分類）")
        self.assertEqual(names.index("（未分類）"), len(names) - 1)

        unclassified_ids = {s["id"] for s in _phase_by_name(report, "（未分類）")["steps"]}
        self.assertEqual(unclassified_ids, {"S2", "S3"})


# ---------------------------------------------------------------------------
# (c) Duration formatting: format_duration() directly, and end-to-end via
# build_report()/render_report_md().
# ---------------------------------------------------------------------------

class DurationFormattingTests(unittest.TestCase):
    def setUp(self):
        self.plan_report = _import_plan_report()

    def test_format_duration_boundary_strings(self):
        fd = self.plan_report.format_duration
        self.assertEqual(fd(45), "45s")
        self.assertEqual(fd(169), "2m49s")  # 2*60 + 49
        self.assertEqual(fd(3722), "1h02m")  # 1*3600 + 2*60 + 2 (seconds dropped)

    def test_format_duration_missing_or_unparseable_or_negative_is_em_dash(self):
        fd = self.plan_report.format_duration
        self.assertEqual(fd(None), "—")
        self.assertEqual(fd(-1), "—")
        self.assertEqual(fd(-0.001), "—")

    def test_build_report_computes_duration_seconds_from_started_and_completed_at(self):
        steps = {
            "S1": _step(
                status="completed", started_at=_iso(0), completed_at=_iso(45),
            ),
            "S2": _step(
                status="completed", started_at=_iso(0), completed_at=_iso(169),
            ),
            "S3": _step(
                status="completed", started_at=_iso(0), completed_at=_iso(3722),
            ),
        }
        state = _state(steps)
        report = self.plan_report.build_report(state, progress=pr.summary(state), now=_iso())
        phase = _phase_by_name(report, "P1")
        self.assertEqual(_step_row(phase, "S1")["duration_seconds"], 45)
        self.assertEqual(_step_row(phase, "S2")["duration_seconds"], 169)
        self.assertEqual(_step_row(phase, "S3")["duration_seconds"], 3722)

        md = self.plan_report.render_report_md(report, strip_unsafe=pr._strip_unsafe_bytes)
        self.assertIn("45s", md)
        self.assertIn("2m49s", md)
        self.assertIn("1h02m", md)

    def test_missing_endpoint_unparseable_or_negative_duration_is_none_and_em_dash(self):
        steps = {
            "S1": _step(status="in_progress", started_at=_iso(0), completed_at=None),
            "S2": _step(status="pending", started_at=None, completed_at=None),
            "S3": _step(
                status="completed", started_at="not-a-timestamp", completed_at=_iso(10),
            ),
            "S4": _step(
                # completed before it started: nonsensical, must not go negative
                status="completed", started_at=_iso(10), completed_at=_iso(0),
            ),
        }
        state = _state(steps)
        report = self.plan_report.build_report(state, progress=pr.summary(state), now=_iso())
        phase = _phase_by_name(report, "P1")
        for sid in ("S1", "S2", "S3", "S4"):
            with self.subTest(sid=sid):
                self.assertIsNone(_step_row(phase, sid)["duration_seconds"])

        json_text = self.plan_report.render_report_json(report)
        payload = json.loads(json_text)
        payload_phase = next(p for p in payload["phases"] if p["name"] == "P1")
        for row in payload_phase["steps"]:
            self.assertIsNone(row["duration_seconds"])

        md = self.plan_report.render_report_md(report, strip_unsafe=pr._strip_unsafe_bytes)
        # At least the 4 missing/invalid rows render the fallback dash.
        self.assertGreaterEqual(md.count("—"), 4)


# ---------------------------------------------------------------------------
# (d) Trailing lists (failed/skipped/pending/blocked/in_progress) and
# progress consistency with plan_runner.summary().
# ---------------------------------------------------------------------------

class TrailingListsAndProgressTests(unittest.TestCase):
    def setUp(self):
        self.plan_report = _import_plan_report()

    def _six_status_state(self) -> dict:
        steps = {
            "S1": _step(status="completed", started_at=_iso(0), completed_at=_iso(5)),
            "S2": _step(status="in_progress", started_at=_iso(0)),
            "S3": _step(status="failed", failure_reason="boom: exit 1"),
            "S4": _step(status="blocked", deps=["S3"]),
            "S5": _step(status="pending"),
            "S6": _step(status="skipped"),
        }
        return _state(steps)

    def test_progress_matches_plan_runner_summary(self):
        state = self._six_status_state()
        progress = pr.summary(state)
        report = self.plan_report.build_report(state, progress=progress, now=_iso())
        self.assertEqual(report["progress"], progress["progress"])
        self.assertEqual(progress["progress"], "2/6")  # completed(1) + skipped(1) of 6

    def test_trailing_lists_cover_every_non_completed_status(self):
        state = self._six_status_state()
        report = self.plan_report.build_report(state, progress=pr.summary(state), now=_iso())

        self.assertEqual([f["id"] for f in report["failed"]], ["S3"])
        self.assertEqual(report["failed"][0]["failure_reason"], "boom: exit 1")
        self.assertEqual([s["id"] for s in report["skipped"]], ["S6"])
        self.assertEqual([s["id"] for s in report["pending"]], ["S5"])
        self.assertEqual([s["id"] for s in report["blocked"]], ["S4"])
        self.assertEqual([s["id"] for s in report["in_progress"]], ["S2"])

    def test_md_trailing_section_lists_each_category(self):
        state = self._six_status_state()
        report = self.plan_report.build_report(state, progress=pr.summary(state), now=_iso())
        md = self.plan_report.render_report_md(report, strip_unsafe=pr._strip_unsafe_bytes)
        self.assertIn("#### 未完成與例外", md)
        tail = md.split("#### 未完成與例外", 1)[1]
        self.assertIn("S3", tail)
        self.assertIn("boom: exit 1", tail)
        self.assertIn("S4", tail)
        self.assertIn("S5", tail)
        self.assertIn("S6", tail)
        self.assertIn("S2", tail)
        self.assertIn("**進度**：2/6", md)


# ---------------------------------------------------------------------------
# (e) `report --format json` stdout is json.loads-able and carries
# schema_version: 1. CLI-level: real plan on disk, in-process cmd_report.
# ---------------------------------------------------------------------------

PLAN_TEXT = """# Report CLI Test Plan

### Phase 1: Setup

- [ ] S1 First step
  - Files: `a.py`
  - Action: do A

- [ ] S2 Second step
  - Dependencies: S1
  - Files: `b.py`
  - Action: do B

### Phase 2: Build

- [ ] S3 Third step
  - Dependencies: S1
  - Files: `c.py`
  - Action: do C

- [ ] S4 Fourth step
  - Dependencies: S3
  - Files: `d.py`
  - Action: do D

- [ ] S5 Fifth step
  - Files: `e.py`
  - Action: do E

- [ ] S6 Sixth step
  - Files: `f.py`
  - Action: do F
"""


def _capture(func, args: argparse.Namespace) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = func(args)
    return rc, out.getvalue()


def _init_args(plan_path: Path, *, fmt="json") -> argparse.Namespace:
    return argparse.Namespace(plan=str(plan_path), force=False, attach=False, format=fmt)


def _start_args(plan_path: Path, step: str) -> argparse.Namespace:
    return argparse.Namespace(plan=str(plan_path), step=step, task_id=None, session_id=None, format="json")


def _complete_args(plan_path: Path, step: str, *, summary=None, evidence=None) -> argparse.Namespace:
    return argparse.Namespace(
        plan=str(plan_path), step=step, format="json", summary=summary, evidence=evidence,
    )


def _fail_args(plan_path: Path, step: str, *, reason="") -> argparse.Namespace:
    return argparse.Namespace(plan=str(plan_path), step=step, reason=reason, format="json")


def _skip_args(plan_path: Path, step: str) -> argparse.Namespace:
    return argparse.Namespace(plan=str(plan_path), step=step, format="json")


def _report_args(plan_path: Path, *, fmt="json", output=None, force=False) -> argparse.Namespace:
    return argparse.Namespace(plan=str(plan_path), format=fmt, output=output, force=force)


def _new_populated_plan(test_case: unittest.TestCase) -> Path:
    """A realistic mixed-status plan: matches _six_status_state()'s shape
    (1 completed, 1 in_progress, 1 failed, 1 blocked-by-failure, 1 pending,
    1 skipped) but driven through the real CLI so state.json is authentic.
    """
    tmp = tempfile.TemporaryDirectory(dir=Path.home(), prefix=".plan-report-test-")
    test_case.addCleanup(tmp.cleanup)
    plan_path = Path(tmp.name).resolve() / "plan.md"
    plan_path.write_text(PLAN_TEXT, encoding="utf-8")
    rc, out = _capture(pr.cmd_init, _init_args(plan_path))
    assert rc == 0, out

    rc, out = _capture(pr.cmd_start, _start_args(plan_path, "S1"))
    assert rc == 0, out
    rc, out = _capture(
        pr.cmd_complete,
        _complete_args(plan_path, "S1", summary="did S1", evidence=["s1-log.txt"]),
    )
    assert rc == 0, out

    rc, out = _capture(pr.cmd_start, _start_args(plan_path, "S2"))
    assert rc == 0, out  # left in_progress

    rc, out = _capture(pr.cmd_start, _start_args(plan_path, "S3"))
    assert rc == 0, out
    rc, out = _capture(pr.cmd_fail, _fail_args(plan_path, "S3", reason="boom: exit 1"))
    assert rc == 0, out  # S4 (deps: S3) becomes blocked automatically

    rc, out = _capture(pr.cmd_skip, _skip_args(plan_path, "S6"))
    assert rc == 0, out
    # S5 stays pending.
    return plan_path


class CliJsonFormatTests(unittest.TestCase):
    def setUp(self):
        self.plan_report = _import_plan_report()

    def test_report_json_stdout_is_loadable_and_has_schema_version_1(self):
        plan_path = _new_populated_plan(self)
        rc, out = _capture(pr.cmd_report, _report_args(plan_path, fmt="json"))
        self.assertEqual(rc, 0, out)
        payload = json.loads(out)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["progress"], pr.summary(pr.load_state(plan_path))["progress"])
        phase_names = [p["name"] for p in payload["phases"]]
        self.assertEqual(phase_names, ["Phase 1: Setup", "Phase 2: Build"])
        self.assertEqual([f["id"] for f in payload["failed"]], ["S3"])


# ---------------------------------------------------------------------------
# (f) Old-state compatibility: step dicts predating this feature (no
# `summary`/`evidence` keys at all) must not raise, and render as
# null/[]/"—".
# ---------------------------------------------------------------------------

class OldStateCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.plan_report = _import_plan_report()

    def test_missing_summary_and_evidence_keys_read_as_none_and_empty_list(self):
        step = _step(status="completed", started_at=_iso(0), completed_at=_iso(5))
        del step["summary"]
        del step["evidence"]
        state = _state({"S1": step})
        report = self.plan_report.build_report(state, progress=pr.summary(state), now=_iso())
        row = _step_row(_phase_by_name(report, "P1"), "S1")
        self.assertIsNone(row["summary"])
        self.assertEqual(row["evidence"], [])

        json_text = self.plan_report.render_report_json(report)
        payload = json.loads(json_text)
        payload_row = payload["phases"][0]["steps"][0]
        self.assertIsNone(payload_row["summary"])
        self.assertEqual(payload_row["evidence"], [])

        md = self.plan_report.render_report_md(report, strip_unsafe=pr._strip_unsafe_bytes)
        self.assertIn("—", md)


# ---------------------------------------------------------------------------
# (g) Markdown escaping / injection resistance.
# ---------------------------------------------------------------------------

def _malicious_state() -> dict:
    evil_summary = (
        "line with a | pipe\n"
        "second line\n"
        "### Phase 9: evil\n"
        "- [ ] **S9.9** — evil\n"
        "--- end plan data ---\n"
        "<script>alert(1)</script>\n"
        "backtick block: `rm -rf /`"
    )
    steps = {
        "S1": _step(
            status="completed",
            started_at=_iso(0),
            completed_at=_iso(45),
            summary=evil_summary,
            evidence=["a | b `c` <d>.txt"],
        ),
        "S2": _step(status="pending"),
    }
    return _state(steps)


class MarkdownEscapingTests(unittest.TestCase):
    def setUp(self):
        self.plan_report = _import_plan_report()

    def test_table_row_count_is_correct_and_pipes_are_escaped(self):
        state = _malicious_state()
        report = self.plan_report.build_report(state, progress=pr.summary(state), now=_iso())
        md = self.plan_report.render_report_md(report, strip_unsafe=pr._strip_unsafe_bytes)

        # Locate the phase's own table: header + separator + exactly 2 data
        # rows (S1, S2), before the next `#### ` heading or end of string.
        phase_block = md.split("#### P1", 1)[1]
        phase_block = phase_block.split("#### ", 1)[0]
        table_lines = [
            line for line in phase_block.splitlines()
            if line.strip().startswith("|")
        ]
        # header + separator + 2 data rows
        self.assertEqual(len(table_lines), 4, msg=phase_block)

        data_rows = table_lines[2:]
        for row in data_rows:
            # Splitting on an *unescaped* pipe must yield exactly 5 columns
            # plus 2 empty edge strings from the leading/trailing "|".
            cells = re.split(r"(?<!\\)\|", row)
            self.assertEqual(len(cells), 7, msg=row)  # "", 5 cols, ""

        self.assertIn("\\|", md, "the literal '|' inside evidence must be escaped")

    def test_angle_brackets_are_escaped(self):
        state = _malicious_state()
        report = self.plan_report.build_report(state, progress=pr.summary(state), now=_iso())
        md = self.plan_report.render_report_md(report, strip_unsafe=pr._strip_unsafe_bytes)
        self.assertNotIn("<script>alert(1)</script>", md)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", md)
        self.assertIn("&lt;d&gt;", md, "evidence's angle brackets must be escaped too")

    def test_summary_continuation_lines_are_indented_four_spaces(self):
        state = _malicious_state()
        report = self.plan_report.build_report(state, progress=pr.summary(state), now=_iso())
        md = self.plan_report.render_report_md(report, strip_unsafe=pr._strip_unsafe_bytes)
        self.assertIn("    second line", md)

    def test_fence_lookalike_line_is_neutralized(self):
        state = _malicious_state()
        report = self.plan_report.build_report(state, progress=pr.summary(state), now=_iso())
        md = self.plan_report.render_report_md(report, strip_unsafe=pr._strip_unsafe_bytes)
        fence_norms = {pr.PLAN_FENCE_START.lower(), pr.PLAN_FENCE_END.lower()}
        for line in md.split("\n"):
            self.assertNotIn(
                line.strip().lower(), fence_norms,
                msg=f"line reads as a real plan-data fence delimiter: {line!r}",
            )


# ---------------------------------------------------------------------------
# (h) Report embedded after a real plan must be inert to parse_plan(): the
# same steps/phase_order/action come out with or without the report.
# ---------------------------------------------------------------------------

class ParserInertTests(unittest.TestCase):
    def setUp(self):
        self.plan_report = _import_plan_report()

    def test_report_appended_under_execution_summary_heading_does_not_change_parse_plan(self):
        state = _malicious_state()  # reuse the injection fixture
        report = self.plan_report.build_report(state, progress=pr.summary(state), now=_iso())
        md = self.plan_report.render_report_md(report, strip_unsafe=pr._strip_unsafe_bytes)

        tmp = tempfile.TemporaryDirectory(dir=Path.home(), prefix=".plan-report-inert-")
        self.addCleanup(tmp.cleanup)
        plan_dir = Path(tmp.name).resolve()

        plain_path = plan_dir / "plain.md"
        plain_path.write_text(PLAN_TEXT, encoding="utf-8")
        parsed_plain = pr.parse_plan(plain_path)

        combined_path = plan_dir / "combined.md"
        combined_text = PLAN_TEXT + "\n## 執行摘要\n\n" + md + "\n"
        combined_path.write_text(combined_text, encoding="utf-8")
        parsed_combined = pr.parse_plan(combined_path)

        self.assertEqual(set(parsed_combined["steps"].keys()), set(parsed_plain["steps"].keys()))
        self.assertEqual(parsed_combined["phase_order"], parsed_plain["phase_order"])
        for sid in parsed_plain["steps"]:
            self.assertEqual(
                parsed_combined["steps"][sid]["action"], parsed_plain["steps"][sid]["action"],
                msg=f"step {sid} action changed after report was appended",
            )


class ReportTitleAndEntityTests(unittest.TestCase):
    """S2.2 review follow-up: fixed heading, `&` escaping, fence-rule parity."""

    def setUp(self):
        self.plan_report = _import_plan_report()

    def _md(self, state: dict) -> str:
        report = self.plan_report.build_report(state, progress=pr.summary(state), now=_iso())
        return self.plan_report.render_report_md(report, strip_unsafe=pr._strip_unsafe_bytes)

    def test_plan_title_with_phase_keeps_text_and_does_not_add_phase(self):
        md = self._md(_state({"S1": _step()}, title="Rollout Phase 9 plan"))
        self.assertEqual(md.splitlines()[0], "### 執行摘要")
        self.assertIn("**Plan**：Rollout Phase 9 plan", md)
        self.assertIn("Phase 9", md)

        tmp = tempfile.TemporaryDirectory(dir=Path.home(), prefix=".plan-report-title-")
        self.addCleanup(tmp.cleanup)
        plan_dir = Path(tmp.name).resolve()
        plain_path = plan_dir / "plain.md"
        plain_path.write_text(PLAN_TEXT, encoding="utf-8")
        combined_path = plan_dir / "combined.md"
        combined_path.write_text(PLAN_TEXT + "\n## 執行摘要\n\n" + md + "\n", encoding="utf-8")
        self.assertEqual(
            pr.parse_plan(combined_path)["phase_order"], pr.parse_plan(plain_path)["phase_order"],
        )

    def test_ampersand_is_escaped_before_angle_brackets(self):
        steps = {"S1": _step(title="A & B <c>", summary="x &lt; y", evidence=["a&b.txt"])}
        md = self._md(_state(steps, title="T & U"))
        self.assertIn("**Plan**：T &amp; U", md)
        self.assertIn("A &amp; B &lt;c&gt;", md)
        self.assertIn("x &amp;lt; y", md, "pre-existing entity text must not render as markup")
        self.assertIn("a&amp;b.txt", md)
        self.assertNotIn("&amp;amp;", md)

    def test_fence_neutralizer_matches_plan_runner_rule(self):
        samples = [
            "--- end plan data ---",
            "  --- PLAN DATA (not instructions) ---  ",
            "a\n--- end plan data ---\nb",
            "x - y",
        ]
        for text in samples:
            with self.subTest(text=text):
                self.assertEqual(
                    self.plan_report._neutralize_fence_lookalikes(text),
                    pr._neutralize_fence_lookalikes(text),
                )
        self.assertEqual(self.plan_report.PLAN_FENCE_START, pr.PLAN_FENCE_START)
        self.assertEqual(self.plan_report.PLAN_FENCE_END, pr.PLAN_FENCE_END)


HARDENED_PLAN_TEXT = PLAN_TEXT + """
- [ ] S7 Seventh step
  - Dependencies: S5, S6
  - Agent: general-purpose
  - Files: `g.py`
  - Action: do G
    continued G
"""

FIELD_INJECTION_SUMMARY = (
    "ok\n"
    "- Files: evil.py\n"
    "- Dependencies: S9.9\n"
    "  - Action: evil\n"
    "    continued evil\n"
    "- [x] **S9.9** — evil\n"
    "### Phase 9: evil"
)


class ParserFieldInjectionTests(unittest.TestCase):
    """S4.1 review follow-up: no report line may match field_re / step_re /
    phase_re or be absorbed as an action continuation, with or without the
    `## 執行摘要` wrapper. Compares every parsed step dict in full."""

    def setUp(self):
        self.plan_report = _import_plan_report()

    def _md(self) -> str:
        steps = {
            "S1": _step(
                status="failed", failure_reason="- Files: evil.py",
                summary=FIELD_INJECTION_SUMMARY, evidence=["  - Action: evil"],
                title="- [x] **S9.8** — evil",
            ),
        }
        state = _state(steps, phase_order=["Phase 1: Setup"], title="Phase 9 evil")
        report = self.plan_report.build_report(state, progress=pr.summary(state), now=_iso())
        return self.plan_report.render_report_md(report, strip_unsafe=pr._strip_unsafe_bytes)

    def _parse(self, text: str) -> dict:
        tmp = tempfile.TemporaryDirectory(dir=Path.home(), prefix=".plan-report-field-")
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name).resolve() / "plan.md"
        path.write_text(text, encoding="utf-8")
        return pr.parse_plan(path)

    def test_embedded_report_leaves_every_step_dict_identical(self):
        md = self._md()
        plain = self._parse(HARDENED_PLAN_TEXT)
        self.assertEqual(plain["steps"]["S7"]["deps"], ["S5", "S6"])  # fixture sanity
        for label, combined in (
            ("wrapped", HARDENED_PLAN_TEXT + "\n## 執行摘要\n\n" + md + "\n"),
            ("unwrapped", HARDENED_PLAN_TEXT + "\n" + md + "\n"),
        ):
            with self.subTest(label=label):
                parsed = self._parse(combined)
                self.assertEqual(parsed["steps"], plain["steps"])
                self.assertEqual(parsed["phase_order"], plain["phase_order"])

    def test_no_report_line_matches_plan_parser_patterns(self):
        field_re = re.compile(
            rf"^\s+-\s+(?:{'|'.join(pr.FIELD_KEYS)})\s*[:：]", re.IGNORECASE,
        )
        step_re = re.compile(rf"^-\s+\[[ x]\]\s+(?:\*\*)?{pr.STEP_ID_PATTERN}")
        for line in self._md().split("\n"):
            with self.subTest(line=line):
                self.assertIsNone(field_re.match(line))
                self.assertIsNone(step_re.match(line))
                self.assertFalse(line.startswith(" "), "would read as action continuation")
                if re.match(r"^###\s+", line):
                    self.assertNotIn("hase", line)


# ---------------------------------------------------------------------------
# (i) `--output`: content matches stdout; overwrite protection; plan/state
# path refusal (including through a symlink); missing parent dir.
# ---------------------------------------------------------------------------

class OutputFlagTests(unittest.TestCase):
    def setUp(self):
        self.plan_report = _import_plan_report()

    def test_output_file_content_matches_stdout(self):
        plan_path = _new_populated_plan(self)
        rc_stdout, stdout_text = _capture(pr.cmd_report, _report_args(plan_path, fmt="md"))
        self.assertEqual(rc_stdout, 0, stdout_text)

        out_path = plan_path.parent / "report-out.md"
        rc_file, _ = _capture(
            pr.cmd_report, _report_args(plan_path, fmt="md", output=str(out_path)),
        )
        self.assertEqual(rc_file, 0)
        self.assertEqual(out_path.read_text(encoding="utf-8"), stdout_text)

    def test_existing_output_without_force_is_rejected_and_left_untouched(self):
        plan_path = _new_populated_plan(self)
        out_path = plan_path.parent / "existing.md"
        out_path.write_text("PRE-EXISTING CONTENT", encoding="utf-8")

        rc, out = _capture(
            pr.cmd_report, _report_args(plan_path, fmt="md", output=str(out_path)),
        )
        self.assertEqual(rc, 1, out)
        self.assertEqual(out_path.read_text(encoding="utf-8"), "PRE-EXISTING CONTENT")

    def test_existing_output_with_force_is_overwritten(self):
        plan_path = _new_populated_plan(self)
        out_path = plan_path.parent / "existing.md"
        out_path.write_text("PRE-EXISTING CONTENT", encoding="utf-8")

        rc, out = _capture(
            pr.cmd_report,
            _report_args(plan_path, fmt="md", output=str(out_path), force=True),
        )
        self.assertEqual(rc, 0, out)
        self.assertNotEqual(out_path.read_text(encoding="utf-8"), "PRE-EXISTING CONTENT")

    def test_output_equal_to_plan_path_is_rejected_even_with_force(self):
        plan_path = _new_populated_plan(self)
        before = plan_path.read_text(encoding="utf-8")
        rc, out = _capture(
            pr.cmd_report,
            _report_args(plan_path, fmt="md", output=str(plan_path), force=True),
        )
        self.assertEqual(rc, 1, out)
        self.assertEqual(plan_path.read_text(encoding="utf-8"), before)

    def test_output_equal_to_state_path_is_rejected_even_with_force(self):
        plan_path = _new_populated_plan(self)
        state_path = pr.state_path_for(plan_path)
        before = state_path.read_bytes()
        rc, out = _capture(
            pr.cmd_report,
            _report_args(plan_path, fmt="json", output=str(state_path), force=True),
        )
        self.assertEqual(rc, 1, out)
        self.assertEqual(state_path.read_bytes(), before)

    def test_output_via_symlink_to_state_path_is_rejected(self):
        plan_path = _new_populated_plan(self)
        state_path = pr.state_path_for(plan_path)
        before = state_path.read_bytes()
        symlink_path = plan_path.parent / "state-link.json"
        try:
            symlink_path.symlink_to(state_path)
        except OSError as e:  # pragma: no cover - platform without symlink perms
            self.skipTest(f"cannot create symlink in this environment: {e}")
        rc, out = _capture(
            pr.cmd_report,
            _report_args(plan_path, fmt="json", output=str(symlink_path), force=True),
        )
        self.assertEqual(rc, 1, out)
        self.assertEqual(state_path.read_bytes(), before)

    def test_output_equal_to_state_lock_path_is_rejected_even_with_force(self):
        plan_path = _new_populated_plan(self)
        lock_path = pr.state_lock_path_for(plan_path)
        existed = lock_path.exists()
        before = lock_path.read_bytes() if existed else None
        rc, out = _capture(
            pr.cmd_report,
            _report_args(plan_path, fmt="md", output=str(lock_path), force=True),
        )
        self.assertEqual(rc, 1, out)
        self.assertEqual(lock_path.exists(), existed)
        if existed:
            self.assertEqual(lock_path.read_bytes(), before)

    def test_missing_parent_directory_is_rejected_and_not_created(self):
        plan_path = _new_populated_plan(self)
        out_path = plan_path.parent / "no-such-dir" / "report.md"
        rc, out = _capture(
            pr.cmd_report, _report_args(plan_path, fmt="md", output=str(out_path)),
        )
        self.assertEqual(rc, 1, out)
        self.assertFalse(out_path.parent.exists())


# ---------------------------------------------------------------------------
# (i.1) Error paths: plan_report import failure, --output pointing at a
# directory, and an atomic-write failure (os.replace raising) must all be
# reported as rc=1 with a JSON `error` key, and the atomic-write failure must
# not leave a stray `.tmp` file behind.
# ---------------------------------------------------------------------------

class ReportErrorPathTests(unittest.TestCase):
    def setUp(self):
        self.plan_report = _import_plan_report()

    def test_plan_report_import_error_is_reported_and_returns_1(self):
        plan_path = _new_populated_plan(self)
        # sys.modules[name] = None is the standard way to force the next
        # bare `import <name>` statement to raise ImportError, regardless of
        # whether the module was already imported earlier in this process.
        with mock.patch.dict(sys.modules, {"plan_report": None}):
            rc, out = _capture(pr.cmd_report, _report_args(plan_path, fmt="json"))
        self.assertEqual(rc, 1, out)
        payload = json.loads(out)
        self.assertIn("report module unavailable", payload["error"])

    def test_output_path_that_is_a_directory_is_rejected(self):
        plan_path = _new_populated_plan(self)
        out_dir = plan_path.parent / "a-directory"
        out_dir.mkdir()
        rc, out = _capture(
            pr.cmd_report, _report_args(plan_path, fmt="md", output=str(out_dir)),
        )
        self.assertEqual(rc, 1, out)
        payload = json.loads(out)
        self.assertIn("--output is a directory", payload["error"])

    def test_atomic_write_failure_is_cleaned_up_and_reported(self):
        plan_path = _new_populated_plan(self)
        out_path = plan_path.parent / "atomic-fail.md"
        with mock.patch("plan_runner.os.replace", side_effect=OSError("disk full")):
            rc, out = _capture(
                pr.cmd_report, _report_args(plan_path, fmt="md", output=str(out_path)),
            )
        self.assertEqual(rc, 1, out)
        payload = json.loads(out)
        self.assertIn("failed to write --output", payload["error"])
        self.assertFalse(out_path.exists())
        leftover_tmp_files = list(plan_path.parent.glob(f".{out_path.name}.*.tmp"))
        self.assertEqual(leftover_tmp_files, [], "temp file must be unlinked on os.replace failure")


# ---------------------------------------------------------------------------
# (j) `report` never calls save_state: state bytes and mtime are unchanged.
# ---------------------------------------------------------------------------

class ReportDoesNotWriteStateTests(unittest.TestCase):
    def setUp(self):
        self.plan_report = _import_plan_report()

    def test_state_bytes_and_mtime_unchanged_after_report(self):
        plan_path = _new_populated_plan(self)
        state_path = pr.state_path_for(plan_path)
        before_bytes = state_path.read_bytes()
        before_mtime = state_path.stat().st_mtime_ns

        for fmt in ("md", "json"):
            with self.subTest(fmt=fmt):
                rc, out = _capture(pr.cmd_report, _report_args(plan_path, fmt=fmt))
                self.assertEqual(rc, 0, out)

        out_path = plan_path.parent / "side-output.md"
        rc, out = _capture(
            pr.cmd_report, _report_args(plan_path, fmt="md", output=str(out_path)),
        )
        self.assertEqual(rc, 0, out)

        self.assertEqual(state_path.read_bytes(), before_bytes)
        self.assertEqual(state_path.stat().st_mtime_ns, before_mtime)


# ---------------------------------------------------------------------------
# (k) Drift guard: plan_report's status constants must equal plan_runner's.
# ---------------------------------------------------------------------------

class StatusConstantDriftGuardTests(unittest.TestCase):
    def test_status_constants_match_plan_runner(self):
        plan_report = _import_plan_report()
        for name in ("PENDING", "IN_PROGRESS", "COMPLETED", "FAILED", "BLOCKED", "SKIPPED"):
            with self.subTest(name=name):
                self.assertTrue(hasattr(plan_report, name), f"plan_report.{name} is missing")
                self.assertEqual(
                    getattr(plan_report, name), getattr(pr, name),
                    f"plan_report.{name} has drifted from plan_runner.{name}",
                )


# ---------------------------------------------------------------------------
# (l) hook-stop must never load plan_report — a positive precondition
# (plan_report.py exists) is asserted FIRST so this test is a true red
# before S2.2: without it, "module not in sys.modules" would trivially pass
# today simply because the module doesn't exist yet.
# ---------------------------------------------------------------------------

class HookStopDoesNotLoadPlanReportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home_dir = Path(self._tmp.name) / "home"
        (self.home_dir / ".claude").mkdir(parents=True, exist_ok=True)

    def test_plan_report_module_absent_from_sys_modules_after_hook_stop(self):
        self.assertTrue(
            PLAN_REPORT_PATH.exists(),
            "scripts/plan_report.py must exist (S2.2) for this check to be meaningful — "
            "otherwise 'not in sys.modules' would trivially pass today.",
        )

        wrapper = (
            "import runpy, sys, json\n"
            f"sys.argv = [{str(SCRIPTS_DIR / 'plan_runner.py')!r}, 'hook-stop']\n"
            "try:\n"
            f"    runpy.run_path({str(SCRIPTS_DIR / 'plan_runner.py')!r}, run_name='__main__')\n"
            "except SystemExit:\n"
            "    pass\n"
            "print('PLAN_REPORT_LOADED=' + str('plan_report' in sys.modules))\n"
        )
        env = {**os.environ, "HOME": str(self.home_dir)}
        result = subprocess.run(
            [sys.executable, "-c", wrapper],
            input=json.dumps({"hook_event_name": "Stop", "cwd": str(self.home_dir)}),
            capture_output=True, text=True, timeout=30, env=env,
        )
        self.assertIn(
            "PLAN_REPORT_LOADED=False", result.stdout,
            msg=f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )


if __name__ == "__main__":
    unittest.main()
