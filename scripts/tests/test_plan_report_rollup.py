"""Tests for the report rollup section (四段式 summary 彙整段).

Covers `plan_report.parse_summary_parts`, `plan_report.is_none_like`, the
`rollup` block `build_report` adds, and how both renderers print it:
    - parser: `1.`/`2.`/`3.`/`4.` markers (half-width period, preceded by
      start-of-text or whitespace, not followed by a digit), full-width
      `１．` and `1、` variants, missing trailing segments, unstructured text;
    - rollup: deviations / side_effects / deferred / unclassified in phase
      order, agent counts, total vs wall-clock duration, status counts;
    - md: header numbers, three lists with step counts, `（無）` when empty,
      escaping, parser-inertness;
    - json: `schema_version` 2, `rollup`, `steps[].summary_parts`.

Run: python3 -m unittest scripts.tests.test_plan_report_rollup -v
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import plan_runner as pr  # noqa: E402

BASE_TIME = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
FOUR_PART = "1.did A 2.dev A 3.side A 4.todo A"
PLAN_TEXT = """# Rollup Test Plan

### Phase 1: Setup

- [ ] S1 First step
  - Files: `a.py`
  - Action: do A
"""


def _import_plan_report():
    import plan_report  # noqa: PLC0415
    return plan_report


def _iso(offset_seconds: float = 0.0) -> str:
    return (BASE_TIME + timedelta(seconds=offset_seconds)).isoformat()


def _step(*, status="completed", phase="P1", agent=None, summary=None, **overrides) -> dict:
    step = {
        "id": None, "title": "t", "phase": phase, "deps": [], "agent": agent,
        "status": status, "started_at": None, "completed_at": None,
        "failure_reason": None, "summary": summary, "evidence": [],
    }
    step.update(overrides)
    return step


def _state(steps: dict, *, phase_order=None) -> dict:
    for sid, s in steps.items():
        if s.get("id") is None:
            s["id"] = sid
    return {
        "slug": "rollup", "title": "Rollup Plan",
        "phase_order": phase_order if phase_order is not None else ["P1"],
        "steps": steps,
    }


def _report(state: dict) -> dict:
    return _import_plan_report().build_report(state, progress=pr.summary(state), now=_iso())


def _md(state: dict) -> str:
    return _import_plan_report().render_report_md(_report(state), strip_unsafe=pr._strip_unsafe_bytes)


def _section(md: str, heading: str) -> list[str]:
    """Lines after `heading` up to the next bold list heading or `####`."""
    lines = md.split("\n")
    start = lines.index(heading) + 1
    out = []
    for line in lines[start:]:
        if line.startswith("####") or (line.startswith("**") and line.endswith("**")):
            break
        if line:
            out.append(line)
    return out


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

class ParseSummaryPartsTests(unittest.TestCase):
    def setUp(self):
        self.parse = _import_plan_report().parse_summary_parts

    def test_half_width_four_part(self):
        self.assertEqual(
            self.parse(FOUR_PART),
            {"did": "did A", "deviation": "dev A", "side_effects": "side A", "deferred": "todo A"},
        )

    def test_full_width_and_dunhao_markers(self):
        expected = {"did": "a", "deviation": "b", "side_effects": "c", "deferred": "d"}
        self.assertEqual(self.parse("１．a ２．b ３．c ４．d"), expected)
        self.assertEqual(self.parse("1、a 2、b 3、c 4、d"), expected)

    def test_markers_inside_ids_decimals_and_ranges_are_not_split(self):
        text = "1.改 S1.10 與 L142-179 2.無偏離；多補 1.5 倍 3.無 4.N1-N5 留 follow-up"
        self.assertEqual(self.parse(text), {
            "did": "改 S1.10 與 L142-179",
            "deviation": "無偏離；多補 1.5 倍",
            "side_effects": "無",
            "deferred": "N1-N5 留 follow-up",
        })

    def test_enumeration_dunhao_inside_period_style_summary_is_not_a_marker(self):
        # Real S1.10 summary shape: "、" enumerations with small numbers
        # ("bridge 2、") must not open segment 2 when marker 1 used a period.
        text = "1.B 9 案（隱藏 6、bridge 2、變體 3）；全綠 2.inner=t 3.等 1 秒 4.只跑 chromium"
        self.assertEqual(self.parse(text), {
            "did": "B 9 案（隱藏 6、bridge 2、變體 3）；全綠",
            "deviation": "inner=t",
            "side_effects": "等 1 秒",
            "deferred": "只跑 chromium",
        })

    def test_period_markers_do_not_close_dunhao_style_segments(self):
        self.assertEqual(
            self.parse("1、a 2、見 v 2.0 版 3、c 4、d"),
            {"did": "a", "deviation": "見 v 2.0 版", "side_effects": "c", "deferred": "d"},
        )

    def test_newline_separated_segments(self):
        parts = self.parse("1.a\n2.b\n3.c\n4.d")
        self.assertEqual(parts["deferred"], "d")
        self.assertEqual(parts["deviation"], "b")

    def test_missing_trailing_segment_is_none(self):
        self.assertEqual(
            self.parse("1.a 2.b 3.c"),
            {"did": "a", "deviation": "b", "side_effects": "c", "deferred": None},
        )

    def test_unstructured_summary_keeps_raw(self):
        for text in ("did S1", "1.only one segment", "前言 1.a 2.b 3.c 4.d"):
            with self.subTest(text=text):
                self.assertEqual(self.parse(text), {
                    "did": None, "deviation": None, "side_effects": None,
                    "deferred": None, "raw": text,
                })

    def test_missing_summary_is_none(self):
        self.assertIsNone(self.parse(None))
        self.assertIsNone(self.parse(""))


class NoneLikeTests(unittest.TestCase):
    def setUp(self):
        self.plan_report = _import_plan_report()

    def test_constant_is_tuple_with_every_spec_value(self):
        values = self.plan_report.NONE_LIKE_SEGMENTS
        self.assertIsInstance(values, tuple)
        for v in ("", "無", "無偏離", "無偏離 plan", "無新增", "無新增項目", "—", "-", "N/A", "無新教訓"):
            self.assertIn(v, values)

    def test_none_like_values_after_strip(self):
        for v in self.plan_report.NONE_LIKE_SEGMENTS + ("  無  ", "無；", None):
            with self.subTest(v=v):
                self.assertTrue(self.plan_report.is_none_like(v))

    def test_text_with_content_is_listed(self):
        for v in ("無實質偏離，只是順序調整", "無偏離；提案未發送", "多改一檔"):
            with self.subTest(v=v):
                self.assertFalse(self.plan_report.is_none_like(v))


# ---------------------------------------------------------------------------
# rollup data
# ---------------------------------------------------------------------------

def _mixed_state() -> dict:
    steps = {
        "S2.1": _step(phase="P2", agent="opus", summary="1.x 2.dev S2.1 3.無 4.todo S2.1",
                      started_at=_iso(100), completed_at=_iso(160)),
        "S1.1": _step(phase="P1", agent="sonnet", summary="1.x 2.無偏離 3.side S1.1 4.無",
                      started_at=_iso(0), completed_at=_iso(60)),
        "S1.2": _step(phase="P1", agent="sonnet", summary="free text summary",
                      started_at=_iso(10), completed_at=_iso(20)),
        "S1.3": _step(phase="P1", agent="", status="in_progress", summary=None,
                      started_at=_iso(200)),
        "S9": _step(phase="", agent=None, status="pending",
                    summary="1.x 2.dev S9 3.— 4.N/A"),
    }
    return _state(steps, phase_order=["P1", "P2"])


class RollupDataTests(unittest.TestCase):
    def test_lists_follow_phase_order_and_skip_none_like(self):
        rollup = _report(_mixed_state())["rollup"]
        self.assertEqual(rollup["deviations"], [
            {"step": "S2.1", "text": "dev S2.1"}, {"step": "S9", "text": "dev S9"},
        ])
        self.assertEqual(rollup["side_effects"], [{"step": "S1.1", "text": "side S1.1"}])
        self.assertEqual(rollup["deferred"], [{"step": "S2.1", "text": "todo S2.1"}])
        self.assertEqual(rollup["unclassified"], [{"step": "S1.2", "text": "free text summary"}])

    def test_agent_counts_sorted_by_count_with_unspecified_bucket(self):
        counts = _report(_mixed_state())["rollup"]["agent_counts"]
        self.assertEqual(counts, {"sonnet": 2, "未指定": 2, "opus": 1})
        self.assertEqual(list(counts), ["sonnet", "未指定", "opus"])

    def test_agent_values_are_normalized_before_counting(self):
        steps = {
            "S1": _step(agent="`sonnet`（readonly-verifier）"),
            "S2": _step(agent="Sonnet (general-purpose)"),
            "S3": _step(agent="   "),
            "S4": _step(agent="opus，深度審查"),
        }
        counts = _report(_state(steps))["rollup"]["agent_counts"]
        self.assertEqual(counts, {"sonnet": 2, "未指定": 1, "opus": 1})

    def test_normalize_agent_cases(self):
        normalize = _import_plan_report().normalize_agent
        cases = {
            "`sonnet`（readonly-verifier）": "sonnet",
            "Opus (deep review)": "opus",
            "  \t ": "未指定",
            "``": "未指定",
            None: "未指定",
            "haiku,fast": "haiku",
            "Sonnet 4.5": "sonnet",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize(raw), expected)

    def test_md_agent_line_uses_normalized_keys(self):
        steps = {"S1": _step(agent="`sonnet`（readonly-verifier）"), "S2": _step(agent="sonnet")}
        self.assertIn("**Agent 分布**：sonnet 2", _md(_state(steps)))

    def test_total_duration_sums_completed_and_wall_clock_spans_all(self):
        rollup = _report(_mixed_state())["rollup"]
        self.assertEqual(rollup["total_duration_seconds"], 60 + 60 + 10)
        self.assertEqual(rollup["wall_clock_seconds"], 160)

    def test_missing_timestamps_give_none(self):
        rollup = _report(_state({"S1": _step(summary=FOUR_PART)}))["rollup"]
        self.assertIsNone(rollup["total_duration_seconds"])
        self.assertIsNone(rollup["wall_clock_seconds"])

    def test_status_counts_come_from_summary(self):
        state = _mixed_state()
        rollup = _report(state)["rollup"]
        self.assertEqual(rollup["status_counts"], pr.summary(state)["by_status"])

    def test_step_rows_carry_summary_parts(self):
        report = _report(_mixed_state())
        rows = {s["id"]: s for p in report["phases"] for s in p["steps"]}
        self.assertEqual(rows["S1.1"]["summary_parts"]["side_effects"], "side S1.1")
        self.assertEqual(rows["S1.2"]["summary_parts"]["raw"], "free text summary")
        self.assertIsNone(rows["S1.3"]["summary_parts"])

    def test_build_report_does_not_mutate_input_state(self):
        state = _mixed_state()
        before = json.loads(json.dumps(state))
        _report(state)
        self.assertEqual(state, before)


# ---------------------------------------------------------------------------
# md
# ---------------------------------------------------------------------------

class RollupMarkdownTests(unittest.TestCase):
    def test_header_numbers(self):
        md = _md(_mixed_state())
        self.assertIn("**總耗時**：2m10s（wall-clock 2m40s）", md)
        self.assertIn("**Agent 分布**：sonnet 2、未指定 2、opus 1", md)
        self.assertIn("**狀態**：完成 3、進行中 1、未開始 1", md)

    def test_header_durations_fall_back_to_em_dash(self):
        md = _md(_state({"S1": _step(summary=FOUR_PART)}))
        self.assertIn("**總耗時**：—（wall-clock —）", md)

    def test_three_lists_with_step_counts_and_order(self):
        md = _md(_mixed_state())
        self.assertEqual(_section(md, "**偏離 plan（2 step）**"),
                         ["- **S2.1**：dev S2.1", "- **S9**：dev S9"])
        self.assertEqual(_section(md, "**副作用（1 step）**"), ["- **S1.1**：side S1.1"])
        self.assertEqual(_section(md, "**延後待辦（1 step）**"), ["- **S2.1**：todo S2.1"])
        self.assertEqual(_section(md, "**未分類摘要（1 step）**"), ["- **S1.2**：free text summary"])

    def test_rollup_sits_between_header_and_first_phase(self):
        md = _md(_mixed_state())
        self.assertLess(md.index("**進度**："), md.index("#### 彙整"))
        self.assertLess(md.index("**總耗時**："), md.index("#### 彙整"))
        self.assertLess(md.index("#### 彙整"), md.index("#### P1"))

    def test_empty_lists_print_none_marker_and_no_unclassified_heading(self):
        md = _md(_state({"S1": _step(summary="1.a 2.無 3.無 4.無")}))
        for heading in ("**偏離 plan（0 step）**", "**副作用（0 step）**", "**延後待辦（0 step）**"):
            with self.subTest(heading=heading):
                self.assertEqual(_section(md, heading), ["（無）"])
        self.assertNotIn("未分類摘要", md)

    def test_list_items_are_escaped(self):
        md = _md(_state({"S1": _step(summary="1.a 2.x | y <b> & z 3.無 4.無")}))
        self.assertIn("- **S1**：x \\| y &lt;b&gt; &amp; z", md)

    def test_rollup_lines_stay_parser_inert(self):
        evil = "1.a 2.- [x] **S9.9** — evil\n  - Files: evil.py 3.### Phase 9: evil 4.無"
        md = _md(_state({"S1": _step(summary=evil)}, phase_order=["Phase 1: Setup"]))
        step_re = re.compile(rf"^-\s+\[[ x]\]\s+(?:\*\*)?{pr.STEP_ID_PATTERN}")
        for line in md.split("\n"):
            with self.subTest(line=line):
                self.assertIsNone(step_re.match(line))
                self.assertFalse(line.startswith(" "))
        tmp = tempfile.TemporaryDirectory(dir=Path.home(), prefix=".plan-report-rollup-")
        self.addCleanup(tmp.cleanup)
        plain = Path(tmp.name) / "plain.md"
        plain.write_text(PLAN_TEXT, encoding="utf-8")
        combined = Path(tmp.name) / "combined.md"
        combined.write_text(PLAN_TEXT + "\n" + md + "\n", encoding="utf-8")
        self.assertEqual(pr.parse_plan(combined)["steps"], pr.parse_plan(plain)["steps"])
        self.assertEqual(pr.parse_plan(combined)["phase_order"], pr.parse_plan(plain)["phase_order"])


# ---------------------------------------------------------------------------
# json
# ---------------------------------------------------------------------------

class RollupJsonTests(unittest.TestCase):
    def setUp(self):
        self.plan_report = _import_plan_report()

    def _payload(self, state: dict) -> dict:
        return json.loads(self.plan_report.render_report_json(_report(state)))

    def test_schema_version_is_2(self):
        self.assertEqual(self.plan_report.REPORT_SCHEMA_VERSION, 2)
        self.assertEqual(self._payload(_mixed_state())["schema_version"], 2)

    def test_rollup_keys_and_shapes(self):
        rollup = self._payload(_mixed_state())["rollup"]
        self.assertEqual(set(rollup), {
            "total_duration_seconds", "wall_clock_seconds", "agent_counts", "status_counts",
            "deviations", "side_effects", "deferred", "unclassified",
        })
        self.assertEqual(rollup["deviations"][0], {"step": "S2.1", "text": "dev S2.1"})

    def test_steps_carry_summary_parts_including_raw_fallback(self):
        rows = {s["id"]: s for p in self._payload(_mixed_state())["phases"] for s in p["steps"]}
        self.assertEqual(rows["S2.1"]["summary_parts"], {
            "did": "x", "deviation": "dev S2.1", "side_effects": "無", "deferred": "todo S2.1",
        })
        self.assertEqual(rows["S1.2"]["summary_parts"], {
            "did": None, "deviation": None, "side_effects": None, "deferred": None,
            "raw": "free text summary",
        })


if __name__ == "__main__":
    unittest.main()
