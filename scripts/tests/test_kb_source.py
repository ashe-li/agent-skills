"""scripts/kb_source.py 的行為測試。

守住五件事：知識來源解析順序與降級（環境變數 > 設定檔 > none，指向不存在的目錄
視同 none）、remote URL 正規化、restricted frontmatter parser、依 remotes 做的
確定性 profile 比對（零筆 / 一筆 / 多筆不猜）、CLI 在未設定時印 NO-KB 且 exit 0。
每個測試自己設定 AGENT_SKILLS_KB_HOME / XDG_CONFIG_HOME / HOME（tmpdir），
不會讀到這台機器真實的 ~/.config/agent-skills/。fixture 一律用虛構的 example-org。

Run: python3 -m unittest scripts.tests.test_kb_source -v
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import kb_source as kb  # noqa: E402

CLI = SCRIPTS_DIR / "kb_source.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
VERIFY_KB = FIXTURES / "verify_kb"
EMPTY_KB = FIXTURES / "empty_kb"
EXAMPLE_REMOTE = "https://github.com/example-org/example-web.git"
EXAMPLE_KEY = "github.com/example-org/example-web"


class TmpEnvCase(unittest.TestCase):
    """Gives each test a private HOME / XDG dir and a scrubbed environment."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.home = self.root / "home"
        self.xdg = self.root / "xdg"
        self.home.mkdir()
        self.xdg.mkdir()

    def env(self, **extra: str) -> dict:
        base = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.xdg),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
        }
        base.update(extra)
        return base

    def resolve(self, **extra: str) -> "kb.KbHome":
        with mock.patch.dict(os.environ, self.env(**extra), clear=True):
            return kb.resolve_kb_home()

    def write_kb_home_file(self, content: str, base: Path | None = None) -> None:
        cfg = (base or self.xdg) / "agent-skills"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "kb-home").write_text(content, encoding="utf-8")

    def make_dir(self, name: str) -> Path:
        path = self.root / name
        path.mkdir(parents=True)
        return path

    def make_repo(self, remote: str | None) -> Path:
        repo = self.make_dir("repo")
        self.git(repo, "init", "-q")
        if remote is not None:
            self.git(repo, "remote", "add", "origin", remote)
        return repo

    def git(self, repo: Path, *args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], env=self.env(),
                       check=True, capture_output=True)


class ResolveOrderTests(TmpEnvCase):
    def test_env_var_wins_over_file(self) -> None:
        env_dir = self.make_dir("kb-env")
        file_dir = self.make_dir("kb-file")
        self.write_kb_home_file(f"{file_dir}\n")
        got = self.resolve(AGENT_SKILLS_KB_HOME=str(env_dir))
        self.assertEqual(got.path, str(env_dir))
        self.assertEqual(got.source, kb.SOURCE_ENV)

    def test_file_used_when_env_unset(self) -> None:
        file_dir = self.make_dir("kb-file")
        self.write_kb_home_file(f"{file_dir}\n")
        got = self.resolve()
        self.assertEqual(got.path, str(file_dir))
        self.assertEqual(got.source, kb.SOURCE_FILE)

    def test_file_lives_under_home_config_when_xdg_unset(self) -> None:
        file_dir = self.make_dir("kb-file")
        self.write_kb_home_file(f"{file_dir}\n", base=self.home / ".config")
        with mock.patch.dict(os.environ, self.env(), clear=True):
            del os.environ["XDG_CONFIG_HOME"]
            got = kb.resolve_kb_home()
        self.assertEqual(got.path, str(file_dir))

    def test_empty_xdg_falls_back_to_home_config(self) -> None:
        file_dir = self.make_dir("kb-file")
        self.write_kb_home_file(f"{file_dir}\n", base=self.home / ".config")
        got = self.resolve(XDG_CONFIG_HOME="")
        self.assertEqual(got.path, str(file_dir))

    def test_only_first_line_of_file_counts_and_is_trimmed(self) -> None:
        file_dir = self.make_dir("kb-file")
        self.write_kb_home_file(f"  {file_dir}  \n/ignored/second/line\n")
        self.assertEqual(self.resolve().path, str(file_dir))

    def test_empty_env_var_counts_as_unset(self) -> None:
        file_dir = self.make_dir("kb-file")
        self.write_kb_home_file(f"{file_dir}\n")
        got = self.resolve(AGENT_SKILLS_KB_HOME="")
        self.assertEqual(got.source, kb.SOURCE_FILE)

    def test_nothing_configured_is_none(self) -> None:
        got = self.resolve()
        self.assertIsNone(got.path)
        self.assertEqual(got.source, kb.SOURCE_NONE)
        self.assertTrue(got.reason)


class ResolveDegradeTests(TmpEnvCase):
    def test_env_pointing_to_missing_dir_is_none_with_reason(self) -> None:
        got = self.resolve(AGENT_SKILLS_KB_HOME=str(self.root / "missing"))
        self.assertIsNone(got.path)
        self.assertIn("missing", got.reason)

    def test_env_pointing_to_a_file_is_none(self) -> None:
        a_file = self.root / "afile"
        a_file.write_text("x", encoding="utf-8")
        got = self.resolve(AGENT_SKILLS_KB_HOME=str(a_file))
        self.assertIsNone(got.path)
        self.assertTrue(got.reason)

    def test_invalid_env_does_not_silently_fall_back_to_file(self) -> None:
        file_dir = self.make_dir("kb-file")
        self.write_kb_home_file(f"{file_dir}\n")
        got = self.resolve(AGENT_SKILLS_KB_HOME=str(self.root / "missing"))
        self.assertIsNone(got.path)

    def test_file_pointing_to_missing_dir_is_none_with_reason(self) -> None:
        self.write_kb_home_file(f"{self.root / 'missing'}\n")
        got = self.resolve()
        self.assertIsNone(got.path)
        self.assertIn("missing", got.reason)

    def test_empty_kb_home_file_is_none(self) -> None:
        self.write_kb_home_file("\n")
        self.assertIsNone(self.resolve().path)


class NormalizeRemoteTests(unittest.TestCase):
    def test_equivalent_forms_collapse_to_one_key(self) -> None:
        forms = [
            "git@github.com:Example-Org/Example-Web.git",
            "git@github.com:example-org/example-web",
            "https://github.com/example-org/example-web.git",
            "https://github.com/example-org/example-web/",
            "https://GitHub.com/Example-Org/example-web",
            "https://user:token123@github.com/example-org/example-web.git",
            "ssh://git@github.com/example-org/example-web.git",
            "ssh://git@github.com:2222/example-org/example-web.git",
            "https://github.com:8443/example-org/example-web.git/",
            "  https://github.com/example-org/example-web.git\n",
            "github.com:example-org/example-web.git",
        ]
        for url in forms:
            with self.subTest(url=url):
                self.assertEqual(kb.normalize_remote(url), EXAMPLE_KEY)

    def test_bare_key_is_not_a_remote_url(self) -> None:
        # A bare host/owner/repo has no scheme or scp colon; it is not a remote URL.
        self.assertIsNone(kb.normalize_remote(EXAMPLE_KEY))

    def test_credentials_never_survive(self) -> None:
        got = kb.normalize_remote("https://u:secretvalue@github.com/o/r.git")
        self.assertEqual(got, "github.com/o/r")

    def test_unparseable_returns_none_without_raising(self) -> None:
        bad = ["", "   ", "not a url", "/tmp/local/path", "https://",
               "git@host:", "https://host", "https://[bad/o/r", "ssh://"]
        for url in bad:
            with self.subTest(url=url):
                self.assertIsNone(kb.normalize_remote(url))

    def test_non_string_returns_none(self) -> None:
        self.assertIsNone(kb.normalize_remote(None))  # type: ignore[arg-type]


class FrontmatterTests(unittest.TestCase):
    def parse(self, text: str):
        return kb.parse_frontmatter(text)

    def test_scalars_and_quotes(self) -> None:
        got = self.parse('---\nschema: verify-profile/1\nname: "web"\n'
                         "other: 'x y'\n---\nbody\n")
        self.assertEqual(got.data["schema"], "verify-profile/1")
        self.assertEqual(got.data["name"], "web")
        self.assertEqual(got.data["other"], "x y")
        self.assertEqual(got.reason, "")

    def test_block_list(self) -> None:
        got = self.parse("---\nremotes:\n  - a/b/c\n  - \"d/e/f\"\nname: n\n---\n")
        self.assertEqual(tuple(got.data["remotes"]), ("a/b/c", "d/e/f"))
        self.assertEqual(got.data["name"], "n")

    def test_inline_list(self) -> None:
        got = self.parse("---\nremotes: [a/b/c, 'd/e/f' , \"g/h/i\"]\n---\n")
        self.assertEqual(tuple(got.data["remotes"]), ("a/b/c", "d/e/f", "g/h/i"))

    def test_empty_inline_list(self) -> None:
        got = self.parse("---\nremotes: []\n---\n")
        self.assertEqual(tuple(got.data["remotes"]), ())

    def test_unknown_keys_are_kept_without_error(self) -> None:
        got = self.parse("---\nfuture_key: hello\nname: n\n---\n")
        self.assertEqual(got.data["future_key"], "hello")
        self.assertEqual(got.reason, "")

    def test_nested_structures_are_ignored(self) -> None:
        text = "---\nname: n\nnested:\n  child: x\n  other: y\nafter: ok\n---\n"
        got = self.parse(text)
        self.assertNotIn("nested", got.data)
        self.assertNotIn("child", got.data)
        self.assertEqual(got.data["after"], "ok")
        self.assertEqual(got.data["name"], "n")

    def test_comments_and_blank_lines_are_skipped(self) -> None:
        got = self.parse("---\n# a comment\n\nname: n\n---\n")
        self.assertEqual(dict(got.data), {"name": "n"})

    def test_crlf_line_endings(self) -> None:
        got = self.parse("---\r\nname: n\r\nremotes:\r\n  - a/b/c\r\n---\r\n")
        self.assertEqual(got.data["name"], "n")
        self.assertEqual(tuple(got.data["remotes"]), ("a/b/c",))

    def test_no_frontmatter_gives_empty_dict_and_reason(self) -> None:
        got = self.parse("# just a heading\n")
        self.assertEqual(dict(got.data), {})
        self.assertTrue(got.reason)

    def test_unclosed_frontmatter_gives_empty_dict_and_reason(self) -> None:
        got = self.parse("---\nname: n\nno closing fence\n")
        self.assertEqual(dict(got.data), {})
        self.assertTrue(got.reason)

    def test_malformed_line_gives_empty_dict_and_reason(self) -> None:
        got = self.parse("---\nname: n\nthis line has no colon\n---\n")
        self.assertEqual(dict(got.data), {})
        self.assertTrue(got.reason)

    def test_empty_and_non_string_input_do_not_raise(self) -> None:
        for text in ("", "---", None):
            with self.subTest(text=text):
                got = self.parse(text)  # type: ignore[arg-type]
                self.assertEqual(dict(got.data), {})
                self.assertTrue(got.reason)


class MatchProfileTests(TmpEnvCase):
    def make_kb(self, profiles: dict[str, str]) -> Path:
        kb_dir = self.make_dir("kb")
        vdir = kb_dir / "verification"
        vdir.mkdir()
        for name, text in profiles.items():
            (vdir / name).write_text(text, encoding="utf-8")
        return kb_dir

    @staticmethod
    def profile(*remotes: str, name: str = "p") -> str:
        items = "".join(f"  - {r}\n" for r in remotes)
        return f"---\nschema: verify-profile/1\nname: {name}\nremotes:\n{items}---\nbody\n"

    def test_exactly_one_match(self) -> None:
        kb_dir = self.make_kb({"web.md": self.profile(EXAMPLE_KEY),
                               "other.md": self.profile("github.com/example-org/other")})
        got = kb.match_profile(str(kb_dir), EXAMPLE_KEY)
        self.assertEqual(got.status, kb.MATCHED)
        self.assertEqual(got.profiles, ("web.md",))

    def test_remote_values_are_normalized_before_comparing(self) -> None:
        kb_dir = self.make_kb({"web.md": self.profile(
            "git@GitHub.com:Example-Org/Example-Web.git")})
        got = kb.match_profile(str(kb_dir), EXAMPLE_KEY)
        self.assertEqual(got.status, kb.MATCHED)

    def test_zero_match_is_no_profile(self) -> None:
        kb_dir = self.make_kb({"web.md": self.profile("github.com/example-org/other")})
        got = kb.match_profile(str(kb_dir), EXAMPLE_KEY)
        self.assertEqual(got.status, kb.NO_PROFILE)
        self.assertEqual(got.profiles, ())

    def test_multiple_matches_are_ambiguous_and_listed(self) -> None:
        kb_dir = self.make_kb({"b.md": self.profile(EXAMPLE_KEY),
                               "a.md": self.profile(EXAMPLE_KEY, "x/y/z")})
        got = kb.match_profile(str(kb_dir), EXAMPLE_KEY)
        self.assertEqual(got.status, kb.AMBIGUOUS)
        self.assertEqual(got.profiles, ("a.md", "b.md"))

    def test_underscore_files_and_readme_are_skipped(self) -> None:
        kb_dir = self.make_kb({"_common.md": self.profile(EXAMPLE_KEY),
                               "README.md": self.profile(EXAMPLE_KEY),
                               "web.md": self.profile(EXAMPLE_KEY)})
        got = kb.match_profile(str(kb_dir), EXAMPLE_KEY)
        self.assertEqual(got.profiles, ("web.md",))

    def test_underscore_candidates_dir_is_not_scanned(self) -> None:
        kb_dir = self.make_kb({"web.md": self.profile(EXAMPLE_KEY)})
        cand = kb_dir / "verification" / "_candidates"
        cand.mkdir()
        (cand / "x.md").write_text(self.profile(EXAMPLE_KEY), encoding="utf-8")
        self.assertEqual(kb.match_profile(str(kb_dir), EXAMPLE_KEY).status, kb.MATCHED)

    def test_profile_without_usable_remotes_is_ignored(self) -> None:
        kb_dir = self.make_kb({"broken.md": "no frontmatter at all\n",
                               "scalar.md": f"---\nremotes: {EXAMPLE_KEY}\n---\n"})
        self.assertEqual(kb.match_profile(str(kb_dir), EXAMPLE_KEY).status,
                         kb.NO_PROFILE)

    def test_missing_verification_dir_is_no_profile(self) -> None:
        kb_dir = self.make_dir("kb")
        self.assertEqual(kb.match_profile(str(kb_dir), EXAMPLE_KEY).status,
                         kb.NO_PROFILE)

    def test_unknown_remote_is_no_profile_with_reason(self) -> None:
        kb_dir = self.make_kb({"web.md": self.profile(EXAMPLE_KEY)})
        got = kb.match_profile(str(kb_dir), None)
        self.assertEqual(got.status, kb.NO_PROFILE)
        self.assertTrue(got.reason)

    def test_symlink_pointing_outside_kb_is_ignored(self) -> None:
        kb_dir = self.make_kb({})
        outside = self.root / "outside.md"
        outside.write_text(self.profile(EXAMPLE_KEY), encoding="utf-8")
        (kb_dir / "verification" / "link.md").symlink_to(outside)
        self.assertEqual(kb.match_profile(str(kb_dir), EXAMPLE_KEY).status,
                         kb.NO_PROFILE)

    def test_fixture_kbs(self) -> None:
        got = kb.match_profile(str(VERIFY_KB), EXAMPLE_KEY)
        self.assertEqual(got.status, kb.MATCHED)
        self.assertEqual(got.profiles, ("example-web.md",))
        self.assertEqual(kb.match_profile(str(EMPTY_KB), EXAMPLE_KEY).status,
                         kb.NO_PROFILE)


class RepoRemoteTests(TmpEnvCase):
    def test_reads_origin_url(self) -> None:
        repo = self.make_repo(EXAMPLE_REMOTE)
        with mock.patch.dict(os.environ, self.env(), clear=True):
            self.assertEqual(kb.get_repo_remote(str(repo)), EXAMPLE_REMOTE)

    def test_no_origin_returns_none(self) -> None:
        repo = self.make_repo(None)
        with mock.patch.dict(os.environ, self.env(), clear=True):
            self.assertIsNone(kb.get_repo_remote(str(repo)))

    def test_missing_path_returns_none(self) -> None:
        with mock.patch.dict(os.environ, self.env(), clear=True):
            self.assertIsNone(kb.get_repo_remote(str(self.root / "nope")))

    def test_git_not_installed_degrades_to_none(self) -> None:
        repo = self.make_repo(EXAMPLE_REMOTE)
        with mock.patch.dict(os.environ, self.env(PATH=str(self.root)), clear=True):
            self.assertIsNone(kb.get_repo_remote(str(repo)))


class CliTests(TmpEnvCase):
    def run_cli(self, *args: str, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(CLI), *args], env=self.env(**extra),
                              capture_output=True, text=True, cwd=str(self.root))

    def test_resolve_unset_prints_no_kb_and_exits_zero(self) -> None:
        proc = self.run_cli("resolve")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("NO-KB", proc.stdout)

    def test_resolve_prints_path(self) -> None:
        proc = self.run_cli("resolve", AGENT_SKILLS_KB_HOME=str(VERIFY_KB))
        self.assertEqual(proc.returncode, 0)
        self.assertIn(str(VERIFY_KB), proc.stdout)
        self.assertNotIn("NO-KB", proc.stdout)

    def test_resolve_bad_dir_prints_no_kb_with_reason(self) -> None:
        proc = self.run_cli("resolve", AGENT_SKILLS_KB_HOME=str(self.root / "gone"))
        self.assertEqual(proc.returncode, 0)
        self.assertIn("NO-KB", proc.stdout)
        self.assertIn("gone", proc.stdout)

    def test_match_unset_prints_no_kb(self) -> None:
        repo = self.make_repo(EXAMPLE_REMOTE)
        proc = self.run_cli("match", "--repo", str(repo))
        self.assertEqual(proc.returncode, 0)
        self.assertIn("NO-KB", proc.stdout)

    def test_match_found(self) -> None:
        repo = self.make_repo("git@github.com:example-org/example-web.git")
        proc = self.run_cli("match", "--repo", str(repo),
                            AGENT_SKILLS_KB_HOME=str(VERIFY_KB))
        self.assertEqual(proc.returncode, 0)
        self.assertIn("MATCH", proc.stdout)
        self.assertIn("example-web.md", proc.stdout)

    def test_match_no_profile(self) -> None:
        repo = self.make_repo("https://github.com/example-org/unrelated.git")
        proc = self.run_cli("match", "--repo", str(repo),
                            AGENT_SKILLS_KB_HOME=str(VERIFY_KB))
        self.assertEqual(proc.returncode, 0)
        self.assertIn("NO-PROFILE", proc.stdout)

    def test_match_ambiguous_lists_files(self) -> None:
        kb_dir = self.make_dir("kb")
        (kb_dir / "verification").mkdir()
        for name in ("a.md", "b.md"):
            (kb_dir / "verification" / name).write_text(
                f"---\nremotes:\n  - {EXAMPLE_KEY}\n---\n", encoding="utf-8")
        repo = self.make_repo(EXAMPLE_REMOTE)
        proc = self.run_cli("match", "--repo", str(repo),
                            AGENT_SKILLS_KB_HOME=str(kb_dir))
        self.assertEqual(proc.returncode, 0)
        self.assertIn("AMBIGUOUS", proc.stdout)
        self.assertIn("a.md", proc.stdout)
        self.assertIn("b.md", proc.stdout)

    def test_match_repo_without_origin_degrades(self) -> None:
        repo = self.make_repo(None)
        proc = self.run_cli("match", "--repo", str(repo),
                            AGENT_SKILLS_KB_HOME=str(VERIFY_KB))
        self.assertEqual(proc.returncode, 0)
        self.assertIn("NO-PROFILE", proc.stdout)

    def test_no_subcommand_is_usage_error(self) -> None:
        self.assertNotEqual(self.run_cli().returncode, 0)


if __name__ == "__main__":
    unittest.main()
