"""Red-phase tests for `complete`'s step summary/evidence feature (S1.1 of
plans/active/plan-runner-step-summary-and-completion-report.md).

Target interface (not yet implemented — this file is the RED half of the
red/green cycle; S1.2 implements it):
    - `STEP_SUMMARY_MAX_CHARS = 500`, `STEP_EVIDENCE_MAX_ITEMS = 20`
      (evidence per-item cap reuses the existing `PLAN_PATH_TRUNCATE_CHARS`
      == 300)
    - `_normalize_step_summary(raw: str | None) -> str | None`
    - `_normalize_evidence(raw: list[str] | None) -> tuple[str, ...] | None`
    - `complete`'s argparse gets `--summary` (default None) and `--evidence`
      (action="append", default None)
    - `complete`'s JSON payload gets a `recorded: {summary_chars,
      evidence_count}` key only when a flag was passed; `format_transition_md`
      emits a matching `Recorded: summary <N> chars, evidence <M>` line.
    - `init_state` seeds every step with `summary: None, evidence: []`;
      `reset` clears both back to that.
    - `complete`/`fail`/`skip` take the state lock the same way `start`
      already does.

Conventions follow scripts/tests/test_plan_run_hook.py (make_step/make_state/
make_pointer/make_budget, `_never_acquires`) and
scripts/tests/test_plan_runner_regression.py (`run_cli` via subprocess,
stdlib-only). This file is self-contained (no cross-import from sibling test
modules) and only adds a new file — it does not touch plan_runner.py.

Isolation: every on-disk plan lives under a
`tempfile.TemporaryDirectory(dir=Path.home(), prefix=".plan-run-test-")`,
mirroring ConcurrencySerializationTests in test_plan_run_hook.py. `init` is
always called with `attach=False`, so no pointer file is ever written under
the real `~/.claude/plan-run/active/`. Pure-function fixtures (SentinelLeak's
render_hook_reason/decide_hook_action checks) build in-memory state/pointer
dicts the same way test_plan_run_hook.py's make_state/make_pointer do, and
never touch disk at all.

Run: cd <worktree> && python3 -m unittest scripts.tests.test_plan_runner_summary -v
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import plan_runner as pr  # noqa: E402


# ---------------------------------------------------------------------------
# Plan fixture — two independent steps (no deps between them) in one phase,
# so tests can `start`/`complete`/`fail`/`skip` S1 and S2 separately without
# one step's terminal status blocking the other.
# ---------------------------------------------------------------------------

PLAN_TEXT = """# Summary Feature Test Plan

### Phase 1: Setup

- [ ] S1 First step
  - Files: `a.py`
  - Action: do A

- [ ] S2 Second step
  - Files: `b.py`
  - Action: do B
"""


# ---------------------------------------------------------------------------
# CLI-level helpers — in-process `pr.cmd_*(Namespace)` calls, so coverage
# tooling can see the lines run (a subprocess would not count).
# ---------------------------------------------------------------------------

def _capture(func, args: argparse.Namespace) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = func(args)
    return rc, out.getvalue()


def _init_args(plan_path: Path, *, fmt: str = "json") -> argparse.Namespace:
    return argparse.Namespace(plan=str(plan_path), force=False, attach=False, format=fmt)


def _start_args(plan_path: Path, step: str, *, task_id=None, fmt: str = "json") -> argparse.Namespace:
    return argparse.Namespace(
        plan=str(plan_path), step=step, task_id=task_id, session_id=None, format=fmt,
    )


def _complete_args(
    plan_path: Path,
    step: str,
    *,
    summary=None,
    evidence=None,
    fmt: str = "json",
) -> argparse.Namespace:
    return argparse.Namespace(
        plan=str(plan_path), step=step, format=fmt, summary=summary, evidence=evidence,
    )


def _fail_args(plan_path: Path, step: str, *, reason: str = "", fmt: str = "json") -> argparse.Namespace:
    return argparse.Namespace(plan=str(plan_path), step=step, reason=reason, format=fmt)


def _skip_args(plan_path: Path, step: str, *, fmt: str = "json") -> argparse.Namespace:
    return argparse.Namespace(plan=str(plan_path), step=step, format=fmt)


def _reset_args(plan_path: Path, *, step=None, all_steps=False) -> argparse.Namespace:
    return argparse.Namespace(plan=str(plan_path), step=step, all=all_steps)


def _new_plan(test_case: unittest.TestCase) -> Path:
    """A freshly `init`-ed (never-attached) plan with S1/S2 pending."""
    tmp = tempfile.TemporaryDirectory(dir=Path.home(), prefix=".plan-run-test-")
    test_case.addCleanup(tmp.cleanup)
    plan_path = Path(tmp.name).resolve() / "plan.md"
    plan_path.write_text(PLAN_TEXT, encoding="utf-8")
    rc, out = _capture(pr.cmd_init, _init_args(plan_path))
    assert rc == 0, out
    return plan_path


def _new_started_plan(test_case: unittest.TestCase, step: str = "S1") -> Path:
    """A freshly `init`-ed plan with `step` already `start`-ed (in_progress)."""
    plan_path = _new_plan(test_case)
    rc, out = _capture(pr.cmd_start, _start_args(plan_path, step))
    assert rc == 0, out
    return plan_path


@contextlib.contextmanager
def _never_acquires(lock_path):
    """Stand-in for exclusive_lock() that always reports contention.

    Copied from test_plan_run_hook.py's helper of the same name/shape —
    duplicated rather than imported to keep this file self-contained.
    """
    yield False


# ---------------------------------------------------------------------------
# Pure-function fixture helpers — mirror test_plan_run_hook.py's
# make_step/make_state/make_pointer/make_budget so SentinelLeakTests can
# drive render_hook_reason() / decide_hook_action() entirely in memory.
# ---------------------------------------------------------------------------

_FIXTURE_DIR = str(Path.home() / ".plan-run-test-fixture-summary")
_FIXTURE_PLAN_PATH = f"{_FIXTURE_DIR}/plan.md"
_FIXTURE_SESSION_ID = "sess-fixed"


def _make_pointer(**overrides) -> dict:
    now = pr.now_iso()
    base = {
        "schema_version": pr.POINTER_SCHEMA_VERSION,
        "plan_path": _FIXTURE_PLAN_PATH,
        "repo_root": _FIXTURE_DIR,
        "cwd": _FIXTURE_DIR,
        "created_at": now,
        "created_by_session": _FIXTURE_SESSION_ID,
        "driver_session_id": _FIXTURE_SESSION_ID,
        "driver_transcript_path": None,
        "last_seen_at": now,
        "last_advance_at": now,
        "paused": False,
        "consecutive_blocks": 0,
        "bg_poll_count": 0,
        "nag_counts": 0,
        "checkpoint_pending": False,
        "completion_announced": False,
        "warned_at": None,
    }
    base.update(overrides)
    return base


def _make_step(*, status="pending", deps=None, phase="P0", title="Do thing", **overrides) -> dict:
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
    }
    step.update(overrides)
    return step


def _make_state(steps: dict, *, slug="sentinel-test-plan", title="Sentinel Test Plan", phase_order=None) -> dict:
    for sid, step in steps.items():
        if step.get("id") is None:
            step["id"] = sid
    return {
        "plan_path": _FIXTURE_PLAN_PATH,
        "slug": slug,
        "title": title,
        "phase_order": phase_order or ["P0"],
        "parent_task_id": None,
        "created_at": pr.now_iso(),
        "updated_at": pr.now_iso(),
        "steps": steps,
    }


def _make_budget(**overrides) -> "pr.BudgetDecision":
    base = {
        "decision": "block",
        "consecutive_blocks": 0,
        "block_budget": 6,
        "checkpoint_pending": False,
        "steps_remaining": 1,
        "checkpoint_from_phase_boundary": False,
    }
    base.update(overrides)
    return pr.BudgetDecision(**base)


# ---------------------------------------------------------------------------
# (a) Length boundary: exactly at the cap succeeds, one over is rejected.
# ---------------------------------------------------------------------------

class SummaryLengthBoundaryTests(unittest.TestCase):
    def test_exactly_500_chars_succeeds_and_501_is_rejected_without_writing(self):
        plan = _new_started_plan(self, "S1")
        limit = pr.STEP_SUMMARY_MAX_CHARS  # AttributeError until S1.2 defines it

        ok_summary = "s" * limit
        rc_ok, out_ok = _capture(
            pr.cmd_complete, _complete_args(plan, "S1", summary=ok_summary, fmt="json"),
        )
        self.assertEqual(rc_ok, 0, out_ok)
        state = pr.load_state(plan)
        self.assertEqual(state["steps"]["S1"]["summary"], ok_summary)

        rc_start2, out_start2 = _capture(pr.cmd_start, _start_args(plan, "S2"))
        self.assertEqual(rc_start2, 0, out_start2)
        before = pr.state_path_for(plan).read_bytes()

        over_summary = "s" * (limit + 1)
        rc_bad, out_bad = _capture(
            pr.cmd_complete, _complete_args(plan, "S2", summary=over_summary, fmt="json"),
        )
        self.assertEqual(rc_bad, 1, out_bad)
        self.assertIn(str(limit), out_bad)
        self.assertEqual(pr.state_path_for(plan).read_bytes(), before, "state must be untouched on rejection")
        state2 = pr.load_state(plan)
        self.assertEqual(state2["steps"]["S2"]["status"], pr.IN_PROGRESS)


# ---------------------------------------------------------------------------
# (b) Normalization: CRLF -> LF, ANSI/bidi stripped before length counts,
#     empty/whitespace-only rejected.
# ---------------------------------------------------------------------------

class NormalizationTests(unittest.TestCase):
    def test_crlf_normalized_to_lf_before_storage(self):
        plan = _new_started_plan(self, "S1")
        raw = "line one\r\nline two\r\nline three"
        rc, out = _capture(pr.cmd_complete, _complete_args(plan, "S1", summary=raw, fmt="json"))
        self.assertEqual(rc, 0, out)
        state = pr.load_state(plan)
        self.assertEqual(state["steps"]["S1"]["summary"], "line one\nline two\nline three")

    def test_ansi_and_bidi_bytes_stripped_and_excluded_from_length_count(self):
        plan = _new_started_plan(self, "S1")
        limit = pr.STEP_SUMMARY_MAX_CHARS
        visible = "v" * limit  # exactly at the cap once noise bytes are gone
        noisy = ("\x1b[31m" * 80) + ("‮" * 80) + visible + ("\x1b[0m" * 40)
        rc, out = _capture(pr.cmd_complete, _complete_args(plan, "S1", summary=noisy, fmt="json"))
        self.assertEqual(rc, 0, out)
        state = pr.load_state(plan)
        self.assertEqual(state["steps"]["S1"]["summary"], visible)
        self.assertNotIn("\x1b", state["steps"]["S1"]["summary"])
        self.assertNotIn("‮", state["steps"]["S1"]["summary"])

    def test_empty_or_whitespace_only_summary_rejected(self):
        plan = _new_started_plan(self, "S1")
        for bad in ("", "   ", "\n\t  \n"):
            with self.subTest(bad=repr(bad)):
                before = pr.state_path_for(plan).read_bytes()
                rc, out = _capture(pr.cmd_complete, _complete_args(plan, "S1", summary=bad, fmt="json"))
                self.assertEqual(rc, 1, out)
                self.assertEqual(pr.state_path_for(plan).read_bytes(), before)
                state = pr.load_state(plan)
                self.assertEqual(state["steps"]["S1"]["status"], pr.IN_PROGRESS)


# ---------------------------------------------------------------------------
# (c) No flag: existing values (or the None/[] default) survive untouched,
#     and no `Recorded:` line appears.
#
# Both cases here can look like they "already work" pre-implementation
# (nothing today writes or reads `summary`/`evidence`), so each asserts on
# the schema/value directly rather than only on "unchanged" — that fails
# loudly (KeyError / AssertionError) until S1.2 exists.
# ---------------------------------------------------------------------------

class NoFlagPreservesExistingValueTests(unittest.TestCase):
    def test_first_complete_without_flags_yields_none_summary_and_empty_evidence(self):
        plan = _new_started_plan(self, "S1")
        rc, out_md = _capture(pr.cmd_complete, _complete_args(plan, "S1", fmt="md"))
        self.assertEqual(rc, 0, out_md)
        state = pr.load_state(plan)
        self.assertIsNone(state["steps"]["S1"]["summary"])
        self.assertEqual(state["steps"]["S1"]["evidence"], [])
        self.assertNotIn("Recorded:", out_md)

    def test_second_complete_without_flags_preserves_the_first_calls_values(self):
        plan = _new_started_plan(self, "S1")
        rc1, out1 = _capture(
            pr.cmd_complete,
            _complete_args(plan, "S1", summary="kept summary", evidence=["kept.txt"], fmt="json"),
        )
        self.assertEqual(rc1, 0, out1)
        state_after_first = pr.load_state(plan)
        self.assertEqual(state_after_first["steps"]["S1"]["summary"], "kept summary")

        rc2, out_md = _capture(pr.cmd_complete, _complete_args(plan, "S1", fmt="md"))
        self.assertEqual(rc2, 0, out_md)
        state_after_second = pr.load_state(plan)
        self.assertEqual(state_after_second["steps"]["S1"]["summary"], "kept summary")
        self.assertEqual(state_after_second["steps"]["S1"]["evidence"], ["kept.txt"])
        self.assertNotIn("Recorded:", out_md)


# ---------------------------------------------------------------------------
# (d) Evidence: repeat+dedup preserving order, per-item newline/length caps,
#     total-count cap, nonexistent paths accepted verbatim (never opened).
# ---------------------------------------------------------------------------

class EvidenceValidationTests(unittest.TestCase):
    def test_repeated_evidence_deduplicated_preserving_order(self):
        plan = _new_started_plan(self, "S1")
        rc, out = _capture(
            pr.cmd_complete,
            _complete_args(plan, "S1", summary="ok", evidence=["a.txt", "b.txt", "a.txt", "b.txt"], fmt="json"),
        )
        self.assertEqual(rc, 0, out)
        state = pr.load_state(plan)
        self.assertEqual(state["steps"]["S1"]["evidence"], ["a.txt", "b.txt"])

    def test_evidence_containing_a_newline_is_rejected_without_writing(self):
        plan = _new_started_plan(self, "S1")
        before = pr.state_path_for(plan).read_bytes()
        rc, out = _capture(
            pr.cmd_complete,
            _complete_args(plan, "S1", summary="ok", evidence=["ok.txt", "bad\nline.txt"], fmt="json"),
        )
        self.assertEqual(rc, 1, out)
        self.assertEqual(pr.state_path_for(plan).read_bytes(), before)

    def test_evidence_item_over_300_chars_is_rejected(self):
        plan = _new_started_plan(self, "S1")
        before = pr.state_path_for(plan).read_bytes()
        limit = pr.PLAN_PATH_TRUNCATE_CHARS  # already exists (300); reused per design
        rc, out = _capture(
            pr.cmd_complete,
            _complete_args(plan, "S1", summary="ok", evidence=["x" * (limit + 1)], fmt="json"),
        )
        self.assertEqual(rc, 1, out)
        self.assertIn(str(limit), out)
        self.assertEqual(pr.state_path_for(plan).read_bytes(), before)

    def test_more_than_20_evidence_items_rejected(self):
        plan = _new_started_plan(self, "S1")
        before = pr.state_path_for(plan).read_bytes()
        max_items = pr.STEP_EVIDENCE_MAX_ITEMS  # AttributeError until S1.2
        too_many = [f"f{i}.txt" for i in range(max_items + 1)]
        rc, out = _capture(
            pr.cmd_complete, _complete_args(plan, "S1", summary="ok", evidence=too_many, fmt="json"),
        )
        self.assertEqual(rc, 1, out)
        self.assertIn(str(max_items), out)
        self.assertEqual(pr.state_path_for(plan).read_bytes(), before)

    def test_evidence_ansi_and_bidi_bytes_stripped_before_storage(self):
        plan = _new_started_plan(self, "S1")
        noisy = "\x1b[31mlogs/‮run.txt\x1b[0m"
        rc, out = _capture(
            pr.cmd_complete,
            _complete_args(plan, "S1", summary="ok", evidence=[noisy, "logs/run.txt"], fmt="json"),
        )
        self.assertEqual(rc, 0, out)
        state = pr.load_state(plan)
        # Cleaned first, then deduplicated: both inputs collapse to one item.
        self.assertEqual(state["steps"]["S1"]["evidence"], ["logs/run.txt"])

    def test_evidence_item_empty_after_ansi_stripping_is_rejected(self):
        # No literal "\n"/"\r" in the raw item (passes the newline check at
        # line ~1718), but _strip_unsafe_bytes() removes the ANSI escapes
        # entirely, leaving "" after .strip() — must hit the dedicated empty-
        # after-normalization ValueError, not silently pass through.
        plan = _new_started_plan(self, "S1")
        before = pr.state_path_for(plan).read_bytes()
        rc, out = _capture(
            pr.cmd_complete,
            _complete_args(plan, "S1", summary="ok", evidence=["\x1b[31m\x1b[0m"], fmt="json"),
        )
        self.assertEqual(rc, 1, out)
        self.assertIn("empty after normalization", out)
        self.assertEqual(pr.state_path_for(plan).read_bytes(), before)

    def test_nonexistent_evidence_path_is_accepted_verbatim(self):
        plan = _new_started_plan(self, "S1")
        rc, out = _capture(
            pr.cmd_complete,
            _complete_args(plan, "S1", summary="ok", evidence=["/nonexistent/path/x.txt"], fmt="json"),
        )
        self.assertEqual(rc, 0, out)
        state = pr.load_state(plan)
        self.assertEqual(state["steps"]["S1"]["evidence"], ["/nonexistent/path/x.txt"])


# ---------------------------------------------------------------------------
# (e) Old-state compatibility: a state that predates this feature (no
# summary/evidence keys at all) must not raise anywhere.
# ---------------------------------------------------------------------------

class OldStateCompatibilityTests(unittest.TestCase):
    def test_missing_summary_evidence_keys_do_not_raise_on_complete_status_next_index(self):
        plan = _new_started_plan(self, "S1")
        state = pr.load_state(plan)
        # Simulates a state.json written before this feature existed. Fails
        # loudly with KeyError today, since init_state doesn't seed these
        # keys yet — that IS the red signal for this test.
        del state["steps"]["S1"]["summary"]
        del state["steps"]["S1"]["evidence"]
        pr.save_state(plan, state)

        rc, out = _capture(pr.cmd_complete, _complete_args(plan, "S1", summary="backfilled", fmt="json"))
        self.assertEqual(rc, 0, out)

        rc_status, out_status = _capture(pr.cmd_status, argparse.Namespace(plan=str(plan), format="json"))
        self.assertEqual(rc_status, 0, out_status)
        rc_next, out_next = _capture(pr.cmd_next, argparse.Namespace(plan=str(plan), format="json"))
        self.assertEqual(rc_next, 0, out_next)
        rc_index, out_index = _capture(pr.cmd_index, argparse.Namespace(plan=str(plan), format="json"))
        self.assertEqual(rc_index, 0, out_index)


# ---------------------------------------------------------------------------
# (f) init_state seeds summary=None/evidence=[]; reset clears both back.
# ---------------------------------------------------------------------------

class SchemaDefaultsTests(unittest.TestCase):
    def test_init_state_seeds_summary_none_and_evidence_empty_list(self):
        plan = _new_plan(self)
        state = pr.load_state(plan)
        self.assertIsNone(state["steps"]["S1"]["summary"])
        self.assertEqual(state["steps"]["S1"]["evidence"], [])
        self.assertIsNone(state["steps"]["S2"]["summary"])
        self.assertEqual(state["steps"]["S2"]["evidence"], [])

    def test_reset_step_clears_summary_and_evidence(self):
        plan = _new_started_plan(self, "S1")
        rc, out = _capture(
            pr.cmd_complete,
            _complete_args(plan, "S1", summary="to be cleared", evidence=["gone.txt"], fmt="json"),
        )
        self.assertEqual(rc, 0, out)
        state_before_reset = pr.load_state(plan)
        self.assertEqual(state_before_reset["steps"]["S1"]["summary"], "to be cleared")

        rc_reset, out_reset = _capture(pr.cmd_reset, _reset_args(plan, step="S1"))
        self.assertEqual(rc_reset, 0, out_reset)
        state_after_reset = pr.load_state(plan)
        self.assertIsNone(state_after_reset["steps"]["S1"]["summary"])
        self.assertEqual(state_after_reset["steps"]["S1"]["evidence"], [])


# ---------------------------------------------------------------------------
# (g) Re-complete (COMPLETED -> COMPLETED) with --summary overwrites, and
# `completed_at` keeps the FIRST completion's timestamp (currently it is
# unconditionally refreshed — see transition_step()'s `elif new_status ==
# COMPLETED: step["completed_at"] = now_iso()`).
# ---------------------------------------------------------------------------

class RecompleteOverwriteTests(unittest.TestCase):
    def test_recomplete_with_summary_overwrites_but_keeps_first_completed_at(self):
        plan = _new_started_plan(self, "S1")
        rc1, out1 = _capture(
            pr.cmd_complete,
            _complete_args(plan, "S1", summary="first", evidence=["a.txt"], fmt="json"),
        )
        self.assertEqual(rc1, 0, out1)
        first_state = pr.load_state(plan)
        first_completed_at = first_state["steps"]["S1"]["completed_at"]
        self.assertIsNotNone(first_completed_at)

        time.sleep(0.01)  # now_iso() has microsecond precision; margin for safety

        rc2, out2 = _capture(
            pr.cmd_complete,
            _complete_args(plan, "S1", summary="second", evidence=["b.txt", "c.txt"], fmt="json"),
        )
        self.assertEqual(rc2, 0, out2)
        second_state = pr.load_state(plan)
        self.assertEqual(second_state["steps"]["S1"]["summary"], "second")
        self.assertEqual(second_state["steps"]["S1"]["evidence"], ["b.txt", "c.txt"])
        self.assertEqual(
            second_state["steps"]["S1"]["completed_at"], first_completed_at,
            "re-complete must not refresh completed_at",
        )


# ---------------------------------------------------------------------------
# (h) Negative assertions: sentinel content must never leak into any output
# that feeds back to the driving LLM.
# ---------------------------------------------------------------------------

class SentinelLeakTests(unittest.TestCase):
    SENTINEL_SUMMARY = "SENTINEL-7f3a-summary"
    SENTINEL_EVIDENCE = "SENTINEL-evidence/path.txt"

    def test_sentinel_absent_from_complete_fail_skip_next_outputs(self):
        plan = _new_started_plan(self, "S1")
        rc, out_json = _capture(
            pr.cmd_complete,
            _complete_args(plan, "S1", summary=self.SENTINEL_SUMMARY, evidence=[self.SENTINEL_EVIDENCE], fmt="json"),
        )
        self.assertEqual(rc, 0, out_json)

        # Pre-assertion: prove the sentinel really landed in state, so the
        # "absent from output" checks below are not vacuously true (they
        # would trivially pass today since nothing writes `summary` at all).
        state = pr.load_state(plan)
        self.assertEqual(state["steps"]["S1"]["summary"], self.SENTINEL_SUMMARY)
        self.assertIn(self.SENTINEL_EVIDENCE, state["steps"]["S1"]["evidence"])

        self.assertNotIn(self.SENTINEL_SUMMARY, out_json)
        self.assertNotIn(self.SENTINEL_EVIDENCE, out_json)

        rc_md, out_md = _capture(pr.cmd_complete, _complete_args(plan, "S1", fmt="md"))
        self.assertEqual(rc_md, 0, out_md)
        self.assertNotIn(self.SENTINEL_SUMMARY, out_md)
        self.assertNotIn(self.SENTINEL_EVIDENCE, out_md)

        rc_start2, out_start2 = _capture(pr.cmd_start, _start_args(plan, "S2"))
        self.assertEqual(rc_start2, 0, out_start2)
        self.assertNotIn(self.SENTINEL_SUMMARY, out_start2)

        rc_fail, out_fail = _capture(pr.cmd_fail, _fail_args(plan, "S2", reason="boom", fmt="json"))
        self.assertEqual(rc_fail, 0, out_fail)
        self.assertNotIn(self.SENTINEL_SUMMARY, out_fail)

        rc_skip_state = pr.load_state(plan)
        rc_skip_state["steps"]["S2"]["status"] = pr.FAILED  # already true; documents pre-req for skip
        rc_skip, out_skip = _capture(pr.cmd_skip, _skip_args(plan, "S2", fmt="json"))
        self.assertEqual(rc_skip, 0, out_skip)
        self.assertNotIn(self.SENTINEL_SUMMARY, out_skip)

        rc_next, out_next = _capture(pr.cmd_next, argparse.Namespace(plan=str(plan), format="json"))
        self.assertEqual(rc_next, 0, out_next)
        self.assertNotIn(self.SENTINEL_SUMMARY, out_next)
        self.assertNotIn(self.SENTINEL_EVIDENCE, out_next)

    def test_sentinel_absent_from_hook_reason_and_decide_hook_action(self):
        # Route the sentinel through the real normalization functions first
        # (not a hand-typed string) so this fixture is coupled to S1.2's
        # actual interface, not just to a string literal this file invented.
        normalized_summary = pr._normalize_step_summary(self.SENTINEL_SUMMARY)
        normalized_evidence = pr._normalize_evidence([self.SENTINEL_EVIDENCE])
        self.assertEqual(normalized_summary, self.SENTINEL_SUMMARY)
        self.assertEqual(list(normalized_evidence), [self.SENTINEL_EVIDENCE])

        state = _make_state({
            "S1": _make_step(
                status="in_progress", summary=normalized_summary, evidence=list(normalized_evidence),
            ),
        })
        budget = _make_budget()
        for kind in pr.HOOK_REASON_KINDS:
            reason = pr.render_hook_reason(state, kind, "S1", budget, plan_path=state["plan_path"])
            self.assertNotIn(self.SENTINEL_SUMMARY, reason, msg=f"kind={kind}")
            self.assertNotIn(self.SENTINEL_EVIDENCE, reason, msg=f"kind={kind}")

        pointer = _make_pointer()
        hook_input = {
            "hook_event_name": "Stop",
            "session_id": _FIXTURE_SESSION_ID,
            "transcript_path": None,
            "cwd": pointer["cwd"],
            "stop_hook_active": True,
        }
        decision = pr.decide_hook_action(hook_input, pointer, state)
        if decision.reason:
            self.assertNotIn(self.SENTINEL_SUMMARY, decision.reason)
            self.assertNotIn(self.SENTINEL_EVIDENCE, decision.reason)
        if decision.system_message:
            self.assertNotIn(self.SENTINEL_SUMMARY, decision.system_message)
            self.assertNotIn(self.SENTINEL_EVIDENCE, decision.system_message)


# ---------------------------------------------------------------------------
# (i) Locking: complete/fail/skip must behave like start() — refuse to write
# when the state lock is unavailable, both via a mocked exclusive_lock and
# via a genuine cross-process lock holder.
# ---------------------------------------------------------------------------

class LockingTests(unittest.TestCase):
    def setUp(self):
        self.plan = _new_started_plan(self, "S1")

    def _state_bytes(self) -> bytes:
        return pr.state_path_for(self.plan).read_bytes()

    def test_complete_rejected_when_lock_unavailable(self):
        before = self._state_bytes()
        with mock.patch.object(pr, "exclusive_lock", _never_acquires):
            rc, out = _capture(pr.cmd_complete, _complete_args(self.plan, "S1"))
        self.assertEqual(rc, 1, out)
        self.assertIn("locked", out.lower())
        self.assertEqual(self._state_bytes(), before)

    def test_fail_rejected_when_lock_unavailable(self):
        before = self._state_bytes()
        with mock.patch.object(pr, "exclusive_lock", _never_acquires):
            rc, out = _capture(pr.cmd_fail, _fail_args(self.plan, "S1", reason="x"))
        self.assertEqual(rc, 1, out)
        self.assertIn("locked", out.lower())
        self.assertEqual(self._state_bytes(), before)

    def test_skip_rejected_when_lock_unavailable(self):
        # S2 is still PENDING (only S1 was start()-ed in setUp) — PENDING ->
        # SKIPPED is a valid transition, unlike IN_PROGRESS -> SKIPPED.
        before = self._state_bytes()
        with mock.patch.object(pr, "exclusive_lock", _never_acquires):
            rc, out = _capture(pr.cmd_skip, _skip_args(self.plan, "S2"))
        self.assertEqual(rc, 1, out)
        self.assertIn("locked", out.lower())
        self.assertEqual(self._state_bytes(), before)

    def test_cross_process_complete_is_blocked_while_this_process_holds_the_lock(self):
        with pr.exclusive_lock(pr.state_lock_path_for(self.plan)) as held:
            self.assertTrue(held, "test setup: this process must acquire the lock first")
            before = self._state_bytes()
            result = subprocess.run(
                [
                    sys.executable, str(SCRIPTS_DIR / "plan_runner.py"),
                    "complete", str(self.plan), "S1", "--format", "json",
                ],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 1, msg=result.stdout + result.stderr)
            self.assertEqual(self._state_bytes(), before)


# ---------------------------------------------------------------------------
# (j) `Recorded: summary N chars, evidence M` — present with flags, absent
# without.
# ---------------------------------------------------------------------------

class RecordedOutputLineTests(unittest.TestCase):
    def test_recorded_line_and_json_key_present_only_when_a_flag_was_passed(self):
        plan = _new_started_plan(self, "S1")
        summary_text = "did the thing, moved on"
        evidence = ["a.txt", "b.txt"]
        rc_md, out_md = _capture(
            pr.cmd_complete,
            _complete_args(plan, "S1", summary=summary_text, evidence=evidence, fmt="md"),
        )
        self.assertEqual(rc_md, 0, out_md)
        self.assertIn(
            f"Recorded: summary {len(summary_text)} chars, evidence {len(evidence)}", out_md,
        )

        rc_start2, out_start2 = _capture(pr.cmd_start, _start_args(plan, "S2"))
        self.assertEqual(rc_start2, 0, out_start2)
        rc_json, out_json = _capture(
            pr.cmd_complete, _complete_args(plan, "S2", summary="ok", fmt="json"),
        )
        self.assertEqual(rc_json, 0, out_json)
        payload = json.loads(out_json)
        self.assertEqual(payload["recorded"], {"summary_chars": 2, "evidence_count": 0})

    def test_no_recorded_line_or_key_when_no_flag_was_passed(self):
        plan = _new_started_plan(self, "S1")
        rc_md, out_md = _capture(pr.cmd_complete, _complete_args(plan, "S1", fmt="md"))
        self.assertEqual(rc_md, 0, out_md)
        self.assertNotIn("Recorded:", out_md)
        # Pre-assertion: prove the no-flag path actually went through the new
        # schema (summary=None was written, not just "never referenced") —
        # without it this test would trivially pass today, since nothing
        # currently emits a `Recorded:` line or a `recorded` key at all.
        state_after_md = pr.load_state(plan)
        self.assertIsNone(state_after_md["steps"]["S1"]["summary"])
        self.assertEqual(state_after_md["steps"]["S1"]["evidence"], [])

        rc_start2, out_start2 = _capture(pr.cmd_start, _start_args(plan, "S2"))
        self.assertEqual(rc_start2, 0, out_start2)
        rc_json, out_json = _capture(pr.cmd_complete, _complete_args(plan, "S2", fmt="json"))
        self.assertEqual(rc_json, 0, out_json)
        payload = json.loads(out_json)
        self.assertNotIn("recorded", payload)
        state_after_json = pr.load_state(plan)
        self.assertIsNone(state_after_json["steps"]["S2"]["summary"])
        self.assertEqual(state_after_json["steps"]["S2"]["evidence"], [])


if __name__ == "__main__":
    unittest.main()
