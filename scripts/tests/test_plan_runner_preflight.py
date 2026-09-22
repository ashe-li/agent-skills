"""Unit tests for plan_runner_preflight — tool extraction and file checks."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import plan_runner_preflight as pf  # noqa: E402


def fake_which(*present: str):
    return lambda name: f"/usr/bin/{name}" if name in present else None


class ExtractToolsTests(unittest.TestCase):
    def test_first_word_of_a_plain_command(self):
        self.assertEqual(pf.extract_tools("git status --short"), ("git",))

    def test_chained_segments_each_contribute_their_tool(self):
        self.assertEqual(
            pf.extract_tools("npm ci && npx tsc || make lint; rg foo | sort"),
            ("npm", "npx", "make", "rg", "sort"),
        )

    def test_env_assignments_are_not_tools(self):
        self.assertEqual(pf.extract_tools("FOO=1 BAR=x pytest -q"), ("pytest",))

    def test_slash_commands_are_skills_not_executables(self):
        for command in ("/verify", "/code-review", "/plugin:skill", "`/pr`".strip("`")):
            self.assertEqual(pf.extract_tools(command), (), command)

    def test_shell_builtins_are_skipped(self):
        self.assertEqual(pf.extract_tools("cd sub && python3 -c pass"), ("python3",))

    def test_duplicates_collapse_in_first_seen_order(self):
        self.assertEqual(pf.extract_tools("git a && make && git b"), ("git", "make"))

    def test_unparseable_segment_is_skipped_not_guessed(self):
        self.assertEqual(pf.extract_tools("echo 'unbalanced && jq ."), ("jq",))

    def test_empty_and_none_yield_nothing(self):
        self.assertEqual(pf.extract_tools(None), ())
        self.assertEqual(pf.extract_tools("   "), ())

    def test_shell_expansions_are_skipped(self):
        for command in ("$RUNNER --x", "${TOOL} run", "$(which jq) .", "`which jq` ."):
            self.assertEqual(pf.extract_tools(command), (), command)
        self.assertEqual(pf.extract_tools("$RUNNER x && git status"), ("git",))

    def test_relative_path_after_cd_is_skipped(self):
        self.assertEqual(pf.extract_tools("cd sub && ./run.sh"), ())
        self.assertEqual(pf.extract_tools("cd sub; bin/x && make"), ("make",))
        self.assertEqual(pf.extract_tools("pushd sub && ./run.sh"), ())

    def test_relative_path_without_cd_is_still_checked(self):
        self.assertEqual(pf.extract_tools("./run.sh --x"), ("./run.sh",))

    def test_absolute_path_after_cd_is_still_checked(self):
        self.assertEqual(pf.extract_tools("cd sub && /usr/bin/env x"), ("/usr/bin/env",))

    def test_absolute_path_executable_is_kept_verbatim(self):
        self.assertEqual(pf.extract_tools("/usr/bin/env python3"), ("/usr/bin/env",))


class CheckToolTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)

    def test_found_on_path(self):
        check = pf.check_tool("git", self.base, fake_which("git"))
        self.assertTrue(check.ok)
        self.assertEqual(check.hint, "")
        self.assertEqual(check.kind, pf.KIND_TOOL)

    def test_missing_on_path_has_a_hint(self):
        check = pf.check_tool("nope", self.base, fake_which())
        self.assertFalse(check.ok)
        self.assertIn("PATH", check.hint)
        self.assertIn("nope", check.hint)

    def test_relative_path_resolved_against_base_dir(self):
        script = self.base / "bin" / "run.sh"
        script.parent.mkdir()
        script.write_text("#!/bin/sh\n")
        script.chmod(0o755)
        self.assertTrue(pf.check_tool("bin/run.sh", self.base, fake_which()).ok)

    def test_path_that_is_not_executable_fails(self):
        script = self.base / "run.sh"
        script.write_text("#!/bin/sh\n")
        script.chmod(0o644)
        check = pf.check_tool("./run.sh", self.base, fake_which())
        self.assertFalse(check.ok)
        self.assertIn("chmod", check.hint)


class RunPreflightTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.runner = self.base / "plan_runner.py"
        self.plan = self.base / "plan.md"
        self.state = self.base / ".plan-state" / "plan.state.json"
        for path in (self.runner, self.plan, self.state):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x")

    def _run(self, commands, which=fake_which("git", "python3")):
        return pf.run_preflight(
            runner_path=self.runner, plan_path=self.plan, state_path=self.state,
            commands=commands, base_dir=self.base, which=which,
        )

    def test_probe_p7_commands_are_not_false_failures(self):
        """verify-ab probe P7: `cd sub && ./run.sh` and `$RUNNER --x` used to
        fail preflight and stop the hook before step one."""
        result = self._run(["cd sub && ./run.sh", "$RUNNER --x"])
        self.assertTrue(result.ok, [c.name for c in result.failures])

    def test_all_present_is_ok(self):
        result = self._run(["git status", "/verify", None, "cd x && python3 -c 1"])
        self.assertTrue(result.ok)
        self.assertEqual(result.failures, ())
        kinds = [c.kind for c in result.checks]
        self.assertEqual(kinds, ["runner", "plan", "state", "tool", "tool"])

    def test_each_missing_tool_is_its_own_failure(self):
        result = self._run(["alpha --x", "FOO=1 beta run", "git log"])
        self.assertFalse(result.ok)
        self.assertEqual([c.name for c in result.failures], ["alpha", "beta"])

    def test_tool_named_twice_is_checked_once(self):
        result = self._run(["git a", "git b"])
        self.assertEqual(sum(1 for c in result.checks if c.name == "git"), 1)

    def test_missing_state_hint_says_init(self):
        self.state.unlink()
        result = self._run([])
        self.assertEqual([c.kind for c in result.failures], ["state"])
        self.assertIn("init", result.failures[0].hint)

    def test_missing_plan_and_runner_named(self):
        self.plan.unlink()
        self.runner.unlink()
        names = [c.name for c in self._run([]).failures]
        self.assertEqual(names, [str(self.runner), str(self.plan)])

    @unittest.skipIf(os.geteuid() == 0, "root reads everything")
    def test_unreadable_runner_fails(self):
        self.runner.chmod(0)
        self.addCleanup(self.runner.chmod, 0o644)
        self.assertEqual([c.kind for c in self._run([]).failures], ["runner"])


class RenderTests(unittest.TestCase):
    def _result(self):
        return pf.PreflightResult((
            pf.PreflightCheck("runner", "/r.py", True),
            pf.PreflightCheck("tool", "alpha", False, "install alpha"),
        ))

    def test_dict_shape(self):
        data = pf.result_to_dict(self._result())
        self.assertFalse(data["ok"])
        self.assertEqual(data["checks"][1], {
            "kind": "tool", "name": "alpha", "ok": False, "hint": "install alpha",
        })

    def test_markdown_one_line_per_check_with_hint(self):
        text = pf.format_md(self._result())
        self.assertIn("preflight: FAIL", text)
        self.assertIn("- [ok  ] runner: /r.py", text)
        self.assertIn("- [FAIL] tool: alpha — install alpha", text)

    def test_results_are_immutable(self):
        with self.assertRaises(Exception):
            self._result().checks[0].ok = False  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
