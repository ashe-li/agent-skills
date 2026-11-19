"""scripts/ci_collected.py 的行為測試。

守住：`ids` 的 PASS / FAIL / UNVERIFIABLE 判定（全找到、少一個、log 不列識別碼、
matrix 聯集、空 ids 檔）、比對模式（exact / suffix / prefix）、log 前綴與 ANSI
的剝除、四個內建 runner 的列出樣式（用真實形狀的片段）、`count` 永不回 PASS，
以及 exit code。fixture 內容一律虛構。

Run: python3 -m unittest scripts.tests.test_ci_collected -v
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import ci_collected as cc  # noqa: E402

CLI = SCRIPTS_DIR / "ci_collected.py"
LOGS = Path(__file__).resolve().parent / "fixtures" / "ci_logs"
IDS_FILE = LOGS / "ids-changed.txt"
ALL_FOUND = LOGS / "ids-all-found.log"
ONE_MISSING = LOGS / "ids-one-missing.log"
NO_IDS = LOGS / "no-ids-listed.log"
MATRIX_A = LOGS / "matrix-a.log"
MATRIX_B = LOGS / "matrix-b.log"
COUNT_PATTERN = r"Ran (\d+) tests?"
TS = "2026-01-02T03:04:05.1234567Z "
ESC = "\x1b"

ID_ADD = "widgets.tests.test_cart.CartTests.test_add_item"
ID_REMOVE = "widgets.tests.test_cart.CartTests.test_remove_item"
ID_DISCOUNT = "widgets.tests.test_price.PriceTests.test_discount"

UNITTEST_V = f"""\
test_add_item ({ID_ADD}) ... ok
test_remove_item ({ID_REMOVE}) ... FAIL
test_later ({ID_DISCOUNT}) ... skipped 'later'
Ran 3 tests in 0.002s
"""

PYTEST_V = """\
============================= test session starts ==============================
tests/test_cart.py::TestCart::test_add PASSED                            [ 33%]
tests/test_cart.py::test_param[a-1] FAILED                               [ 66%]
tests/test_price.py::test_discount SKIPPED (not today)                   [100%]
"""

JEST_OUT = """\
PASS  src/cart.test.ts (1.2 s)
FAIL  src/price.test.ts
  ● price › applies discount
Test Suites: 1 failed, 1 passed, 2 total
"""

PLAYWRIGHT_OUT = """\
Running 3 tests using 2 workers
  ✓  1 [chromium] › login.spec.ts:3:5 › login › shows form (1.2s)
  ✘  2 [chromium] › cart.spec.ts:8:5 › cart › adds item (3.4s)
  -  3 [firefox] › cart.spec.ts:12:5 › cart › removes item
"""


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CLI), *args], capture_output=True, text=True
    )


def verdict_of(proc: subprocess.CompletedProcess) -> str:
    return proc.stdout.strip().splitlines()[-1]


def write_tmp(tmp: str, name: str, text: str) -> str:
    path = Path(tmp) / name
    path.write_text(text, encoding="utf-8")
    return str(path)


class IdsVerdictTests(unittest.TestCase):
    def test_all_found_is_pass(self):
        proc = run_cli(
            "ids", "--ids-file", str(IDS_FILE), "--log", str(ALL_FOUND),
            "--runner", "unittest",
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(verdict_of(proc), "VERDICT: PASS")
        self.assertIn(f"FOUND {ID_REMOVE}", proc.stdout)
        self.assertNotIn("NOT-FOUND", proc.stdout)

    def test_one_missing_is_fail_exit_1(self):
        proc = run_cli(
            "ids", "--ids-file", str(IDS_FILE), "--log", str(ONE_MISSING),
            "--runner", "unittest",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(verdict_of(proc), "VERDICT: FAIL")
        self.assertIn(f"NOT-FOUND {ID_REMOVE}", proc.stdout)
        self.assertIn(f"FOUND {ID_ADD}", proc.stdout)

    def test_log_without_listed_ids_is_unverifiable(self):
        proc = run_cli(
            "ids", "--ids-file", str(IDS_FILE), "--log", str(NO_IDS),
            "--runner", "unittest",
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(verdict_of(proc), "VERDICT: UNVERIFIABLE")

    def test_matrix_union_passes_but_each_alone_fails(self):
        base = ["ids", "--ids-file", str(IDS_FILE), "--runner", "unittest"]
        both = run_cli(*base, "--log", str(MATRIX_A), "--log", str(MATRIX_B))
        self.assertEqual(verdict_of(both), "VERDICT: PASS", both.stdout)
        for single in (MATRIX_A, MATRIX_B):
            proc = run_cli(*base, "--log", str(single))
            self.assertEqual(verdict_of(proc), "VERDICT: FAIL", single.name)
            self.assertEqual(proc.returncode, 1)

    def test_duplicate_log_ids_are_deduplicated(self):
        proc = run_cli(
            "ids", "--ids-file", str(IDS_FILE), "--runner", "unittest",
            "--log", str(ALL_FOUND), "--log", str(ALL_FOUND),
        )
        self.assertEqual(proc.stdout.count(f"FOUND {ID_ADD}"), 1)

    def test_empty_ids_file_is_unverifiable(self):
        with tempfile.TemporaryDirectory() as tmp:
            ids = write_tmp(tmp, "ids.txt", "\n# only a comment\n\n")
            proc = run_cli(
                "ids", "--ids-file", ids, "--log", str(ALL_FOUND),
                "--runner", "unittest",
            )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(verdict_of(proc), "VERDICT: UNVERIFIABLE")

    def test_custom_pattern_with_one_group(self):
        proc = run_cli(
            "ids", "--ids-file", str(IDS_FILE), "--log", str(ALL_FOUND),
            "--pattern", r"\((\S+)\) \.\.\. ok$",
        )
        self.assertEqual(verdict_of(proc), "VERDICT: PASS")


class UsageErrorTests(unittest.TestCase):
    def _ids(self, *extra: str) -> subprocess.CompletedProcess:
        return run_cli(
            "ids", "--ids-file", str(IDS_FILE), "--log", str(ALL_FOUND), *extra
        )

    def test_zero_capture_groups_is_usage_error(self):
        proc = self._ids("--pattern", r"test_\w+")
        self.assertEqual(proc.returncode, 2)

    def test_two_capture_groups_is_usage_error(self):
        proc = self._ids("--pattern", r"(test_\w+) \((\S+)\)")
        self.assertEqual(proc.returncode, 2)

    def test_invalid_regex_is_usage_error(self):
        proc = self._ids("--pattern", r"(unclosed")
        self.assertEqual(proc.returncode, 2)

    def test_pattern_and_runner_are_mutually_exclusive(self):
        proc = self._ids("--pattern", r"(x)", "--runner", "unittest")
        self.assertEqual(proc.returncode, 2)

    def test_pattern_or_runner_is_required(self):
        self.assertEqual(self._ids().returncode, 2)

    def test_missing_log_file_is_usage_error(self):
        proc = run_cli(
            "ids", "--ids-file", str(IDS_FILE), "--log", "/nonexistent.log",
            "--runner", "unittest",
        )
        self.assertEqual(proc.returncode, 2)

    def test_count_pattern_needs_exactly_one_group(self):
        proc = run_cli(
            "count", "--before", str(ONE_MISSING), "--after", str(ALL_FOUND),
            "--pattern", r"Ran \d+ tests",
        )
        self.assertEqual(proc.returncode, 2)


class MatchModeTests(unittest.TestCase):
    def test_exact_is_default_and_rejects_partial(self):
        self.assertTrue(cc.is_match("a.b.c", "a.b.c", cc.MATCH_EXACT))
        self.assertFalse(cc.is_match("b.c", "a.b.c", cc.MATCH_EXACT))
        self.assertFalse(cc.is_match("a.b", "a.b.c", cc.MATCH_EXACT))

    def test_suffix_matches_only_log_ending_with_id_at_boundary(self):
        self.assertFalse(cc.is_match("e2e/tests/a.spec.ts", "tests/a.spec.ts", cc.MATCH_SUFFIX))
        self.assertTrue(cc.is_match("tests/a.spec.ts", "e2e/tests/a.spec.ts", cc.MATCH_SUFFIX))
        self.assertTrue(cc.is_match("Cls.test_x", "pkg.mod.Cls.test_x", cc.MATCH_SUFFIX))

    def test_suffix_does_not_match_shorter_log_identifier(self):
        """S5.1 M4：log 只有短名 TestRefund 時，不得把別的套件的同名測試判成 FOUND。"""
        with tempfile.TemporaryDirectory() as tmp:
            ids = write_tmp(tmp, "ids.txt", "pkg/billing.TestRefund\n")
            log = write_tmp(tmp, "log.txt", "--- PASS: TestRefund (0.00s)\n")
            proc = run_cli("ids", "--ids-file", ids, "--log", log,
                           "--pattern", r"^--- PASS: (\w+)", "--match", "suffix")
        self.assertEqual(verdict_of(proc), "VERDICT: FAIL")

    def test_suffix_of_id_matches_repo_rooted_id_against_runner_relative_log(self):
        self.assertTrue(cc.is_match("e2e/tests/a.spec.ts", "tests/a.spec.ts",
                                    cc.MATCH_SUFFIX_OF_ID))
        self.assertFalse(cc.is_match("tests/a.spec.ts", "e2e/tests/a.spec.ts",
                                     cc.MATCH_SUFFIX_OF_ID))
        self.assertFalse(cc.is_match("e2e/xa.spec.ts", "a.spec.ts", cc.MATCH_SUFFIX_OF_ID))
        with tempfile.TemporaryDirectory() as tmp:
            ids = write_tmp(tmp, "ids.txt", "e2e/tests/a.spec.ts\n")
            log = write_tmp(tmp, "log.txt", "PASS tests/a.spec.ts (1.2 s)\n")
            proc = run_cli("ids", "--ids-file", ids, "--log", log,
                           "--runner", "jest", "--match", "suffix-of-id")
        self.assertIn("MATCH: suffix-of-id", proc.stdout)
        self.assertEqual(verdict_of(proc), "VERDICT: PASS")

    def test_suffix_requires_a_boundary_character(self):
        self.assertFalse(cc.is_match("a.spec.ts", "xa.spec.ts", cc.MATCH_SUFFIX))

    def test_prefix_matches_module_against_full_test_id(self):
        self.assertTrue(cc.is_match("tests/test_a.py", "tests/test_a.py::T::t", cc.MATCH_PREFIX))
        self.assertTrue(cc.is_match("pkg.test_a", "pkg.test_a.Cls.test_x", cc.MATCH_PREFIX))
        self.assertFalse(cc.is_match("pkg.test_a", "pkg.test_ab.Cls.test_x", cc.MATCH_PREFIX))

    def test_cli_announces_the_match_mode_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            ids = write_tmp(tmp, "ids.txt", "CartTests.test_add_item\n")
            default = run_cli("ids", "--ids-file", ids, "--log", str(ALL_FOUND), "--runner", "unittest")
            suffix = run_cli(
                "ids", "--ids-file", ids, "--log", str(ALL_FOUND),
                "--runner", "unittest", "--match", "suffix",
            )
        self.assertIn("MATCH: exact", default.stdout)
        self.assertEqual(verdict_of(default), "VERDICT: FAIL")
        self.assertIn("MATCH: suffix", suffix.stdout)
        self.assertEqual(verdict_of(suffix), "VERDICT: PASS")


class NormalizeTests(unittest.TestCase):
    def test_ansi_codes_are_stripped(self):
        line = f"{ESC}[32mtest_a (pkg.T.test_a){ESC}[0m ... {ESC}[32mok{ESC}[0m"
        ids = cc.extract_ids(line, cc.RUNNER_PATTERNS["unittest"])
        self.assertEqual(ids, ("pkg.T.test_a",))

    def test_actions_timestamp_prefix_is_stripped(self):
        line = f"{TS}test_a (pkg.T.test_a) ... ok"
        self.assertEqual(cc.normalize_line(line), "test_a (pkg.T.test_a) ... ok")

    def test_gh_job_step_prefix_is_stripped(self):
        line = f"unit-tests (3.11)\tRun tests\t{TS}test_a (pkg.T.test_a) ... ok"
        self.assertEqual(cc.normalize_line(line), "test_a (pkg.T.test_a) ... ok")

    def test_anchored_pattern_still_works_behind_prefixes(self):
        pattern = r"^(PASS\S*) "
        line = f"job\tstep\t{TS}{ESC}[32mPASS{ESC}[0m x"
        self.assertEqual(cc.extract_ids(line, pattern), ("PASS",))

    def test_tabs_in_plain_output_are_left_alone(self):
        line = "ok\tpkg/cart\t0.012s"
        self.assertEqual(cc.normalize_line(line), line)


class RunnerPatternTests(unittest.TestCase):
    def ids(self, runner: str, text: str) -> tuple[str, ...]:
        return cc.extract_ids(text, cc.RUNNER_PATTERNS[runner])

    def test_four_runners_are_builtin(self):
        self.assertEqual(
            set(cc.RUNNER_PATTERNS), {"unittest", "pytest", "jest", "playwright"}
        )

    def test_each_builtin_has_exactly_one_group(self):
        import re
        for name, pat in cc.RUNNER_PATTERNS.items():
            self.assertEqual(re.compile(pat).groups, 1, name)

    def test_unittest_verbose_lines(self):
        self.assertEqual(
            self.ids("unittest", UNITTEST_V), (ID_ADD, ID_REMOVE, ID_DISCOUNT)
        )

    def test_unittest_dots_only_output_lists_nothing(self):
        self.assertEqual(self.ids("unittest", "....\nRan 4 tests in 0.1s\nOK\n"), ())

    def test_pytest_verbose_lines(self):
        self.assertEqual(
            self.ids("pytest", PYTEST_V),
            (
                "tests/test_cart.py::TestCart::test_add",
                "tests/test_cart.py::test_param[a-1]",
                "tests/test_price.py::test_discount",
            ),
        )

    def test_pytest_param_id_with_spaces(self):
        """CodeRabbit L-3：參數化 id 含空白也要抓得到。"""
        log = ("tests/t.py::test_a[two words] PASSED [ 50%]\n"
               "tests/t.py::test_b PASSED [100%]\n")
        self.assertEqual(self.ids("pytest", log),
                         ("tests/t.py::test_a[two words]", "tests/t.py::test_b"))

    def test_unittest_docstring_style_two_line_output(self):
        """CodeRabbit C24：-v 遇到 docstring 時識別碼單獨一行，下一行才是 `... ok`。"""
        log = ("test_x (pkg.mod.Cls.test_x)\nDoes a thing. ... ok\n"
               "test_y (pkg.mod.Cls.test_y) ... ok\n"
               "FAIL: test_z (pkg.mod.Cls.test_z)\n")
        self.assertEqual(self.ids("unittest", log), ("pkg.mod.Cls.test_x", "pkg.mod.Cls.test_y"))

    def test_skipped_tests_are_not_counted_as_run(self):
        """R19／C24：pytest 的 SKIPPED 與 unittest 的 skipped（含 docstring 兩行式）不算已執行。"""
        cases = {
            "pytest": ("tests/t.py::test_a",
                       "tests/t.py::test_a SKIPPED (not today) [ 50%]\n"
                       "tests/t.py::test_b PASSED [100%]\n"),
            "unittest": ("pkg.T.test_a",
                         "test_a (pkg.T.test_a) ... skipped 'later'\ntest_b (pkg.T.test_b) ... ok\n"),
            "unittest docstring": ("pkg.T.test_a",
                                   "test_a (pkg.T.test_a)\nDoes a thing. ... skipped 'later'\n"
                                   "test_b (pkg.T.test_b) ... ok\n"),
        }
        for label, (ident, log) in cases.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as tmp:
                runner = label.split()[0]
                proc = run_cli("ids", "--ids-file", write_tmp(tmp, "ids.txt", ident + "\n"),
                               "--log", write_tmp(tmp, "log.txt", log), "--runner", runner)
                self.assertIn(f"SKIPPED {ident}", proc.stdout)
                self.assertNotIn(f"\nFOUND {ident}", proc.stdout)
                self.assertEqual((verdict_of(proc), proc.returncode), ("VERDICT: FAIL", 1))

    def test_skipped_in_one_matrix_job_but_run_in_another_is_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_cli("ids", "--ids-file", write_tmp(tmp, "ids.txt", "pkg.T.test_a\n"),
                           "--log", write_tmp(tmp, "a.log", "test_a (pkg.T.test_a) ... skipped 'x'\n"),
                           "--log", write_tmp(tmp, "b.log", "test_a (pkg.T.test_a) ... ok\n"),
                           "--runner", "unittest")
        self.assertEqual(verdict_of(proc), "VERDICT: PASS")

    def test_pytest_quiet_output_lists_nothing(self):
        self.assertEqual(self.ids("pytest", "..F.\n1 failed, 3 passed in 0.2s\n"), ())

    def test_jest_pass_fail_file_lines(self):
        self.assertEqual(
            self.ids("jest", JEST_OUT), ("src/cart.test.ts", "src/price.test.ts")
        )

    def test_playwright_list_reporter_lines(self):
        self.assertEqual(
            self.ids("playwright", PLAYWRIGHT_OUT),
            ("login.spec.ts", "cart.spec.ts"),
        )

    def test_playwright_dot_reporter_lists_nothing(self):
        self.assertEqual(self.ids("playwright", "Running 3 tests\n···\n3 passed\n"), ())

    def test_runner_output_behind_actions_prefix(self):
        text = f"e2e\tRun tests\t{TS}{PLAYWRIGHT_OUT.splitlines()[1]}"
        self.assertEqual(self.ids("playwright", text), ("login.spec.ts",))


class CountTests(unittest.TestCase):
    def count(self, before: Path, after: Path, pattern: str = COUNT_PATTERN):
        return run_cli(
            "count", "--before", str(before), "--after", str(after),
            "--pattern", pattern,
        )

    def test_reports_before_after_delta_and_info(self):
        proc = self.count(ONE_MISSING, ALL_FOUND)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("before: 4", proc.stdout)
        self.assertIn("after: 5", proc.stdout)
        self.assertIn("delta: +1", proc.stdout)
        self.assertEqual(verdict_of(proc), "VERDICT: INFO")

    def test_negative_delta(self):
        proc = self.count(ALL_FOUND, ONE_MISSING)
        self.assertIn("delta: -1", proc.stdout)

    def test_either_side_without_a_count_is_unverifiable(self):
        for before, after in ((NO_IDS, ALL_FOUND), (ALL_FOUND, NO_IDS)):
            proc = self.count(before, after, r"collected (\d+) items")
            self.assertEqual(verdict_of(proc), "VERDICT: UNVERIFIABLE")
            self.assertEqual(proc.returncode, 0)

    def test_count_never_prints_pass(self):
        logs = (ALL_FOUND, ONE_MISSING, NO_IDS, MATRIX_A, MATRIX_B)
        patterns = (COUNT_PATTERN, r"(\d+)", r"collected (\d+)", r"in ([\d.]+)s")
        for before in logs:
            for after in logs:
                for pattern in patterns:
                    out = self.count(before, after, pattern).stdout
                    self.assertNotIn("PASS", out, (before.name, after.name, pattern))
                    self.assertRegex(out, r"VERDICT: (INFO|UNVERIFIABLE)\s*$")


if __name__ == "__main__":
    unittest.main()
