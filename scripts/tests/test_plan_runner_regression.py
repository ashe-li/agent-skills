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
import os
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


def run_cli(
    *args: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run plan_runner.py with args, capturing text stdout/stderr.

    `env` exists so attach-path tests can point $HOME at a temp dir —
    plan_runner resolves POINTER_ACTIVE_DIR from Path.home() at import
    time, so a child process with a faked $HOME writes its pointer there
    instead of into the real ~/.claude/plan-run/active/.
    """
    return subprocess.run(
        [sys.executable, str(PLAN_RUNNER), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        env=env,
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
        # S2.6: attach's one-line `Attached: <pointer>` became three lines
        # (Plan:/Cwd:/Pointer:), so assert on the new markers instead — the
        # old string can no longer appear regardless of --no-attach.
        self.assertNotIn("Pointer:", r.stdout, "must not attach a pointer")
        self.assertNotIn("Cwd:", r.stdout, "must not attach a pointer")
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


class InitAttachStreamTestCase(unittest.TestCase):
    """`init --format json` must leave stdout as a single parseable JSON
    document even when the default attach path runs.

    Regression source: CodeRabbit on PR #67 — cmd_init emitted the JSON
    payload and then printed the attach result (or its error) to stdout,
    so a JSON consumer got trailing non-JSON text. Docs were changed to
    say "always pass --no-attach in JSON mode"; these tests pin the runner
    side so the default path is safe too.

    Isolation: every case runs with $HOME redirected to a fresh temp dir,
    so the pointer file lands under <tmp>/.claude/plan-run/active/ and the
    real user HOME (and this repo) is never written to. Both the plan and
    the cwd live under that fake HOME because _is_within_allowed_root()
    refuses to attach a plan outside $HOME.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # resolve(): on macOS the temp dir is reached through the /var ->
        # /private/var symlink, and cmd_init resolve()s plan_path before
        # comparing it against Path.home(). Resolving here makes both
        # sides of _is_within_allowed_root() the same real path.
        self.home = Path(self._tmp.name).resolve()
        # _ensure_pointer_active_dir() mkdirs ~/.claude/plan-run without
        # parents=True — on a real HOME ~/.claude always exists already.
        (self.home / ".claude").mkdir()
        self.workdir = self.home / "work"
        self.workdir.mkdir()
        self.plan_path = self.home / "attach-stream-plan.md"
        self.plan_path.write_text(PLAN_TEXT, encoding="utf-8")
        self.env = {**os.environ, "HOME": str(self.home)}

    def _init(self, *extra: str) -> subprocess.CompletedProcess:
        return run_cli(
            "init", str(self.plan_path), *extra, cwd=self.workdir, env=self.env,
        )

    def _assert_pointer_under_fake_home(self) -> None:
        active_dir = self.home / ".claude" / "plan-run" / "active"
        pointers = list(active_dir.glob("*.json")) if active_dir.is_dir() else []
        self.assertTrue(
            pointers, f"expected a pointer file under {active_dir}, found none",
        )

    def test_json_mode_with_attach_keeps_stdout_pure_json(self) -> None:
        r = self._init("--format", "json")
        self.assertEqual(r.returncode, 0, msg=r.stderr)

        payload = json.loads(r.stdout)  # fails loudly if attach text leaked
        self.assertEqual(payload["status"], "initialized")
        self.assertEqual(payload["total_steps"], 5)

        # The human-facing attach lines moved to stderr, and are still emitted.
        self.assertIn("Pointer:", r.stderr)
        self.assertIn(f"Plan: {self.plan_path}", r.stderr)
        self.assertNotIn("Pointer:", r.stdout)
        self._assert_pointer_under_fake_home()

    def test_json_mode_with_no_attach_writes_nothing_to_stderr(self) -> None:
        r = self._init("--format", "json", "--no-attach")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        json.loads(r.stdout)
        self.assertEqual(r.stderr, "")

    def test_md_mode_attach_lines_stay_on_stdout(self) -> None:
        """md mode is unchanged: attach output still goes to stdout, and
        stderr stays empty."""
        r = self._init()
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("Pointer:", r.stdout)
        self.assertIn(f"Cwd: {self.workdir}", r.stdout)
        self.assertEqual(r.stderr, "")
        self._assert_pointer_under_fake_home()

    def test_json_mode_attach_conflict_error_goes_to_stderr(self) -> None:
        """The failure branch of attach (cwd already bound to a different
        plan) printed to stdout too — it must follow the payload's stream
        rule as well, and stdout must still parse."""
        other_plan = self.home / "other-plan.md"
        other_plan.write_text(PLAN_TEXT, encoding="utf-8")
        first = self._init("--format", "json")
        self.assertEqual(first.returncode, 0, msg=first.stderr)

        conflict = run_cli(
            "init", str(other_plan), "--format", "json",
            cwd=self.workdir, env=self.env,
        )
        payload = json.loads(conflict.stdout)
        self.assertEqual(payload["status"], "initialized")
        self.assertNotEqual(conflict.stderr.strip(), "", "conflict must be reported")
        self.assertNotIn("Pointer:", conflict.stdout)


# ---------------------------------------------------------------------------
# Non-step sections must not register steps; duplicate step ids fail init.
#
# Root cause (KB learned `plan-runner-step-regex-matches-any-checkbox-with-
# step-id`, recurred 2026-06-26 / 07-16 / 07-27 / 10-14 / 10-22 / 10-27):
# step_re ran on every line, so `- [ ] S0.6 ...` under `## Acceptance
# Criteria` re-registered S0.6 and overwrote the real step's deps.
# ---------------------------------------------------------------------------

ACCEPTANCE_GOLDEN_PLAN = GOLDEN_DIR / "plan_acceptance_checkbox_step_id.md"
ACCEPTANCE_GOLDEN_EXPECTED = GOLDEN_DIR / "plan_acceptance_checkbox_step_id.expected.json"


def _plan_with_tail(heading: str, tail: str) -> str:
    """Two real steps (S2 depends on S1), then a `## <heading>` section."""
    return (
        "# Section Plan\n\n"
        "## Implementation Steps\n\n"
        "### Phase 1: Build\n\n"
        "- [ ] **S1** — First\n"
        "  - Action: do A\n\n"
        "- [ ] **S2** — Second\n"
        "  - Dependencies: S1\n"
        "  - Action: do B\n\n"
        f"## {heading}\n\n"
        f"{tail}\n"
    )


class NonStepSectionParsingTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)
        self.mod = load_module_from_path(PLAN_RUNNER, "plan_runner_under_test")

    def _write(self, text: str, name: str = "section-plan.md") -> Path:
        path = self.tmp_path / name
        path.write_text(text, encoding="utf-8")
        return path

    def _state(self, plan: Path) -> dict:
        return json.loads(
            (plan.parent / ".plan-state" / f"{plan.stem}.state.json").read_text("utf-8")
        )

    # (a) Acceptance-style checkbox with a step id is not a step ---------

    def test_acceptance_checkbox_does_not_create_or_overwrite_step(self) -> None:
        plan = self._write(
            _plan_with_tail("Acceptance Criteria", "- [ ] S2 寫完裁決，附證據路徑\n- [ ] S1：報告")
        )
        parsed = self.mod.parse_plan(plan)
        self.assertEqual(list(parsed["steps"]), ["S1", "S2"])
        self.assertEqual(parsed["steps"]["S2"]["deps"], ["S1"])
        self.assertEqual(parsed["steps"]["S2"]["title"], "Second")
        self.assertEqual(parsed["warnings"], [])

        r = run_cli("init", str(plan), "--no-attach")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("Ready now: S1", r.stdout)
        self.assertNotIn("S2", r.stdout.split("Ready now:", 1)[1].splitlines()[0])
        self.assertNotIn("Duplicate", r.stdout + r.stderr)
        self.assertEqual(self._state(plan)["steps"]["S2"]["deps"], ["S1"])

    def test_blacklisted_headings_cover_zh_en_and_case(self) -> None:
        headings = [
            "Acceptance Criteria", "acceptance criteria", "ACCEPTANCE CRITERIA",
            "驗收條件", "驗收", "Review Notes", "review notes",
            "Risks & Mitigations", "Risks", "風險", "風險與對策",
            "2. Acceptance Criteria",
        ]
        for heading in headings:
            with self.subTest(heading=heading):
                plan = self._write(_plan_with_tail(heading, "- [ ] S2 不是 step"))
                parsed = self.mod.parse_plan(plan)
                self.assertEqual(list(parsed["steps"]), ["S1", "S2"])
                self.assertEqual(parsed["steps"]["S2"]["deps"], ["S1"])
                self.assertEqual(parsed["warnings"], [])

    def test_skip_ends_at_next_level2_heading(self) -> None:
        """A step list after a blacklisted section resumes parsing."""
        text = _plan_with_tail(
            "Acceptance Criteria", "- [ ] S2 不是 step\n\n## Follow-up Steps\n\n"
            "- [ ] **S3** — Third\n  - Dependencies: S2\n  - Action: do C",
        )
        parsed = self.mod.parse_plan(self._write(text))
        self.assertEqual(list(parsed["steps"]), ["S1", "S2", "S3"])
        self.assertEqual(parsed["steps"]["S3"]["deps"], ["S2"])
        self.assertEqual(parsed["steps"]["S2"]["deps"], ["S1"])

    def test_real_fixture_acceptance_lines_keep_deps(self) -> None:
        """Trimmed from the 2026-10-27 plan that hit this bug live."""
        expected = json.loads(ACCEPTANCE_GOLDEN_EXPECTED.read_text("utf-8"))
        plan = self._write(
            ACCEPTANCE_GOLDEN_PLAN.read_text("utf-8"), "sw-fallback-golden.md"
        )
        parsed = self.mod.parse_plan(plan)
        self.assertEqual(
            {sid: s["deps"] for sid, s in parsed["steps"].items()}, expected["steps"]
        )
        self.assertEqual(parsed["warnings"], [])

        r = run_cli("init", str(plan), "--no-attach", "--format", "json")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["ready_steps"], expected["ready"])
        self.assertEqual(payload["total_steps"], len(expected["steps"]))

    def test_step_section_with_blacklisted_word_in_heading_is_not_swallowed(self) -> None:
        """PR 91 review: headings that start with a blacklisted word but hold
        real steps (step id + field lines) must still parse, with a warning."""
        headings = [
            "Risk-based rollout steps", "Risk-first Implementation Steps",
            "風險評估步驟", "Acceptance-driven steps",
        ]
        for heading in headings:
            with self.subTest(heading=heading):
                text = (
                    "# P\n\n## Implementation Steps\n\n### Phase 1: Build\n\n"
                    "- [ ] **S1** — First\n  - Action: do A\n\n"
                    f"## {heading}\n\n"
                    "- [ ] **S2** — Second\n  - Dependencies: S1\n  - Action: do B\n"
                    "    continued action line\n\n"
                    "- [ ] **S3** — Third\n  - Dependencies: S2\n  - Action: do C\n"
                )
                parsed = self.mod.parse_plan(self._write(text, "risky-heading.md"))
                self.assertEqual(list(parsed["steps"]), ["S1", "S2", "S3"])
                self.assertEqual(parsed["steps"]["S2"]["deps"], ["S1"])
                self.assertEqual(parsed["steps"]["S3"]["deps"], ["S2"])
                self.assertIn("do B continued action line", parsed["steps"]["S2"]["action"])
                hits = [w for w in parsed["warnings"] if "看起來像非 step 的區段" in w]
                self.assertEqual(len(hits), 2, parsed["warnings"])
                self.assertIn(f"標題 {heading}", hits[0])
                self.assertIn("行號 12", hits[0])
                plan = self.tmp_path / "risky-heading.md"
                r = run_cli("init", str(plan), "--no-attach", "--force")
                self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
                self.assertIn("Steps: 3", r.stdout)

    def test_acceptance_item_without_fields_ignored_even_before_blank_and_prose(self) -> None:
        tail = "- [ ] S2 沒有欄位\n\n補充說明文字\n- [ ] S1：另一項\n  備註"
        parsed = self.mod.parse_plan(self._write(_plan_with_tail("Acceptance Criteria", tail)))
        self.assertEqual(list(parsed["steps"]), ["S1", "S2"])
        self.assertEqual(parsed["steps"]["S2"]["deps"], ["S1"])
        self.assertEqual(parsed["warnings"], [])

    def test_step_with_fields_in_acceptance_colliding_with_real_id_fails_init(self) -> None:
        tail = "- [ ] **S2** — 撞號\n  - Action: 假的"
        plan = self._write(_plan_with_tail("Acceptance Criteria", tail), "collide.md")
        r = run_cli("init", str(plan), "--no-attach")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Duplicate step id: S2", r.stdout)

    # (b) A real duplicate id inside Implementation Steps fails init -----

    def test_real_duplicate_id_fails_init_without_state(self) -> None:
        text = (
            "# Dup Plan\n\n## Implementation Steps\n\n### Phase 1: Build\n\n"
            "- [ ] **S1** — First\n  - Action: do A\n\n"
            "- [ ] **S2** — Second\n  - Dependencies: S1\n  - Action: do B\n\n"
            "- [ ] **S2** — Second again\n  - Action: do B2\n"
        )
        plan = self._write(text, "dup-plan.md")
        r = run_cli("init", str(plan), "--no-attach")
        self.assertNotEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        out = r.stdout + r.stderr
        self.assertIn("S2", out)
        self.assertIn("line 10 (kept)", out)  # first definition
        self.assertIn("line 14 (duplicate)", out)
        self.assertIn("驗收項不要以 step id 開頭", out)
        self.assertFalse((self.tmp_path / ".plan-state").exists(), "must not create state")

    def test_duplicate_keeps_first_definition_in_parse(self) -> None:
        text = (
            "# Dup Plan\n\n### Phase 1: Build\n\n"
            "- [ ] **S1** — First\n  - Action: do A\n\n"
            "- [ ] **S2** — Second\n  - Dependencies: S1\n  - Action: do B\n\n"
            "- [ ] **S2** — Second again\n  - Action: do B2\n"
        )
        parsed = self.mod.parse_plan(self._write(text, "dup-first.md"))
        self.assertEqual(parsed["steps"]["S2"]["title"], "Second")
        self.assertEqual(parsed["steps"]["S2"]["deps"], ["S1"])
        self.assertEqual(parsed["steps"]["S2"]["action"], "do B")
        self.assertEqual([d["id"] for d in parsed["duplicate_steps"]], ["S2"])

    def test_force_reinit_also_fails_on_duplicate_and_keeps_old_state(self) -> None:
        good = self._write(_plan_with_tail("Overview", "text"), "reinit-plan.md")
        self.assertEqual(run_cli("init", str(good), "--no-attach").returncode, 0)
        before = (self.tmp_path / ".plan-state" / "reinit-plan.state.json").read_text("utf-8")
        good.write_text(
            good.read_text("utf-8") + "\n- [ ] **S2** — Dup\n", encoding="utf-8"
        )
        r = run_cli("init", str(good), "--no-attach", "--force")
        self.assertNotEqual(r.returncode, 0)
        after = (self.tmp_path / ".plan-state" / "reinit-plan.state.json").read_text("utf-8")
        self.assertEqual(before, after)

    def test_commands_on_existing_state_unaffected_by_duplicate_plan(self) -> None:
        """Only `init` rejects duplicates; next/status read the state."""
        plan = self._write(_plan_with_tail("Overview", "text"), "existing-plan.md")
        self.assertEqual(run_cli("init", str(plan), "--no-attach").returncode, 0)
        plan.write_text(plan.read_text("utf-8") + "\n- [ ] **S2** — Dup\n", "utf-8")
        for cmd in ("next", "status"):
            r = run_cli(cmd, str(plan))
            self.assertEqual(r.returncode, 0, msg=f"{cmd}: {r.stdout}{r.stderr}")

    # (c) Non-blacklisted sections behave as before ----------------------

    def test_checkbox_without_step_id_under_phase_heading_is_ignored(self) -> None:
        text = (
            "# Phase Plan\n\n### Phase 1: Build\n\n"
            "- [ ] **S1** — First\n  - Action: do A\n\n"
            "### Phase 2: 品質保障\n\n"
            "- [ ] 跑一次 lint，沒有 step id\n"
            "- [ ] 另一個普通待辦\n\n"
            "- [ ] **S2** — Second\n  - Dependencies: S1\n  - Action: do B\n"
        )
        parsed = self.mod.parse_plan(self._write(text, "phase-plan.md"))
        self.assertEqual(list(parsed["steps"]), ["S1", "S2"])
        self.assertEqual(parsed["steps"]["S2"]["phase"], "Phase 2: 品質保障")
        self.assertEqual(parsed["steps"]["S2"]["deps"], ["S1"])
        self.assertEqual(parsed["warnings"], [])

    def test_steps_directly_under_phase_without_implementation_steps_heading(self) -> None:
        text = (
            "# Flat Plan\n\n## Overview\n\ntext\n\n### Phase 1: Build\n\n"
            "- [ ] **S1** — First\n  - Action: do A\n"
        )
        parsed = self.mod.parse_plan(self._write(text, "flat-plan.md"))
        self.assertEqual(list(parsed["steps"]), ["S1"])

    def test_non_blacklisted_level2_heading_still_parses_steps(self) -> None:
        for heading in ("Implementation Steps", "Rollout Steps", "Phase 3 收尾", "Verification"):
            with self.subTest(heading=heading):
                text = f"# P\n\n## {heading}\n\n- [ ] **S1** — First\n  - Action: do A\n"
                parsed = self.mod.parse_plan(self._write(text, "other-plan.md"))
                self.assertEqual(list(parsed["steps"]), ["S1"])

    def test_repo_plans_step_counts_unchanged(self) -> None:
        """Existing plans in this repo keep their step count (12/7/19) and
        stay warning-free."""
        expected = {
            "ecc-decoupling-and-model-adaptation.md": 12,
            "knowledge-base-quality-optimization.md": 7,
            "plan-run-stop-hook-control-flow.md": 19,
        }
        for name, count in expected.items():
            with self.subTest(plan=name):
                parsed = self.mod.parse_plan(REPO_ROOT / "plans" / "active" / name)
                self.assertEqual(len(parsed["steps"]), count)
                self.assertEqual(parsed["warnings"], [])


if __name__ == "__main__":
    unittest.main()
