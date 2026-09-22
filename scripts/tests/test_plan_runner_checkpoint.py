"""Unit tests for plan_runner_checkpoint — build, atomic write, resume text."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import plan_runner_checkpoint as ck  # noqa: E402

NOW = "2026-09-22T10:00:00+00:00"


def make_state():
    return {
        "slug": "demo",
        "title": "Demo Plan",
        "steps": {
            "S1": {"status": "completed", "title": "one", "summary": "did one",
                   "evidence": ["out/a.txt", "PR 12"]},
            "S2": {"status": "failed", "title": "two", "failure_reason": "boom"},
            "S3": {"status": "skipped", "title": "three"},
            "S4": {"status": "pending", "title": "four"},
            "S5": {"status": "failed", "title": "five", "failure_reason": ""},
        },
    }


class BuildCheckpointTests(unittest.TestCase):
    def _build(self, **overrides):
        kwargs = {"ready_steps": ["S4"], "stuck": None, "preflight": None, "now": NOW}
        kwargs.update(overrides)
        return ck.build_checkpoint(make_state(), **kwargs)

    def test_fields(self):
        data = self._build()
        self.assertEqual(data["schema_version"], ck.CHECKPOINT_SCHEMA_VERSION)
        self.assertEqual(data["slug"], "demo")
        self.assertEqual(data["updated_at"], NOW)
        self.assertEqual([s["id"] for s in data["completed_steps"]], ["S1"])
        self.assertEqual(data["completed_steps"][0]["summary"], "did one")
        self.assertEqual(data["skipped_steps"], ["S3"])
        self.assertEqual(data["artifacts"], ["out/a.txt", "PR 12"])
        self.assertEqual(data["next_ready_step"], "S4")

    def test_failed_reasons_become_open_questions(self):
        questions = self._build()["open_questions"]
        self.assertIn("S2 failed: boom", questions)
        self.assertIn("S5 failed: (no reason given)", questions)

    def test_stuck_is_recorded_and_raised_as_open_question(self):
        stuck = {"step_id": "S4", "kind": "ready", "count": 3, "stuck_at": NOW}
        data = self._build(stuck=stuck)
        self.assertEqual(data["stuck"], stuck)
        self.assertIn(f"S4 STUCK (ready) since {NOW}", data["open_questions"])

    def test_no_ready_step(self):
        self.assertIsNone(self._build(ready_steps=[])["next_ready_step"])

    def test_input_state_is_not_mutated(self):
        state = make_state()
        before = json.dumps(state, sort_keys=True)
        ck.build_checkpoint(state, ready_steps=[], stuck=None, preflight=None, now=NOW)
        self.assertEqual(json.dumps(state, sort_keys=True), before)


class WriteLoadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name) / ".plan-state"
        self.path = ck.checkpoint_path_for(self.dir, "demo")

    def test_path(self):
        self.assertEqual(self.path.name, "demo.checkpoint.json")

    def test_round_trip_and_no_tmp_left(self):
        ck.write_checkpoint_atomic(self.path, {"a": "中文"})
        self.assertEqual(ck.load_checkpoint(self.path), {"a": "中文"})
        self.assertEqual([p.name for p in self.dir.iterdir()], ["demo.checkpoint.json"])

    def test_failed_replace_leaves_old_file_and_no_tmp(self):
        ck.write_checkpoint_atomic(self.path, {"v": 1})
        with mock.patch.object(ck.os, "replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                ck.write_checkpoint_atomic(self.path, {"v": 2})
        self.assertEqual(ck.load_checkpoint(self.path), {"v": 1})
        self.assertEqual(len(list(self.dir.iterdir())), 1)

    def test_absent_is_none(self):
        self.assertIsNone(ck.load_checkpoint(self.path))

    def test_non_object_raises(self):
        self.dir.mkdir()
        self.path.write_text("[1]")
        with self.assertRaises(ValueError):
            ck.load_checkpoint(self.path)

    def test_corrupt_raises(self):
        self.dir.mkdir()
        self.path.write_text("{")
        with self.assertRaises(ValueError):
            ck.load_checkpoint(self.path)


class FormatResumeTests(unittest.TestCase):
    def test_full_summary(self):
        data = ck.build_checkpoint(
            make_state(), ready_steps=["S4"], stuck=None,
            preflight={"ok": False, "failed": ["jq"]}, now=NOW,
        )
        text = ck.format_resume_md(data, Path("/x/demo.checkpoint.json"))
        self.assertIn("# Resume: Demo Plan", text)
        self.assertIn("- S1: did one", text)
        self.assertIn("- out/a.txt", text)
        self.assertIn("- S2 failed: boom", text)
        self.assertIn("preflight: FAIL (jq)", text)
        self.assertIn("next (at checkpoint): S4", text)

    def test_empty_checkpoint(self):
        text = ck.format_resume_md({"slug": "demo"}, Path("/x"))
        self.assertIn("- (none)", text)
        self.assertIn("next (at checkpoint): (none)", text)
        self.assertNotIn("Artifacts", text)
        self.assertNotIn("preflight", text)


if __name__ == "__main__":
    unittest.main()
