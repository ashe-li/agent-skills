"""Tests for S1.1 of plans/active/plan-runner-auto-summary-and-completion-report.md.

Covers:
    (a) `init --require-summary` persists `require_summary: true`; without the
        flag the key is absent or false; `init --force` resets per the new call.
    (b) A require-summary plan rejects `complete` without `--summary` when the
        step has no stored summary (rc=1, state bytes unchanged); an existing
        summary lets it through; fail/skip are unaffected; plans without the
        flag keep today's behaviour.
    (c) The printed `ok:` command (next template + hook reasons
        report_result/settle_background) carries the `--summary=` placeholder,
        survives the hook's JSON wire format, and no stored summary leaks.
    (d) A complete/skip that makes the plan all_done writes
        `<state_dir>/<slug>.report.md` byte-identical to `report --format md`
        stdout and prints `Report: <path>` (md) / `report_path` (json).
    (e) Report generation failure (ImportError / OSError) keeps rc=0 and
        prints `Report: failed (...)` / json `report_error`.
    (f) hook-stop never loads plan_report, even for an all_done plan.

Conventions follow test_plan_runner_summary.py: in-process `pr.cmd_*`
calls on Namespace objects, plans under
`TemporaryDirectory(dir=Path.home(), prefix=".plan-run-test-")`, init with
attach=False (no pointer written under the real ~/.claude).

Run: cd <worktree> && python3 -m unittest scripts.tests.test_plan_runner_auto_report -v
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import plan_runner as pr  # noqa: E402

RUNNER = SCRIPTS_DIR / "plan_runner.py"

PLACEHOLDER = '--summary="<1.做了什麼 2.偏離plan 3.副作用 4.延後待辦>"'
# Derived the same way plan_runner derives COMPLETE_SUMMARY_PLACEHOLDER_TEXT
# from COMPLETE_SUMMARY_PLACEHOLDER, so this test never drifts from it.
PLACEHOLDER_TEXT = PLACEHOLDER.split("=", 1)[1].strip('"')
SENTINEL = "SENTINEL-auto-report-9c1e"

PLAN_TEXT = """# Auto Report Test Plan

### Phase 1: Setup

- [ ] S1 First step
  - Files: `a.py`
  - Action: do A

- [ ] S2 Second step
  - Files: `b.py`
  - Action: do B
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _capture(func, args: argparse.Namespace) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = func(args)
    return rc, out.getvalue()


def _init_args(plan_path: Path, *, require_summary=None, force=False) -> argparse.Namespace:
    ns = argparse.Namespace(plan=str(plan_path), force=force, attach=False, format="json")
    if require_summary is not None:
        ns.require_summary = require_summary
    return ns


def _start_args(plan_path: Path, step: str) -> argparse.Namespace:
    return argparse.Namespace(
        plan=str(plan_path), step=step, task_id=None, session_id=None, format="json",
    )


def _complete_args(plan_path: Path, step: str, *, summary=None, fmt="json") -> argparse.Namespace:
    return argparse.Namespace(
        plan=str(plan_path), step=step, format=fmt, summary=summary, evidence=None,
    )


def _fail_args(plan_path: Path, step: str, *, fmt="json") -> argparse.Namespace:
    return argparse.Namespace(plan=str(plan_path), step=step, reason="boom", format=fmt)


def _skip_args(plan_path: Path, step: str, *, fmt="json") -> argparse.Namespace:
    return argparse.Namespace(plan=str(plan_path), step=step, format=fmt)


def _report_args(plan_path: Path) -> argparse.Namespace:
    return argparse.Namespace(plan=str(plan_path), format="md", output=None, force=False)


def _new_plan(test_case: unittest.TestCase, *, require_summary=None) -> Path:
    tmp = tempfile.TemporaryDirectory(dir=Path.home(), prefix=".plan-run-test-")
    test_case.addCleanup(tmp.cleanup)
    plan_path = Path(tmp.name).resolve() / "plan.md"
    plan_path.write_text(PLAN_TEXT, encoding="utf-8")
    rc, out = _capture(pr.cmd_init, _init_args(plan_path, require_summary=require_summary))
    assert rc == 0, out
    return plan_path


def _ok(test_case, func, args) -> str:
    rc, out = _capture(func, args)
    test_case.assertEqual(rc, 0, out)
    return out


def _state_bytes(plan_path: Path) -> bytes:
    return pr.state_path_for(plan_path).read_bytes()


def _expected_report_path(plan_path: Path) -> Path:
    return pr.state_path_for(plan_path).parent / f"{plan_path.stem}.report.md"


def _ok_line(text: str) -> str:
    for line in text.splitlines():
        if " complete " in line and "ok:" in line:
            return line
    raise AssertionError(f"no ok: complete line in:\n{text}")


# ---------------------------------------------------------------------------
# (a) init --require-summary
# ---------------------------------------------------------------------------

class InitRequireSummaryTests(unittest.TestCase):
    def test_flag_persists_true(self):
        plan = _new_plan(self, require_summary=True)
        self.assertIs(pr.load_state(plan).get("require_summary"), True)

    def test_without_flag_key_absent_or_false(self):
        plan = _new_plan(self)
        state = pr.load_state(plan)
        self.assertIn("steps", state)  # precondition: real state was loaded
        self.assertFalse(state.get("require_summary"))

    def test_force_reinit_resets_per_new_flag(self):
        plan = _new_plan(self, require_summary=True)
        self.assertIs(pr.load_state(plan).get("require_summary"), True)
        _ok(self, pr.cmd_init, _init_args(plan, force=True, require_summary=False))
        self.assertFalse(pr.load_state(plan).get("require_summary"))
        _ok(self, pr.cmd_init, _init_args(plan, force=True, require_summary=True))
        self.assertIs(pr.load_state(plan).get("require_summary"), True)

    def test_cli_flag_is_parsed(self):
        tmp = tempfile.TemporaryDirectory(dir=Path.home(), prefix=".plan-run-test-")
        self.addCleanup(tmp.cleanup)
        plan = Path(tmp.name).resolve() / "plan.md"
        plan.write_text(PLAN_TEXT, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(RUNNER), "init", str(plan), "--require-summary", "--no-attach"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIs(pr.load_state(plan).get("require_summary"), True)


# ---------------------------------------------------------------------------
# (b) required summary on complete
# ---------------------------------------------------------------------------

class RequiredSummaryCompleteTests(unittest.TestCase):
    def test_missing_summary_rejected_without_touching_state(self):
        plan = _new_plan(self, require_summary=True)
        _ok(self, pr.cmd_start, _start_args(plan, "S1"))
        before = _state_bytes(plan)
        self.assertEqual(pr.load_state(plan)["steps"]["S1"]["status"], pr.IN_PROGRESS)

        rc, out = _capture(pr.cmd_complete, _complete_args(plan, "S1"))
        self.assertEqual(rc, 1, out)
        error = json.loads(out)["error"]
        self.assertIn("--summary", error)
        for item in ("做了什麼", "偏離", "副作用", "延後待辦"):
            self.assertIn(item, error)
        self.assertEqual(_state_bytes(plan), before)

    def test_with_summary_completes(self):
        plan = _new_plan(self, require_summary=True)
        _ok(self, pr.cmd_start, _start_args(plan, "S1"))
        _ok(self, pr.cmd_complete, _complete_args(plan, "S1", summary="did A"))
        step = pr.load_state(plan)["steps"]["S1"]
        self.assertEqual(step["status"], pr.COMPLETED)
        self.assertEqual(step["summary"], "did A")

    def test_existing_summary_allows_flagless_recomplete(self):
        plan = _new_plan(self, require_summary=True)
        _ok(self, pr.cmd_start, _start_args(plan, "S1"))
        _ok(self, pr.cmd_complete, _complete_args(plan, "S1", summary="did A"))
        _ok(self, pr.cmd_complete, _complete_args(plan, "S1"))
        self.assertEqual(pr.load_state(plan)["steps"]["S1"]["summary"], "did A")

    def test_fail_and_skip_not_required(self):
        plan = _new_plan(self, require_summary=True)
        _ok(self, pr.cmd_start, _start_args(plan, "S1"))
        _ok(self, pr.cmd_fail, _fail_args(plan, "S1"))
        _ok(self, pr.cmd_skip, _skip_args(plan, "S2"))
        steps = pr.load_state(plan)["steps"]
        self.assertEqual(steps["S1"]["status"], pr.FAILED)
        self.assertEqual(steps["S2"]["status"], pr.SKIPPED)

    def test_unrequired_plan_completes_without_flag_and_output_unchanged(self):
        plan = _new_plan(self)
        _ok(self, pr.cmd_start, _start_args(plan, "S1"))
        out = _ok(self, pr.cmd_complete, _complete_args(plan, "S1"))
        payload = json.loads(out)
        self.assertEqual(payload["status"], "completed")
        for key in ("error", "recorded", "report_path", "report_error"):
            self.assertNotIn(key, payload)
        state = pr.load_state(plan)
        expected_keys = {"status", "step", "task_id"} | set(pr._build_state_view(state))
        self.assertEqual(set(payload), expected_keys)


# ---------------------------------------------------------------------------
# (b2) copying the placeholder verbatim into --summary is rejected
# ---------------------------------------------------------------------------

class PlaceholderAsSummaryRejectedTests(unittest.TestCase):
    def test_verbatim_placeholder_rejected_on_required_plan(self):
        plan = _new_plan(self, require_summary=True)
        _ok(self, pr.cmd_start, _start_args(plan, "S1"))
        before = _state_bytes(plan)

        rc, out = _capture(
            pr.cmd_complete, _complete_args(plan, "S1", summary=PLACEHOLDER_TEXT),
        )
        self.assertEqual(rc, 1, out)
        error = json.loads(out)["error"]
        self.assertIn("placeholder", error.lower())
        self.assertEqual(_state_bytes(plan), before)

    def test_verbatim_placeholder_rejected_on_unrequired_plan(self):
        # The placeholder is meaningless on any plan, required or not.
        plan = _new_plan(self)
        _ok(self, pr.cmd_start, _start_args(plan, "S1"))
        before = _state_bytes(plan)

        rc, out = _capture(
            pr.cmd_complete, _complete_args(plan, "S1", summary=PLACEHOLDER_TEXT),
        )
        self.assertEqual(rc, 1, out)
        error = json.loads(out)["error"]
        self.assertIn("placeholder", error.lower())
        self.assertEqual(_state_bytes(plan), before)

    def test_real_summary_still_accepted(self):
        plan = _new_plan(self, require_summary=True)
        _ok(self, pr.cmd_start, _start_args(plan, "S1"))
        _ok(self, pr.cmd_complete, _complete_args(plan, "S1", summary="did A, no deviation"))
        step = pr.load_state(plan)["steps"]["S1"]
        self.assertEqual(step["summary"], "did A, no deviation")


# ---------------------------------------------------------------------------
# (c) placeholder in printed ok commands
# ---------------------------------------------------------------------------

class PlaceholderTests(unittest.TestCase):
    def _hook_state(self, status: str) -> dict:
        plan_path = str(Path.home() / ".plan-run-test-fixture-auto" / "plan.md")
        step = {
            "id": "S1", "title": "Do", "phase": "P0", "deps": [], "agent": None,
            "skill": None, "command": None, "files": None, "action": "do",
            "risk": None, "status": status, "task_id": None, "started_at": None,
            "completed_at": None, "failure_reason": None,
            "summary": SENTINEL, "evidence": [SENTINEL + "/e.txt"],
        }
        return {
            "plan_path": plan_path, "slug": "plan", "title": "T", "phase_order": ["P0"],
            "parent_task_id": None, "created_at": pr.now_iso(),
            "updated_at": pr.now_iso(), "steps": {"S1": step},
        }

    def _budget(self):
        return pr.BudgetDecision(
            decision="block", consecutive_blocks=0, block_budget=6,
            checkpoint_pending=False, steps_remaining=1,
            checkpoint_from_phase_boundary=False,
        )

    def test_next_md_ok_line_has_placeholder(self):
        plan = _new_plan(self)
        out = _ok(self, pr.cmd_next, argparse.Namespace(plan=str(plan), format="md"))
        line = _ok_line(out)
        self.assertIn(f"complete <plan> S1 {PLACEHOLDER}", line)

    def test_hook_reasons_have_placeholder_and_survive_json(self):
        for kind, status in (("next_step", "pending"), ("report_result", "in_progress"),
                             ("settle_background", "in_progress")):
            state = self._hook_state(status)
            # precondition: sentinel really is stored on the step
            self.assertEqual(state["steps"]["S1"]["summary"], SENTINEL)
            reason = pr.render_hook_reason(
                state, kind, "S1", self._budget(), plan_path=state["plan_path"],
            )
            wire = json.loads(json.dumps(pr._hook_output_payload(
                pr.HookDecision(decision=pr.HOOK_BLOCK, reason=reason),
            ), ensure_ascii=False))["reason"]
            line = _ok_line(wire)
            self.assertIn(f"complete {shlex.quote(state['plan_path'])} S1 {PLACEHOLDER}", line,
                          msg=f"kind={kind}")
            self.assertNotIn(SENTINEL, wire, msg=f"kind={kind}")

    def test_placeholder_is_one_shell_word(self):
        tokens = shlex.split(PLACEHOLDER)
        self.assertEqual(tokens, ["--summary=<1.做了什麼 2.偏離plan 3.副作用 4.延後待辦>"])
        plan = _new_plan(self)
        out = _ok(self, pr.cmd_next, argparse.Namespace(plan=str(plan), format="md"))
        cmd = _ok_line(out).split("ok:", 1)[1].split(" | err:", 1)[0]
        self.assertIn(tokens[0], shlex.split(cmd))


# ---------------------------------------------------------------------------
# (d) completion report written on all_done
# ---------------------------------------------------------------------------

class AutoReportTests(unittest.TestCase):
    def test_last_complete_writes_report_identical_to_report_cmd(self):
        plan = _new_plan(self)
        report_path = _expected_report_path(plan)
        _ok(self, pr.cmd_start, _start_args(plan, "S1"))
        out1 = _ok(self, pr.cmd_complete, _complete_args(plan, "S1", summary=SENTINEL, fmt="md"))
        self.assertNotIn("Report:", out1)
        self.assertFalse(report_path.exists())

        _ok(self, pr.cmd_start, _start_args(plan, "S2"))
        out2 = _ok(self, pr.cmd_complete, _complete_args(plan, "S2", fmt="md"))
        self.assertIn(f"Report: {report_path}", out2.splitlines())
        self.assertTrue(report_path.exists())
        _, stdout_report = _capture(pr.cmd_report, _report_args(plan))
        self.assertEqual(report_path.read_bytes(), stdout_report.encode("utf-8"))
        # the report itself carries the summary; the complete output must not
        self.assertIn(SENTINEL, report_path.read_text(encoding="utf-8"))
        self.assertNotIn(SENTINEL, out2)

    def test_json_payload_has_report_path(self):
        plan = _new_plan(self)
        _ok(self, pr.cmd_start, _start_args(plan, "S1"))
        payload1 = json.loads(_ok(self, pr.cmd_complete, _complete_args(plan, "S1")))
        self.assertEqual(payload1["status"], "completed")
        self.assertNotIn("report_path", payload1)
        _ok(self, pr.cmd_start, _start_args(plan, "S2"))
        payload2 = json.loads(_ok(self, pr.cmd_complete, _complete_args(plan, "S2")))
        self.assertEqual(payload2["report_path"], str(_expected_report_path(plan)))
        self.assertNotIn("report_error", payload2)

    def test_skip_reaching_all_done_writes_report(self):
        plan = _new_plan(self)
        _ok(self, pr.cmd_start, _start_args(plan, "S1"))
        _ok(self, pr.cmd_complete, _complete_args(plan, "S1"))
        self.assertFalse(_expected_report_path(plan).exists())
        out = _ok(self, pr.cmd_skip, _skip_args(plan, "S2", fmt="md"))
        self.assertIn(f"Report: {_expected_report_path(plan)}", out.splitlines())
        self.assertTrue(_expected_report_path(plan).exists())

    def test_fail_does_not_write_report(self):
        plan = _new_plan(self)
        _ok(self, pr.cmd_start, _start_args(plan, "S1"))
        _ok(self, pr.cmd_complete, _complete_args(plan, "S1"))
        _ok(self, pr.cmd_start, _start_args(plan, "S2"))
        out = _ok(self, pr.cmd_fail, _fail_args(plan, "S2", fmt="md"))
        self.assertEqual(pr.load_state(plan)["steps"]["S1"]["status"], pr.COMPLETED)
        self.assertNotIn("Report:", out)
        self.assertFalse(_expected_report_path(plan).exists())


# ---------------------------------------------------------------------------
# (e) report failure does not change rc
# ---------------------------------------------------------------------------

class AutoReportFailureTests(unittest.TestCase):
    def _plan_one_step_left(self) -> Path:
        plan = _new_plan(self)
        _ok(self, pr.cmd_start, _start_args(plan, "S1"))
        _ok(self, pr.cmd_complete, _complete_args(plan, "S1"))
        _ok(self, pr.cmd_start, _start_args(plan, "S2"))
        return plan

    def test_import_error_keeps_rc_zero(self):
        plan = self._plan_one_step_left()
        with mock.patch.dict(sys.modules, {"plan_report": None}):
            out_md = _ok(self, pr.cmd_complete, _complete_args(plan, "S2", fmt="md"))
        self.assertTrue(any(l.startswith("Report: failed (") for l in out_md.splitlines()), out_md)
        self.assertEqual(pr.load_state(plan)["steps"]["S2"]["status"], pr.COMPLETED)
        self.assertFalse(_expected_report_path(plan).exists())
        with mock.patch.dict(sys.modules, {"plan_report": None}):
            payload = json.loads(_ok(self, pr.cmd_complete, _complete_args(plan, "S2")))
        self.assertIn("report_error", payload)
        self.assertNotIn("report_path", payload)

    def test_os_error_keeps_rc_zero(self):
        plan = self._plan_one_step_left()
        boom = OSError(13, "Permission denied")
        with mock.patch.object(pr, "_write_text_atomic", side_effect=boom):
            out_md = _ok(self, pr.cmd_complete, _complete_args(plan, "S2", fmt="md"))
        self.assertIn("Report: failed (", out_md)
        self.assertEqual(pr.load_state(plan)["steps"]["S2"]["status"], pr.COMPLETED)

    def test_other_exception_from_report_build_keeps_rc_zero(self):
        plan = self._plan_one_step_left()
        with mock.patch.object(pr, "_render_report_text", side_effect=ValueError("bad")):
            out_md = _ok(self, pr.cmd_complete, _complete_args(plan, "S2", fmt="md"))
        self.assertIn("Report: failed (ValueError)", out_md.splitlines())
        self.assertEqual(pr.load_state(plan)["steps"]["S2"]["status"], pr.COMPLETED)
        with mock.patch.object(pr, "_render_report_text", side_effect=ValueError("bad")):
            payload = json.loads(_ok(self, pr.cmd_complete, _complete_args(plan, "S2")))
        self.assertEqual(payload["report_error"], "ValueError")
        self.assertNotIn("report_path", payload)


# ---------------------------------------------------------------------------
# (f) hook-stop never loads plan_report, even with an all_done plan attached
# ---------------------------------------------------------------------------

class HookStopAllDoneNoPlanReportTests(unittest.TestCase):
    def test_all_done_plan_hook_stop_does_not_import_plan_report(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = Path(tmp.name).resolve() / "home"
        proj = home / "proj"
        proj.mkdir(parents=True)
        (home / ".claude").mkdir()
        plan = proj / "plan.md"
        plan.write_text(PLAN_TEXT, encoding="utf-8")
        env = {**os.environ, "HOME": str(home)}

        def run(*argv):
            r = subprocess.run([sys.executable, str(RUNNER), *argv], cwd=str(proj),
                               capture_output=True, text=True, timeout=30, env=env)
            self.assertEqual(r.returncode, 0, msg=f"{argv}: {r.stdout} {r.stderr}")

        run("init", str(plan))
        for sid in ("S1", "S2"):
            run("start", str(plan), sid)
            run("complete", str(plan), sid)
        # precondition: the transition path really produced the report
        self.assertTrue(_expected_report_path(plan).exists())

        wrapper = (
            "import runpy, sys\n"
            f"sys.argv = [{str(RUNNER)!r}, 'hook-stop']\n"
            "try:\n"
            f"    runpy.run_path({str(RUNNER)!r}, run_name='__main__')\n"
            "except SystemExit:\n"
            "    pass\n"
            "print('PLAN_REPORT_LOADED=' + str('plan_report' in sys.modules))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", wrapper], cwd=str(proj),
            input=json.dumps({"hook_event_name": "Stop", "cwd": str(proj), "session_id": "s"}),
            capture_output=True, text=True, timeout=30, env=env,
        )
        # precondition: hook reached the all_done completion branch
        self.assertIn("全部 step 已完成", result.stdout, msg=result.stderr)
        self.assertIn("PLAN_REPORT_LOADED=False", result.stdout, msg=result.stderr)


if __name__ == "__main__":
    unittest.main()
