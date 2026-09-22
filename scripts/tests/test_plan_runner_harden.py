"""PR-A hardening: preflight, STUCK progress assertion, resumable checkpoint.

Two layers, matching the rest of the suite: decide_hook_action() driven in
memory (pure), and the CLI / `hook-stop` driven as a subprocess under a
temp $HOME so no real pointer is ever touched.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent
# Both: plan_runner lives in scripts/, the shared fixtures in scripts/tests/
# (on sys.path already under `discover`, but not when this file runs alone).
for _path in (SCRIPTS_DIR, TESTS_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import plan_runner as pr  # noqa: E402
import plan_runner_preflight as pf  # noqa: E402
from test_plan_run_hook import (  # noqa: E402
    make_hook_input, make_pointer, make_state, make_step,
)

PLAN_RUNNER = SCRIPTS_DIR / "plan_runner.py"
ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")

PLAN_TEXT = """# Harden Fixture

### Phase 1
- [ ] **S1** — first
  - Action: run first
  - Command: `git status --short`
- [ ] **S2** — second
  - Action: run second
  - Command: `/verify`
  - Dependencies: S1
- [ ] **S3** — third
  - Action: run third
  - Dependencies: S2
"""


def failing_preflight(*names: str) -> pf.PreflightResult:
    return pf.PreflightResult(tuple(
        pf.PreflightCheck(pf.KIND_TOOL, n, False, f"install {n}") for n in names
    ))


OK_PREFLIGHT = pf.PreflightResult((pf.PreflightCheck(pf.KIND_TOOL, "git", True),))


def advance(pointer, state, *, active=True, preflight=None):
    return pr.decide_hook_action(
        make_hook_input(stop_hook_active=active), pointer, state, preflight=preflight,
    )


def two_pending():
    return make_state({
        "S1.1": make_step(status="pending", phase="P1"),
        "S1.2": make_step(status="pending", phase="P1", deps=["S1.1"]),
    })


def run_n(pointer, state, n, **kwargs):
    decisions = []
    for _ in range(n):
        decision = advance(pointer, state, **kwargs)
        decisions.append(decision)
        pointer = decision.pointer_updates or pointer
    return decisions, pointer


class ReadyStuckTests(unittest.TestCase):
    def test_third_assignment_is_stuck_and_later_ones_stay_quiet(self):
        decisions, pointer = run_n(make_pointer(), two_pending(), pr.HOOK_STUCK_AT + 2)
        pre = decisions[:pr.HOOK_STUCK_AT - 1]
        hit = decisions[pr.HOOK_STUCK_AT - 1]
        self.assertTrue(all(d.decision == pr.HOOK_BLOCK for d in pre))
        self.assertEqual(hit.decision, pr.HOOK_ALLOW)
        self.assertIn("STUCK", hit.system_message)
        self.assertIn("`S1.1`", hit.system_message)
        self.assertIn(f"{pr.HOOK_STUCK_AT} 次", hit.system_message)
        self.assertEqual(len(ISO_RE.findall(hit.system_message)), 2)
        self.assertIn(" start ", hit.system_message)
        self.assertIn(" skip ", hit.system_message)
        for later in decisions[pr.HOOK_STUCK_AT:]:
            self.assertEqual(later.decision, pr.HOOK_ALLOW)
            self.assertIsNone(later.system_message)
        self.assertEqual(pointer["stuck_step_id"], "S1.1")
        self.assertEqual(pointer["stuck_kind"], "ready")
        self.assertIsNotNone(pointer["stuck_at"])
        self.assertIsNotNone(pointer["attempt_first_at"])

    def test_stuck_does_not_spend_block_budget(self):
        decisions, pointer = run_n(make_pointer(), two_pending(), pr.HOOK_STUCK_AT)
        self.assertEqual(pointer["consecutive_blocks"], pr.HOOK_STUCK_AT - 1)

    def test_latch_survives_a_fresh_user_turn(self):
        _, pointer = run_n(make_pointer(), two_pending(), pr.HOOK_STUCK_AT)
        decision = advance(pointer, two_pending(), active=False)
        self.assertEqual(decision.decision, pr.HOOK_ALLOW)

    def test_start_releases_the_latch(self):
        _, pointer = run_n(make_pointer(), two_pending(), pr.HOOK_STUCK_AT)
        state = two_pending()
        state["steps"]["S1.1"]["status"] = "in_progress"
        decision = advance(pointer, state)
        self.assertEqual(decision.decision, pr.HOOK_BLOCK)
        self.assertIsNone(decision.pointer_updates["stuck_step_id"])
        self.assertIsNone(decision.pointer_updates["stuck_kind"])

    def test_new_step_restarts_the_count(self):
        _, pointer = run_n(make_pointer(), two_pending(), pr.HOOK_STUCK_AT)
        state = two_pending()
        state["steps"]["S1.1"]["status"] = "completed"
        decision = advance(pointer, state)
        self.assertEqual(decision.decision, pr.HOOK_BLOCK)
        self.assertIn("S1.2", decision.reason)
        self.assertEqual(decision.pointer_updates["assign_repeat_count"], 1)
        self.assertIsNone(decision.pointer_updates["stuck_step_id"])


class InProgressStuckTests(unittest.TestCase):
    def _state(self):
        return make_state({"S1.1": make_step(
            status="in_progress", phase="P1", started_at="2026-09-22T01:02:03+00:00",
        )})

    def test_third_nag_is_stuck(self):
        decisions, pointer = run_n(make_pointer(), self._state(), pr.HOOK_STUCK_AT + 1)
        self.assertTrue(all(d.decision == pr.HOOK_BLOCK for d in decisions[:2]))
        hit = decisions[pr.HOOK_STUCK_AT - 1]
        self.assertEqual(hit.decision, pr.HOOK_ALLOW)
        self.assertIn("STUCK", hit.system_message)
        self.assertIn("2026-09-22T01:02:03", hit.system_message)
        self.assertIn(" complete ", hit.system_message)
        self.assertIn(" fail ", hit.system_message)
        self.assertEqual(decisions[-1].decision, pr.HOOK_ALLOW)
        self.assertIsNone(decisions[-1].system_message)
        self.assertEqual(pointer["stuck_kind"], "in_progress")

    def test_count_restarts_on_a_fresh_user_turn(self):
        _, pointer = run_n(make_pointer(), self._state(), pr.HOOK_STUCK_AT)
        decision = advance(pointer, self._state(), active=False)
        self.assertEqual(decision.decision, pr.HOOK_BLOCK)
        self.assertIsNone(decision.pointer_updates["stuck_step_id"])

    def test_missing_started_at_still_reports(self):
        state = make_state({"S1.1": make_step(status="in_progress", phase="P1")})
        decisions, _ = run_n(make_pointer(), state, pr.HOOK_STUCK_AT)
        self.assertIn("STUCK", decisions[-1].system_message)


class PreflightBranchTests(unittest.TestCase):
    def test_failure_before_any_start_allows_loudly(self):
        decision = advance(make_pointer(), two_pending(), preflight=failing_preflight("jq", "rg"))
        self.assertEqual(decision.decision, pr.HOOK_ALLOW)
        self.assertIn("PREFLIGHT", decision.system_message)
        self.assertIn("jq", decision.system_message)
        self.assertIn("install rg", decision.system_message)
        self.assertIn(" preflight ", decision.system_message)

    def test_failure_never_counts_an_assignment(self):
        _, pointer = run_n(make_pointer(), two_pending(), 4, preflight=failing_preflight("jq"))
        self.assertIsNone(pointer.get("last_assigned_step_id"))
        self.assertEqual(pointer.get("consecutive_blocks"), 0)

    def test_passing_preflight_advances_normally(self):
        decision = advance(make_pointer(), two_pending(), preflight=OK_PREFLIGHT)
        self.assertEqual(decision.decision, pr.HOOK_BLOCK)

    def test_ignored_once_a_step_has_started(self):
        state = two_pending()
        state["steps"]["S1.1"]["status"] = "completed"
        decision = advance(make_pointer(), state, preflight=failing_preflight("jq"))
        self.assertEqual(decision.decision, pr.HOOK_BLOCK)

    def test_paused_still_wins(self):
        decision = advance(make_pointer(paused=True), two_pending(),
                           preflight=failing_preflight("jq"))
        self.assertIsNone(decision.system_message)


class PointerSchemaTests(unittest.TestCase):
    def test_new_fields_are_optional_strings(self):
        good = make_pointer(stuck_step_id="S1", stuck_kind="ready",
                            stuck_at=pr.now_iso(), attempt_first_at=pr.now_iso())
        self.assertTrue(pr._pointer_fields_well_typed(good))
        self.assertTrue(pr._pointer_fields_well_typed(make_pointer()))
        self.assertFalse(pr._pointer_fields_well_typed(make_pointer(stuck_step_id=3)))


class HookPreflightIoTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.plan = Path(self._tmp.name) / "p.md"
        self.plan.write_text(PLAN_TEXT.replace("git status", "pra-nope-tool x"))
        self.pointer = {"plan_path": str(self.plan)}

    def test_runs_only_before_any_start(self):
        result = pr._hook_preflight(self.pointer, two_pending(), self._tmp.name)
        self.assertFalse(result.ok)
        started = two_pending()
        started["steps"]["S1.1"]["status"] = "in_progress"
        self.assertIsNone(pr._hook_preflight(self.pointer, started, self._tmp.name))

    def test_bad_inputs_yield_none(self):
        self.assertIsNone(pr._hook_preflight(self.pointer, None, self._tmp.name))
        self.assertIsNone(pr._hook_preflight({}, two_pending(), self._tmp.name))

    def test_missing_module_degrades_to_none(self):
        with mock.patch.object(pr, "_import_sibling", side_effect=ImportError("x")):
            self.assertIsNone(pr._hook_preflight(self.pointer, two_pending(), self._tmp.name))


class CliTestCase(unittest.TestCase):
    """Subprocess CLI under a temp $HOME with the plan inside it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name).resolve()
        (self.home / ".claude").mkdir()
        self.proj = self.home / "proj"
        self.proj.mkdir()
        self.plan = self.proj / "harden.md"
        self.plan.write_text(PLAN_TEXT, encoding="utf-8")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("PLAN_RUN")}
        self.env["HOME"] = str(self.home)

    def cli(self, *args, stdin=None):
        return subprocess.run(
            [sys.executable, str(PLAN_RUNNER), *args], cwd=self.proj, env=self.env,
            input=stdin, capture_output=True, text=True, timeout=60,
        )

    def init(self):
        self.assertEqual(self.cli("init", str(self.plan)).returncode, 0)

    def hook(self):
        payload = json.dumps({
            "hook_event_name": "Stop", "session_id": "s1", "cwd": str(self.proj),
            "transcript_path": "/tmp/none.jsonl", "stop_hook_active": True,
        })
        out = self.cli("hook-stop", stdin=payload).stdout.strip()
        return json.loads(out) if out else {}

    @property
    def checkpoint_file(self):
        return self.proj / ".plan-state" / "harden.checkpoint.json"


class PreflightCliTests(CliTestCase):
    def test_ok_json(self):
        self.init()
        r = self.cli("preflight", str(self.plan), "--format", "json")
        self.assertEqual(r.returncode, 0, r.stdout)
        data = json.loads(r.stdout)
        self.assertTrue(data["ok"])
        runner = [c for c in data["checks"] if c["kind"] == "runner"][0]
        self.assertEqual(runner["name"], str(PLAN_RUNNER.resolve()))

    def test_missing_state_md(self):
        r = self.cli("preflight", str(self.plan))
        self.assertEqual(r.returncode, 1)
        self.assertIn("[FAIL] state", r.stdout)
        self.assertIn("init", r.stdout)

    def test_missing_plan(self):
        r = self.cli("preflight", str(self.proj / "nope.md"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("nope.md", r.stdout)

    def test_hook_reports_preflight_failure_without_blocking(self):
        self.plan.write_text(PLAN_TEXT.replace("git status", "pra-nope-tool x"))
        self.init()
        outs = [self.hook() for _ in range(2)]
        self.assertTrue(all(o.get("decision") != "block" for o in outs))
        self.assertIn("pra-nope-tool", outs[0]["systemMessage"])


class PreflightDegradeTests(unittest.TestCase):
    def test_cli_reports_missing_module(self):
        args = mock.Mock(plan="/x/p.md", format="md")
        with mock.patch.object(pr, "_import_sibling", side_effect=ImportError("gone")), \
                mock.patch.object(pr, "emit") as emitted:
            self.assertEqual(pr.cmd_preflight(args), 1)
        self.assertIn("gone", emitted.call_args[0][0]["error"])


class SiblingImportTests(unittest.TestCase):
    def test_loads_without_scripts_dir_on_sys_path(self):
        code = (
            "import importlib.util,sys;"
            f"s=importlib.util.spec_from_file_location('m',{str(PLAN_RUNNER)!r});"
            "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
            "print(m._import_sibling('plan_runner_preflight').KIND_TOOL)"
        )
        r = subprocess.run([sys.executable, "-I", "-c", code], cwd="/",
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.stdout.strip(), "tool", r.stderr)


if __name__ == "__main__":
    unittest.main()
