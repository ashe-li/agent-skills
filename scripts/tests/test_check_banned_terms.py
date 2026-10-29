"""scripts/check-banned-terms.sh 的行為測試。

守住五件事：命中時 exit 1 且輸出只有 path:line（不洩漏命中字樣）、沒有黑名單時
exit 0、--staged 只看新增行、--range 同時掃 diff 與 commit message、
.githooks/pre-commit 會先串接全域 core.hooksPath 的 pre-commit 再跑字樣守衛。
測試用的假黑名單一律用無關字樣，真名稱不得出現在 repo。

Run: python3 -m unittest scripts.tests.test_check_banned_terms -v
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "check-banned-terms.sh"
HOOK = Path(__file__).resolve().parent.parent.parent / ".githooks" / "pre-commit"
WORD = "forbiddenword"
REGEX = r"(forbiddenword|\bsecret-[0-9]+)"


class BannedTermsBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.git("config", "commit.gpgsign", "false")

    def env(self, **extra: str) -> dict:
        base = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.home),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
        }
        base.update(extra)
        return base

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.repo, env=self.env(), check=True,
            capture_output=True, text=True,
        ).stdout.strip()

    def write(self, name: str, text: str) -> None:
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit_all(self, msg: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD")

    def run_check(self, *args: str, regex: str | None = REGEX, **extra: str):
        env = self.env(**extra)
        if regex is not None:
            env["BANNED_TERMS_REGEX"] = regex
        return subprocess.run(
            ["bash", str(SCRIPT), *args], cwd=self.repo, env=env,
            capture_output=True, text=True,
        )

    def assert_no_leak(self, proc: subprocess.CompletedProcess) -> None:
        combined = (proc.stdout + proc.stderr).lower()
        self.assertNotIn(WORD, combined)
        self.assertNotIn("secret-", combined)
        self.assertNotIn("bsecret", combined)


class TestAllMode(BannedTermsBase):
    def test_hit_exits_1_prints_only_path_line(self) -> None:
        self.write("docs/a.md", f"ok\nline with {WORD.upper()} inside\n")
        self.commit_all("init")
        proc = self.run_check("--all")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout.strip(), "docs/a.md:2")
        self.assert_no_leak(proc)

    def test_clean_tree_exits_0(self) -> None:
        self.write("a.md", "nothing here\n")
        self.commit_all("init")
        proc = self.run_check("--all")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    def test_binary_files_skipped(self) -> None:
        (self.repo / "bin.dat").write_bytes(b"\x00" + WORD.encode() + b"\n")
        self.commit_all("init")
        self.assertEqual(self.run_check("--all").returncode, 0)

    def test_regex_word_boundary_honoured(self) -> None:
        self.write("a.md", "xsecret-12\nsecret-12\n")
        self.commit_all("init")
        proc = self.run_check("--all")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout.strip(), "a.md:2")


class TestNoBlacklist(BannedTermsBase):
    def test_no_regex_no_file_exits_0_with_notice(self) -> None:
        self.write("a.md", f"{WORD}\n")
        self.commit_all("init")
        proc = self.run_check("--all", regex=None)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(len(proc.stdout.strip().splitlines()), 1)
        self.assertNotIn(WORD, proc.stdout)

    def test_banned_terms_file_is_used(self) -> None:
        self.write("a.md", f"{WORD}\n")
        self.commit_all("init")
        terms = self.root / "terms.regex"
        terms.write_text(REGEX + "\n", encoding="utf-8")
        proc = self.run_check("--all", regex=None, BANNED_TERMS_FILE=str(terms))
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout.strip(), "a.md:1")

    def test_default_home_file_is_used(self) -> None:
        self.write("a.md", f"{WORD}\n")
        self.commit_all("init")
        cfg = self.home / ".config" / "agent-skills"
        cfg.mkdir(parents=True)
        (cfg / "banned-terms.regex").write_text(REGEX, encoding="utf-8")
        self.assertEqual(self.run_check("--all", regex=None).returncode, 1)


class TestStagedMode(BannedTermsBase):
    def test_only_added_lines_are_scanned(self) -> None:
        # 既有檔案已含黑名單字樣（歷史遺留），這次只改無關行 -> 不該擋
        self.write("a.md", f"old {WORD}\nkeep\n")
        self.commit_all("init")
        self.write("a.md", f"old {WORD}\nkeep\nbrand new clean line\n")
        self.git("add", "a.md")
        self.assertEqual(self.run_check("--staged").returncode, 0)

    def test_added_hit_reports_new_file_line_number(self) -> None:
        self.write("a.md", "l1\nl2\n")
        self.commit_all("init")
        self.write("a.md", f"l1\nl2\nl3 {WORD}\n")
        self.git("add", "a.md")
        proc = self.run_check("--staged")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout.strip(), "a.md:3")
        self.assert_no_leak(proc)

    def test_removed_lines_are_not_flagged(self) -> None:
        self.write("a.md", f"{WORD}\nkeep\n")
        self.commit_all("init")
        self.write("a.md", "keep\n")
        self.git("add", "a.md")
        self.assertEqual(self.run_check("--staged").returncode, 0)

    def test_unstaged_change_is_ignored(self) -> None:
        self.write("a.md", "clean\n")
        self.commit_all("init")
        self.write("a.md", f"clean\n{WORD}\n")  # 沒 git add
        self.assertEqual(self.run_check("--staged").returncode, 0)

    def test_new_file_hit(self) -> None:
        self.write("seed.md", "x\n")
        self.commit_all("init")
        self.write("dir/new.md", f"a\nb\n{WORD}\n")
        self.git("add", "dir/new.md")
        proc = self.run_check("--staged")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout.strip(), "dir/new.md:3")


class TestRangeMode(BannedTermsBase):
    def test_diff_hit_in_range(self) -> None:
        self.write("a.md", "clean\n")
        base = self.commit_all("init")
        self.write("a.md", f"clean\n{WORD}\n")
        head = self.commit_all("feat: add")
        proc = self.run_check("--range", f"{base}..{head}")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout.strip().splitlines()[0], "a.md:2")
        self.assert_no_leak(proc)

    def test_commit_message_hit_prints_sha_only(self) -> None:
        self.write("a.md", "clean\n")
        base = self.commit_all("init")
        self.write("a.md", "clean\nmore clean\n")
        head = self.commit_all(f"feat: mention {WORD} here")
        proc = self.run_check("--range", f"{base}..{head}")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout.strip(), f"commit {head[:12]}")
        self.assert_no_leak(proc)

    def test_clean_range_exits_0(self) -> None:
        self.write("a.md", f"legacy {WORD}\n")  # base 內已有，不在範圍內
        base = self.commit_all("init")
        self.write("b.md", "clean\n")
        head = self.commit_all("feat: clean")
        self.assertEqual(self.run_check("--range", f"{base}..{head}").returncode, 0)

    def test_three_dot_range_supported(self) -> None:
        self.write("a.md", "clean\n")
        base = self.commit_all("init")
        self.write("a.md", f"clean\n{WORD}\n")
        head = self.commit_all("feat: add")
        proc = self.run_check("--range", f"{base}...{head}")
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(re.fullmatch(r"a\.md:2", proc.stdout.strip().splitlines()[0]))


class TestUsage(BannedTermsBase):
    def test_bad_args_exit_2(self) -> None:
        self.assertEqual(self.run_check().returncode, 2)
        self.assertEqual(self.run_check("--bogus").returncode, 2)
        self.assertEqual(self.run_check("--range").returncode, 2)


if __name__ == "__main__":
    unittest.main()


class TestPreCommitHookChain(BannedTermsBase):
    """.githooks/pre-commit 先跑全域 core.hooksPath 的 pre-commit，再跑字樣守衛。"""

    def setUp(self) -> None:
        super().setUp()
        (self.repo / "scripts").mkdir()
        (self.repo / ".githooks").mkdir()
        shutil.copy(SCRIPT, self.repo / "scripts" / "check-banned-terms.sh")
        shutil.copy(HOOK, self.repo / ".githooks" / "pre-commit")
        (self.repo / ".githooks" / "pre-commit").chmod(0o755)
        self.marker = self.root / "global-hook-ran"
        self.gitconfig = self.root / "gitconfig"

    def make_global_hook(self, exit_code: int, dirname: str = "global-hooks") -> Path:
        hooks = self.root / dirname
        hooks.mkdir(parents=True, exist_ok=True)
        hook = hooks / "pre-commit"
        hook.write_text(
            f'#!/bin/sh\necho "$@" > "{self.marker}"\nexit {exit_code}\n',
            encoding="utf-8",
        )
        hook.chmod(0o755)
        return hooks

    def set_global_hooks_path(self, value: str) -> None:
        self.gitconfig.write_text(f"[core]\n\thooksPath = {value}\n", encoding="utf-8")

    def run_hook(self, staged_text: str, *args: str):
        self.write("doc.md", staged_text)
        self.git("add", "doc.md")
        env = self.env(BANNED_TERMS_REGEX=REGEX, GIT_CONFIG_GLOBAL=str(self.gitconfig))
        return subprocess.run(
            [str(self.repo / ".githooks" / "pre-commit"), *args], cwd=self.repo,
            env=env, capture_output=True, text=True, timeout=30,
        )

    def test_global_hook_runs_then_banned_terms_still_checked(self) -> None:
        self.set_global_hooks_path(str(self.make_global_hook(0)))
        proc = self.run_hook(f"line with {WORD}\n", "arg1")
        self.assertTrue(self.marker.exists())
        self.assertEqual(self.marker.read_text().strip(), "arg1")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout.strip(), "doc.md:1")
        self.assert_no_leak(proc)

    def test_global_hook_ok_and_clean_staged_exits_0(self) -> None:
        self.set_global_hooks_path(str(self.make_global_hook(0)))
        proc = self.run_hook("nothing here\n")
        self.assertTrue(self.marker.exists())
        self.assertEqual(proc.returncode, 0)

    def test_global_hook_failure_exit_code_propagates_and_skips_check(self) -> None:
        self.set_global_hooks_path(str(self.make_global_hook(3)))
        proc = self.run_hook(f"line with {WORD}\n")
        self.assertTrue(self.marker.exists())
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(proc.stdout.strip(), "")

    def test_no_global_hooks_path_only_runs_banned_terms(self) -> None:
        self.gitconfig.write_text("", encoding="utf-8")
        proc = self.run_hook(f"line with {WORD}\n")
        self.assertFalse(self.marker.exists())
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout.strip(), "doc.md:1")

    def test_tilde_in_global_hooks_path_is_expanded(self) -> None:
        self.make_global_hook(0, dirname="home/hooks")
        self.set_global_hooks_path("~/hooks")
        proc = self.run_hook("nothing here\n")
        self.assertTrue(self.marker.exists())
        self.assertEqual(proc.returncode, 0)

    def test_global_hooks_path_pointing_at_repo_githooks_does_not_recurse(self) -> None:
        self.set_global_hooks_path(str(self.repo / ".githooks"))
        proc = self.run_hook("nothing here\n")
        self.assertEqual(proc.returncode, 0)

    def test_non_executable_global_hook_is_skipped(self) -> None:
        hooks = self.make_global_hook(3)
        (hooks / "pre-commit").chmod(0o644)
        self.set_global_hooks_path(str(hooks))
        proc = self.run_hook("nothing here\n")
        self.assertFalse(self.marker.exists())
        self.assertEqual(proc.returncode, 0)
