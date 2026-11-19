"""守衛：agents/ 下每份 agent 定義的 frontmatter 與 verifier 類定義的權限設定。

注意：這支測試只證明「文字與工具白名單設定存在」，不證明 agent 實際行為。
工具白名單只在定義被註冊成真正的 subagent 時才由 harness 強制，而且只擋得掉
Write 與 Edit；其餘限制是提示，不是機械強制。

規則適用於所有檔名含 `verifier` 的定義，不寫死檔名清單。

Run: python3 -m unittest scripts.tests.test_agent_definitions -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from kb_source import parse_frontmatter  # noqa: E402

AGENTS_DIR = ROOT / "agents"
REQUIRED_KEYS = ("name", "description", "tools", "model")
FORBIDDEN_TOOLS = ("Write", "Edit")
VERIFIER_PHRASES = ("Bash 仍能寫檔", "提示")

# headed-verifier 六條限制各一個關鍵詞，加上回報的兩個計數欄位。
# 注意：這些只證明「文字存在於定義檔」，不證明 agent 實際行為；
# 六條限制都是提示，Bash 仍能寫檔、瀏覽器 CLI 能送出任何請求。
HEADED_PHRASES = (
    "寫入攔截任何時候都掛著",  # (1)
    "假資料",  # (2)
    "非 GET 請求",  # (3)
    "不執行任何 rm",  # (4)
    "marker",  # (5)
    "使用者原話",  # (6)
    "載入次數",  # 回報欄位
    "掛攔截的載入次數",  # 回報欄位
)


def _definitions() -> list[Path]:
    """agents/ 下有 frontmatter 的定義檔（略過沒有 frontmatter 的 README.md）。"""
    found = []
    for path in sorted(AGENTS_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if path.name == "README.md" and not text.startswith("---"):
            continue
        found.append(path)
    return found


def _verifiers() -> list[Path]:
    return [p for p in _definitions() if "verifier" in p.name]


def _tools(path: Path) -> list[str]:
    value = parse_frontmatter(path.read_text(encoding="utf-8")).data.get("tools", "")
    if isinstance(value, str):
        value = value.split(",")
    return [t.strip() for t in value if t.strip()]


class AgentDefinitionGuard(unittest.TestCase):
    def test_definitions_discovered(self) -> None:
        self.assertTrue(_definitions(), "agents/ 下找不到任何定義檔")

    def test_frontmatter_complete(self) -> None:
        for path in _definitions():
            with self.subTest(agent=path.name):
                fm = parse_frontmatter(path.read_text(encoding="utf-8"))
                self.assertEqual(fm.reason, "", f"{path.name}: {fm.reason}")
                for key in REQUIRED_KEYS:
                    self.assertTrue(fm.data.get(key), f"{path.name} 缺 frontmatter 欄位 {key}")

    def test_name_matches_filename(self) -> None:
        for path in _definitions():
            with self.subTest(agent=path.name):
                fm = parse_frontmatter(path.read_text(encoding="utf-8"))
                self.assertEqual(fm.data.get("name"), path.stem)


class VerifierGuard(unittest.TestCase):
    def test_verifier_exists(self) -> None:
        self.assertTrue(_verifiers(), "找不到任何檔名含 verifier 的定義")

    def test_verifier_tools_have_no_write_or_edit(self) -> None:
        for path in _verifiers():
            with self.subTest(agent=path.name):
                tools = _tools(path)
                self.assertTrue(tools, f"{path.name} 沒有 tools 白名單")
                for forbidden in FORBIDDEN_TOOLS:
                    self.assertNotIn(forbidden, tools)

    def test_verifier_states_enforcement_boundary(self) -> None:
        for path in _verifiers():
            text = path.read_text(encoding="utf-8")
            for phrase in VERIFIER_PHRASES:
                with self.subTest(agent=path.name, phrase=phrase):
                    self.assertIn(phrase, text)


class HeadedVerifierGuard(unittest.TestCase):
    """只證明關鍵句存在，不證明行為。通用的 verifier 規則已由 VerifierGuard 涵蓋。"""

    def test_headed_verifier_has_six_constraints_and_report_fields(self) -> None:
        text = (AGENTS_DIR / "headed-verifier.md").read_text(encoding="utf-8")
        for phrase in HEADED_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
