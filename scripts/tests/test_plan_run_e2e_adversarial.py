"""Adversarial E2E: inject the failures plan-run is supposed to survive.

Sixteen scenarios, each in its own fake $HOME, driven only through the CLI and
the `hook-stop` entry point as subprocesses (see plan_run_e2e_support.py):

  C1 a script is missing (a Command: tool, the state file, the runner itself)
  C2 a step never advances (STUCK)
  C3 an out-of-scope instruction shows up (sandbox rules + log-out-of-scope)
  C4 a step fails
  C5 the context is reset mid-plan (new session, lease handover, --resume)
  C6 a step requires human approval
  C7 valid shell syntax in `Command:` (review F1: no false "missing tool")
  C8 a flaky step retried with start -> fail -> reset (review F2: not STUCK)
  C9 history rewritten by reset / init --force (review F3: --resume is live)
  C10 Requires-Approval written loosely (review F4: the gate still holds)
  C11 a shell comment in `Command:` (fresh review N1: no false "missing tool")
  C12 a misspelled Requires-Approval key (fresh review N2: gated and named)
  C13 conflicting Requires-Approval lines (fresh review N3: any true wins)
  C14 a `#` right after an escaped `;` (round-3 N1-R: not a comment)
  C15 numbered / colon-less / full-width Requires-Approval (round-3 N2-R)
  C16 a `### Notes` section after the last step (round-3 N2-FP)

What this suite cannot see, stated so a green run is not over-read:

- Whether the model obeys. C3 proves the rules reach the reason text and
  that `log-out-of-scope` really records; it cannot prove a model refuses
  an injected instruction or chooses to log it. The rules have no
  enforcement at the tool-call layer.
- Two live sessions racing for the pointer lock. C5 hands the lease over
  sequentially; concurrent Stop events are not exercised here.
- How Claude Code itself treats exit 2 or `systemMessage`. C1.7 asserts the
  wrapper exits 0 with no output; the harness's reaction is inferred from
  its documented behaviour, not observed.
"""
from __future__ import annotations

import shutil
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import plan_run_e2e_support as e2e  # noqa: E402
from plan_run_e2e_support import (  # noqa: E402
    GATED_PLAN, LINEAR_PLAN, RunLog, Sandbox,
    assigns_only, is_block, parse_json, ready_ids, reason_of, system_message,
)

# Review F1: one Command: exercising loops, conditionals, [[ ]], a subshell,
# `python3 -c` with `;` inside quotes and a quoted `;` argument. The only
# executables it really needs are these, all present on macOS and CI.
SHELL_SYNTAX_COMMAND = (
    'for f in *.md; do wc -l "$f"; done && if [ -f plan.md ]; then cat plan.md; fi'
    ' && (cd .plan-state && git --version) && [[ -n x ]] && echo \'a;b\' \'c;d\''
    ' && python3 -c "import json; import sys; print(1)"'
)
SHELL_SYNTAX_TOOLS = ["wc", "cat", "git", "python3"]
MISSING_TOOL = "plan-run-e2e-missing-tool-zz9"

# Fresh review N1: `;` inside a trailing comment used to split out
# `harmless` as a tool; `a#b`-style words must still not be comments.
COMMENT_COMMAND = "git --version  # prints version; harmless && nope-tool-zz9"
# Fresh review N2: misspelled keys used to be dropped without a warning.
MISSPELLED_APPROVAL_FIELDS = (
    ("require", "  - Require-Approval: true\n"),
    ("aproval", "  - Requires-Aproval: true\n"),
    ("approval-required", "  - Approval-Required: true\n"),
)
# Fresh review N3: a later `false` used to reopen the gate.
CONFLICTING_APPROVAL_FIELDS = "  - Requires-Approval: true\n  - Requires-Approval: false\n"

# Round-3 N1-R: `\;#y` is one word, so what follows is a real command.
ESCAPED_HASH_MISSING = "plan-run-e2e-missing-zz8"
ESCAPED_HASH_COMMAND = f"echo x\\;#y; {ESCAPED_HASH_MISSING} --run"
# Round-3 N2-R: (label, field, init must warn).
RESIDUAL_APPROVAL_FIELDS = (
    ("numbered", "  1. Requires-Approval: true\n", False),
    ("no-colon", "  - Requires-Approval true\n", True),
    ("no-bullet-typo", "  Require-Approval: true\n", True),
    ("apprval", "  - Requires-Apprval: true\n", True),
    ("fullwidth", "  - Ｒｅｑｕｉｒｅｓ－Ａｐｐｒｏｖａｌ： true\n", False),
)
# Round-3 N2-FP: prose after the last step, lifted from a real plan.
NOTES_AFTER_LAST_STEP = (
    "  - Requires-Approval: false\n\n### Notes\n\n"
    "- Approval-Required: true\n- User review + approve（改 Status: APPROVED）\n"
)

# Review F4: each of these used to parse as "no approval needed".
LOOSE_APPROVAL_FIELDS = (
    ("required", "  - Requires-Approval: required\n"),
    ("annotated", "  - Requires-Approval: true (prod deploy)\n"),
    ("quoted", '  - Requires-Approval: "true"\n'),
    ("y", "  - Requires-Approval: y\n"),
    ("bold-key", "  - **Requires-Approval**: true\n"),
    ("space-key", "  - Requires Approval: true\n"),
)


def _strings(node: object) -> list[str]:
    """Every string leaf of a JSON payload."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        node = list(node.values())
    if isinstance(node, list):
        return [leaf for item in node for leaf in _strings(item)]
    return []


class AdversarialCase(unittest.TestCase):
    """One RunLog per test; written (when requested) even if the test fails."""

    scenario = ""

    def setUp(self) -> None:
        self.log = RunLog(self.scenario or self._testMethodName)
        self.addCleanup(self.log.write_if_requested)

    def sandbox(self, plan_text: str) -> Sandbox:
        sb = Sandbox(self.log, plan_text)
        self.addCleanup(sb.cleanup)
        return sb

    def check(self, ac: str, desc: str, ok: bool, detail: object = "") -> None:
        self.log.check(ac, desc, bool(ok), detail)

    def assert_all_checks_passed(self) -> None:
        self.assertTrue(self.log.checks, "scenario recorded no checks")
        self.assertEqual(self.log.failures(), [])


class C1MissingScript(AdversarialCase):

    def test_c1a_missing_command_tool(self):
        self.log.scenario = "C1a-missing-tool"
        sb = self.sandbox(LINEAR_PLAN.format(
            s1_extra="  - Command: `scripts/does_not_exist.sh --flag`\n"))
        sb.cli("init", "plan.md")
        pf = sb.cli("preflight", "plan.md", "--format", "json")
        report = parse_json(pf.stdout)
        bad = [c for c in report.get("checks", []) if not c.get("ok")]
        # rc alone would pass against a runner with no `preflight` at all:
        # argparse rejects the unknown subcommand with rc=2.
        self.check("AC-C1.1", "preflight 缺工具：rc=1 且 ok=false",
                   pf.returncode == e2e.PREFLIGHT_FAIL_RC and report.get("ok") is False,
                   f"rc={pf.returncode} ok={report.get('ok')}")
        self.check("AC-C1.2", "checks 有 kind=tool、ok=false 且點名缺的路徑",
                   any(c.get("kind") == "tool" and "does_not_exist.sh" in c.get("name", "")
                       for c in bad), bad)
        self.check("AC-C1.3", "每個失敗項都有修復建議（hint）",
                   bool(bad) and all(c.get("hint") for c in bad), bad)
        rc, payload, _ = sb.hook("s1", sb.transcript("t1"))
        self.check("AC-C1.4", "hook 不 block，改用 PREFLIGHT 失敗 systemMessage",
                   rc == 0 and not is_block(payload)
                   and system_message(payload).startswith(e2e.PREFLIGHT_MSG), payload)
        self.assert_all_checks_passed()

    def test_c1b_state_file_deleted(self):
        self.log.scenario = "C1b-missing-state"
        sb = self.sandbox(LINEAR_PLAN.format(s1_extra=""))
        sb.cli("init", "plan.md")
        (sb.proj / ".plan-state" / "plan.state.json").unlink()
        pf = sb.cli("preflight", "plan.md", "--format", "json")
        missing = [c for c in parse_json(pf.stdout).get("checks", [])
                   if c.get("kind") == "state" and not c.get("ok")]
        self.check("AC-C1.5", "state 被刪：preflight rc=1，kind=state 失敗且有 hint",
                   pf.returncode == e2e.PREFLIGHT_FAIL_RC and bool(missing)
                   and all(c.get("hint") for c in missing), f"rc={pf.returncode} {missing}")
        rc, payload, _ = sb.hook("s1", sb.transcript("t1"))
        self.check("AC-C1.6", "state 缺：hook rc=0 且不 block",
                   rc == 0 and not is_block(payload), payload)
        self.assert_all_checks_passed()

    def test_c1c_runner_missing_through_real_wrapper(self):
        self.log.scenario = "C1c-missing-runner"
        sb = self.sandbox(LINEAR_PLAN.format(s1_extra=""))
        checkout = sb.home / "fake-agent-skills"
        (checkout / "scripts").mkdir(parents=True)
        sb.env["AGENT_SKILLS_DIR"] = str(checkout)
        rc, _, out = sb.hook("s1", sb.transcript("t1"), via_wrapper=True)
        # `python3 <missing file>` exits 2, and exit 2 from a Stop hook is a
        # blocking error on every turn of every session.
        self.check("AC-C1.7", "runner 不存在：wrapper rc=0 且完全不輸出",
                   rc == 0 and out == "", f"rc={rc} out={out!r}")
        # Positive control: the same wrapper and fake HOME must reach a
        # runner once one is there, or C1.7's silence proves nothing (the
        # wrapper also exits 0 silently when $HOME does not match).
        for module in sb.runner.parent.glob("*.py"):
            shutil.copy2(module, checkout / "scripts" / module.name)
        sb.cli("init", "plan.md")
        rc, payload, _ = sb.hook("s1", sb.transcript("t1"), via_wrapper=True)
        self.check("AC-C1.8", "對照：補上 runner 後同一個 wrapper 會派 S1",
                   rc == 0 and assigns_only(payload, "S1"), payload)
        self.assert_all_checks_passed()


class C2StepNeverAdvances(AdversarialCase):

    def _run(self, variant: str) -> None:
        self.log.scenario = f"C2-{variant}"
        sb = self.sandbox(LINEAR_PLAN.format(s1_extra=""))
        sb.cli("init", "plan.md")
        if variant == "in_progress":
            sb.cli("start", "plan.md", "S1")
        transcript = sb.transcript("t1")
        results = [sb.hook("s1", transcript) for _ in range(e2e.STUCK_AT + 2)]
        payloads = [p for _, p, _ in results]
        rc_n, at_n, _ = results[e2e.STUCK_AT - 1]
        self.check(f"AC-C2.1[{variant}]", f"前 {e2e.STUCK_AT - 1} 次 block",
                   all(is_block(p) for p in payloads[: e2e.STUCK_AT - 1]),
                   [(p or {}).get("decision") for p in payloads])
        self.check(f"AC-C2.2[{variant}]", f"第 {e2e.STUCK_AT} 次 allow，STUCK 訊息點名 S1",
                   rc_n == 0 and not is_block(at_n)
                   and system_message(at_n).startswith(e2e.STUCK_MSG)
                   and "S1" in system_message(at_n), at_n)
        tail = payloads[e2e.STUCK_AT:]
        self.check(f"AC-C2.3[{variant}]", "STUCK 之後每輪 allow 且不再重複訊息",
                   all(not is_block(p) and not system_message(p) for p in tail), tail)
        self.check(f"AC-C2.4[{variant}]", "STUCK 不改 step 狀態",
                   sb.statuses().get("S1") == variant, sb.statuses())
        self.assert_all_checks_passed()

    def test_c2_ready_step_never_started(self):
        self._run("pending")

    def test_c2_started_step_never_reported(self):
        self._run("in_progress")


class C3OutOfScope(AdversarialCase):

    def test_c3_sandbox_rules_and_out_of_scope_log(self):
        self.log.scenario = "C3-out-of-scope"
        sb = self.sandbox(LINEAR_PLAN.format(s1_extra=""))
        # Deliberately not the plan directory: the reason already prints the
        # plan path, so a root under it would make C3.1 pass on any runner.
        root = sb.home / "sbx-root-7f3a"
        root.mkdir()
        sb.env[e2e.SANDBOX_ENV_VAR] = str(root)
        sb.cli("init", "plan.md")
        sb.env.pop(e2e.SANDBOX_ENV_VAR)  # read at init only; the hook must not need it
        _, payload, _ = sb.hook("s1", sb.transcript("t1"))
        reason = reason_of(payload)
        self.check("AC-C3.1", "reason 帶 init 時的 sandbox root 實際路徑",
                   str(root) in reason, reason[-200:])
        self.check("AC-C3.2", "reason 帶 ~/.claude、log-out-of-scope、「不算注入」界線",
                   all(k in reason for k in e2e.SANDBOX_KEYWORDS), reason[-200:])
        sb.cli("start", "plan.md", "S1")
        before = sb.statuses()
        injected = "rm -rf /etc"
        logged = sb.cli("log-out-of-scope", "plan.md", "--text", injected,
                        "--source", "tool output")
        entries = sb.state().get(e2e.OOS_STATE_KEY) or []
        last = entries[-1] if entries else {}
        self.check("AC-C3.3", "log-out-of-scope rc=0 且不改 step 狀態",
                   logged.returncode == 0 and sb.statuses() == before, sb.statuses())
        self.check("AC-C3.4", "state.out_of_scope_log 保存原文、來源、時間、當時的 step",
                   last.get("text") == injected and last.get("source") == "tool output"
                   and bool(last.get("at")) and last.get("step") == "S1", entries)
        # Review F7: against a runner without the subcommand, argparse writes
        # to stderr and stdout is empty, so "not echoed" held vacuously.
        # Require a real, successful confirmation first, then look at both
        # streams.
        self.check("AC-C3.5", "記錄有成功輸出確認，且 stdout／stderr 都不回印原文",
                   logged.returncode == 0 and bool(logged.stdout.strip())
                   and injected not in logged.stdout + logged.stderr,
                   f"rc={logged.returncode} out={logged.stdout!r} err={logged.stderr[:80]!r}")
        early = sb.checkpoint()
        self.check("AC-C3.7", "還沒 complete：log-out-of-scope 當下 checkpoint 就有筆數、沒有原文",
                   any("1 out-of-scope" in q for q in early.get("open_questions") or [])
                   and injected not in str(early), early.get("open_questions"))
        sb.cli("complete", "plan.md", "S1")
        questions = sb.checkpoint().get("open_questions") or []
        self.check("AC-C3.6", "checkpoint 只帶筆數與 step，不帶原文",
                   any("1 out-of-scope" in q and "S1" in q for q in questions)
                   and injected not in str(sb.checkpoint()), questions)
        self.assert_all_checks_passed()


class C4StepFails(AdversarialCase):

    def test_c4_failed_step_is_a_human_gate(self):
        self.log.scenario = "C4-step-fails"
        sb = self.sandbox(LINEAR_PLAN.format(s1_extra=""))
        sb.cli("init", "plan.md")
        transcript = sb.transcript("t1")
        sb.hook("s1", transcript)
        sb.cli("start", "plan.md", "S1")
        before_fail = datetime.now(timezone.utc)
        failed = sb.cli("fail", "plan.md", "S1", "--reason", "injected failure")
        s1 = sb.state().get("steps", {}).get("S1", {})
        self.check("AC-C4.1", "fail rc=0，S1=failed 且保存 failure_reason",
                   failed.returncode == 0 and s1.get("status") == "failed"
                   and s1.get("failure_reason") == "injected failure", s1)
        rc, payload, _ = sb.hook("s1", transcript)
        self.check("AC-C4.2", "failed 後 hook allow，不叫模型自己收拾",
                   rc == 0 and not is_block(payload), payload)
        ready = ready_ids(parse_json(sb.cli("next", "plan.md", "--format", "json").stdout))
        self.check("AC-C4.3", "下游 S2 變 blocked 且不在 ready",
                   "S2" not in ready and sb.statuses().get("S2") == "blocked", ready)
        cp = sb.checkpoint()
        try:
            fresh = datetime.fromisoformat(cp.get("updated_at", "")) >= before_fail
        except ValueError:
            fresh = False
        self.check("AC-C4.4", "checkpoint 在 fail 後刷新，open_questions 帶失敗原因",
                   fresh and "S1 failed: injected failure" in (cp.get("open_questions") or [])
                   and cp.get("next_ready_step") != "S2", cp)
        self.assert_all_checks_passed()


class C5ContextReset(AdversarialCase):

    def test_c5_new_session_resumes_without_redoing(self):
        self.log.scenario = "C5-context-reset"
        sb = self.sandbox(LINEAR_PLAN.format(s1_extra=""))
        sb.cli("init", "plan.md")
        early = sb.cli("next", "plan.md", "--resume", "--format", "json")
        self.check("AC-C5.0", "還沒有 checkpoint 時 --resume exit 1 並回 error",
                   early.returncode == 1 and "error" in parse_json(early.stdout),
                   f"rc={early.returncode}")
        old = sb.transcript("t1")
        sb.hook("s1", old)
        sb.cli("start", "plan.md", "S1")
        sb.cli("complete", "plan.md", "S1", "--summary", "did S1")
        done_at = sb.state()["steps"]["S1"]["completed_at"]
        cp = sb.checkpoint()
        self.check("AC-C5.1", "checkpoint 記 S1 完成（含摘要），next_ready_step=S2",
                   any(c.get("id") == "S1" and c.get("summary") == "did S1"
                       for c in cp.get("completed_steps", []))
                   and cp.get("next_ready_step") == "S2", cp)
        new = sb.transcript("t2")
        rc, payload, _ = sb.hook("s2", new)
        self.check("AC-C5.2", "舊 session 還活著：新 session 不接手、不派工",
                   rc == 0 and not is_block(payload), payload)
        sb.age(old, e2e.STALE_TRANSCRIPT_SECONDS)
        _, payload, _ = sb.hook("s2", new)
        self.check("AC-C5.3", "舊 transcript 過期：新 session 接手，只派 S2",
                   assigns_only(payload, "S2")
                   and reason_of(payload).startswith("[plan-run] Progress 1/3"),
                   reason_of(payload)[:200])
        self.check("AC-C5.4", "pointer 的 driver_session_id 換成 s2",
                   sb.pointer().get("driver_session_id") == "s2", sb.pointer())
        resumed = sb.cli("next", "plan.md", "--resume", "--format", "json")
        view = parse_json(resumed.stdout)
        done = [c.get("id") for c in (view.get("checkpoint") or {}).get("completed_steps", [])]
        self.check("AC-C5.5", "--resume rc=0：已完成含 S1，ready 只有 S2",
                   resumed.returncode == 0 and "S1" in done and ready_ids(view) == ["S2"],
                   {"done": done, "ready": ready_ids(view)})
        self.check("AC-C5.6", "S1 的 completed_at 沒被改寫（沒有重做）",
                   sb.state()["steps"]["S1"]["completed_at"] == done_at, done_at)
        self.assert_all_checks_passed()


class C6RequiresApproval(AdversarialCase):

    def test_c6_gate_holds_until_a_human_approves(self):
        self.log.scenario = "C6-requires-approval"
        sb = self.sandbox(GATED_PLAN.format(gate=e2e.APPROVAL_FIELD))
        init_lines = sb.cli("init", "plan.md").stdout.splitlines()
        ready_now = [ln for ln in init_lines if ln.startswith("Ready now:")]
        self.check("AC-C6.8", "init 的 Ready now 只列 S4，不列 gated 的 S1",
                   ready_now == ["Ready now: S4"], ready_now)
        transcript = sb.transcript("t1")
        _, payload, _ = sb.hook("s1", transcript)
        self.check("AC-C6.1", "gated S1 與非 gated S4 同時 ready：先派 S4",
                   assigns_only(payload, "S4"), reason_of(payload)[:200])
        sb.cli("start", "plan.md", "S4")
        done_s4 = parse_json(sb.cli("complete", "plan.md", "S4", "--format", "json").stdout)
        self.check("AC-C6.7", "complete S4 之後：附帶的 ready 與 checkpoint.next_ready_step 都不是 S1",
                   "S1" not in ready_ids(done_s4)
                   and sb.checkpoint().get("next_ready_step") != "S1",
                   {"ready": ready_ids(done_s4),
                    "next_ready_step": sb.checkpoint().get("next_ready_step")})
        # Wait at the gate for longer than the STUCK threshold: waiting for a
        # human is not being stuck, so none of these may count.
        waits = [sb.hook("s1", transcript) for _ in range(e2e.STUCK_AT)]
        self.check("AC-C6.2", "只剩未核准 step：每輪 allow，訊息附 approve 指令與 S1",
                   all(rc == 0 and not is_block(p) and "approve" in system_message(p)
                       and "S1" in system_message(p) for rc, p, _ in waits),
                   [p for _, p, _ in waits])
        view = parse_json(sb.cli("next", "plan.md", "--format", "json").stdout)
        waiting = [w if isinstance(w, str) else w.get("id")
                   for w in view.get("awaiting_approval_steps", [])]
        # An unapproved step must not be handed out through `next` either:
        # it leaves the ready lists entirely, so no task_create/start
        # instruction for it exists anywhere in the payload.
        instructions = [s for s in view.get("ready_steps_new", []) if s.get("id") == "S1"]
        start_hints = [t for t in _strings(view) if " start " in t and "S1" in t]
        self.check("AC-C6.6", "next：S1 只在 awaiting_approval_steps，不在 ready、沒有 S1 的派工指令",
                   waiting == ["S1"] and "S1" not in ready_ids(view)
                   and not instructions and not start_hints,
                   {"waiting": waiting, "ready": ready_ids(view), "start_hints": start_hints})
        refused = sb.cli("start", "plan.md", "S1")
        self.check("AC-C6.3", "未核准 start S1 被拒，S1 仍是 pending",
                   refused.returncode != 0 and "invalid choice" not in refused.stderr
                   and sb.statuses().get("S1") == "pending", f"rc={refused.returncode}")
        approved = sb.cli("approve", "plan.md", "S1")
        after = [sb.hook("s1", transcript) for _ in range(e2e.STUCK_AT)]
        payloads = [p for _, p, _ in after]
        self.check("AC-C6.4", "approve 後派 S1",
                   approved.returncode == 0 and assigns_only(payloads[0], "S1"), payloads[0])
        self.check("AC-C6.5", "等核准那幾輪不算 STUCK：核准後從 1 重算，第 3 次才 STUCK",
                   all(assigns_only(p, "S1") for p in payloads[: e2e.STUCK_AT - 1])
                   and system_message(payloads[-1]).startswith(e2e.STUCK_MSG), payloads)
        self.assert_all_checks_passed()


class C7ShellSyntaxPreflight(AdversarialCase):

    def test_c7_valid_shell_is_not_a_missing_tool(self):
        self.log.scenario = "C7-shell-syntax"
        sb = self.sandbox(LINEAR_PLAN.format(
            s1_extra=f"  - Command: `{SHELL_SYNTAX_COMMAND}`\n"))
        sb.cli("init", "plan.md")
        pf = sb.cli("preflight", "plan.md", "--format", "json")
        report = parse_json(pf.stdout)
        tools = [c.get("name") for c in report.get("checks", []) if c.get("kind") == "tool"]
        self.check("AC-C7.1", "合法 shell 語法：preflight rc=0 且 ok=true",
                   pf.returncode == 0 and report.get("ok") is True,
                   f"rc={pf.returncode} failed={[c for c in report.get('checks', []) if not c.get('ok')]}")
        self.check("AC-C7.2", "只抽出真正的執行檔，不含關鍵字、引號內片段或 import",
                   tools == SHELL_SYNTAX_TOOLS, tools)
        _, payload, _ = sb.hook("s1", sb.transcript("t1"))
        self.check("AC-C7.3", "hook 照常派 S1，不發 PREFLIGHT 失敗",
                   assigns_only(payload, "S1")
                   and not system_message(payload).startswith(e2e.PREFLIGHT_MSG), payload)
        # Negative control: the same syntax plus one really missing tool must
        # still fail, naming only that tool -- or C7.1 proves nothing.
        bad = self.sandbox(LINEAR_PLAN.format(
            s1_extra=f"  - Command: `{SHELL_SYNTAX_COMMAND} && {MISSING_TOOL} x`\n"))
        bad.cli("init", "plan.md")
        pf_bad = bad.cli("preflight", "plan.md", "--format", "json")
        failed = [c.get("name") for c in parse_json(pf_bad.stdout).get("checks", [])
                  if not c.get("ok")]
        self.check("AC-C7.4", "對照：多一個真的不存在的工具就只點名它",
                   pf_bad.returncode == e2e.PREFLIGHT_FAIL_RC and failed == [MISSING_TOOL],
                   f"rc={pf_bad.returncode} failed={failed}")
        self.assert_all_checks_passed()


class C8FlakyRetryIsNotStuck(AdversarialCase):

    def test_c8_start_fail_reset_cycles(self):
        self.log.scenario = "C8-flaky-retry"
        sb = self.sandbox(LINEAR_PLAN.format(s1_extra=""))
        sb.cli("init", "plan.md")
        transcript = sb.transcript("t1")
        cycles = []
        for n in range(e2e.STUCK_AT + 1):
            _, payload, _ = sb.hook("s1", transcript)
            cycles.append(payload)
            sb.cli("start", "plan.md", "S1")
            sb.cli("fail", "plan.md", "S1", "--reason", f"flaky #{n + 1}")
            sb.cli("reset", "plan.md", "--step=S1")
        self.check("AC-C8.1", f"start→fail→reset 重試 {e2e.STUCK_AT + 1} 輪：每輪都派 S1、沒有 STUCK",
                   all(assigns_only(p, "S1") and not system_message(p).startswith(e2e.STUCK_MSG)
                       for p in cycles), [(p or {}).get("decision") for p in cycles])
        # Positive control: once start stops happening, STUCK still fires on
        # the STUCK_AT-th assignment counted from the last start.
        idle = [sb.hook("s1", transcript)[1] for _ in range(e2e.STUCK_AT)]
        self.check("AC-C8.2", f"之後不再 start：第 {e2e.STUCK_AT} 次指派才 STUCK",
                   all(assigns_only(p, "S1") for p in idle[:-1])
                   and system_message(idle[-1]).startswith(e2e.STUCK_MSG), idle)
        self.check("AC-C8.3", "S1 回到 pending（重試沒有改壞狀態）",
                   sb.statuses().get("S1") == "pending", sb.statuses())
        self.assert_all_checks_passed()


class C9ResumeAfterRewrite(AdversarialCase):

    SUMMARY = "deployed S1 to prod"

    def _resume(self, sb: Sandbox) -> tuple[int, dict, str]:
        js = sb.cli("next", "plan.md", "--resume", "--format", "json")
        md = sb.cli("next", "plan.md", "--resume")
        return js.returncode, parse_json(js.stdout), md.stdout

    def _live(self, rc: int, view: dict, md: str) -> bool:
        cp = view.get("checkpoint") or {}
        return (rc == 0 and cp.get("completed_steps") == []
                and cp.get("next_ready_step") == "S1" and ready_ids(view) == ["S1"]
                and self.SUMMARY not in md and "done S1" not in md)

    def test_c9_reset_and_init_force_refresh_checkpoint(self):
        self.log.scenario = "C9-resume-after-rewrite"
        sb = self.sandbox(LINEAR_PLAN.format(s1_extra=""))
        sb.cli("init", "plan.md")
        sb.cli("start", "plan.md", "S1")
        sb.cli("complete", "plan.md", "S1", "--summary", self.SUMMARY)
        rc, view, md = self._resume(sb)
        self.check("AC-C9.1", "對照：complete 後 --resume 帶 S1 的摘要",
                   rc == 0 and self.SUMMARY in md, md[:200])
        sb.cli("reset", "plan.md", "--step=S1")
        rc, view, md = self._resume(sb)
        self.check("AC-C9.2", "reset 後 --resume：checkpoint 不再說 S1 done，next 是 S1",
                   self._live(rc, view, md), {"checkpoint": view.get("checkpoint"), "md": md[:200]})
        sb.cli("start", "plan.md", "S1")
        sb.cli("complete", "plan.md", "S1", "--summary", self.SUMMARY)
        sb.cli("init", "plan.md", "--force")
        rc, view, md = self._resume(sb)
        self.check("AC-C9.3", "init --force 後 --resume：同樣反映全新 state",
                   self._live(rc, view, md), {"checkpoint": view.get("checkpoint"), "md": md[:200]})
        self.assert_all_checks_passed()


class C10LooseApprovalSyntax(AdversarialCase):

    def _gate(self, label: str, field: str) -> None:
        sb = self.sandbox(GATED_PLAN.format(gate=field))
        init = parse_json(sb.cli("init", "plan.md", "--format", "json").stdout)
        self.check(f"AC-C10.1[{label}]", "init：S1 不在 ready，列在 awaiting_approval_steps",
                   "S1" not in init.get("ready_steps", [])
                   and init.get("awaiting_approval_steps") == ["S1"],
                   {k: init.get(k) for k in ("ready_steps", "awaiting_approval_steps")})
        _, payload, _ = sb.hook("s1", sb.transcript("t1"))
        refused = sb.cli("start", "plan.md", "S1")
        self.check(f"AC-C10.2[{label}]", "hook 先派 S4 不派 S1；start S1 被拒且仍 pending",
                   assigns_only(payload, "S4") and refused.returncode != 0
                   and "invalid choice" not in refused.stderr
                   and sb.statuses().get("S1") == "pending",
                   {"reason": reason_of(payload)[:120], "start_rc": refused.returncode})

    def test_c10_loose_requires_approval_still_gates(self):
        self.log.scenario = "C10-loose-approval"
        for label, field in LOOSE_APPROVAL_FIELDS:
            self._gate(label, field)
        # Values the parser cannot read are gated *and* reported, so a typo
        # is visible at init rather than discovered at the gate.
        sb = self.sandbox(GATED_PLAN.format(gate=LOOSE_APPROVAL_FIELDS[1][1]))
        warnings = parse_json(sb.cli("init", "plan.md", "--format", "json").stdout).get("warnings", [])
        self.check("AC-C10.3", "無法辨識的值：init warnings 點名 S1 與 Requires-Approval",
                   any("S1" in w and "Requires-Approval" in w for w in warnings), warnings)
        self.assert_all_checks_passed()


class C11CommentInCommand(AdversarialCase):

    def test_c11_shell_comment_is_not_a_tool(self):
        self.log.scenario = "C11-shell-comment"
        sb = self.sandbox(LINEAR_PLAN.format(s1_extra=f"  - Command: `{COMMENT_COMMAND}`\n"))
        sb.cli("init", "plan.md")
        pf = sb.cli("preflight", "plan.md", "--format", "json")
        report = parse_json(pf.stdout)
        tools = [c.get("name") for c in report.get("checks", []) if c.get("kind") == "tool"]
        self.check("AC-C11.1", "註解裡的 `;` 之後不算指令：preflight rc=0 且只查 git",
                   pf.returncode == 0 and report.get("ok") is True and tools == ["git"],
                   f"rc={pf.returncode} tools={tools}")
        _, payload, _ = sb.hook("s1", sb.transcript("t1"))
        self.check("AC-C11.2", "hook 照常派 S1，不發 PREFLIGHT 失敗",
                   assigns_only(payload, "S1")
                   and not system_message(payload).startswith(e2e.PREFLIGHT_MSG), payload)
        self.assert_all_checks_passed()


class C12MisspelledApprovalKey(AdversarialCase):

    def test_c12_misspelled_key_is_gated_and_named(self):
        self.log.scenario = "C12-misspelled-approval-key"
        for label, field in MISSPELLED_APPROVAL_FIELDS:
            sb = self.sandbox(GATED_PLAN.format(gate=field))
            init = parse_json(sb.cli("init", "plan.md", "--format", "json").stdout)
            warnings = init.get("warnings", [])
            self.check(f"AC-C12.1[{label}]", "init warnings 點名 S1 與拼錯的那一行",
                       any("S1" in w and field.strip().lstrip("- ") in w for w in warnings),
                       warnings)
            _, payload, _ = sb.hook("s1", sb.transcript("t1"))
            refused = sb.cli("start", "plan.md", "S1")
            self.check(f"AC-C12.2[{label}]", "S1 列在 awaiting_approval、hook 先派 S4、start S1 被拒",
                       init.get("awaiting_approval_steps") == ["S1"]
                       and assigns_only(payload, "S4") and refused.returncode != 0
                       and sb.statuses().get("S1") == "pending",
                       {"awaiting": init.get("awaiting_approval_steps"),
                        "start_rc": refused.returncode})
        self.assert_all_checks_passed()


class C13ConflictingApproval(AdversarialCase):

    def test_c13_any_true_line_wins(self):
        self.log.scenario = "C13-conflicting-approval"
        sb = self.sandbox(GATED_PLAN.format(gate=CONFLICTING_APPROVAL_FIELDS))
        init = parse_json(sb.cli("init", "plan.md", "--format", "json").stdout)
        self.check("AC-C13.1", "true 後面接 false：S1 仍需核准，init 警告衝突",
                   init.get("awaiting_approval_steps") == ["S1"]
                   and any("S1" in w and "conflict" in w for w in init.get("warnings", [])),
                   {k: init.get(k) for k in ("awaiting_approval_steps", "warnings")})
        refused = sb.cli("start", "plan.md", "S1")
        self.check("AC-C13.2", "start S1 被拒且仍是 pending",
                   refused.returncode != 0 and sb.statuses().get("S1") == "pending",
                   f"rc={refused.returncode}")
        self.assert_all_checks_passed()


class C14EscapedHashIsNotAComment(AdversarialCase):

    def test_c14_hash_after_escaped_separator(self):
        self.log.scenario = "C14-escaped-hash"
        sb = self.sandbox(LINEAR_PLAN.format(s1_extra=f"  - Command: `{ESCAPED_HASH_COMMAND}`\n"))
        sb.cli("init", "plan.md")
        pf = sb.cli("preflight", "plan.md", "--format", "json")
        failed = [c.get("name") for c in parse_json(pf.stdout).get("checks", []) if not c.get("ok")]
        self.check("AC-C14.1", "`\\;#y` 不是註解：後面真的缺的工具要被點名",
                   pf.returncode == e2e.PREFLIGHT_FAIL_RC and failed == [ESCAPED_HASH_MISSING],
                   f"rc={pf.returncode} failed={failed}")
        _, payload, _ = sb.hook("s1", sb.transcript("t1"))
        self.check("AC-C14.2", "hook 不 block，PREFLIGHT 失敗訊息點名缺的工具",
                   not is_block(payload)
                   and system_message(payload).startswith(e2e.PREFLIGHT_MSG)
                   and ESCAPED_HASH_MISSING in system_message(payload), payload)
        self.assert_all_checks_passed()


class C15ResidualApprovalShapes(AdversarialCase):

    def test_c15_numbered_colonless_fullwidth_still_gate(self):
        self.log.scenario = "C15-residual-approval-shapes"
        for label, field, warns in RESIDUAL_APPROVAL_FIELDS:
            sb = self.sandbox(GATED_PLAN.format(gate=field))
            init = parse_json(sb.cli("init", "plan.md", "--format", "json").stdout)
            approval_warns = [w for w in init.get("warnings", [])
                              if "S1" in w and "Requires-Approval" in w]
            self.check(f"AC-C15.1[{label}]", "S1 列在 awaiting_approval；該 warning 的才 warning",
                       init.get("awaiting_approval_steps") == ["S1"]
                       and bool(approval_warns) == warns,
                       {k: init.get(k) for k in ("awaiting_approval_steps", "warnings")})
            _, payload, _ = sb.hook("s1", sb.transcript("t1"))
            refused = sb.cli("start", "plan.md", "S1")
            self.check(f"AC-C15.2[{label}]", "hook 先派 S4；start S1 被拒且仍 pending",
                       assigns_only(payload, "S4") and refused.returncode != 0
                       and sb.statuses().get("S1") == "pending",
                       {"reason": reason_of(payload)[:120], "start_rc": refused.returncode})
        self.assert_all_checks_passed()


class C16NotesAfterLastStep(AdversarialCase):

    def test_c16_notes_heading_does_not_gate_the_last_step(self):
        self.log.scenario = "C16-notes-after-last-step"
        sb = self.sandbox(LINEAR_PLAN + NOTES_AFTER_LAST_STEP)
        init = parse_json(sb.cli("init", "plan.md", "--format", "json").stdout)
        s3 = sb.state().get("steps", {}).get("S3", {})
        self.check("AC-C16.1", "### Notes 之後的條列不算 S3 的欄位：S3 不需核准、沒有 approval warning",
                   s3.get("requires_approval") is False
                   and not [w for w in init.get("warnings", []) if "pproval" in w],
                   {"S3": s3.get("requires_approval"), "warnings": init.get("warnings")})
        for sid in ("S1", "S2"):
            sb.cli("start", "plan.md", sid)
            sb.cli("complete", "plan.md", sid)
        started = sb.cli("start", "plan.md", "S3")
        self.check("AC-C16.2", "S1、S2 完成後 start S3 放行",
                   started.returncode == 0 and sb.statuses().get("S3") == "in_progress",
                   f"rc={started.returncode} {sb.statuses()}")
        # Positive control: the very same line inside S3's fields does gate.
        inside = self.sandbox(LINEAR_PLAN + "  - Approval-Required: true\n")
        inside.cli("init", "plan.md")
        self.check("AC-C16.3", "對照：同一行放在 S3 欄位裡就會擋",
                   inside.state().get("steps", {}).get("S3", {}).get("requires_approval") is True,
                   inside.state().get("steps", {}).get("S3"))
        self.assert_all_checks_passed()


if __name__ == "__main__":
    unittest.main()
