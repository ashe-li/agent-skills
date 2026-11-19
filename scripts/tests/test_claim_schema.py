"""scripts/claim_schema.py 的行為測試。

守住：lint 對新格式（每條宣稱一個二級小節、指令放 bash fenced block）的各種
缺陷判定、舊表格格式的相容處理（LEGACY-TABLE 警告、儲存格含直線判 SCHEMA-DEFECT）、
extract 只印不跑，以及抽出的腳本內容原樣保留。fixture 內容一律虛構。

Run: python3 -m unittest scripts.tests.test_claim_schema -v
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

import claim_schema as cs  # noqa: E402

CLI = SCRIPTS_DIR / "claim_schema.py"
CLAIMS = Path(__file__).resolve().parent / "fixtures" / "claims"
SAMPLE = CLAIMS / "sample-20.md"
LEGACY = CLAIMS / "legacy-table.md"
BT = "`" * 3


def section(n: int, cmd: str | None = "echo ok", verdict: str | None = "PASS") -> str:
    parts = [f"## {n}. 宣稱 {n}", ""]
    if cmd is not None:
        parts += [f"{BT}bash", cmd, BT, ""]
    parts.append("關鍵輸出：ok")
    if verdict is not None:
        parts.append(f"判定：{verdict}")
    return "\n".join(parts) + "\n"


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CLI), *args], capture_output=True, text=True
    )


def codes(text: str) -> list[str]:
    return [i.code for i in cs.lint_text(text, "f.md").issues]


class LintNewFormat(unittest.TestCase):
    def test_valid_file_passes(self) -> None:
        text = section(1) + "\n" + section(2, cmd=None, verdict="UNVERIFIABLE")
        result = cs.lint_text(text, "f.md")
        self.assertEqual(result.issues, ())
        self.assertEqual(result.claims, 2)

    def test_missing_verdict(self) -> None:
        self.assertIn("NO-VERDICT", codes(section(1, verdict=None)))

    def test_invalid_verdict_value(self) -> None:
        self.assertIn("BAD-VERDICT", codes(section(1, verdict="MAYBE")))

    def test_numbering_gap(self) -> None:
        text = section(1) + "\n" + section(3)
        self.assertIn("NUMBERING", codes(text))

    def test_numbering_must_start_at_one(self) -> None:
        self.assertIn("NUMBERING", codes(section(2)))

    def test_escaped_pipe_in_block_is_schema_defect(self) -> None:
        text = section(1, cmd=r"grep -E 'a\|b' file.txt")
        self.assertIn("SCHEMA-DEFECT", codes(text))

    def test_escaped_pipe_with_extended_regex_tools_is_schema_defect(self) -> None:
        """複驗 N4：ERE／PCRE 下的 `\\|` 比對的是字面直線，會靜默回 0 筆，仍要擋。"""
        for cmd in (r"grep -iE 'a\|b' f", r"grep -P 'a\|b' f", r"egrep 'a\|b' f",
                    r"rg 'a\|b' src", r"awk '/a\|b/' f", r"sed -E 's/a\|b/x/' f",
                    r"git log | grep --extended-regexp 'a\|b'"):
            with self.subTest(cmd=cmd):
                result = cs.lint_text(section(1, cmd=cmd), "f.md")
                self.assertIn("SCHEMA-DEFECT", [i.code for i in result.issues])
                self.assertEqual(result.errors, 1)

    def test_plain_pipe_and_ere_alternation_are_fine(self) -> None:
        text = section(1, cmd="git log | grep -E 'a|b' | wc -l")
        self.assertEqual(codes(text), [])

    def test_unverifiable_with_block_is_error(self) -> None:
        self.assertIn("UNEXPECTED-BLOCK", codes(section(1, verdict="UNVERIFIABLE")))

    def test_non_unverifiable_without_block_is_error(self) -> None:
        self.assertIn("NO-BLOCK", codes(section(1, cmd=None)))

    def test_second_level_heading_without_number_is_error(self) -> None:
        self.assertIn("BAD-HEADING", codes(section(1) + "\n## 總結\n"))

    def test_heading_inside_fenced_block_is_not_a_heading(self) -> None:
        text = section(1, cmd="cat <<'X'\n## 1. 假標題\nX")
        self.assertEqual(codes(text), [])

    def test_issue_line_format_and_summary(self) -> None:
        proc = run_cli_with_text(section(1, verdict=None))
        lines = proc.stdout.strip().splitlines()
        self.assertRegex(lines[0], r"^.+:\d+: NO-VERDICT: .+")
        self.assertRegex(lines[-1], r"1 條宣稱.*1 個錯誤.*0 個警告")
        self.assertEqual(proc.returncode, 1)

    def test_bre_alternation_in_block_is_not_an_error(self) -> None:
        """CodeRabbit：`grep 'a\\|b'` 是合法的 BRE 交替，fenced block 內只警告。"""
        result = cs.lint_text(section(1, cmd=r"grep 'a\|b' f.txt"), "f.md")
        self.assertEqual([(i.code, i.severity) for i in result.issues],
                         [("ESCAPED-PIPE", cs.EC_WARNING)])

    def test_missing_key_output_line_is_error(self) -> None:
        """CodeRabbit L-4：缺「關鍵輸出：」行要擋；fence 內的同字樣不算。"""
        text = section(1).replace("關鍵輸出：ok\n", "")
        self.assertIn("NO-KEY-OUTPUT", codes(text))
        inside = section(1, cmd="echo '關鍵輸出：假的'").replace("關鍵輸出：ok\n", "")
        self.assertIn("NO-KEY-OUTPUT", codes(inside))
        self.assertNotIn("NO-KEY-OUTPUT", codes(section(1)))

    def test_usage_error_exit_2(self) -> None:
        self.assertEqual(run_cli().returncode, 2)
        self.assertEqual(run_cli("lint").returncode, 2)
        self.assertEqual(run_cli("bogus", "x").returncode, 2)

    def test_missing_file_exit_2(self) -> None:
        self.assertEqual(run_cli("lint", "/nonexistent/claims.md").returncode, 2)

    def test_empty_file_has_no_claims_warning_only(self) -> None:
        result = cs.lint_text("", "f.md")
        self.assertEqual(result.claims, 0)
        self.assertEqual(result.errors, 0)
        self.assertEqual([i.code for i in result.issues], ["NO-CLAIMS"])

    def test_file_without_claims(self) -> None:
        result = cs.lint_text("# 只有標題\n\n一些文字。\n", "f.md")
        self.assertEqual(result.claims, 0)
        self.assertEqual(result.errors, 0)

    def test_sample_20_lints_clean(self) -> None:
        proc = run_cli("lint", str(SAMPLE))
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("0 個錯誤", proc.stdout)


def run_cli_with_text(text: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "c.md"
        path.write_text(text, encoding="utf-8")
        return run_cli("lint", str(path))


class LintLegacyTable(unittest.TestCase):
    def test_legacy_table_warns_and_exits_zero(self) -> None:
        proc = run_cli("lint", str(LEGACY))
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("LEGACY-TABLE", proc.stdout)
        self.assertIn("0 個錯誤", proc.stdout)

    def test_legacy_cell_with_unescaped_pipe_is_defect(self) -> None:
        text = (
            "| Claim | 證據指令 | 關鍵輸出 | 判定 |\n|---|---|---|---|\n"
            "| 宣稱 | `grep -E 'a|b' f` | out | PASS |\n"
        )
        found = codes(text)
        self.assertIn("SCHEMA-DEFECT", found)
        self.assertIn("LEGACY-TABLE", found)

    def test_legacy_cell_with_escaped_pipe_is_defect(self) -> None:
        text = (
            "| Claim | 證據指令 | 關鍵輸出 | 判定 |\n|---|---|---|---|\n"
            "| 宣稱 | `grep -E 'a\\|b' f` | out | PASS |\n"
        )
        self.assertIn("SCHEMA-DEFECT", codes(text))

    def test_legacy_defect_makes_exit_one(self) -> None:
        text = (
            "| Claim | 證據指令 | 關鍵輸出 | 判定 |\n|---|---|---|---|\n"
            "| 宣稱 | `a | b` | out | PASS |\n"
        )
        self.assertEqual(run_cli_with_text(text).returncode, 1)


class Extract(unittest.TestCase):
    def test_sample_20_has_exactly_20_echo_lines(self) -> None:
        proc = run_cli("extract", str(SAMPLE))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        echoes = [l for l in proc.stdout.splitlines() if l.startswith('echo "=== #')]
        self.assertEqual(len(echoes), 20)

    def test_unverifiable_claim_is_not_emitted(self) -> None:
        out = cs.extract_text(
            section(1) + "\n" + section(2, cmd=None, verdict="UNVERIFIABLE")
        )
        self.assertIn('echo "=== #1"', out)
        self.assertNotIn("=== #2", out)

    def test_claim_numbers_are_preserved(self) -> None:
        text = section(1) + "\n" + section(2, cmd=None, verdict="UNVERIFIABLE")
        text += "\n" + section(3, cmd="echo third")
        out = cs.extract_text(text)
        self.assertIn('echo "=== #3"', out)
        self.assertIn("echo third", out)

    def test_extract_does_not_execute(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "created-by-extract"
            claim = Path(tmp) / "c.md"
            claim.write_text(section(1, cmd=f"touch '{marker}'"), encoding="utf-8")
            proc = run_cli("extract", str(claim))
            self.assertEqual(proc.returncode, 0)
            self.assertIn("touch", proc.stdout)
            self.assertFalse(marker.exists())

    def test_special_content_is_preserved_verbatim(self) -> None:
        body = (
            "cat <<'EOF'\n"
            'name: "demo" and \'single\'\n'
            "EOF\n"
            'echo "$(git rev-parse --short HEAD)" | grep -E \'a|b\''
        )
        out = cs.extract_text(section(1, cmd=body))
        self.assertIn(body, out)

    def test_extract_isolates_shell_state_between_claims(self) -> None:
        """CodeRabbit M8：前一條宣稱的 cd、變數、set -e 不得影響下一條。"""
        text = (section(1, cmd="cd /\nX=leaked\nset -e\nfalse") + "\n"
                + section(2, cmd='pwd\necho "x=${X:-unset}"'))
        script = cs.extract_text(text)
        with tempfile.TemporaryDirectory() as tmp:
            here = str(Path(tmp).resolve())
            proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, cwd=here)
        self.assertEqual(proc.stdout.splitlines(), ["=== #1", "=== #2", here, "x=unset"])

    def test_extracted_sample_is_valid_bash_syntax(self) -> None:
        out = run_cli("extract", str(SAMPLE)).stdout
        proc = subprocess.run(["bash", "-n"], input=out, text=True, capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_empty_file_extracts_nothing(self) -> None:
        self.assertEqual(cs.extract_text(""), "")

    def test_file_without_claims_extracts_nothing(self) -> None:
        self.assertEqual(cs.extract_text("# 標題\n\n沒有宣稱\n"), "")

    def test_extract_legacy_table_emits_nothing_and_warns(self) -> None:
        proc = run_cli("extract", str(LEGACY))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertIn("LEGACY-TABLE", proc.stderr)

    def test_extract_usage_error(self) -> None:
        self.assertEqual(run_cli("extract").returncode, 2)


if __name__ == "__main__":
    unittest.main()
