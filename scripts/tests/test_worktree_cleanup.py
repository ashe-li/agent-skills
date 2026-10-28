"""Integration tests for scripts/worktree-cleanup.sh 的盤點覆蓋與 detached HEAD 判定。

背景：2026-10-28 live 對帳發現舊版漏掃 19 個 worktree（detached HEAD 被 continue 掉、
ROOT 外的 /private/tmp、第三層巢狀 <repo>-wt/<name>），而且沒有任何提示。
KB: wiki/learned/worktree-cleanup-script-silently-skips-detached-head-tmp-and-nested-worktrees.md

每個 test class 在 tempfile 目錄裡建一組假 git repo + bare remote，gh 用 PATH 前置的
stub 取代（只回 fixture，不會打真 GitHub）。bare remote 放在 .../github.com/fake/<name>.git，
讓腳本的 remote URL 解析得到 nwo，走得到 PR 查詢分支。

Run: cd <worktree> && python3 -m unittest scripts.tests.test_worktree_cleanup -v
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "worktree-cleanup.sh"
# macOS 上明確用系統 bash 3.2，守住腳本的相容性宣告；Linux CI 沒有差別。
BASH = "/bin/bash" if Path("/bin/bash").exists() else "bash"

GH_STUB = """#!/bin/sh
# 假 gh：只回應 `gh pr list --repo <nwo> ...`，其他呼叫一律失敗，避免測試打到真 GitHub。
echo "$*" >> "$GH_STUB_DIR/calls.log"
[ "$1" = "pr" ] && [ "$2" = "list" ] || exit 99
repo=""
while [ $# -gt 0 ]; do [ "$1" = "--repo" ] && repo="$2"; shift; done
f="$GH_STUB_DIR/$(echo "$repo" | tr '/' '_').tsv"
[ -f "$f" ] && cat "$f"
exit 0
"""


def git(*args: str, cwd: Path | None = None, env: dict | None = None) -> str:
    out = subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


class World:
    """一組固定場景：ROOT 內外的主 repo、各種 worktree、gh fixture。"""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.root = base / "root"
        self.root.mkdir()
        self.env = self._make_env()
        self.repo = self.root / "repo"            # 主 repo 在 ROOT 內
        self.repo2 = base / "elsewhere" / "repo2"  # 主 repo 在 ROOT 外
        self._make_repo(self.repo, "repo")
        self._make_repo(self.repo2, "repo2")
        self.paths: dict[str, Path] = {}
        self._make_worktrees()

    def _make_env(self) -> dict:
        stub_dir = self.base / "stub"
        stub_dir.mkdir()
        gh = stub_dir / "gh"
        gh.write_text(GH_STUB)
        gh.chmod(0o755)
        gitconfig = self.base / "gitconfig"
        gitconfig.write_text(
            "[user]\n\tname = t\n\temail = t@example.com\n[init]\n\tdefaultBranch = main\n"
        )
        env = dict(os.environ)
        env.update(
            PATH=f"{stub_dir}{os.pathsep}{env.get('PATH', '')}",
            GH_STUB_DIR=str(stub_dir),
            GIT_CONFIG_GLOBAL=str(gitconfig),
            GIT_CONFIG_NOSYSTEM="1",
        )
        return env

    def _make_repo(self, path: Path, name: str) -> None:
        bare = self.base / "remotes" / "github.com" / "fake" / f"{name}.git"
        bare.parent.mkdir(parents=True, exist_ok=True)
        git("init", "--bare", "-q", "-b", "main", str(bare), env=self.env)
        path.parent.mkdir(parents=True, exist_ok=True)
        git("clone", "-q", str(bare), str(path), env=self.env)
        (path / "README").write_text("init\n")
        git("add", "README", cwd=path, env=self.env)
        git("commit", "-q", "-m", "init", cwd=path, env=self.env)
        git("push", "-q", "origin", "main", cwd=path, env=self.env)

    def _wt(self, key: str, repo: Path, path: Path, *add_args: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        git("worktree", "add", "-q", *add_args, str(path), "main", cwd=repo, env=self.env)
        self.paths[key] = Path(os.path.realpath(path))
        return path

    def _commit(self, wt: Path, msg: str) -> str:
        (wt / f"{msg}.txt").write_text(msg)
        git("add", ".", cwd=wt, env=self.env)
        git("commit", "-q", "-m", msg, cwd=wt, env=self.env)
        return git("rev-parse", "HEAD", cwd=wt, env=self.env)

    def _make_worktrees(self) -> None:
        r = self.root
        # (a) detached，HEAD 已在 origin/main
        self._wt("det_pushed", self.repo, r / "repo-det-pushed", "--detach")
        # (b) detached，HEAD 是本機 commit
        self._commit(self._wt("det_local", self.repo, r / "repo-det-local", "--detach"), "local")
        # (e) detached、HEAD 在 remote，但工作區 dirty
        dirty = self._wt("det_dirty", self.repo, r / "repo-det-dirty", "--detach")
        (dirty / "untracked.txt").write_text("wip")
        # (c) worktree 在 ROOT 外
        self._wt("outside", self.repo, self.base / "outside" / "wt-out", "-b", "feat/outside")
        # (d) 第三層巢狀，且主 repo 在 ROOT 外 → 只能靠 worktree 側反查找到
        self._wt("nested", self.repo2, r / "repo2-wt" / "nested", "-b", "feat/nested")
        # 有 branch、PR MERGED（squash：headRefOid == HEAD，但 rev-list > 0）
        merged = self._wt("merged", self.repo, r / "repo-merged", "-b", "feat/merged")
        merged_oid = self._commit(merged, "merged")
        git("push", "-q", "origin", "feat/merged", cwd=merged, env=self.env)
        # (f) orphan：PR 也標 MERGED 且 oid 相符，確認目錄不在時仍不會被判 remove
        orphan = self._wt("orphan", self.repo, r / "repo-orphan", "-b", "feat/orphan")
        orphan_oid = git("rev-parse", "HEAD", cwd=orphan, env=self.env)
        shutil.rmtree(orphan)
        stub = Path(self.env["GH_STUB_DIR"])
        (stub / "fake_repo.tsv").write_text(
            f"feat/merged\t11\tMERGED\t{merged_oid}\n"
            f"feat/orphan\t12\tMERGED\t{orphan_oid}\n"
            f"feat/outside\t13\tOPEN\t0000000\n"
        )
        (stub / "fake_repo2.tsv").write_text("feat/nested\t21\tOPEN\t0000000\n")

    def run(self, *flags: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [BASH, str(SCRIPT), "--root", str(self.root), *flags],
            cwd=self.base, env=self.env, capture_output=True, text=True, timeout=120,
        )

    def worktree_paths(self, repo: Path) -> list[str]:
        out = git("worktree", "list", "--porcelain", cwd=repo, env=self.env)
        return [ln[len("worktree "):] for ln in out.splitlines() if ln.startswith("worktree ")]


def row_for(output: str, path: Path) -> list[str] | None:
    for line in output.splitlines():
        tokens = line.split()
        if len(tokens) >= 3 and tokens[2] == str(path):
            return tokens
    return None


class DryRunClassificationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.world = World(Path(os.path.realpath(cls._tmp.name)))
        cls.result = cls.world.run()
        cls.out = cls.result.stdout

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def action(self, key: str) -> str:
        row = row_for(self.out, self.world.paths[key])
        self.assertIsNotNone(row, f"{key} 沒出現在輸出\n{self.out}\n{self.result.stderr}")
        return row[0]

    def test_exit_ok(self) -> None:
        self.assertEqual(self.result.returncode, 0, self.result.stderr)

    def test_a_detached_on_remote_is_remove(self) -> None:
        self.assertEqual(self.action("det_pushed"), "remove")
        row = row_for(self.out, self.world.paths["det_pushed"])
        self.assertEqual(row[1], "detached")

    def test_b_detached_not_on_remote_is_keep(self) -> None:
        self.assertEqual(self.action("det_local"), "keep")
        self.assertIn("branch -r --contains = 0", self.out)

    def test_c_worktree_outside_root_is_listed(self) -> None:
        self.assertEqual(self.action("outside"), "keep")

    def test_d_nested_third_level_is_listed(self) -> None:
        self.assertEqual(self.action("nested"), "keep")

    def test_e_detached_dirty_is_not_remove(self) -> None:
        self.assertEqual(self.action("det_dirty"), "skip-dirty")

    def test_f_orphan_is_not_remove(self) -> None:
        self.assertEqual(self.action("orphan"), "orphan")
        self.assertIn("worktree prune", self.out)
        self.assertNotIn(str(self.world.paths["orphan"]), self._removable())

    def test_merged_branch_squash_is_remove(self) -> None:
        self.assertEqual(self.action("merged"), "remove")
        self.assertIn("squash-fp", " ".join(row_for(self.out, self.world.paths["merged"])))

    def test_main_worktrees_excluded_and_total(self) -> None:
        self.assertIsNone(row_for(self.out, Path(os.path.realpath(self.world.repo))))
        self.assertIn("共 7 個（含 detached 3、orphan 1），判定可移除 2、保留 4", self.out)

    def test_gh_stub_only_pr_list(self) -> None:
        calls = (Path(self.world.env["GH_STUB_DIR"]) / "calls.log").read_text().splitlines()
        self.assertTrue(calls)
        self.assertTrue(all(c.startswith("pr list --repo fake/") for c in calls), calls)

    def _removable(self) -> list[str]:
        return [r[2] for r in (ln.split() for ln in self.out.splitlines()) if r[:1] == ["remove"]]


@unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root 讀得到 chmod 000 目錄")
class UnreadableDirTest(unittest.TestCase):
    """ROOT 下有讀不到的目錄時 find 回 1；pipefail + set -e 曾讓腳本 rc=1 且零輸出。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.world = World(Path(os.path.realpath(self._tmp.name)))
        self.locked = self.world.root / "no-perm" / "inner"
        self.locked.mkdir(parents=True)
        self.locked.chmod(0o000)

    def tearDown(self) -> None:
        self.locked.chmod(0o755)
        self._tmp.cleanup()

    def test_unreadable_dir_does_not_abort_scan(self) -> None:
        result = self.world.run()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for key in ("det_pushed", "outside", "nested", "merged"):
            self.assertIsNotNone(row_for(result.stdout, self.world.paths[key]), key)
        self.assertIn("共 7 個", result.stdout)


class ApplyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.world = World(Path(os.path.realpath(self._tmp.name)))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_apply_removes_only_remove_rows_and_keeps_branch(self) -> None:
        w = self.world
        result = w.run("--fetch", "--apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        listed = w.worktree_paths(w.repo)
        for key in ("det_pushed", "merged"):
            self.assertFalse(w.paths[key].exists(), key)
            self.assertNotIn(str(w.paths[key]), listed, key)
        for key in ("det_local", "det_dirty", "outside", "nested"):
            self.assertTrue(w.paths[key].exists(), key)
        # 硬規則 1：branch 永遠不刪
        git("rev-parse", "--verify", "refs/heads/feat/merged", cwd=w.repo, env=w.env)
        # orphan 只提示 prune，不代跑：metadata 仍在
        self.assertIn(str(w.paths["orphan"]), listed)

    def test_apply_without_fetch_refused(self) -> None:
        result = self.world.run("--apply")
        self.assertEqual(result.returncode, 1)
        self.assertTrue(self.world.paths["det_pushed"].exists())


if __name__ == "__main__":
    unittest.main()
