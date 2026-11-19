"""守衛：公開內容不得依賴作者本機的知識庫路徑或本機規則檔。

兩組樣式，命中數都必須為 0：
- 第一組掃 */SKILL.md、README.md、agents/、docs/、rules/、scripts/hooks/。
- 第二組只掃 */SKILL.md。
排除 CHANGELOG.md、plans/、research/、各 skill 的 fixtures/、scripts/tests/。
失敗訊息只列「檔案:行號 與樣式名」，不印整行內容。

Run: python3 -m unittest scripts.tests.test_no_local_paths -v
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

GROUP1 = {
    "Documents/knowledge-base": re.compile(r"Documents/knowledge-base"),
    "bm25_retrieve": re.compile(r"bm25_retrieve"),
    "/Users/<非 <>": re.compile(r"/Users/(?!<)\S"),
}
GROUP2 = {
    "rules/common/": re.compile(r"rules/common/"),
    "repo-ownership": re.compile(r"repo-ownership"),
    "knowledge-base/": re.compile(r"knowledge-base/"),
    "wiki/(learned|rules|decisions)/<小寫>.md": re.compile(
        r"wiki/(?:learned|rules|decisions)/[a-z][^\s`)]*\.md"
    ),
}
EXCLUDED_TOP = {"plans", "research"}
EXCLUDED_NAMES = {"CHANGELOG.md"}


def _excluded(rel: Path) -> bool:
    parts = rel.parts
    if parts[0] in EXCLUDED_TOP or rel.name in EXCLUDED_NAMES:
        return True
    if "fixtures" in parts[:-1]:
        return True
    return parts[:2] == ("scripts", "tests")


def _files(root: Path, group: int) -> list[Path]:
    found: list[Path] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or ".git" in p.relative_to(root).parts:
            continue
        rel = p.relative_to(root)
        if _excluded(rel):
            continue
        top = rel.parts[0]
        is_skill = len(rel.parts) == 2 and rel.name == "SKILL.md"
        if group == 2:
            ok = is_skill
        else:
            ok = (
                is_skill
                or rel == Path("README.md")
                or top in {"agents", "docs", "rules"}
                or rel.parts[:2] == ("scripts", "hooks")
            )
        if ok:
            found.append(p)
    return found


def scan(root: Path, group: int) -> list[str]:
    patterns = GROUP1 if group == 1 else GROUP2
    hits: list[str] = []
    for path in _files(root, group):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        for no, line in enumerate(lines, 1):
            for name, rx in patterns.items():
                if rx.search(line):
                    hits.append(f"{path.relative_to(root)}:{no} [{name}]")
    return hits


class RepoGuard(unittest.TestCase):
    def test_group1_no_local_paths(self) -> None:
        hits = scan(ROOT, 1)
        self.assertEqual(hits, [], "第一組樣式命中:\n" + "\n".join(hits))

    def test_group2_no_local_rule_references_in_skills(self) -> None:
        hits = scan(ROOT, 2)
        self.assertEqual(hits, [], "第二組樣式命中:\n" + "\n".join(hits))


class ScannerSelfCheck(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def put(self, rel: str, text: str) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def test_group1_hits_and_misses(self) -> None:
        self.put("a/SKILL.md", "cd ~/Documents/knowledge-base\n/Users/<name>/x\n/Users/bob/x\n")
        hits = scan(self.root, 1)
        self.assertEqual(len(hits), 2)
        self.assertTrue(any("a/SKILL.md:1" in h for h in hits))
        self.assertTrue(any("a/SKILL.md:3" in h for h in hits))
        self.assertFalse(any("a/SKILL.md:2" in h for h in hits))

    def test_group2_case_sensitive_wiki_entries(self) -> None:
        self.put("a/SKILL.md", "wiki/rules/INDEX.md\nwiki/learned/foo-bar.md\n")
        hits = scan(self.root, 2)
        self.assertEqual(len(hits), 1)
        self.assertIn("a/SKILL.md:2", hits[0])

    def test_exclusions_and_scope(self) -> None:
        bad = "Documents/knowledge-base rules/common/\n"
        for rel in ("CHANGELOG.md", "plans/x.md", "research/x.md",
                    "a/fixtures/SKILL.md", "scripts/tests/t.md"):
            self.put(rel, bad)
        self.put("docs/d.md", bad)  # group2 只掃 SKILL.md
        self.assertEqual(scan(self.root, 1), ["docs/d.md:1 [Documents/knowledge-base]"])
        self.assertEqual(scan(self.root, 2), [])

    def test_report_does_not_leak_line_content(self) -> None:
        self.put("a/SKILL.md", "secret-text bm25_retrieve\n")
        self.assertNotIn("secret-text", " ".join(scan(self.root, 1)))


if __name__ == "__main__":
    unittest.main()
