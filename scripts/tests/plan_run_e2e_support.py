"""Shared harness for test_plan_run_e2e_adversarial.py.

Everything here drives plan_runner.py from the outside: every CLI call and
every Stop hook invocation is a subprocess, each scenario gets its own fake
$HOME, and nothing imports runner internals. What these tests can see is
therefore exactly what a real session sees: exit codes, stdout JSON, and the
files the runner leaves behind.

Two environment variables, both optional:

- PLAN_RUN_E2E_RUNNER: run the scenarios against another copy of
  plan_runner.py (a mutant under the scratchpad, an older checkout). The
  copy needs its sibling modules next to it, as a real install does.
- PLAN_RUN_E2E_LOG_DIR: write one Markdown + one JSONL run log per scenario
  into this directory. Unset (the default, and in CI) nothing is written.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent
DEFAULT_RUNNER = SCRIPTS_DIR / "plan_runner.py"
WRAPPER = SCRIPTS_DIR / "hooks" / "plan-run-stop.sh"

RUNNER_ENV_VAR = "PLAN_RUN_E2E_RUNNER"
LOG_DIR_ENV_VAR = "PLAN_RUN_E2E_LOG_DIR"
SUBPROCESS_TIMEOUT_SECONDS = 60

# The observable contract under test, in one place. A rename in the runner
# should be a one-line change here, not a hunt through every scenario.
PREFLIGHT_FAIL_RC = 1
PREFLIGHT_MSG = "[plan-run] PREFLIGHT 失敗："
STUCK_MSG = "[plan-run] STUCK："
STUCK_AT = 3
OOS_STATE_KEY = "out_of_scope_log"
SANDBOX_ENV_VAR = "PLAN_SANDBOX_ROOT"
SANDBOX_KEYWORDS = ("~/.claude", "log-out-of-scope", "不算注入")
APPROVAL_FIELD = "  - Requires-Approval: true\n"
STALE_TRANSCRIPT_SECONDS = 3600

LINEAR_PLAN = """# E2E Plan

### Phase 1: Setup

- [ ] S1 First step
  - Files: `a.py`
  - Action: do A
{s1_extra}
- [ ] S2 Second step
  - Dependencies: S1
  - Files: `b.py`
  - Action: do B

- [ ] S3 Third step
  - Dependencies: S2
  - Files: `c.py`
  - Action: do C
"""

# S1 is gated, S4 is an independent non-gated step that is ready at the same
# time, so the order in which they are handed out is observable.
GATED_PLAN = """# E2E Gate Plan

### Phase 1: Setup

- [ ] S1 Gated step
  - Files: `a.py`
  - Action: do A
{gate}
- [ ] S2 After gate
  - Dependencies: S1
  - Files: `b.py`
  - Action: do B

- [ ] S4 Independent step
  - Files: `d.py`
  - Action: do D
"""


def runner_under_test() -> Path:
    override = os.environ.get(RUNNER_ENV_VAR)
    return Path(override).resolve() if override else DEFAULT_RUNNER


def is_block(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("decision") == "block"


def system_message(payload: Any) -> str:
    return (payload or {}).get("systemMessage") or ""


def reason_of(payload: Any) -> str:
    return (payload or {}).get("reason") or ""


def start_lines(payload: Any) -> list[str]:
    """The `start` command lines a block reason tells the model to run.

    Matched on the runner invocation, not the bare word: the repeat-assign
    note also says "start" in prose.
    """
    return [ln for ln in reason_of(payload).splitlines()
            if "plan_runner.py" in ln and " start " in ln]


def assigns_only(payload: Any, step_id: str) -> bool:
    lines = start_lines(payload)
    return is_block(payload) and bool(lines) and all(
        ln.rstrip().endswith(step_id) for ln in lines
    )


def parse_json(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def ready_ids(next_payload: dict[str, Any]) -> list[str]:
    fresh = [s.get("id") for s in next_payload.get("ready_steps_new", [])]
    return fresh + list(next_payload.get("ready_steps_still", []))


class RunLog:
    """Per-scenario record of every call and every assertion.

    Assertions are collected rather than raised one by one, so a failing
    scenario still produces a complete log of what happened after the first
    miss; the test asserts on `failures()` at the end.
    """

    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self.rows: list[dict[str, Any]] = []
        self.checks: list[dict[str, str]] = []

    def event(self, row: dict[str, Any]) -> None:
        self.rows.append({"seq": len(self.rows) + 1, **row})

    def check(self, ac: str, desc: str, ok: bool, detail: Any = "") -> None:
        text = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
        self.checks.append({
            "ac": ac, "desc": desc, "verdict": "PASS" if ok else "FAIL", "detail": text[:300],
        })

    def failures(self) -> list[str]:
        return [f"{c['ac']} {c['desc']} :: {c['detail']}"
                for c in self.checks if c["verdict"] == "FAIL"]

    def write_if_requested(self) -> None:
        target = os.environ.get(LOG_DIR_ENV_VAR)
        if not target:
            return
        out = Path(target)
        out.mkdir(parents=True, exist_ok=True)
        with (out / f"{self.scenario}.runlog.jsonl").open("w", encoding="utf-8") as fh:
            for row in self.rows:
                fh.write(json.dumps({"scenario": self.scenario, **row}, ensure_ascii=False) + "\n")
            for check in self.checks:
                fh.write(json.dumps({"scenario": self.scenario, "check": check},
                                    ensure_ascii=False) + "\n")
        (out / f"{self.scenario}.runlog.md").write_text(self._markdown(), encoding="utf-8")

    def _markdown(self) -> str:
        lines = [f"# Run log: {self.scenario}", "",
                 f"runner: `{runner_under_test()}`", "",
                 "| # | actor | action | rc | decision | statuses | note |",
                 "|---|---|---|---|---|---|---|"]
        for r in self.rows:
            statuses = ",".join(f"{k}={v}" for k, v in r["statuses"].items())
            lines.append(f"| {r['seq']} | {r['actor']} | `{r['action']}` | {r['rc']} | "
                         f"{r.get('decision', '-')} | {statuses} | {_cell(r.get('note', ''))} |")
        lines += ["", "## Checks", "", "| AC | verdict | assertion | detail |",
                  "|---|---|---|---|"]
        for c in self.checks:
            lines.append(f"| {c['ac']} | {c['verdict']} | {c['desc']} | {_cell(c['detail'])} |")
        return "\n".join(lines) + "\n"


def _cell(text: str, limit: int = 140) -> str:
    return text.replace("\n", " ").replace("|", "/")[:limit]


class Sandbox:
    """One fake $HOME holding one plan, driven only through subprocesses."""

    def __init__(self, log: RunLog, plan_text: str) -> None:
        self.log = log
        self.runner = runner_under_test()
        # resolve(): macOS /tmp is a symlink to /private/tmp, and the Stop
        # hook wrapper compares `pwd -P` against $HOME -- an unresolved fake
        # HOME makes the wrapper exit 0 silently, which looks like success.
        self.home = Path(tempfile.mkdtemp(prefix="plan-run-e2e-")).resolve()
        (self.home / ".claude").mkdir()
        self.proj = self.home / "proj"
        self.proj.mkdir()
        self.plan = self.proj / "plan.md"
        self.plan.write_text(plan_text, encoding="utf-8")
        env = {k: v for k, v in os.environ.items()
               if k not in ("PLAN_RUN_BLOCK_BUDGET", SANDBOX_ENV_VAR, "AGENT_SKILLS_DIR")}
        self.env = {**env, "HOME": str(self.home)}

    def cleanup(self) -> None:
        shutil.rmtree(self.home, ignore_errors=True)

    def cli(self, *args: str) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            [sys.executable, str(self.runner), *args],
            cwd=self.proj, env=self.env, capture_output=True, text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
        note = (proc.stderr.strip() or proc.stdout.strip())[:200]
        self.log.event({"actor": "cli", "action": " ".join(args), "rc": proc.returncode,
                        "statuses": self.statuses(), "note": note})
        return proc

    def hook(self, session: str, transcript: Path,
             via_wrapper: bool = False) -> tuple[int, dict[str, Any] | None, str]:
        """One Stop event. rc is taken from the process itself, never
        through a pipe (a `| tail` would report the pipe's rc instead)."""
        payload = json.dumps({
            "hook_event_name": "Stop", "session_id": session,
            "transcript_path": str(transcript), "cwd": str(self.proj),
            "stop_hook_active": True,
        })
        argv = (["bash", str(WRAPPER)] if via_wrapper
                else [sys.executable, str(self.runner), "hook-stop"])
        proc = subprocess.run(argv, input=payload, cwd=self.proj, env=self.env,
                              capture_output=True, text=True,
                              timeout=SUBPROCESS_TIMEOUT_SECONDS)
        out = proc.stdout.strip()
        parsed = parse_json(out) if out else None
        decision = "silent" if parsed is None else parsed.get("decision", "allow")
        note = system_message(parsed) or reason_of(parsed)
        self.log.event({"actor": "hook", "action": f"hook-stop session={session}",
                        "rc": proc.returncode, "decision": decision,
                        "statuses": self.statuses(), "note": note[:200]})
        return proc.returncode, parsed, out

    def transcript(self, name: str, age_seconds: float = 0) -> Path:
        path = self.home / f"{name}.jsonl"
        path.write_text("{}\n", encoding="utf-8")
        self.age(path, age_seconds)
        return path

    @staticmethod
    def age(path: Path, seconds: float) -> None:
        stamp = time.time() - seconds
        os.utime(path, (stamp, stamp))

    def state(self) -> dict[str, Any]:
        path = self.proj / ".plan-state" / "plan.state.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def statuses(self) -> dict[str, str]:
        return {k: v.get("status") for k, v in self.state().get("steps", {}).items()}

    def checkpoint(self) -> dict[str, Any]:
        path = self.proj / ".plan-state" / "plan.checkpoint.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def pointer(self) -> dict[str, Any]:
        found = sorted((self.home / ".claude" / "plan-run" / "active").glob("*.json"))
        return json.loads(found[0].read_text()) if found else {}
