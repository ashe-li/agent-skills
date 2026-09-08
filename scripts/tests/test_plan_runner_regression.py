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

import hashlib
import importlib.util
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
    *args: str, cwd: Path | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """Run plan_runner.py with args, capturing text stdout/stderr.

    `env=None` (the default) inherits the parent process environment,
    identical to every pre-existing call site. Passing an explicit `env`
    (e.g. a copy of os.environ with HOME redirected to a tempdir) is how
    RecapCliTestCase isolates pointer lookups from the real
    ~/.claude/plan-run/active/ — same isolation goal as this file's header
    comment ("Never touches ~/.claude/"), applied to a command that reads
    the pointer registry rather than writing it via `--attach`.
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


# ---------------------------------------------------------------------------
# S2.1 — plan.md SHA-256 fingerprint & drift detection
# ---------------------------------------------------------------------------

# A real-plan stand-in in canonical plan_runner format, including the two
# checkbox shapes that get ticked during a run: step lines (parsed into
# state) and acceptance-criteria lines (never parsed, still ticked).
FINGERPRINT_PLAN_TEXT = """# Fingerprint Fixture

> Status: IN PROGRESS

### Phase 1: Setup

- [ ] **S1** — first step
  - Files: `a.py`
  - Action: do A

- [ ] **S2** — second step
  - Dependencies: S1
  - Files: `b.py`
  - Action: do B

## Acceptance Criteria

- [ ] AC1 — something is true
"""


class PointerStalenessTestCase(unittest.TestCase):
    """S3.4: `_is_pointer_stale()` reads `_pointer_progress_timestamp()`
    (last_advance_at, falling back to created_at), the same field
    decide_budget()'s wall-clock rule reads. Before S3.4 gave that field a
    writer, last_advance_at never moved, so this function was really
    testing "how long ago did the pointer attach" -- an old-but-busy
    pointer and an old-and-abandoned one were indistinguishable, both
    judged purely by `created_at`. This pins the behaviour the fix
    restores: a pointer whose last_advance_at keeps getting refreshed by
    real progress is never stale, no matter how old `created_at` is.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_module_from_path(PLAN_RUNNER, "plan_runner_pointer_staleness")

    def iso_before_now(self, seconds: float) -> str:
        return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()

    def test_old_created_at_with_no_advance_is_stale(self):
        """Baseline: an ancestor pointer that never advanced is exactly
        the case _is_pointer_stale() exists to catch."""
        pointer = {
            "created_at": self.iso_before_now(48 * 3600),
            "last_advance_at": None,
        }
        self.assertTrue(self.mod._is_pointer_stale(pointer))

    def test_old_created_at_but_recent_advance_is_not_stale(self):
        """The bug this test pins: created_at is 48h old (well past
        POINTER_STALE_SECONDS = 24h), but last_advance_at was refreshed 5
        minutes ago by real progress -- the pointer is actively driving a
        plan and must not be treated as an abandoned ancestor."""
        pointer = {
            "created_at": self.iso_before_now(48 * 3600),
            "last_advance_at": self.iso_before_now(5 * 60),
        }
        self.assertFalse(self.mod._is_pointer_stale(pointer))

    def test_continuously_advancing_pointer_never_goes_stale(self):
        """The end-to-end shape: simulate a pointer that started over 24h
        ago and has had a fresh last_advance_at written every ~40 minutes
        since (well under CHECKPOINT_STALE_SECONDS = 45m) -- at no point
        should it read as stale, because each write is well inside the
        24h window measured from *that* write, not from created_at."""
        created = self.iso_before_now(30 * 3600)
        for minutes_ago in (35, 25, 15, 5):
            pointer = {
                "created_at": created,
                "last_advance_at": self.iso_before_now(minutes_ago * 60),
            }
            with self.subTest(minutes_ago=minutes_ago):
                self.assertFalse(self.mod._is_pointer_stale(pointer))


class PlanFingerprintTestCase(unittest.TestCase):
    """plan_fingerprint() is a pure function -- exercise it directly."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_module_from_path(PLAN_RUNNER, "plan_runner_fingerprint")

    def fp(self, text: str) -> str:
        return self.mod.plan_fingerprint(text)

    def test_returns_stable_sha256_hexdigest(self) -> None:
        digest = self.fp(FINGERPRINT_PLAN_TEXT)
        self.assertEqual(len(digest), 64)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertEqual(digest, self.fp(FINGERPRINT_PLAN_TEXT))

    def test_step_checkbox_toggle_is_ignored(self) -> None:
        """R2 core: ticking a step must not change the fingerprint."""
        ticked = FINGERPRINT_PLAN_TEXT.replace("- [ ] **S1**", "- [x] **S1**")
        self.assertNotEqual(ticked, FINGERPRINT_PLAN_TEXT)
        self.assertEqual(self.fp(ticked), self.fp(FINGERPRINT_PLAN_TEXT))

    def test_acceptance_criteria_checkbox_toggle_is_ignored(self) -> None:
        """AC lines never reach state, so ticking one must not drift either."""
        ticked = FINGERPRINT_PLAN_TEXT.replace("- [ ] AC1", "- [x] AC1")
        self.assertEqual(self.fp(ticked), self.fp(FINGERPRINT_PLAN_TEXT))

    def test_all_checkboxes_ticked_is_ignored(self) -> None:
        ticked = FINGERPRINT_PLAN_TEXT.replace("- [ ]", "- [x]")
        self.assertEqual(self.fp(ticked), self.fp(FINGERPRINT_PLAN_TEXT))

    def test_trailing_whitespace_is_ignored(self) -> None:
        noisy = "\n".join(
            line + "   \t" for line in FINGERPRINT_PLAN_TEXT.splitlines()
        )
        self.assertEqual(self.fp(noisy), self.fp(FINGERPRINT_PLAN_TEXT))

    def test_line_endings_and_final_newline_are_ignored(self) -> None:
        crlf = FINGERPRINT_PLAN_TEXT.replace("\n", "\r\n")
        self.assertEqual(self.fp(crlf), self.fp(FINGERPRINT_PLAN_TEXT))
        self.assertEqual(
            self.fp(FINGERPRINT_PLAN_TEXT.rstrip("\n")),
            self.fp(FINGERPRINT_PLAN_TEXT),
        )
        self.assertEqual(
            self.fp(FINGERPRINT_PLAN_TEXT + "\n\n\n"),
            self.fp(FINGERPRINT_PLAN_TEXT),
        )

    def test_content_change_is_detected(self) -> None:
        for changed in (
            FINGERPRINT_PLAN_TEXT.replace("Files: `a.py`", "Files: `zzz.py`"),
            FINGERPRINT_PLAN_TEXT.replace("Dependencies: S1", "Dependencies: "),
            FINGERPRINT_PLAN_TEXT.replace("first step", "FIRST STEP"),
            FINGERPRINT_PLAN_TEXT + "\n- [ ] **S3** — sneaked in\n",
        ):
            with self.subTest(changed=changed[:40]):
                self.assertNotEqual(
                    self.fp(changed), self.fp(FINGERPRINT_PLAN_TEXT)
                )

    def test_status_metadata_line_change_is_detected(self) -> None:
        """Measured on 216 real KB plans: every `Status:` rewrite carried
        semantic scope info, so it must NOT be normalized away."""
        changed = FINGERPRINT_PLAN_TEXT.replace(
            "> Status: IN PROGRESS", "> Status: BLOCKED on backend"
        )
        self.assertNotEqual(self.fp(changed), self.fp(FINGERPRINT_PLAN_TEXT))

    def test_non_parser_checkbox_markers_are_a_real_change(self) -> None:
        """parse_plan()'s step_re accepts only `[ ]` and `[x]`. Turning a
        step into `[~]` removes it from the graph -- that is drift, not a
        cosmetic tick."""
        base = self.fp(FINGERPRINT_PLAN_TEXT)
        for marker in ("[~]", "[X]", "[-]", "[\u23f3]"):
            with self.subTest(marker=marker):
                changed = FINGERPRINT_PLAN_TEXT.replace(
                    "- [ ] **S1**", f"- {marker} **S1**"
                )
                self.assertNotEqual(self.fp(changed), base)

    def test_ac2_real_plan_file_checkbox_toggle_does_not_drift(self) -> None:
        """AC2, verified against the repo's own real plan file (not a
        fixture): ticking every unchecked box changes the raw SHA-256 but
        must leave the fingerprint identical."""
        real_plan = REPO_ROOT / "plans" / "active" / "unattended-long-run-governance.md"
        if not real_plan.exists():  # pragma: no cover - repo layout guard
            self.skipTest(f"real plan not present: {real_plan}")
        original = real_plan.read_text(encoding="utf-8")
        self.assertIn("- [ ] **S", original, "fixture plan has no step boxes")

        ticked = re.sub(r"^(\s*[-*+] )\[ \]", r"\1[x]", original, flags=re.M)
        self.assertNotEqual(ticked, original)

        raw = lambda s: hashlib.sha256(s.encode("utf-8")).hexdigest()
        self.assertNotEqual(
            raw(ticked), raw(original), "raw SHA must change (file really differs)"
        )
        self.assertEqual(
            self.fp(ticked), self.fp(original),
            "AC2: ticking checkboxes must not change the plan fingerprint",
        )


class PlanDriftCliTestCase(unittest.TestCase):
    """CLI-level drift behaviour for `init` / `status` / `next`."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)
        self.plan_path = self.tmp_path / "drift-plan.md"
        self.plan_path.write_text(FINGERPRINT_PLAN_TEXT, encoding="utf-8")
        r = run_cli("init", str(self.plan_path), "--no-attach")
        self.assertEqual(r.returncode, 0, msg=r.stderr)

    def read_state(self) -> dict:
        sp = self.tmp_path / ".plan-state" / "drift-plan.state.json"
        return json.loads(sp.read_text(encoding="utf-8"))

    def write_state(self, state: dict) -> None:
        sp = self.tmp_path / ".plan-state" / "drift-plan.state.json"
        sp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

    def edit_plan_content(self) -> None:
        self.plan_path.write_text(
            FINGERPRINT_PLAN_TEXT.replace("Files: `a.py`", "Files: `rewritten.py`"),
            encoding="utf-8",
        )

    def tick_all_checkboxes(self) -> None:
        text = self.plan_path.read_text(encoding="utf-8")
        self.plan_path.write_text(text.replace("- [ ]", "- [x]"), encoding="utf-8")

    # -- init -----------------------------------------------------------

    def test_init_records_plan_sha256(self) -> None:
        state = self.read_state()
        self.assertIn("plan_sha256", state)
        self.assertRegex(state["plan_sha256"], r"^[0-9a-f]{64}$")

    # -- drift detected --------------------------------------------------

    def test_next_blocks_on_content_drift(self) -> None:
        self.edit_plan_content()
        r = run_cli("next", str(self.plan_path))
        self.assertNotEqual(r.returncode, 0, msg="next must refuse to hand out work")
        self.assertIn("DRIFT:", r.stdout)
        self.assertIn("rm ", r.stdout)
        self.assertIn("--ignore-drift", r.stdout)
        self.assertNotIn("## Newly unlocked", r.stdout)

    def test_drift_warning_is_at_the_top_of_output(self) -> None:
        self.edit_plan_content()
        r = run_cli("status", str(self.plan_path))
        self.assertTrue(
            r.stdout.lstrip().startswith("DRIFT:"),
            f"DRIFT banner must lead the output, got:\n{r.stdout[:200]}",
        )

    def test_status_warns_on_drift_but_still_succeeds(self) -> None:
        self.edit_plan_content()
        r = run_cli("status", str(self.plan_path))
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("DRIFT:", r.stdout)
        self.assertIn("S1", r.stdout)  # the normal status body still renders

    def test_next_ignore_drift_escape_hatch(self) -> None:
        self.edit_plan_content()
        r = run_cli("next", str(self.plan_path), "--ignore-drift")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("DRIFT:", r.stdout)
        self.assertIn("## Newly unlocked", r.stdout)

    def test_blocked_next_leaves_state_untouched(self) -> None:
        """A refused `next` must not consume the ready-set delta, or the
        step would silently vanish from the next successful call."""
        before = self.read_state()
        self.edit_plan_content()
        run_cli("next", str(self.plan_path))
        self.assertEqual(self.read_state(), before)

    def test_blocked_next_json_output_carries_drift(self) -> None:
        self.edit_plan_content()
        r = run_cli("next", str(self.plan_path), "--format", "json")
        self.assertEqual(r.returncode, 2)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["plan_drift"]["status"], "drift")
        self.assertIn("--ignore-drift", payload["hint"])

    def test_drift_surfaces_in_json_format(self) -> None:
        self.edit_plan_content()
        r = run_cli("next", str(self.plan_path), "--format", "json", "--ignore-drift")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["plan_drift"]["status"], "drift")

    # -- AC2 at the CLI level ---------------------------------------------

    def test_ticking_checkboxes_does_not_trigger_drift(self) -> None:
        self.tick_all_checkboxes()
        r = run_cli("next", str(self.plan_path))
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertNotIn("DRIFT:", r.stdout)

    # -- legacy state (no plan_sha256) ------------------------------------

    def test_legacy_state_without_plan_sha256_is_never_blocked(self) -> None:
        """AC3: 187 in-flight states across other repos carry no
        plan_sha256. They must keep working, hinted but never blocked --
        even after the plan really changed."""
        state = self.read_state()
        del state["plan_sha256"]
        self.write_state(state)
        self.edit_plan_content()

        r = run_cli("next", str(self.plan_path))
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertNotIn("DRIFT:", r.stdout)
        self.assertIn("plan_sha256", r.stdout)  # the one-line legacy hint
        self.assertIn("## Newly unlocked", r.stdout)

        s = run_cli("status", str(self.plan_path))
        self.assertEqual(s.returncode, 0, msg=s.stderr)
        self.assertNotIn("DRIFT:", s.stdout)

    def test_legacy_hint_does_not_write_plan_sha256_back(self) -> None:
        """No trust-on-first-use: adopting the current hash would silently
        baseline a plan that may already have drifted (plan section 1
        non-goal: never mutate an existing state's content)."""
        state = self.read_state()
        del state["plan_sha256"]
        self.write_state(state)
        run_cli("next", str(self.plan_path))
        self.assertNotIn("plan_sha256", self.read_state())

    # -- unreadable plan ---------------------------------------------------

    def test_missing_plan_file_does_not_block(self) -> None:
        """A deleted plan is not drift; blocking here would only add a
        second failure mode on top of the missing file."""
        self.plan_path.unlink()
        r = run_cli("next", str(self.plan_path))
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertNotIn("DRIFT:", r.stdout)


# ---------------------------------------------------------------------------
# S3.2 -- wall-clock checkpoint trigger inside decide_budget()
# ---------------------------------------------------------------------------


class WallClockCheckpointTestCase(unittest.TestCase):
    """decide_budget()'s fourth rule: a stale `last_advance_at` sets
    `checkpoint_pending` -- and touches nothing else (plan R1).

    decide_budget() stays a pure function: `now` is injected, never read
    from the clock inside, so every case below is fully deterministic.
    """

    # A fixed epoch so nothing here depends on the real clock.
    NOW = 1_800_000_000.0

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_module_from_path(PLAN_RUNNER, "plan_runner_wallclock")

    # -- fixtures ----------------------------------------------------------

    def iso_before_now(self, seconds: float) -> str:
        """ISO-8601 UTC timestamp `seconds` before self.NOW."""
        moment = datetime.fromtimestamp(self.NOW - seconds, timezone.utc)
        return moment.isoformat()

    def make_state(self) -> dict:
        """Two pending steps in one phase, so finishing S1 never closes out
        the phase -- rule 3 (phase boundary) stays out of the way and any
        checkpoint_pending we observe can only come from rule 2 or rule 4.
        """
        return {
            "steps": {
                "S1": {"id": "S1", "phase": "P1", "status": "pending"},
                "S2": {"id": "S2", "phase": "P1", "status": "pending"},
            }
        }

    def make_pointer(self, *, consecutive_blocks: int = 0, **fields) -> dict:
        pointer = {
            "consecutive_blocks": consecutive_blocks,
            "created_at": self.iso_before_now(30),
            "last_advance_at": self.iso_before_now(30),
        }
        pointer.update(fields)
        return pointer

    def decide(self, *, consecutive_blocks: int = 0, now=..., **fields):
        pointer = self.make_pointer(consecutive_blocks=consecutive_blocks, **fields)
        if now is ...:
            return self.mod.decide_budget(pointer, self.make_state(), "S1")
        return self.mod.decide_budget(pointer, self.make_state(), "S1", now=now)

    # -- the constant and its env override ---------------------------------

    def test_default_stale_threshold_is_45_minutes(self) -> None:
        self.assertEqual(self.mod.CHECKPOINT_STALE_SECONDS, 2700)

    def test_env_override_applies(self) -> None:
        with mock.patch.dict(os.environ, {"PLAN_RUN_CHECKPOINT_STALE_SECONDS": "1800"}):
            self.assertEqual(self.mod._effective_checkpoint_stale_seconds(), 1800)

    def test_env_override_malformed_falls_back_to_default(self) -> None:
        for raw in ("", "   ", "abc", "45m", "0", "-1", "2700.5"):
            with self.subTest(raw=raw):
                with mock.patch.dict(
                    os.environ, {"PLAN_RUN_CHECKPOINT_STALE_SECONDS": raw}
                ):
                    self.assertEqual(
                        self.mod._effective_checkpoint_stale_seconds(),
                        self.mod.CHECKPOINT_STALE_SECONDS,
                    )

    def test_env_override_absent_falls_back_to_default(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PLAN_RUN_CHECKPOINT_STALE_SECONDS", None)
            self.assertEqual(
                self.mod._effective_checkpoint_stale_seconds(),
                self.mod.CHECKPOINT_STALE_SECONDS,
            )

    def test_env_override_clamped_at_pointer_stale_seconds(self) -> None:
        """A threshold above POINTER_STALE_SECONDS could never fire --
        resolve_pointer() drops the pointer first -- so it is clamped."""
        with mock.patch.dict(
            os.environ, {"PLAN_RUN_CHECKPOINT_STALE_SECONDS": "999999"}
        ):
            self.assertEqual(
                self.mod._effective_checkpoint_stale_seconds(),
                self.mod.POINTER_STALE_SECONDS,
            )

    # -- the rule itself ---------------------------------------------------

    def test_now_omitted_never_triggers(self) -> None:
        """The default `now=None` means "no clock supplied" -- the rule is
        skipped entirely, which is what keeps every existing caller and the
        golden baseline byte-identical."""
        d = self.decide(last_advance_at=self.iso_before_now(99999))
        self.assertFalse(d.checkpoint_pending)

    def test_now_explicit_none_never_triggers(self) -> None:
        d = self.decide(now=None, last_advance_at=self.iso_before_now(99999))
        self.assertFalse(d.checkpoint_pending)

    def test_recent_advance_does_not_trigger(self) -> None:
        d = self.decide(now=self.NOW, last_advance_at=self.iso_before_now(60))
        self.assertFalse(d.checkpoint_pending)

    def test_stale_advance_triggers(self) -> None:
        d = self.decide(now=self.NOW, last_advance_at=self.iso_before_now(3600))
        self.assertTrue(d.checkpoint_pending)

    def test_exactly_at_threshold_does_not_trigger(self) -> None:
        """Strict `>`, matching _is_pointer_stale()'s own comparison."""
        d = self.decide(now=self.NOW, last_advance_at=self.iso_before_now(2700))
        self.assertFalse(d.checkpoint_pending)

    def test_one_second_past_threshold_triggers(self) -> None:
        d = self.decide(now=self.NOW, last_advance_at=self.iso_before_now(2701))
        self.assertTrue(d.checkpoint_pending)

    def test_env_override_shortens_the_window(self) -> None:
        with mock.patch.dict(
            os.environ, {"PLAN_RUN_CHECKPOINT_STALE_SECONDS": "60"}
        ):
            d = self.decide(now=self.NOW, last_advance_at=self.iso_before_now(61))
        self.assertTrue(d.checkpoint_pending)

    # -- R1: nothing but checkpoint_pending may move ------------------------

    def test_only_checkpoint_pending_differs_between_stale_and_fresh(self) -> None:
        """The R1 guard. For every reachable consecutive_blocks value, run
        the same pointer/state twice -- differing only in how old
        `last_advance_at` is -- and assert the two BudgetDecisions are
        equal once checkpoint_pending is neutralised on both sides.

        Comparing whole NamedTuples (rather than naming decision and
        steps_remaining one by one) means a field added to BudgetDecision
        later is covered by this test the day it is added.
        """
        budget = self.mod._effective_block_budget()
        for blocks in range(0, budget + 2):
            with self.subTest(consecutive_blocks=blocks):
                fresh = self.decide(
                    consecutive_blocks=blocks,
                    now=self.NOW,
                    last_advance_at=self.iso_before_now(60),
                )
                stale = self.decide(
                    consecutive_blocks=blocks,
                    now=self.NOW,
                    last_advance_at=self.iso_before_now(99999),
                )
                self.assertEqual(stale.decision, fresh.decision)
                self.assertEqual(stale.steps_remaining, fresh.steps_remaining)
                self.assertEqual(
                    stale._replace(checkpoint_pending=None),
                    fresh._replace(checkpoint_pending=None),
                )

    def test_stale_clock_flips_only_checkpoint_pending_mid_budget(self) -> None:
        """The concrete case behind the loop above: mid-budget, a fresh
        pointer blocks with checkpoint_pending False and a stale one blocks
        with it True -- same decision, same steps_remaining."""
        fresh = self.decide(
            consecutive_blocks=2, now=self.NOW,
            last_advance_at=self.iso_before_now(60),
        )
        stale = self.decide(
            consecutive_blocks=2, now=self.NOW,
            last_advance_at=self.iso_before_now(99999),
        )
        self.assertFalse(fresh.checkpoint_pending)
        self.assertTrue(stale.checkpoint_pending)
        self.assertEqual(fresh.decision, "block")
        self.assertEqual(stale.decision, "block")
        self.assertEqual(stale.steps_remaining, fresh.steps_remaining)

    def test_stale_clock_does_not_claim_a_phase_boundary(self) -> None:
        """checkpoint_from_phase_boundary drives the footer text ("phase
        boundary reached"); a wall-clock stop is not a phase boundary and
        must not borrow that wording."""
        d = self.decide(now=self.NOW, last_advance_at=self.iso_before_now(99999))
        self.assertTrue(d.checkpoint_pending)
        self.assertFalse(d.checkpoint_from_phase_boundary)

    def test_budget_exhausted_allow_is_unaffected_by_a_stale_clock(self) -> None:
        """Rule 1 (natural wind-down) returns before the wall-clock rule:
        the round is ending anyway, so there is no block to attach a
        checkpoint instruction to."""
        budget = self.mod._effective_block_budget()
        d = self.decide(
            consecutive_blocks=budget, now=self.NOW,
            last_advance_at=self.iso_before_now(99999),
        )
        self.assertEqual(d.decision, "allow")
        self.assertFalse(d.checkpoint_pending)
        self.assertEqual(d.steps_remaining, 0)

    # -- missing / malformed timestamps ------------------------------------

    def test_missing_last_advance_at_falls_back_to_created_at(self) -> None:
        d = self.decide(
            now=self.NOW,
            last_advance_at=None,
            created_at=self.iso_before_now(99999),
        )
        self.assertTrue(d.checkpoint_pending)

    def test_fallback_created_at_recent_does_not_trigger(self) -> None:
        """A plan initialised moments ago has advanced nothing, but it has
        also not been stuck -- no checkpoint."""
        d = self.decide(
            now=self.NOW, last_advance_at=None, created_at=self.iso_before_now(10),
        )
        self.assertFalse(d.checkpoint_pending)

    def test_both_timestamps_missing_never_triggers(self) -> None:
        d = self.decide(now=self.NOW, last_advance_at=None, created_at=None)
        self.assertFalse(d.checkpoint_pending)

    def test_unparseable_timestamps_never_trigger(self) -> None:
        for bad in ("", "not-a-date", "2026-13-45T99:99:99", 12345, [], {}):
            with self.subTest(value=bad):
                d = self.decide(now=self.NOW, last_advance_at=bad, created_at=bad)
                self.assertFalse(d.checkpoint_pending)

    def test_unparseable_last_advance_at_falls_back_to_created_at(self) -> None:
        d = self.decide(
            now=self.NOW,
            last_advance_at="not-a-date",
            created_at=self.iso_before_now(99999),
        )
        self.assertTrue(d.checkpoint_pending)

    def test_naive_timestamp_is_read_as_utc(self) -> None:
        """_parse_iso_timestamp() assumes UTC for naive strings; the rule
        must not blow up on one."""
        naive = datetime.fromtimestamp(self.NOW - 99999, timezone.utc)
        naive = naive.replace(tzinfo=None).isoformat()
        d = self.decide(now=self.NOW, last_advance_at=naive)
        self.assertTrue(d.checkpoint_pending)

    # -- clock skew ---------------------------------------------------------

    def test_clock_running_backwards_does_not_trigger(self) -> None:
        """`now` earlier than the reference yields a negative age. That is
        evidence the clock moved, not evidence a step ran long -- so it
        must not fire."""
        d = self.decide(
            now=self.NOW, last_advance_at=self.iso_before_now(-99999),
        )
        self.assertFalse(d.checkpoint_pending)

    def test_non_numeric_now_is_ignored(self) -> None:
        for bad in ("2700", [], {}, object()):
            with self.subTest(value=bad):
                d = self.decide(now=bad, last_advance_at=self.iso_before_now(99999))
                self.assertFalse(d.checkpoint_pending)

    # -- wiring: the production caller actually supplies a clock ------------

    def test_hook_ready_step_branch_supplies_a_real_clock(self) -> None:
        """decide_budget() only ever sees a clock if _branch_ready_step()
        hands it one -- without this the whole rule is dead code in
        production while every unit test above still passes."""
        mod = self.mod
        plan_dir = Path.home() / ".plan-run-s32-fixture"
        pointer = mod.new_pointer_record(
            plan_path=plan_dir / "plan.md",
            repo_root=plan_dir,
            cwd=plan_dir,
            session_id="s32-session",
        )
        pointer["last_advance_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=99999)
        ).isoformat()
        # S3.4: also pre-seed last_seen_completed_count at the fixture's
        # current (zero) completed count. Without this,
        # _record_advance_if_progressed() reads a bare new_pointer_record()
        # (last_seen_completed_count=None) as "never observed before" and
        # baselines last_advance_at to now on this very call -- masking the
        # staleness this test manually injected above. A real pointer that
        # has already had at least one hook turn (which is what a stale,
        # non-None last_advance_at implies) would already carry this field;
        # a bare new_pointer_record() with a hand-set last_advance_at is not
        # a reachable production state post-S3.4.
        pointer["last_seen_completed_count"] = 0
        state = {
            "plan_path": str(plan_dir / "plan.md"),
            "slug": "s32-fixture",
            "title": "S3.2 fixture",
            "phase_order": ["P1"],
            "parent_task_id": None,
            "created_at": mod.now_iso(),
            "updated_at": mod.now_iso(),
            "steps": {
                "S1": {
                    "id": "S1", "title": "first", "phase": "P1", "deps": [],
                    "agent": None, "skill": None, "command": None, "files": None,
                    "action": "do it", "risk": None, "status": "pending",
                    "task_id": None, "started_at": None, "completed_at": None,
                    "failure_reason": None,
                },
                "S2": {
                    "id": "S2", "title": "second", "phase": "P1", "deps": [],
                    "agent": None, "skill": None, "command": None, "files": None,
                    "action": "do it too", "risk": None, "status": "pending",
                    "task_id": None, "started_at": None, "completed_at": None,
                    "failure_reason": None,
                },
            },
        }
        hook_input = {
            "hook_event_name": "Stop",
            "session_id": "s32-session",
            "transcript_path": str(plan_dir / "transcript.jsonl"),
            "cwd": str(plan_dir),
            "stop_hook_active": True,
        }
        decision = mod.decide_hook_action(
            hook_input, pointer, state, lambda path: None,
        )
        self.assertEqual(decision.decision, "block")
        self.assertTrue(decision.pointer_updates.get("checkpoint_pending"))


# ---------------------------------------------------------------------------
# S3.3 -- `recap`, the single unattended-recovery entrypoint
# ---------------------------------------------------------------------------

RECAP_PLAN_TEXT = """# Recap Fixture

### Phase 1: Setup

- [ ] S1 First step
  - Agent: general-purpose
  - Files: `a.py`
  - Action: do A
"""


class RecapCliTestCase(unittest.TestCase):
    """S3.3: `recap` prints, in order, (1) stop.md verbatim and nothing
    else if present, else (2) drift status, (3) checkpoint.md (bounded),
    (4) the next ready step with a runnable dispatch command, (5) the
    cwd's pointer `last_advance_at` and elapsed time. It must never write
    state, checkpoint.md, stop.md, or the pointer file (T6: print-only).

    Isolation: every subprocess call gets its own fake HOME (a fresh
    tempdir) so pointer lookups never touch the real
    ~/.claude/plan-run/active/ -- same isolation goal as this file's
    header note about `--no-attach`, applied to a read path instead of a
    write path.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_module_from_path(PLAN_RUNNER, "plan_runner_recap")

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)
        self.plan_path = self.tmp_path / "recap-plan.md"
        self.plan_path.write_text(RECAP_PLAN_TEXT, encoding="utf-8")
        self.state_dir = self.tmp_path / ".plan-state"

        self._home_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._home_tmp.cleanup)
        self.home = Path(self._home_tmp.name)
        self.env = self._env_with_home(self.home)

        r = run_cli("init", str(self.plan_path), "--no-attach", env=self.env)
        self.assertEqual(r.returncode, 0, msg=r.stderr)

    # -- fixture helpers ---------------------------------------------------

    def _env_with_home(self, home: Path) -> dict[str, str]:
        env = dict(os.environ)
        env["HOME"] = str(home)
        return env

    def checkpoint_path(self) -> Path:
        return self.state_dir / "recap-plan.checkpoint.md"

    def stop_path(self) -> Path:
        return self.state_dir / "recap-plan.stop.md"

    def state_path(self) -> Path:
        return self.state_dir / "recap-plan.state.json"

    def read_state(self) -> dict:
        return json.loads(self.state_path().read_text(encoding="utf-8"))

    def write_state(self, state: dict) -> None:
        self.state_path().write_text(
            json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def write_pointer(
        self,
        *,
        last_advance_at_seconds_ago: float | None,
        plan_path: Path | None = None,
    ) -> None:
        """Write a pointer for cwd == self.tmp_path under self.home, using
        the module's own record/path helpers under a patched Path.home()
        so the file lands exactly where a recap subprocess run with
        HOME=self.home and cwd=self.tmp_path will look for it."""
        # _ensure_pointer_active_dir() calls PLAN_RUN_DIR.mkdir() without
        # parents=True (mirrors production, where it only ever needs to
        # create one level under an already-existing $HOME) -- so the fake
        # HOME's ".claude" parent must pre-exist here too.
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)
        with mock.patch.object(Path, "home", return_value=self.home):
            mod = load_module_from_path(PLAN_RUNNER, "plan_runner_recap_ptr_writer")
            pointer = mod.new_pointer_record(
                plan_path=plan_path or self.plan_path,
                repo_root=self.tmp_path,
                cwd=self.tmp_path,
                session_id="recap-test-session",
            )
            if last_advance_at_seconds_ago is not None:
                ts = datetime.now(timezone.utc) - timedelta(
                    seconds=last_advance_at_seconds_ago
                )
                pointer["last_advance_at"] = ts.isoformat()
            mod._ensure_pointer_active_dir()
            pointer_path = mod.pointer_path_for(self.tmp_path)
            mod.write_pointer_atomic(pointer_path, pointer)

    def recap(self, *extra: str) -> subprocess.CompletedProcess:
        return run_cli(
            "recap", str(self.plan_path), *extra, cwd=self.tmp_path, env=self.env,
        )

    # -- (1) stop.md precedence --------------------------------------------

    def test_stop_marker_wins_and_suppresses_everything_else(self) -> None:
        self.checkpoint_path().write_text("# Checkpoint\ndid stuff\n", encoding="utf-8")
        self.stop_path().write_text(
            "# STOP\nReason: CI 一直紅\n", encoding="utf-8",
        )
        # Drift too, so both conditions from cmd_next's precedent are live.
        self.plan_path.write_text(RECAP_PLAN_TEXT + "\n- extra line\n", encoding="utf-8")

        r = self.recap()
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("CI 一直紅", r.stdout)
        self.assertNotIn("Checkpoint", r.stdout)
        self.assertNotIn("DRIFT", r.stdout)
        self.assertNotIn("did stuff", r.stdout)

    def test_stop_marker_json_precedence(self) -> None:
        self.stop_path().write_text("halted: investigate\n", encoding="utf-8")
        r = self.recap("--format", "json")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        payload = json.loads(r.stdout)
        self.assertTrue(payload["stopped"])
        self.assertIn("halted: investigate", payload["stop_marker"])

    # -- (2) drift status ----------------------------------------------------

    def test_drift_ok_shown_as_single_line_when_clean(self) -> None:
        r = self.recap()
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("Drift: ok", r.stdout)
        self.assertNotIn("DRIFT:", r.stdout)  # no multi-line banner when clean

    def test_drift_banner_shown_when_plan_changed(self) -> None:
        self.plan_path.write_text(RECAP_PLAN_TEXT.replace("do A", "do A now"), encoding="utf-8")
        r = self.recap()
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("DRIFT:", r.stdout)

    # -- (3) checkpoint, bounded -----------------------------------------

    def test_checkpoint_full_text_when_present_and_short(self) -> None:
        self.checkpoint_path().write_text(
            "# Checkpoint\nDone: nothing yet\nNext: start S1\n", encoding="utf-8"
        )
        r = self.recap()
        self.assertIn("Done: nothing yet", r.stdout)
        self.assertIn("Next: start S1", r.stdout)

    def test_checkpoint_section_omitted_when_absent(self) -> None:
        """The clean-state case: no stop, no drift, no checkpoint yet --
        recap must not print a placeholder '(none)' line for it."""
        r = self.recap()
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertNotIn("## Checkpoint", r.stdout)

    def test_checkpoint_at_200_lines_is_not_truncated(self) -> None:
        text = "\n".join(f"line {i}" for i in range(200)) + "\n"
        self.checkpoint_path().write_text(text, encoding="utf-8")
        r = self.recap()
        self.assertIn("line 0", r.stdout)
        self.assertIn("line 199", r.stdout)
        self.assertNotIn("省略", r.stdout)

    def test_checkpoint_over_200_lines_is_bounded_to_head_and_tail(self) -> None:
        text = "\n".join(f"line {i}" for i in range(250)) + "\n"
        self.checkpoint_path().write_text(text, encoding="utf-8")
        r = self.recap()
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        # head 60 present
        self.assertIn("line 0", r.stdout)
        self.assertIn("line 59", r.stdout)
        # middle omitted
        self.assertNotIn("line 100", r.stdout)
        # tail 60 present
        self.assertIn("line 190", r.stdout)
        self.assertIn("line 249", r.stdout)
        # explicit omission count, not a silent cut
        self.assertIn("130", r.stdout)  # 250 - 60 - 60 omitted lines
        self.assertIn("省略", r.stdout)

    # -- (4) next ready step -----------------------------------------------

    def test_next_ready_step_has_runnable_dispatch_command(self) -> None:
        r = self.recap()
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("S1", r.stdout)
        # Real path, not the '<plan>' placeholder next/status print.
        self.assertIn(f"start {self.plan_path.resolve()} S1", r.stdout)
        self.assertNotIn("<plan>", r.stdout)

    def test_all_done_message_when_plan_complete(self) -> None:
        run_cli("start", str(self.plan_path), "S1", env=self.env)
        run_cli("complete", str(self.plan_path), "S1", env=self.env)
        r = self.recap()
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("ALL DONE", r.stdout)

    # -- (5) pointer ---------------------------------------------------------

    def test_no_pointer_message_when_cwd_has_no_active_pointer(self) -> None:
        r = self.recap()
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("無 active pointer", r.stdout)

    def test_pointer_last_advance_at_and_elapsed_shown(self) -> None:
        self.write_pointer(last_advance_at_seconds_ago=3 * 3600 + 5 * 60)
        r = self.recap()
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("last_advance_at", r.stdout)
        self.assertIn("3 小時", r.stdout)

    def test_pointer_missing_last_advance_at_falls_back_to_created_at(self) -> None:
        self.write_pointer(last_advance_at_seconds_ago=None)
        r = self.recap()
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("created_at", r.stdout)

    # -- print-only: T6 ------------------------------------------------------

    def test_recap_never_writes_state_checkpoint_stop_or_pointer(self) -> None:
        self.checkpoint_path().write_text("# Checkpoint\nx\n", encoding="utf-8")
        self.write_pointer(last_advance_at_seconds_ago=60)
        before_state = self.state_path().read_bytes()
        before_checkpoint = self.checkpoint_path().read_bytes()
        # Pointer path must be derived under the test's fake HOME, not this
        # process's own Path.home() -- the module was imported before HOME
        # was patched, so its baked-in PLAN_RUN_DIR points elsewhere.
        with mock.patch.object(Path, "home", return_value=self.home):
            mod = load_module_from_path(PLAN_RUNNER, "plan_runner_recap_ptr_check")
            pointer_path = mod.pointer_path_for(self.tmp_path.resolve())
        before_pointer = pointer_path.read_bytes()

        r = self.recap()
        self.assertEqual(r.returncode, 0, msg=r.stderr)

        self.assertEqual(self.state_path().read_bytes(), before_state)
        self.assertEqual(self.checkpoint_path().read_bytes(), before_checkpoint)
        self.assertEqual(pointer_path.read_bytes(), before_pointer)
        self.assertFalse(self.stop_path().exists())

    # -- json shape ----------------------------------------------------------

    def test_json_shape_carries_all_five_sections(self) -> None:
        self.write_pointer(last_advance_at_seconds_ago=60)
        r = self.recap("--format", "json")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        payload = json.loads(r.stdout)
        self.assertFalse(payload["stopped"])
        self.assertEqual(payload["plan_drift"]["status"], "ok")
        self.assertIn("next_step", payload)
        self.assertEqual(payload["next_step"]["id"], "S1")
        self.assertIn("pointer", payload)
        self.assertIsNotNone(payload["pointer"]["last_advance_at"])


class EstimatedFieldTestCase(unittest.TestCase):
    """S4.1: optional `Estimated: <N>m` step field + LARGE-WORK phase
    subtotal warning. Compatibility is the load-bearing property here --
    ~187 in-flight states and every existing plan.md have zero `Estimated:`
    lines, so absence must parse silently and never warn or LARGE-WORK.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_module_from_path(PLAN_RUNNER, "plan_runner_estimated")

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    def write_plan(self, text: str) -> Path:
        p = self.tmp_path / "estimated-plan.md"
        p.write_text(text, encoding="utf-8")
        return p

    # -- parse_plan(): field parsing ---------------------------------------

    def test_missing_estimated_defaults_to_zero_no_warning(self) -> None:
        plan_path = self.write_plan(PLAN_TEXT)  # module-level fixture, no Estimated: at all
        parsed = self.mod.parse_plan(plan_path)
        self.assertEqual(parsed["warnings"], [])
        for step in parsed["steps"].values():
            self.assertEqual(step["estimated"], 0)

    def test_valid_estimated_parsed_as_minutes(self) -> None:
        text = (
            "# Fixture\n\n### Phase 1: P\n\n"
            "- [ ] S1 Step one\n"
            "  - Files: a.py\n"
            "  - Estimated: 90m\n"
            "  - Action: do A\n"
        )
        parsed = self.mod.parse_plan(self.write_plan(text))
        self.assertEqual(parsed["warnings"], [])
        self.assertEqual(parsed["steps"]["S1"]["estimated"], 90)

    def test_invalid_estimated_warns_but_does_not_abort_parse(self) -> None:
        text = (
            "# Fixture\n\n### Phase 1: P\n\n"
            "- [ ] S1 Step one\n"
            "  - Files: a.py\n"
            "  - Estimated: 1.5h\n"
            "  - Action: do A\n"
        )
        parsed = self.mod.parse_plan(self.write_plan(text))
        self.assertEqual(len(parsed["warnings"]), 1)
        self.assertIn("Estimated", parsed["warnings"][0])
        self.assertIn("S1", parsed["steps"])
        self.assertEqual(parsed["steps"]["S1"]["estimated"], 0)
        self.assertEqual(parsed["steps"]["S1"]["files"], "a.py")
        self.assertEqual(parsed["steps"]["S1"]["action"], "do A")

    def test_only_bare_minute_shape_accepted(self) -> None:
        """Deliberately narrow surface (S4.1 design decision): '90', '1.5h',
        '2h30m' all warn+zero exactly like a typo would."""
        for raw in ("90", "1.5h", "2h30m", "90 minutes", "m90"):
            with self.subTest(raw=raw):
                text = (
                    "# Fixture\n\n### Phase 1: P\n\n"
                    "- [ ] S1 Step one\n"
                    f"  - Estimated: {raw}\n"
                    "  - Action: do A\n"
                )
                parsed = self.mod.parse_plan(self.write_plan(text))
                self.assertEqual(parsed["steps"]["S1"]["estimated"], 0)
                self.assertEqual(len(parsed["warnings"]), 1)

    # -- init_state(): snapshot propagation ---------------------------------

    def test_init_state_carries_estimated_into_step_snapshot(self) -> None:
        text = (
            "# Fixture\n\n### Phase 1: P\n\n"
            "- [ ] S1 Step one\n"
            "  - Estimated: 45m\n"
            "  - Action: do A\n"
        )
        plan_path = self.write_plan(text)
        parsed = self.mod.parse_plan(plan_path)
        state = self.mod.init_state(plan_path, parsed)
        self.assertEqual(state["steps"]["S1"]["estimated"], 45)

    # -- LARGE_PHASE_MINUTES + env override ----------------------------------

    def test_default_large_phase_minutes_is_180(self) -> None:
        self.assertEqual(self.mod.LARGE_PHASE_MINUTES, 180)

    def test_env_override_applies(self) -> None:
        with mock.patch.dict(os.environ, {"PLAN_RUN_LARGE_PHASE_MINUTES": "60"}):
            self.assertEqual(self.mod._effective_large_phase_minutes(), 60)

    def test_env_override_malformed_falls_back_to_default(self) -> None:
        for raw in ("", "   ", "abc", "60m", "0", "-1"):
            with self.subTest(raw=raw):
                with mock.patch.dict(
                    os.environ, {"PLAN_RUN_LARGE_PHASE_MINUTES": raw}
                ):
                    self.assertEqual(
                        self.mod._effective_large_phase_minutes(),
                        self.mod.LARGE_PHASE_MINUTES,
                    )

    def test_env_override_absent_falls_back_to_default(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PLAN_RUN_LARGE_PHASE_MINUTES", None)
            self.assertEqual(
                self.mod._effective_large_phase_minutes(),
                self.mod.LARGE_PHASE_MINUTES,
            )

    # -- CLI: `next` delta output --------------------------------------------

    def large_phase_plan_text(self) -> str:
        return (
            "# Large Phase Fixture\n\n"
            "### Phase 1: Small\n\n"
            "- [ ] S1 First step\n"
            "  - Estimated: 10m\n"
            "  - Action: do A\n"
        )

    def test_no_estimated_fields_never_triggers_large_work(self) -> None:
        plan_path = self.write_plan(PLAN_TEXT)
        r = run_cli("init", str(plan_path), "--no-attach")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        r = run_cli("next", str(plan_path))
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertNotIn("LARGE-WORK", r.stdout)

    def test_phase_subtotal_over_threshold_emits_large_work_line(self) -> None:
        text = (
            "# Fixture\n\n### Phase 1: Big\n\n"
            "- [ ] S1 First\n  - Estimated: 90m\n  - Action: do A\n\n"
            "- [ ] S2 Second\n  - Dependencies: S1\n"
            "  - Estimated: 90m\n  - Action: do B\n\n"
            "- [ ] S3 Third\n  - Dependencies: S1\n"
            "  - Estimated: 90m\n  - Action: do C\n"
        )
        plan_path = self.write_plan(text)
        run_cli("init", str(plan_path), "--no-attach")
        r = run_cli("next", str(plan_path))
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("LARGE-WORK", r.stdout)
        self.assertIn("Phase 1: Big", r.stdout)
        self.assertIn("270", r.stdout)
        self.assertIn("建議拆分", r.stdout)

    def test_phase_subtotal_under_threshold_no_warning(self) -> None:
        text = (
            "# Fixture\n\n### Phase 1: Small\n\n"
            "- [ ] S1 First\n  - Estimated: 30m\n  - Action: do A\n"
        )
        plan_path = self.write_plan(text)
        run_cli("init", str(plan_path), "--no-attach")
        r = run_cli("next", str(plan_path))
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertNotIn("LARGE-WORK", r.stdout)

    def test_partial_estimates_annotated_not_silently_understated(self) -> None:
        """Only some steps in the phase carry Estimated: -- the subtotal is
        a lower bound, and the line must say so rather than imply
        completeness."""
        text = (
            "# Fixture\n\n### Phase 1: Mixed\n\n"
            "- [ ] S1 First\n  - Estimated: 200m\n  - Action: do A\n\n"
            "- [ ] S2 Second\n  - Dependencies: S1\n  - Action: do B\n"
        )
        plan_path = self.write_plan(text)
        run_cli("init", str(plan_path), "--no-attach")
        r = run_cli("next", str(plan_path))
        self.assertIn("LARGE-WORK", r.stdout)
        self.assertIn("1/2", r.stdout)

    def test_completed_steps_excluded_from_subtotal(self) -> None:
        """A step already COMPLETED no longer represents work ahead, so it
        must drop out of both the minutes sum and the estimated/total
        denominator once done."""
        text = (
            "# Fixture\n\n### Phase 1: Big\n\n"
            "- [ ] S1 First\n  - Estimated: 190m\n  - Action: do A\n\n"
            "- [ ] S2 Second\n  - Dependencies: S1\n"
            "  - Estimated: 10m\n  - Action: do B\n"
        )
        plan_path = self.write_plan(text)
        run_cli("init", str(plan_path), "--no-attach")
        r = run_cli("next", str(plan_path))
        self.assertIn("LARGE-WORK", r.stdout)

        run_cli("start", str(plan_path), "S1")
        run_cli("complete", str(plan_path), "S1")
        r = run_cli("next", str(plan_path))
        # S1 (190m) is done; only S2 (10m) remains -- well under threshold,
        # and S2 alone is newly-unlocked so the phase is re-evaluated.
        self.assertNotIn("LARGE-WORK", r.stdout)

    def test_env_override_changes_trigger_point(self) -> None:
        plan_path = self.write_plan(self.large_phase_plan_text())
        run_cli("init", str(plan_path), "--no-attach")
        with mock.patch.dict(os.environ, {"PLAN_RUN_LARGE_PHASE_MINUTES": "5"}):
            env = os.environ.copy()
        r = run_cli("next", str(plan_path), env=env)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("LARGE-WORK", r.stdout)

    # -- normalize: planner-agent format also carries Estimated -------------

    PLANNER_AGENT_WITH_ESTIMATED = """# Normalize Estimated Fixture

### Phase 1: P

**Step 1: 做一件事**
- **Files**：a.py
- **Agent**：sonnet
- **Estimated**：90m
- **Action**：做
- **Dependencies**：none
"""

    def test_normalize_converts_estimated_field_to_canonical(self) -> None:
        planner_plan = self.write_plan(self.PLANNER_AGENT_WITH_ESTIMATED)
        r = run_cli("normalize", str(planner_plan), "--write")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        normalized = planner_plan.read_text(encoding="utf-8")
        self.assertIn("  - Estimated: 90m", normalized)
        self.assertNotIn("**Estimated**", normalized)

    def test_normalized_estimated_field_reaches_state(self) -> None:
        planner_plan = self.write_plan(self.PLANNER_AGENT_WITH_ESTIMATED)
        r = run_cli("normalize", str(planner_plan), "--write")
        self.assertEqual(r.returncode, 0, msg=r.stderr)

        r = run_cli("init", str(planner_plan), "--no-attach")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(r.stdout.count("Warnings:"), 0)

        state_path = planner_plan.parent / ".plan-state" / f"{planner_plan.stem}.state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["steps"]["S1.1"]["estimated"], 90)

        r = run_cli("next", str(planner_plan))
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertNotIn("LARGE-WORK", r.stdout)  # 90m alone, under threshold


# ---------------------------------------------------------------------------
# S4.2: auto-reply setting + four-category hard-stop check
# ---------------------------------------------------------------------------
#
# The hard-stop check is a *reverse* whitelist: it enumerates what must stop
# for a human, not what may proceed. Every test below therefore asserts one
# of two things -- "this must be caught" (a missed catch is the dangerous
# failure) or "this must not be caught" (a false catch is only noise). The
# two bypass tests at the end are the ones the plan calls out by name: a
# guard test that has never been red proves nothing.


def _hard_stop_step(**over):
    """A step snapshot shaped like init_state() writes them."""
    step = {
        "id": "S1",
        "title": "Step one",
        "phase": "Phase 1: P",
        "deps": [],
        "agent": "",
        "skill": "",
        "command": "",
        "files": "",
        "action": "",
        "risk": "",
        "estimated": 0,
        "status": "pending",
        "task_id": None,
        "started_at": None,
        "completed_at": None,
        "failure_reason": None,
    }
    step.update(over)
    return step


class HardStopCheckTestCase(unittest.TestCase):
    """S4.2 core: hard_stop_check() / hard_stop_findings() pure functions."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_module_from_path(PLAN_RUNNER, "plan_runner_hardstop")

    def check(self, action: str, **step_over) -> list:
        return self.mod.hard_stop_check(action, _hard_stop_step(**step_over), None)

    def findings(self, action: str, **step_over) -> list:
        return self.mod.hard_stop_findings(action, _hard_stop_step(**step_over), None)

    # -- shape / contract ---------------------------------------------------

    def test_category_ids_are_the_four_from_ag_guide(self) -> None:
        self.assertEqual(self.mod.HARD_STOP_CATEGORIES, ("H1", "H2", "H3", "H4"))

    def test_clean_routine_step_hits_nothing(self) -> None:
        self.assertEqual(
            self.check("依既有慣例補一個單元測試，測試檔命名沿用 test_*.py"),
            [],
        )

    def test_result_is_sorted_and_deduplicated(self) -> None:
        # rm twice + a glob delete: several H2 findings, one H2 category.
        hits = self.check("先 rm build/tmp.txt 再 rm -rf dist/*")
        self.assertEqual(hits, ["H2"])
        self.assertGreater(len(self.findings("先 rm build/tmp.txt 再 rm -rf dist/*")), 1)

    def test_every_finding_carries_a_rule_source(self) -> None:
        for f in self.findings("跑 sudo rm -rf ./build 然後 gh pr merge 8102"):
            with self.subTest(category=f.category):
                self.assertTrue(f.rule.strip(), "finding must cite a rule source")
                self.assertRegex(f.rule, r":\d+", "rule source must carry a line number")
                self.assertTrue(f.evidence.strip())

    def test_malformed_input_hits_all_four(self) -> None:
        """Uncertainty resolves toward stopping, never toward proceeding."""
        self.assertEqual(self.mod.hard_stop_check(None, None, None), ["H1", "H2", "H3", "H4"])
        self.assertEqual(self.mod.hard_stop_check("x", "not-a-step", None), ["H1", "H2", "H3", "H4"])
        self.assertEqual(self.mod.hard_stop_check("x", _hard_stop_step(), 7), ["H1", "H2", "H3", "H4"])

    def test_detection_is_not_truncated_at_the_hook_render_limit(self) -> None:
        """PLAN_ACTION_TRUNCATE_CHARS bounds what the hook *prints*; it must
        not bound what the guard *scans*, or `rm -rf` at char 900 is a
        one-line bypass."""
        padding = "說明文字。" * 400
        self.assertGreater(len(padding), self.mod.PLAN_ACTION_TRUNCATE_CHARS)
        self.assertIn("H2", self.check(padding + " 最後跑 rm -rf ./out"))

    # -- H1: only the owner can decide --------------------------------------

    def test_h1_owner_marker_on_step(self) -> None:
        self.assertIn("H1", self.check("Owner: 使用者\n照既有慣例改個檔名"))
        self.assertIn("H1", self.check("照既有慣例改個檔名", owner="使用者"))

    def test_h1_designer_spec_parameters(self) -> None:
        for action in (
            "把 border-radius 由 4px 調成 8px",
            "調整卡片的 padding 與 line-height",
            "依 Figma 規格換掉色票",
            "改 design token 的 opacity",
        ):
            with self.subTest(action=action):
                self.assertIn("H1", self.check(action))

    def test_h1_visual_adjudication(self) -> None:
        for action in ("做截圖比對確認一致", "UI 對齊裁決由主模型下", "Figma 判讀後回報"):
            with self.subTest(action=action):
                self.assertIn("H1", self.check(action))

    def test_h1_resume_and_outward_copy(self) -> None:
        self.assertIn("H1", self.check("改寫履歷的經歷段落"))
        self.assertIn("H1", self.check("潤飾對外文案後交付"))

    def test_h1_decision_request(self) -> None:
        for action in (
            "用 AskUserQuestion 問使用者要哪個",
            "列出待裁決項目",
            "三選一由使用者決定",
            "請使用者選擇 方案 A / 方案 B / 方案 C",
        ):
            with self.subTest(action=action):
                self.assertIn("H1", self.check(action))

    def test_h1_does_not_fire_on_ordinary_implementation_prose(self) -> None:
        self.assertNotIn("H1", self.check("新增一個純函式並補三個表格式測試"))

    # -- H2: irreversible ----------------------------------------------------

    def test_h2_rm(self) -> None:
        self.assertIn("H2", self.check("rm scripts/tmp.json"))

    def test_h2_wildcard_delete_is_flagged_separately(self) -> None:
        cats = [f.rule for f in self.findings("rm -rf dist/*")]
        self.assertTrue(
            any("delete_explicit_paths_no_wildcard" in r for r in cats),
            f"wildcard delete must cite the no-wildcard rule, got {cats}",
        )

    def test_h2_force_push_all_flag_spellings(self) -> None:
        for flag in ("--force", "-f", "--force-with-lease", "--force-if-includes"):
            with self.subTest(flag=flag):
                self.assertIn("H2", self.check(f"git push {flag} origin HEAD"))

    def test_h2_git_reset_hard_and_stash(self) -> None:
        self.assertIn("H2", self.check("git reset --hard origin/main"))
        self.assertIn("H2", self.check("git stash -u 之後再切分支"))

    def test_h2_destructive_infra_commands(self) -> None:
        for action in (
            "kubectl delete pod foo",
            "terraform destroy -target=module.x",
            "helm uninstall frontend",
            "aws s3api delete-bucket --bucket x",
            "curl -X DELETE https://api.cloudflare.com/...",
        ):
            with self.subTest(action=action):
                self.assertIn("H2", self.check(action))

    def test_h2_sql_drop_and_delete(self) -> None:
        self.assertIn("H2", self.check("DROP TABLE sessions"))
        self.assertIn("H2", self.check("DELETE FROM sessions WHERE id = 1"))

    # -- H3: leaves the machine through a new channel ------------------------

    def test_h3_gh_outward_writes(self) -> None:
        for action in (
            "gh pr create --fill",
            "gh pr merge --merge --delete-branch 8102",
            "gh pr comment 8102 --body 驗收結果",
            "gh issue create --title x",
        ):
            with self.subTest(action=action):
                self.assertIn("H3", self.check(action))

    def test_h3_other_outward_channels(self) -> None:
        for action in (
            "把結果寫回 notion 頁面",
            "發一則 slack 通知",
            "用 sendmail 寄出",
            "curl -X POST https://example.test/webhook -d @body.json",
        ):
            with self.subTest(action=action):
                self.assertIn("H3", self.check(action))

    def test_h3_backend_owned_resources(self) -> None:
        self.assertIn("H3", self.check("改 helm/smb-api/values.yaml 的 replicas"))
        self.assertIn("H3", self.check("調 payment-gateway 的 secret"))
        self.assertIn("H3", self.check("跑 make PHASE=prod deploy 更新 cloudflared"))
        self.assertIn("H3", self.check("調整 cert-manager 的 issuer"))

    def test_h3_backend_owned_path_also_detected_from_files_field(self) -> None:
        self.assertIn(
            "H3",
            self.check("照既有慣例改一行", files="helm/smb-api/values-production.yaml"),
        )

    def test_h3_dev_pointed_to_prod_api(self) -> None:
        self.assertIn("H3", self.check("API_SERVER_IP=https://api.vocus.cc pnpm dev"))

    def test_h3_staging_target_is_not_a_new_outward_channel(self) -> None:
        self.assertNotIn(
            "H3", self.check("API_SERVER_IP=https://api-staging.vocus.cc pnpm dev")
        )
        self.assertNotIn("H3", self.check("API_SERVER_IP=http://localhost:3000 pnpm dev"))

    # -- H4: over the agreed spend ceiling -----------------------------------

    def test_h4_step_over_two_times_estimate(self) -> None:
        budget = {"step_estimated_tokens": 130_000, "step_actual_tokens": 270_000}
        self.assertIn("H4", self.mod.hard_stop_check("補測試", _hard_stop_step(), budget))

    def test_h4_step_under_two_times_estimate_is_clear(self) -> None:
        budget = {"step_estimated_tokens": 130_000, "step_actual_tokens": 200_000}
        self.assertEqual([], self.mod.hard_stop_check("補測試", _hard_stop_step(), budget))

    def test_h4_phase_over_one_and_a_half_times_subtotal(self) -> None:
        budget = {"phase_estimated_tokens": 475_000, "phase_actual_tokens": 750_000}
        self.assertIn("H4", self.mod.hard_stop_check("補測試", _hard_stop_step(), budget))

    def test_h4_declared_ceiling_with_unknown_actual_stops(self) -> None:
        """A ceiling exists but consumption is unmeasured -> cannot certify
        we are under it -> stop. Uncertainty resolves toward stopping."""
        budget = {"step_estimated_tokens": 130_000}
        self.assertIn("H4", self.mod.hard_stop_check("補測試", _hard_stop_step(), budget))

    def test_h4_no_declared_ceiling_cannot_be_exceeded(self) -> None:
        """'超過約定花費上限' presupposes an agreement. No estimate in the
        budget state = no ceiling = nothing to exceed."""
        self.assertEqual([], self.mod.hard_stop_check("補測試", _hard_stop_step(), {}))
        self.assertEqual([], self.mod.hard_stop_check("補測試", _hard_stop_step(), None))

    # -- overlap -------------------------------------------------------------

    def test_overlapping_categories_are_all_reported_not_first_match(self) -> None:
        """force-push is both irreversible (H2) and outward (H3). Reporting
        only the first would make S4.3's `Hard-stop check: H3 no` line a
        false statement in the audit record."""
        hits = self.check("git push --force-with-lease origin feat/x")
        self.assertIn("H2", hits)
        self.assertIn("H3", hits)

    # -- bypass tests (must have been red first) -----------------------------

    def test_bypass_a_word_present_but_not_in_command_position(self) -> None:
        """The lesson from
        guard-regex-must-anchor-on-command-position-not-word-presence:
        `\\b` made `helm/frontend/...` and `rm-guide.md` look like commands."""
        for action in (
            "更新 docs/rm-guide.md 的說明",
            "為 rmdir 這個 helper 補測試",
            "調整 performance 相關的取樣率",
            "git add helm/frontend/values-production.yaml",
            "把 scripts/delete-helper.py 的註解改掉",
            "重構 dropdown 元件",
        ):
            with self.subTest(action=action):
                self.assertNotIn(
                    "H2", self.check(action),
                    f"word-presence false positive: {action!r}",
                )

    def test_bypass_b_command_position_reached_through_a_wrapper(self) -> None:
        """A guard that only looks at the first token of a line is bypassed
        by any wrapper. Command position is not 'token index 0'."""
        for action in (
            "sudo rm -rf ./build",
            "find . -name '*.tmp' | xargs rm",
            "time rm ./out.json",
            "env FOO=1 rm ./out.json",
            "bash -c \"rm ./out.json\"",
            "nohup kubectl delete pod foo &",
            "make deploy && gh pr merge 8102",
        ):
            with self.subTest(action=action):
                self.assertTrue(
                    self.check(action),
                    f"wrapper bypass: {action!r} produced no hard stop",
                )


class AutoReplySettingTestCase(unittest.TestCase):
    """S4.2 setting: `auto_reply` in state JSON, default and legacy `off`.

    S1.2 measured ~187 in-flight states across every repo sharing this
    runner: zero carry any auto-reply field. 'missing field == off' is
    therefore the whole backward-compatibility story -- no version
    negotiation, and a missing field is never invalid.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_module_from_path(PLAN_RUNNER, "plan_runner_autoreply")

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)
        self.plan_path = self.tmp_path / "auto-reply-plan.md"
        self.plan_path.write_text(PLAN_TEXT, encoding="utf-8")

    def state_file(self) -> Path:
        return self.tmp_path / ".plan-state" / "auto-reply-plan.state.json"

    def test_init_writes_auto_reply_off(self) -> None:
        r = run_cli("init", str(self.plan_path), "--no-attach")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        state = json.loads(self.state_file().read_text(encoding="utf-8"))
        self.assertEqual(state["auto_reply"], "off")

    def test_init_output_does_not_mention_auto_reply(self) -> None:
        """Golden `init` stdout is byte-compared against base e745670."""
        r = run_cli("init", str(self.plan_path), "--no-attach")
        self.assertNotIn("auto_reply", r.stdout)
        self.assertNotIn("AUTO-REPLY", r.stdout)

    def test_missing_field_reads_as_off_and_is_not_invalid(self) -> None:
        run_cli("init", str(self.plan_path), "--no-attach")
        state = json.loads(self.state_file().read_text(encoding="utf-8"))
        del state["auto_reply"]
        self.state_file().write_text(json.dumps(state), encoding="utf-8")

        loaded = self.mod.load_state(self.plan_path)
        self.assertIsNotNone(loaded, "legacy state must still load")
        self.assertEqual(self.mod.auto_reply_setting(loaded), "off")

        r = run_cli("next", str(self.plan_path))
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertNotIn("AUTO-REPLY", r.stdout)

    def test_on_reads_as_on(self) -> None:
        self.assertEqual(self.mod.auto_reply_setting({"auto_reply": "on"}), "on")
        self.assertEqual(self.mod.auto_reply_setting({"auto_reply": " ON "}), "on")

    def test_anything_that_is_not_on_reads_as_off(self) -> None:
        for raw in ("off", "", "yes", "true", None, 1, [], {}, "onn"):
            with self.subTest(raw=raw):
                self.assertEqual(self.mod.auto_reply_setting({"auto_reply": raw}), "off")
        self.assertEqual(self.mod.auto_reply_setting({}), "off")
        self.assertEqual(self.mod.auto_reply_setting(None), "off")

    def test_no_auto_reply_flag_overrides_on_for_one_call(self) -> None:
        state = {"auto_reply": "on"}
        self.assertEqual(self.mod.resolve_auto_reply(state), "on")
        self.assertEqual(self.mod.resolve_auto_reply(state, no_auto_reply=True), "off")
        self.assertEqual(state["auto_reply"], "on", "escape hatch must not write back")

    def test_next_surfaces_auto_reply_only_when_enabled(self) -> None:
        run_cli("init", str(self.plan_path), "--no-attach")
        state = json.loads(self.state_file().read_text(encoding="utf-8"))
        state["auto_reply"] = "on"
        self.state_file().write_text(json.dumps(state), encoding="utf-8")

        r = run_cli("next", str(self.plan_path))
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("AUTO-REPLY: on", r.stdout)

        r = run_cli("next", str(self.plan_path), "--no-auto-reply")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("AUTO-REPLY: off", r.stdout)
        reread = json.loads(self.state_file().read_text(encoding="utf-8"))
        self.assertEqual(reread["auto_reply"], "on", "must not persist the override")

    def test_init_force_resets_auto_reply_to_off(self) -> None:
        """The documented drift remedy (`rm state && init`) and `init
        --force` both rebuild state wholesale, which clears the setting.
        Failing back to 'ask every time' is the safe direction."""
        run_cli("init", str(self.plan_path), "--no-attach")
        state = json.loads(self.state_file().read_text(encoding="utf-8"))
        state["auto_reply"] = "on"
        self.state_file().write_text(json.dumps(state), encoding="utf-8")

        r = run_cli("init", str(self.plan_path), "--force", "--no-attach")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        reread = json.loads(self.state_file().read_text(encoding="utf-8"))
        self.assertEqual(reread["auto_reply"], "off")


class HardStopCliTestCase(unittest.TestCase):
    """S4.2 query interface: `plan_runner.py hard-stop <plan> <step>`.

    Judgment only -- this subcommand answers a question, it never answers
    *for* anyone and never writes state. Auto-answering behaviour is S4.3.
    """

    HARD_STOP_PLAN = """# Hard Stop Fixture

### Phase 1: Setup

- [ ] S1 Routine step
  - Files: `a.py`
  - Action: 依既有慣例補一個單元測試

- [ ] S2 Destructive step
  - Dependencies: S1
  - Files: `helm/smb-api/values.yaml`
  - Action: 跑 sudo rm -rf ./build 後 gh pr merge 8102
"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)
        self.plan_path = self.tmp_path / "hard-stop-plan.md"
        self.plan_path.write_text(self.HARD_STOP_PLAN, encoding="utf-8")
        r = run_cli("init", str(self.plan_path), "--no-attach")
        self.assertEqual(r.returncode, 0, msg=r.stderr)

    def test_clear_step_reports_all_four_no(self) -> None:
        r = run_cli("hard-stop", str(self.plan_path), "S1")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("Hard-stop check: H1 no / H2 no / H3 no / H4 no", r.stdout)
        self.assertIn("CLEAR", r.stdout)

    def test_hit_step_reports_categories_rules_and_stop_verdict(self) -> None:
        r = run_cli("hard-stop", str(self.plan_path), "S2")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("H2 yes", r.stdout)
        self.assertIn("H3 yes", r.stdout)
        self.assertIn("STOP", r.stdout)
        self.assertIn("rule:", r.stdout)

    def test_json_output_shape(self) -> None:
        r = run_cli("hard-stop", str(self.plan_path), "S2", "--format", "json")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["step"], "S2")
        self.assertFalse(payload["clear"])
        self.assertIn("H3", payload["hard_stop"])
        self.assertEqual(payload["auto_reply"], "off")
        self.assertTrue(all("rule" in f for f in payload["findings"]))

    def test_budget_flags_feed_h4(self) -> None:
        r = run_cli(
            "hard-stop", str(self.plan_path), "S1",
            "--step-estimated-tokens", "100000",
            "--step-actual-tokens", "250000",
            "--format", "json",
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("H4", json.loads(r.stdout)["hard_stop"])

    def test_unknown_step_is_an_error(self) -> None:
        r = run_cli("hard-stop", str(self.plan_path), "S99")
        self.assertNotEqual(r.returncode, 0)

    def test_subcommand_never_mutates_state(self) -> None:
        state_path = self.tmp_path / ".plan-state" / "hard-stop-plan.state.json"
        before = state_path.read_bytes()
        run_cli("hard-stop", str(self.plan_path), "S2")
        self.assertEqual(before, state_path.read_bytes())


# ---------------------------------------------------------------------------
# S4.3 -- hard-stop findings ride the hook reason; `Auto-answered:` /
# `--keep-going` / the `auto_reply="on"` enable gate.
#
# Per the plan's S4.3 Addendum, only half of this step is code-enforceable:
# "判定的送達可以強制，留痕的執行不行." HookHardStopWiringTestCase covers the
# enforceable half (findings reaching the Stop hook reason unconditionally).
# The unenforceable half (an agent actually writing `## Auto-answered` to
# checkpoint.md) has no interception point to test against; it is a content
# contract in plan-run/SKILL.md, checked below only for presence of the
# documented text, never for compliance.
# ---------------------------------------------------------------------------


class HookHardStopWiringTestCase(unittest.TestCase):
    """S4.3 main deliverable: `_branch_ready_step()` calls
    `hard_stop_findings()` on the ready step and threads any hits into the
    Stop hook's `next_step` block reason via `_hook_block()`'s `suffix` --
    the same mechanism `_ASSIGN_REPEAT_NOTE` already uses, and the two must
    coexist rather than overwrite each other.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_module_from_path(PLAN_RUNNER, "plan_runner_hardstop_hook")

    def _fixture(self, action: str, *, assign_repeat_count: int = 0):
        mod = self.mod
        plan_dir = Path.home() / ".plan-run-s43-fixture"
        pointer = mod.new_pointer_record(
            plan_path=plan_dir / "plan.md",
            repo_root=plan_dir,
            cwd=plan_dir,
            session_id="s43-session",
        )
        pointer["last_seen_completed_count"] = 0
        if assign_repeat_count:
            pointer["last_assigned_step_id"] = "S1"
            pointer["assign_repeat_count"] = assign_repeat_count
        state = {
            "plan_path": str(plan_dir / "plan.md"),
            "slug": "s43-fixture",
            "title": "S4.3 fixture",
            "phase_order": ["P1"],
            "parent_task_id": None,
            "created_at": mod.now_iso(),
            "updated_at": mod.now_iso(),
            "steps": {
                "S1": {
                    "id": "S1", "title": "first", "phase": "P1", "deps": [],
                    "agent": None, "skill": None, "command": None, "files": None,
                    "action": action, "risk": None, "status": "pending",
                    "task_id": None, "started_at": None, "completed_at": None,
                    "failure_reason": None,
                },
            },
        }
        hook_input = {
            "hook_event_name": "Stop",
            "session_id": "s43-session",
            "transcript_path": str(plan_dir / "transcript.jsonl"),
            "cwd": str(plan_dir),
            "stop_hook_active": True,
        }
        return hook_input, pointer, state

    def _decide(self, action: str, **kw):
        hook_input, pointer, state = self._fixture(action, **kw)
        return self.mod.decide_hook_action(hook_input, pointer, state, lambda p: None)

    def test_hard_stop_hit_reaches_the_hook_reason(self) -> None:
        decision = self._decide("跑 sudo rm -rf ./build 然後收工")
        self.assertEqual(decision.decision, "block")
        self.assertIn("HARD-STOP", decision.reason)
        self.assertIn("H2", decision.reason)
        self.assertIn("不得自動決定", decision.reason)

    def test_clean_step_carries_no_hard_stop_note(self) -> None:
        decision = self._decide("依既有慣例補一個單元測試")
        self.assertEqual(decision.decision, "block")
        self.assertNotIn("HARD-STOP", decision.reason)

    def test_hard_stop_note_cites_rule_and_evidence(self) -> None:
        decision = self._decide("跑 sudo rm -rf ./build")
        self.assertIn("rule:", decision.reason)
        self.assertIn("evidence:", decision.reason)

    def test_multiple_categories_all_listed(self) -> None:
        decision = self._decide(
            "Owner: 使用者\n跑 sudo rm -rf ./build 然後 gh pr merge 8102"
        )
        self.assertIn("H1", decision.reason)
        self.assertIn("H2", decision.reason)
        self.assertIn("H3", decision.reason)

    def test_coexists_with_assign_repeat_note_both_present(self) -> None:
        """R11/plan §5: the two suffixes must not overwrite each other."""
        decision = self._decide("跑 sudo rm -rf ./build", assign_repeat_count=1)
        self.assertIn("HARD-STOP", decision.reason)
        self.assertIn("連續第", decision.reason)  # _ASSIGN_REPEAT_NOTE text

    def test_hard_stop_note_precedes_repeat_note(self) -> None:
        decision = self._decide("跑 sudo rm -rf ./build", assign_repeat_count=1)
        self.assertLess(
            decision.reason.index("HARD-STOP"),
            decision.reason.index("連續第"),
        )

    def test_h4_never_fires_from_the_hook_path(self) -> None:
        """No real token accounting reaches this call site (S4.2 finding,
        unresolved by S4.3 -- see plan-run/SKILL.md's H4 caveat). H4 must
        stay silent here rather than appear to auto-clear or false-fire."""
        decision = self._decide("依既有慣例補一個單元測試")
        self.assertNotIn("H4", decision.reason)
        decision = self._decide("跑 sudo rm -rf ./build")
        self.assertNotIn("H4", decision.reason)

    def test_evidence_text_is_sanitized(self) -> None:
        """The hook reason is the authoritative (non-fenced) region; a
        control byte smuggled in via the step's action text must not
        survive into it."""
        decision = self._decide("跑 sudo rm -rf ./build\x07 完成")
        self.assertNotIn("\x07", decision.reason)


class CheckpointWritableProbeTestCase(unittest.TestCase):
    """`_checkpoint_writable()`: the real-I/O probe backing the R11/T12
    enable gate. Called with a real `Path` -- the same type
    `checkpoint_path_for()` returns to its one production caller,
    `cmd_auto_reply()` -- per the standing lesson that a probe tested only
    with the "wrong" type for its real caller can pass while the
    production wiring is still broken.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_module_from_path(PLAN_RUNNER, "plan_runner_checkpoint_writable")

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    def test_writable_directory_with_no_existing_file_passes(self) -> None:
        path = self.tmp_path / "sub" / "plan.checkpoint.md"
        self.assertIsNone(self.mod._checkpoint_writable(path))

    def test_probe_leaves_no_file_behind(self) -> None:
        target_dir = self.tmp_path / "sub"
        path = target_dir / "plan.checkpoint.md"
        self.mod._checkpoint_writable(path)
        self.assertFalse(path.exists(), "must not create the checkpoint file itself")
        leftovers = list(target_dir.glob(".*autoreply-probe*")) if target_dir.exists() else []
        self.assertEqual(leftovers, [], "probe file must be cleaned up")

    def test_unwritable_directory_fails(self) -> None:
        locked = self.tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o500)
        self.addCleanup(locked.chmod, 0o700)
        path = locked / "plan.checkpoint.md"
        problem = self.mod._checkpoint_writable(path)
        self.assertIsNotNone(problem)

    def test_existing_unwritable_checkpoint_file_fails(self) -> None:
        path = self.tmp_path / "plan.checkpoint.md"
        path.write_text("existing content", encoding="utf-8")
        path.chmod(0o400)
        self.addCleanup(path.chmod, 0o600)
        problem = self.mod._checkpoint_writable(path)
        self.assertIsNotNone(problem)

    def test_existing_writable_checkpoint_file_passes_and_is_untouched(self) -> None:
        path = self.tmp_path / "plan.checkpoint.md"
        path.write_text("existing content", encoding="utf-8")
        self.assertIsNone(self.mod._checkpoint_writable(path))
        self.assertEqual(path.read_text(encoding="utf-8"), "existing content")


class AutoReplyEnableCliTestCase(unittest.TestCase):
    """`plan_runner.py auto-reply <plan> on|off` -- the only place
    `state["auto_reply"]` is ever written (S4.2 only wrote the "off"
    default at init time). Enabling is refused when the checkpoint path
    is not writable (R11/T12): "no record possible" must never be
    silently accepted as "on".
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)
        self.plan_path = self.tmp_path / "auto-reply-enable-plan.md"
        self.plan_path.write_text(PLAN_TEXT, encoding="utf-8")
        r = run_cli("init", str(self.plan_path), "--no-attach")
        self.assertEqual(r.returncode, 0, msg=r.stderr)

    def state_file(self) -> Path:
        return self.tmp_path / ".plan-state" / "auto-reply-enable-plan.state.json"

    def checkpoint_path(self) -> Path:
        return self.tmp_path / ".plan-state" / "auto-reply-enable-plan.checkpoint.md"

    def test_enables_when_checkpoint_path_writable(self) -> None:
        r = run_cli("auto-reply", str(self.plan_path), "on")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        state = json.loads(self.state_file().read_text(encoding="utf-8"))
        self.assertEqual(state["auto_reply"], "on")

    def test_enabling_does_not_create_the_checkpoint_file(self) -> None:
        run_cli("auto-reply", str(self.plan_path), "on")
        self.assertFalse(self.checkpoint_path().exists())

    def test_refuses_when_checkpoint_dir_not_writable(self) -> None:
        state_dir = self.tmp_path / ".plan-state"
        state_dir.chmod(0o500)
        self.addCleanup(state_dir.chmod, 0o700)

        r = run_cli("auto-reply", str(self.plan_path), "on")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("REFUSED", r.stdout)

        state_dir.chmod(0o700)
        state = json.loads(self.state_file().read_text(encoding="utf-8"))
        self.assertEqual(state["auto_reply"], "off")
        state_dir.chmod(0o500)  # restore for addCleanup's own chmod to be a no-op-safe reset

    def test_refuses_when_checkpoint_file_itself_not_writable(self) -> None:
        self.checkpoint_path().write_text("existing", encoding="utf-8")
        self.checkpoint_path().chmod(0o400)
        self.addCleanup(self.checkpoint_path().chmod, 0o600)

        r = run_cli("auto-reply", str(self.plan_path), "on")
        self.assertNotEqual(r.returncode, 0)
        state = json.loads(self.state_file().read_text(encoding="utf-8"))
        self.assertEqual(state["auto_reply"], "off")

    def test_json_format_refusal_shape(self) -> None:
        state_dir = self.tmp_path / ".plan-state"
        state_dir.chmod(0o500)
        self.addCleanup(state_dir.chmod, 0o700)

        r = run_cli("auto-reply", str(self.plan_path), "on", "--format", "json")
        state_dir.chmod(0o700)
        self.assertNotEqual(r.returncode, 0)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["status"], "refused")
        self.assertEqual(payload["auto_reply"], "off")
        state_dir.chmod(0o500)

    def test_off_resets_to_off(self) -> None:
        run_cli("auto-reply", str(self.plan_path), "on")
        r = run_cli("auto-reply", str(self.plan_path), "off")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        state = json.loads(self.state_file().read_text(encoding="utf-8"))
        self.assertEqual(state["auto_reply"], "off")

    def test_off_never_probes_the_checkpoint_path(self) -> None:
        """Turning off is always safe: it must not refuse even when the
        checkpoint file itself is unwritable, since off writes no records
        to it -- only state.json, which this scenario leaves untouched."""
        self.checkpoint_path().write_text("existing", encoding="utf-8")
        self.checkpoint_path().chmod(0o400)
        self.addCleanup(self.checkpoint_path().chmod, 0o600)
        r = run_cli("auto-reply", str(self.plan_path), "off")
        self.assertEqual(r.returncode, 0, msg=r.stderr)


class NextKeepGoingFlagTestCase(unittest.TestCase):
    """`next --keep-going` (Addendum-2): one-shot, this-call-only auto-reply
    override. Never written to state; `next`-only (the Stop hook path takes
    no such argument, so it structurally cannot honor it -- that half is
    documented in plan-run/SKILL.md, not tested here beyond string
    presence, since there is no CLI surface to call it against).
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)
        self.plan_path = self.tmp_path / "keep-going-plan.md"
        self.plan_path.write_text(PLAN_TEXT, encoding="utf-8")
        r = run_cli("init", str(self.plan_path), "--no-attach")
        self.assertEqual(r.returncode, 0, msg=r.stderr)

    def state_file(self) -> Path:
        return self.tmp_path / ".plan-state" / "keep-going-plan.state.json"

    def test_keep_going_reports_on_for_this_call_only(self) -> None:
        r = run_cli("next", str(self.plan_path), "--keep-going")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("AUTO-REPLY: on", r.stdout)

    def test_keep_going_is_not_written_back_to_state(self) -> None:
        run_cli("next", str(self.plan_path), "--keep-going")
        state = json.loads(self.state_file().read_text(encoding="utf-8"))
        self.assertEqual(state["auto_reply"], "off")

    def test_without_the_flag_state_stays_off_and_silent(self) -> None:
        r = run_cli("next", str(self.plan_path))
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertNotIn("AUTO-REPLY", r.stdout)

    def test_no_auto_reply_overrides_keep_going(self) -> None:
        """Turning off always wins over turning on -- consistent with
        `resolve_auto_reply()`'s existing precedence for the stored-"on"
        case (T11d escape hatch)."""
        r = run_cli(
            "next", str(self.plan_path), "--keep-going", "--no-auto-reply",
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("AUTO-REPLY: off", r.stdout)

    def test_keep_going_json_payload(self) -> None:
        r = run_cli(
            "next", str(self.plan_path), "--keep-going", "--format", "json",
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["auto_reply"], "on")


class ResolveAutoReplyKeepGoingTestCase(unittest.TestCase):
    """Pure-function coverage for `resolve_auto_reply(..., keep_going=...)`."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_module_from_path(PLAN_RUNNER, "plan_runner_keep_going")

    def test_keep_going_turns_on_a_stored_off(self) -> None:
        self.assertEqual(
            self.mod.resolve_auto_reply({"auto_reply": "off"}, keep_going=True),
            "on",
        )

    def test_keep_going_is_redundant_with_a_stored_on(self) -> None:
        self.assertEqual(
            self.mod.resolve_auto_reply({"auto_reply": "on"}, keep_going=True),
            "on",
        )

    def test_no_auto_reply_beats_keep_going(self) -> None:
        self.assertEqual(
            self.mod.resolve_auto_reply(
                {"auto_reply": "off"}, no_auto_reply=True, keep_going=True,
            ),
            "off",
        )

    def test_neither_flag_is_unchanged(self) -> None:
        self.assertEqual(self.mod.resolve_auto_reply({"auto_reply": "off"}), "off")


class SkillDocContractTestCase(unittest.TestCase):
    """plan-run/SKILL.md's content contract for S4.3 -- checked only for
    presence of the documented terms, exactly as the plan's Addendum
    frames it: this half cannot be enforced by code, only written down and
    kept from silently disappearing on a future edit.

    Assertions are scoped to the `## Auto-reply` section specifically
    (via `_section()`), not the whole file: several of these terms
    (`hook`, `token`, `log`, "內容契約") already occur elsewhere in
    SKILL.md for unrelated reasons (the pre-existing checkpoint contract,
    Stop hook setup docs), so an unscoped `assertIn` would pass by
    coincidence whether or not S4.3's own text was ever written.
    """

    SKILL_MD = REPO_ROOT / "plan-run" / "SKILL.md"

    def setUp(self) -> None:
        self.text = self.SKILL_MD.read_text(encoding="utf-8")

    def _section(self, heading: str) -> str:
        """Text of a `## <heading>` section, up to the next top-level `## `
        heading or EOF. Fails the test immediately if the heading is
        absent.

        Lines inside fenced code blocks (```...```) are never treated as a
        section boundary -- this section's own worked example is a
        checkpoint.md snippet that itself contains a literal `## Auto-
        answered` line, which a fence-blind scan would mistake for the end
        of the SKILL.md section.
        """
        marker = f"## {heading}"
        start = self.text.find(marker)
        self.assertGreaterEqual(start, 0, f"missing section: {marker!r}")
        lines = self.text[start + len(marker):].split("\n")
        in_fence = False
        end_line = len(lines)
        for i, line in enumerate(lines):
            if line.strip().startswith("```"):
                in_fence = not in_fence
                continue
            if not in_fence and line.startswith("## "):
                end_line = i
                break
        return "\n".join(lines[:end_line])

    def test_auto_reply_section_exists(self) -> None:
        self._section("Auto-reply")  # raises via assertGreaterEqual if absent

    def test_auto_answered_checkpoint_block_is_documented(self) -> None:
        section = self._section("Auto-reply")
        self.assertIn("## Auto-answered", section)

    def test_auto_answered_required_subfields_are_documented(self) -> None:
        section = self._section("Auto-reply")
        for token in ("Took:", "Safe because:", "Hard-stop check"):
            with self.subTest(token=token):
                self.assertIn(token, section)

    def test_auto_answered_is_a_content_contract_not_a_program_gate(self) -> None:
        section = self._section("Auto-reply")
        self.assertIn("內容契約，不是程式 gate", section)

    def test_h4_caveat_is_documented(self) -> None:
        section = self._section("Auto-reply")
        self.assertIn("H4 需呼叫端提供實際值，否則不成立", section)

    def test_enable_refusal_is_documented(self) -> None:
        section = self._section("Auto-reply")
        self.assertIn("拒絕啟用", section)

    def test_keep_going_semantics_are_documented(self) -> None:
        section = self._section("Auto-reply")
        self.assertIn("--keep-going", section)
        self.assertIn("不寫回 state", section)

    def test_keep_going_hook_path_exclusion_is_documented(self) -> None:
        section = self._section("Auto-reply")
        self.assertIn("Stop hook 路徑不吃這個旗標", section)

    def test_hard_stops_still_apply_under_keep_going_is_documented(self) -> None:
        section = self._section("Auto-reply")
        self.assertIn("四類硬停止", section)

    def test_four_delivery_paths_are_documented(self) -> None:
        """Review finding: the doc used to conflate CLI `next` with the
        Stop hook's block reason, and to claim `next` pushed findings into
        a "hook reason" it never touches in mode A. A later follow-up
        found `recap` -- the post-compaction/handoff recovery entrypoint --
        missing entirely. All four real delivery surfaces -- mode A's CLI
        output, `recap`, mode B's Stop hook, and the manual `hard-stop`
        query -- must be named distinctly."""
        section = self._section("Auto-reply")
        self.assertIn("模式 A", section)
        self.assertIn("recap", section)
        self.assertIn("模式 B", section)
        self.assertIn("Newly unlocked", section)
        self.assertIn("plan_runner.py hard-stop", section)

    def test_no_log_excerpt_or_secret_rule_is_restated(self) -> None:
        section = self._section("Auto-reply")
        self.assertIn("log 原文", section)
        self.assertIn("token / key / password / JWT", section)


# ---------------------------------------------------------------------------
# S4.3 follow-up (review finding): hard_stop_findings() was wired into the
# Stop hook path (_branch_ready_step(), mode B) only. Mode A -- the
# *default* mode per plan-run/SKILL.md Step 1.5 -- drives entirely off CLI
# `next` (and the transition commands' own delta output), so a plan run in
# mode A never saw a hard-stop finding at all. This section covers the CLI
# path specifically; HookHardStopWiringTestCase above already covers the
# Stop hook path and is unaffected by this fix.
# ---------------------------------------------------------------------------


class NextCliHardStopTestCase(unittest.TestCase):
    """`_format_full_step_block()` backs every CLI surface that hands a
    ready step to a driving agent: `next`'s full listing, and every
    transition command's ("start"/"complete"/"fail"/"skip") "Newly
    unlocked" delta block. Hard-stop findings must appear there
    unconditionally -- no separate `hard-stop` invocation to remember.
    """

    HARD_STOP_NEXT_PLAN = """# Next Hard Stop Fixture

### Phase 1: Setup

- [ ] S1 Destructive step
  - Files: `helm/smb-api/values.yaml`
  - Action: 跑 sudo rm -rf ./data 後 git push --force
"""

    HARD_STOP_TRANSITION_PLAN = """# Next Hard Stop Transition Fixture

### Phase 1: Setup

- [ ] S1 Benign step
  - Action: 依既有慣例補一個單元測試

- [ ] S2 Destructive step
  - Dependencies: S1
  - Files: `helm/smb-api/values.yaml`
  - Action: 跑 sudo rm -rf ./data 後 git push --force
"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)
        self.plan_path = self.tmp_path / "next-hard-stop-plan.md"
        self.plan_path.write_text(self.HARD_STOP_NEXT_PLAN, encoding="utf-8")
        r = run_cli("init", str(self.plan_path), "--no-attach")
        self.assertEqual(r.returncode, 0, msg=r.stderr)

    def test_hard_stop_is_surfaced_in_next_output(self) -> None:
        r = run_cli("next", str(self.plan_path))
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("HARD-STOP", r.stdout)
        self.assertIn("H2", r.stdout)
        self.assertIn("H3", r.stdout)
        self.assertIn("rule:", r.stdout)
        self.assertIn("evidence:", r.stdout)

    def test_benign_plan_has_no_hard_stop_text_in_next_output(self) -> None:
        """The negative direction: a plan whose only ready step is benign
        must not gain a spurious HARD-STOP paragraph."""
        benign_path = self.tmp_path / "benign-plan.md"
        benign_path.write_text(PLAN_TEXT, encoding="utf-8")
        run_cli("init", str(benign_path), "--no-attach")
        r = run_cli("next", str(benign_path))
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertNotIn("HARD-STOP", r.stdout)

    def test_hard_stop_survives_keep_going(self) -> None:
        """SKILL.md claims the four categories are delivered regardless of
        `--keep-going`; this is the CLI-path half of that claim."""
        r = run_cli("next", str(self.plan_path), "--keep-going")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("HARD-STOP", r.stdout)

    def test_h4_never_fires_from_cli_next(self) -> None:
        """No real token accounting reaches this call site either (same
        S4.2 finding as the hook path) -- H4 must stay silent, not appear
        clear."""
        r = run_cli("next", str(self.plan_path))
        self.assertNotIn("H4", r.stdout)

    def test_hard_stop_surfaces_in_complete_delta_output(self) -> None:
        """The shared rendering function also backs `complete`'s "Newly
        unlocked" block -- a driving agent that reacts only to each
        transition's own output (never calling `next` on its own) must
        see the finding there too."""
        plan_path = self.tmp_path / "next-hard-stop-transition-plan.md"
        plan_path.write_text(self.HARD_STOP_TRANSITION_PLAN, encoding="utf-8")
        run_cli("init", str(plan_path), "--no-attach")
        r = run_cli("start", str(plan_path), "S1")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        r = run_cli("complete", str(plan_path), "S1")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("HARD-STOP", r.stdout)


# ---------------------------------------------------------------------------
# S4.3 second follow-up (review finding): `recap` uses its own renderer
# (`_format_recap_next_step()`), separate from `_format_full_step_block()`
# -- so fixing the CLI `next` gap above did not fix `recap`. `recap` is
# S3.3's single unattended-*recovery* entrypoint: the first command run
# after compaction, a new session, or a handoff to someone else -- exactly
# the moment with the least context to independently think to check
# hard-stop. Reviewer's call: in scope, must be fixed the same way.
# ---------------------------------------------------------------------------


class RecapCliHardStopTestCase(unittest.TestCase):
    """CLI `recap` counterpart to `NextCliHardStopTestCase`. Uses the same
    HOME-isolated env pattern as `RecapCliTestCase` above, since `recap`
    also does a pointer lookup keyed on cwd.
    """

    HARD_STOP_RECAP_PLAN = """# Recap Hard Stop Fixture

### Phase 1: Setup

- [ ] S1 Destructive step
  - Files: `helm/smb-api/values.yaml`
  - Action: 跑 rm -rf ./data 然後 git push --force origin main
"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)
        self.plan_path = self.tmp_path / "recap-hard-stop-plan.md"
        self.plan_path.write_text(self.HARD_STOP_RECAP_PLAN, encoding="utf-8")

        self._home_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._home_tmp.cleanup)
        self.env = dict(os.environ)
        self.env["HOME"] = self._home_tmp.name

        r = run_cli("init", str(self.plan_path), "--no-attach", env=self.env)
        self.assertEqual(r.returncode, 0, msg=r.stderr)

    def recap(self) -> subprocess.CompletedProcess:
        return run_cli(
            "recap", str(self.plan_path), cwd=self.tmp_path, env=self.env,
        )

    def test_hard_stop_is_surfaced_in_recap_output(self) -> None:
        r = self.recap()
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("HARD-STOP", r.stdout)
        self.assertIn("H2", r.stdout)
        self.assertIn("H3", r.stdout)

    def test_benign_plan_has_no_hard_stop_text_in_recap_output(self) -> None:
        benign_path = self.tmp_path / "recap-benign-plan.md"
        benign_path.write_text(RECAP_PLAN_TEXT, encoding="utf-8")
        run_cli("init", str(benign_path), "--no-attach", env=self.env)
        r = run_cli("recap", str(benign_path), cwd=self.tmp_path, env=self.env)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertNotIn("HARD-STOP", r.stdout)


class ReadyStepHardStopDeliveryTestCase(unittest.TestCase):
    """Enumerates every renderer that hands a ready step's fields to a
    human/agent to read, and asserts each one delivers hard-stop findings
    on a hit and stays silent on a benign step. This list must be kept in
    sync with `_hard_stop_hook_note()`'s own docstring, which names the
    same call sites in prose.

    This is an enumeration, not introspection: it cannot catch a fourth
    renderer nobody added an entry for. Its job is to make forgetting
    *visible* -- a new ready-step renderer belongs in `RENDERERS` below in
    the same change that adds it, and a reviewer (or future editor) who
    greps for `_hard_stop_hook_note(` will find every existing call site
    listed here, once, in one place.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_module_from_path(PLAN_RUNNER, "plan_runner_ready_step_delivery")

    HITTING_ACTION = "跑 rm -rf ./data 然後 git push --force origin main"
    BENIGN_ACTION = "依既有慣例補一個單元測試"

    def _step(self, action: str) -> dict:
        # `files` only carries a backend-owned path for the hitting
        # fixture -- reusing it for the benign action would trip H3 on
        # `files` alone regardless of `action`, which is a fixture bug,
        # not a finding about the code under test.
        files = "helm/smb-api/values.yaml" if action == self.HITTING_ACTION else "a.py"
        return {
            "id": "S1", "title": "t", "phase": "P1", "deps": [],
            "agent": None, "skill": None, "command": None,
            "files": files, "action": action, "risk": None,
        }

    def _render_full_step_block(self, action: str) -> str:
        return "\n".join(self.mod._format_full_step_block(self._step(action)))

    def _render_recap_next_step(self, action: str) -> str:
        return "\n".join(
            self.mod._format_recap_next_step(self._step(action), Path("/tmp/plan.md"))
        )

    def _render_hook_suffix(self, action: str) -> str:
        step = self._step(action)
        findings = self.mod.hard_stop_findings(action, step, None)
        return self.mod._hard_stop_hook_note(findings) if findings else ""

    # Keep in sync with _hard_stop_hook_note()'s docstring list.
    RENDERERS = (
        ("next / transition delta (_format_full_step_block)", _render_full_step_block),
        ("recap (_format_recap_next_step)", _render_recap_next_step),
        ("Stop hook block reason (_branch_ready_step via _hard_stop_hook_note)", _render_hook_suffix),
    )

    def test_every_known_renderer_delivers_on_a_hit(self) -> None:
        for name, render in self.RENDERERS:
            with self.subTest(renderer=name):
                self.assertIn("HARD-STOP", render(self, self.HITTING_ACTION))

    def test_every_known_renderer_is_silent_on_benign(self) -> None:
        for name, render in self.RENDERERS:
            with self.subTest(renderer=name):
                self.assertNotIn("HARD-STOP", render(self, self.BENIGN_ACTION))


if __name__ == "__main__":
    unittest.main()
