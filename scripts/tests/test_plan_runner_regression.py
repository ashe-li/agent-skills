"""Regression tests for scripts/plan_runner.py's 12 documented subcommands.

Purpose: prove the Stop-hook control-flow change (feat/plan-run-stop-hook-
control-flow) introduces zero regression in `plan_runner.py`'s existing CLI
surface: init / next / start / complete / fail / skip / status / index /
dag / normalize / set-parent / reset.

Constraints (S2.2 of plans/active/... plan):
- stdlib only (unittest + tempfile + subprocess).
- Read-only w.r.t. scripts/plan_runner.py, plans/, scripts/hooks/ — this
  file only adds new test/golden fixtures.
- All plan/state fixtures are synthesized inside tempfile.TemporaryDirectory().
- Never touches ~/.claude/ — every `init` call passes --no-attach, since
  the default --attach path writes a pointer file under
  ~/.claude/plan-run/active/ (see plan_runner.py's POINTER_ACTIVE_DIR).
"""

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAN_RUNNER = REPO_ROOT / "scripts" / "plan_runner.py"
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
GOLDEN_BASE_SCRIPT = GOLDEN_DIR / "plan_runner_base_e745670.py"

# 5 steps across 2 phases:
#   S1 -> S2 -> {S3, S4}   S3 -> S5
# gives us: a single-unlock wave (S1 -> S2), a two-way fan-out unlock
# (S2 -> S3,S4), and a step (S5) that can be driven blocked/unblocked by
# failing/resetting its dependency (S3).
PLAN_TEXT = """# Regression Test Plan

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
  - Dependencies: S2
  - Files: `c.py`
  - Action: do C

- [ ] S4 Fourth step
  - Dependencies: S2
  - Files: `d.py`
  - Action: do D

- [ ] S5 Fifth step
  - Dependencies: S3
  - Files: `e.py`
  - Action: do E
"""

# Minimal planner-agent-format snippet (pre-canonical) for the normalize
# idempotency check.
PLANNER_AGENT_TEXT = """# Normalize Idempotency Fixture

### Phase 1: Setup

**Step 1: First step**
- **Files**: `a.py`
- **Action**: do A
- **Dependencies**: none
"""


def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run plan_runner.py with args, capturing text stdout/stderr."""
    return subprocess.run(
        [sys.executable, str(PLAN_RUNNER), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )


def load_module_from_path(path: Path, module_name: str):
    """Import a standalone script (not a package member) as a module, so
    we can call its pure functions directly (e.g. transition_step) without
    going through argparse/sys.exit."""
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class PlanRunnerRegressionTestCase(unittest.TestCase):
    """Shared fixture: a fresh temp dir + synthesized plan per test."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)
        self.plan_path = self.tmp_path / "regression-plan.md"
        self.plan_path.write_text(PLAN_TEXT, encoding="utf-8")

    def init_plan(self) -> subprocess.CompletedProcess:
        r = run_cli("init", str(self.plan_path), "--no-attach")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertNotIn("Attached:", r.stdout, "must not attach a pointer")
        return r

    def _progress_and_counts_lines(self, stdout: str) -> tuple[str, str]:
        """Locate the embedded state-view block's 'Progress: N/M' line and
        the counts line immediately after it (e.g. 'pending:3 | completed:1').
        Contract enforced by _format_state_view_lines()."""
        lines = stdout.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("Progress:"):
                # The next non-empty line is the by-status counts line
                # only when there IS at least one non-zero status besides
                # what's already reflected in Progress; guard for absence.
                if i + 1 < len(lines) and lines[i + 1].strip():
                    return line, lines[i + 1]
                return line, ""
        self.fail(f"no 'Progress:' line found in stdout:\n{stdout}")

    # -- init -----------------------------------------------------------

    def test_init_basic(self) -> None:
        r = self.init_plan()
        self.assertIn("Steps: 5 across 2 phases", r.stdout)
        self.assertIn("Ready now: S1", r.stdout)

    def test_init_golden_byte_identical_to_base_e745670(self) -> None:
        """Assertion 4: `init --no-attach` output must match base commit
        e745670's plain `init` output verbatim, modulo the tempdir-specific
        absolute path embedded in the 'State:' line."""
        self.assertTrue(
            GOLDEN_BASE_SCRIPT.exists(),
            f"missing golden base script: {GOLDEN_BASE_SCRIPT}",
        )

        base_dir = self.tmp_path / "base"
        base_dir.mkdir()
        base_plan = base_dir / "regression-plan.md"
        base_plan.write_text(PLAN_TEXT, encoding="utf-8")

        # Base commit predates --attach/--no-attach entirely — plain init.
        base_result = subprocess.run(
            [sys.executable, str(GOLDEN_BASE_SCRIPT), "init", str(base_plan)],
            capture_output=True, text=True,
        )
        self.assertEqual(base_result.returncode, 0, msg=base_result.stderr)

        current_dir = self.tmp_path / "current"
        current_dir.mkdir()
        current_plan = current_dir / "regression-plan.md"
        current_plan.write_text(PLAN_TEXT, encoding="utf-8")
        current_result = run_cli("init", str(current_plan), "--no-attach")
        self.assertEqual(current_result.returncode, 0, msg=current_result.stderr)

        # Normalize away the only expected diff: the tempdir-specific
        # absolute path baked into the 'State: <path>' line.
        base_norm = base_result.stdout.replace(str(base_dir), "<TMP>")
        current_norm = current_result.stdout.replace(str(current_dir), "<TMP>")
        self.assertEqual(
            base_norm, current_norm,
            "init output diverged from base commit e745670 (path-normalized)",
        )

    # -- next -------------------------------------------------------------

    def test_next_shows_newly_unlocked_ready_step(self) -> None:
        self.init_plan()
        r = run_cli("next", str(self.plan_path))
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("Progress: 0/5", r.stdout)
        self.assertIn("## Newly unlocked (1)", r.stdout)
        self.assertIn("S1", r.stdout)

    # -- complete without task_id (assertion 1) ----------------------------

    def test_complete_without_task_id_omits_required_sync(self) -> None:
        self.init_plan()
        r = run_cli("start", str(self.plan_path), "S1")
        self.assertEqual(r.returncode, 0, msg=r.stderr)

        r = run_cli("complete", str(self.plan_path), "S1")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertNotIn("## Required sync", r.stdout)
        self.assertNotIn("TaskUpdate(", r.stdout)

        # Assertion 3: embedded state-view block still has 'Progress: N/M'
        # immediately followed by a 'pending:X | completed:Y'-shaped line.
        progress_line, counts_line = self._progress_and_counts_lines(r.stdout)
        self.assertRegex(progress_line, r"^Progress: \d+/5")
        self.assertRegex(counts_line, r"pending:\d+")
        self.assertRegex(counts_line, r"completed:\d+")

    # -- complete with task_id (assertion 2) -------------------------------

    def test_complete_with_task_id_includes_required_sync(self) -> None:
        self.init_plan()
        run_cli("start", str(self.plan_path), "S1")
        run_cli("complete", str(self.plan_path), "S1")

        r = run_cli("start", str(self.plan_path), "S2", "--task-id", "T2")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        # Sliding-window next-hint block on start when a downstream step
        # would unblock.
        self.assertIn("## Next hints", r.stdout)

        r = run_cli("complete", str(self.plan_path), "S2")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("## Required sync", r.stdout)
        self.assertIn("TaskUpdate(", r.stdout)
        self.assertIn("T2", r.stdout)
        # S2 completing unlocks both S3 and S4 in one wave.
        self.assertIn("## Newly unlocked (2)", r.stdout)

    # -- illegal transition rejected (assertion 5) -------------------------

    def test_illegal_transition_completed_to_pending_rejected_at_function_level(
        self,
    ) -> None:
        """Direct check on transition_step(): completed -> pending must
        raise ValueError. VALID_TRANSITIONS[COMPLETED] == {COMPLETED} only."""
        module = load_module_from_path(PLAN_RUNNER, "plan_runner_under_test")
        parsed = module.parse_plan(self.plan_path)
        state = module.init_state(self.plan_path, parsed)
        module.transition_step(state, "S1", module.IN_PROGRESS)
        module.transition_step(state, "S1", module.COMPLETED)
        with self.assertRaises(ValueError):
            module.transition_step(state, "S1", module.PENDING)

    def test_illegal_transition_rejected_via_cli(self) -> None:
        """CLI-level companion: re-`start`-ing an already-completed step
        attempts completed -> in_progress, also outside VALID_TRANSITIONS,
        and must be rejected (non-zero exit + explicit error), never
        silently accepted."""
        self.init_plan()
        run_cli("start", str(self.plan_path), "S1")
        r = run_cli("complete", str(self.plan_path), "S1")
        self.assertEqual(r.returncode, 0, msg=r.stderr)

        r = run_cli("start", str(self.plan_path), "S1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("error", r.stdout.lower())

    # -- normalize idempotent (assertion 6) ---------------------------------

    def test_normalize_idempotent(self) -> None:
        planner_plan = self.tmp_path / "planner-agent-plan.md"
        planner_plan.write_text(PLANNER_AGENT_TEXT, encoding="utf-8")

        first = run_cli("normalize", str(planner_plan), "--write")
        self.assertEqual(first.returncode, 0, msg=first.stderr)

        second = run_cli("normalize", str(planner_plan), "--diff")
        self.assertEqual(second.returncode, 0, msg=second.stderr)
        self.assertEqual(
            second.stdout, "",
            f"second normalize --diff must be empty (idempotent); got:\n{second.stdout}",
        )

    # -- fail / skip / status / index / dag / set-parent / reset -----------
    # (assertion 7: exit 0 + expected markdown blocks for the remaining
    # subcommands not already covered above.)

    def _advance_to_s3_s4_ready(self) -> None:
        self.init_plan()
        run_cli("start", str(self.plan_path), "S1")
        run_cli("complete", str(self.plan_path), "S1")
        run_cli("start", str(self.plan_path), "S2")
        run_cli("complete", str(self.plan_path), "S2")

    def test_fail_marks_step_and_blocks_dependent(self) -> None:
        self._advance_to_s3_s4_ready()
        r = run_cli("start", str(self.plan_path), "S3")
        self.assertEqual(r.returncode, 0, msg=r.stderr)

        r = run_cli("fail", str(self.plan_path), "S3", "--reason", "boom")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("# failed: S3", r.stdout)
        self.assertIn("Reason: boom", r.stdout)
        # S5 depends on S3 -> now blocked.
        self.assertIn("## Blocked (1)", r.stdout)
        self.assertIn("S5", r.stdout)

    def test_skip_marks_step_completed_equivalent(self) -> None:
        self._advance_to_s3_s4_ready()
        r = run_cli("skip", str(self.plan_path), "S4")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("# skipped: S4", r.stdout)

    def test_status_shows_phases_and_step_icons(self) -> None:
        self._advance_to_s3_s4_ready()
        run_cli("start", str(self.plan_path), "S3")
        run_cli("fail", str(self.plan_path), "S3", "--reason", "boom")
        run_cli("skip", str(self.plan_path), "S4")

        r = run_cli("status", str(self.plan_path))
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("## Phase 1: Setup", r.stdout)
        self.assertIn("## Phase 2: Build", r.stdout)
        self.assertIn("[!] S3", r.stdout)  # failed
        self.assertIn("[-] S4", r.stdout)  # skipped
        self.assertIn("[B] S5", r.stdout)  # blocked (dep S3 failed)

    def test_index_ultra_compact_view(self) -> None:
        self._advance_to_s3_s4_ready()
        r = run_cli("index", str(self.plan_path))
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("Progress: 2/5", r.stdout)
        self.assertIn("S1", r.stdout)
        self.assertIn("S5", r.stdout)

    def test_dag_text_format(self) -> None:
        self._advance_to_s3_s4_ready()
        run_cli("start", str(self.plan_path), "S3")
        run_cli("fail", str(self.plan_path), "S3", "--reason", "boom")
        run_cli("skip", str(self.plan_path), "S4")

        r = run_cli("dag", str(self.plan_path))
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("Phase 1: Setup", r.stdout)
        self.assertIn("Phase 2: Build", r.stdout)
        # done = completed(S1,S2) + skipped(S4) = 3/5
        self.assertIn("Progress: 3/5", r.stdout.splitlines()[-1])

    def test_set_parent_json_output(self) -> None:
        self.init_plan()
        r = run_cli("set-parent", str(self.plan_path), "--task-id", "PARENT1")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["status"], "set_parent")
        self.assertEqual(payload["parent_task_id"], "PARENT1")

    def test_reset_step_unblocks_dependents(self) -> None:
        self._advance_to_s3_s4_ready()
        run_cli("start", str(self.plan_path), "S3")
        run_cli("fail", str(self.plan_path), "S3", "--reason", "boom")

        # Sanity: S5 is blocked before reset.
        status_before = run_cli("status", str(self.plan_path))
        self.assertIn("[B] S5", status_before.stdout)

        r = run_cli("reset", str(self.plan_path), "--step", "S3")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["status"], "reset")

        status_after = run_cli("status", str(self.plan_path))
        self.assertIn("[ ] S3", status_after.stdout)
        self.assertIn("[ ] S5", status_after.stdout)  # unblocked back to pending


if __name__ == "__main__":
    unittest.main()
