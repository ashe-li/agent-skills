"""receipt、CI 證據核對、PR 註冊表的行為規格。

本測試假設的 API
----------------
可 import 的模組 `scripts/verify_receipts.py`（引擎與 gate hook 共用的唯一實作）：

- `ENGINE_VERSION: str`：引擎版本字串，寫進快檢 receipt
- `Validity(valid: bool, reason: str, receipt: dict | None)`（NamedTuple）
  有效時 reason 為空字串；免檢判定（`check_exemption`）有效時 reason 為
  `exempt-closed` 或 `exempt-none`
- `HeadResolution(sha: str | None, source: str, reason: str)`（NamedTuple），
  source 為 `local-ref`、`last-pushed`、`registration`、`unresolved`；
  unresolved 時 reason 為 `head-unresolved`
- `check_quick_receipt(repo, *, kb_home=None) -> Validity`
  reason：`no-receipt`、`dirty-now`、`stale-config`、`stale-base`、`engine-changed`、
  `required-incomplete`
- `check_ci_receipt(repo, branch, *, kb_home=None, state_dir=None) -> Validity`
  reason：`no-receipt`、`stale-ci-config`、`head-unresolved`；有效時 receipt["conclusion"]
  為 green、red、pending-declared、exempt 之一（red 是否放行由呼叫端決定）
- `check_acceptance_receipt(repo, branch, *, kb_home=None, state_dir=None) -> Validity`
  reason：`no-receipt`、`ui-changed`、`head-unresolved`
- `check_exemption(repo, branch, *, state_dir=None) -> Validity`
  無效時 reason：`registration-stale`、`not-exempt`、`pr-unregistered`、`head-unresolved`
- `resolve_head(repo, branch, *, state_dir=None) -> HeadResolution`
- `read_registry_entry(repo, branch, *, state_dir=None) -> dict | None`
- `record_push(repo, branch, *, state_dir=None, head_sha=None, pushed_at=None) -> dict`
  （head_sha 省略時取該 branch 本機 ref；pushed_at 為 ISO 8601 UTC 字串，省略時取現在）
- `mark_push_unresolved(repo, *, state_dir=None, at=None) -> None`

`repo` 一律是 repo 路徑（str），模組自己讀 `origin` 並正規化。`kb_home=None` 時依
`scripts/kb_source.py` 解析；`state_dir=None` 時讀 `AGENT_SKILLS_VERIFY_STATE_DIR`，
再沒有才用 `~/.claude/verify-gate/`。

引擎 CLI（`scripts/verify_engine.py`，共用旗標見 test_verify_engine.py）：

- `pr register (--from-gh [--pr N] | --evidence FILE | --none [--branch B])`
  → `{"result": "registered", "entry": {...註冊表條目}}`
- `receipt ci (--from-gh [--pr N] | --evidence FILE) [--pending-declared]
  [--exempt --reason TEXT]` → `{"result": "written"|"rejected"|"unverifiable",
  "conclusion": str|null, "reason": str, "receipt": 路徑|null}`；
  rejected 與 red 的 exit 為 1，unverifiable 與其他 written 為 0，缺來源或
  `--exempt` 缺 `--reason` 為 2
- `receipt acceptance --pr N --attachments K` → 同上形狀

`--from-gh` 以 argv 呼叫 `gh <group> <verb> ...`（見 fixtures/gh_samples/README.md）；
本檔以 PATH 前置的假 `gh` 取代，不連網路。

Run: python3 -m unittest scripts.tests.test_verify_receipts -v
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from typing import Optional

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent
for _p in (TESTS_DIR, SCRIPTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import test_verify_engine as eng  # noqa: E402

try:
    import verify_receipts as vr  # noqa: E402
except ImportError:  # 紅燈階段：模組尚未實作
    vr = None

FEATURE = eng.FEATURE
EXAMPLE_KEY = eng.EXAMPLE_KEY
READ_ONLY_GH = {("repo", "view"), ("pr", "view"), ("run", "list"), ("run", "view")}
SHA_RE = r"^[0-9a-f]{64}$"
OLD_PUSH_TIME = "2000-01-01T00:00:00Z"

FAKE_GH = """#!{python}
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\\n")
data = json.load(open(os.environ["FAKE_GH_DATA"], encoding="utf-8"))
key = " ".join(args[:2])
out = data["run view"].get(args[2]) if key == "run view" else data.get(key)
if out is None:
    sys.stderr.write("fake gh: unsupported " + key + "\\n")
    sys.exit(1)
print(json.dumps(out))
"""


def gh_responses(evidence: dict) -> dict:
    """Translate a verify-ci-evidence/1 document into fake `gh` answers."""
    owner_repo = evidence["repo"].split("/", 1)[1]
    runs = evidence["runs"]
    return {
        "repo view": {"nameWithOwner": owner_repo, "url": f"https://{evidence['repo']}"},
        "pr view": {"number": evidence["pr"], "state": evidence["pr_state"].upper(),
                    "headRefName": evidence["pr_head_branch"],
                    "headRefOid": evidence["pr_head_sha"],
                    "url": f"https://{evidence['repo']}/pull/{evidence['pr']}"},
        "run list": [{"databaseId": r["run_id"], "attempt": r["attempt"],
                      "headSha": r["head_sha"], "workflowName": r["workflow"],
                      "status": r["status"], "conclusion": r["conclusion"]} for r in runs],
        "run view": {str(r["run_id"]): {
            "databaseId": r["run_id"], "attempt": r["attempt"], "headSha": r["head_sha"],
            "workflowName": r["workflow"], "status": r["status"],
            "conclusion": r["conclusion"], "jobs": r["jobs"]} for r in runs},
    }


class ReceiptCase(eng.VerifyCase):
    def setUp(self) -> None:
        super().setUp()
        if vr is None:
            self.fail("scripts/verify_receipts.py 尚未實作（ImportError）")

    def write_json(self, data: dict) -> Path:
        self._counter += 1
        path = self.root / f"evidence-{self._counter}.json"
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return path

    def evidence_data(self, name: str, head: str, run_head: Optional[str] = None,
                      **overrides) -> dict:
        """Load a fixture as a template and point it at a real commit."""
        data = json.loads((eng.CI_EVIDENCE / name).read_text(encoding="utf-8"))
        data["pr_head_sha"] = head
        for run in data["runs"]:
            run["head_sha"] = run_head or head
        data.update(overrides)
        return data

    def evidence(self, name: str, head: str, run_head: Optional[str] = None,
                 **overrides) -> Path:
        return self.write_json(self.evidence_data(name, head, run_head, **overrides))

    def setup_pr(self, required_jobs: Optional[str] = "unit-tests, quality",
                 pr_state: str = "open", **profile) -> Path:
        """KB + repo with one feature commit, registered as PR 42."""
        self.use_kb(self.make_kb(eng.profile_text(required_jobs=required_jobs, **profile)))
        repo = self.make_repo()
        head = self.commit(repo, {"src/app.ts": "export const app = 2;\n"}, "feature")
        proc, _ = self.register(repo, self.evidence("green-head.json", head, pr_state=pr_state))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return repo

    def set_profile(self, **kwargs) -> None:
        kb = Path(os.environ["AGENT_SKILLS_KB_HOME"])
        (kb / "verification" / "example-web.md").write_text(
            eng.profile_text(**kwargs), encoding="utf-8")

    def register(self, repo: Path, evidence: Path):
        return self.engine_json("pr", "register", "--repo", str(repo), "--evidence", str(evidence))

    def ci(self, repo: Path, evidence: Path, *extra: str):
        return self.engine_json("receipt", "ci", "--repo", str(repo), "--evidence",
                                str(evidence), *extra)

    def ci_receipts(self, repo: Path) -> list:
        return [n for n in self.receipt_files(repo) if n.startswith("ci-")]

    def entry(self, repo: Path, branch: str = FEATURE) -> Optional[dict]:
        return vr.read_registry_entry(str(repo), branch)

    def assert_rejected(self, proc, data, reason: str) -> None:
        self.assertEqual(data["result"], "rejected", data)
        self.assertEqual(data["reason"], reason)
        self.assertIsNone(data["receipt"])
        self.assertEqual(proc.returncode, 1)

    def assert_written(self, proc, data, conclusion: str, exit_code: int = 0) -> dict:
        self.assertEqual(data["result"], "written", data)
        self.assertEqual(data["conclusion"], conclusion)
        self.assertEqual(proc.returncode, exit_code, proc.stderr)
        return json.loads(Path(data["receipt"]).read_text(encoding="utf-8"))

    def delete_branch(self, repo: Path, branch: str = FEATURE) -> None:
        self.git(repo, "checkout", "-q", "main")
        self.git(repo, "branch", "-q", "-D", branch)

    def install_fake_gh(self, evidence: dict) -> dict:
        bindir = self.root / "fakebin"
        bindir.mkdir(exist_ok=True)
        gh = bindir / "gh"
        gh.write_text(FAKE_GH.format(python=sys.executable), encoding="utf-8")
        gh.chmod(0o755)
        data_file = self.write_json(gh_responses(evidence))
        self.gh_log = self.root / "gh-calls.jsonl"
        return {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
                "FAKE_GH_DATA": str(data_file), "FAKE_GH_LOG": str(self.gh_log)}

    def assert_gh_read_only(self) -> None:
        calls = [json.loads(line) for line in self.gh_log.read_text().splitlines()]
        self.assertTrue(calls, "假 gh 沒有被呼叫")
        for args in calls:
            self.assertIn(tuple(args[:2]), READ_ONLY_GH, f"非唯讀的 gh 呼叫：{args}")


class QuickReceiptTests(ReceiptCase):
    def _verified_repo(self, **profile) -> Path:
        self.use_kb(self.make_kb(eng.profile_text(**profile)))
        repo = self.make_repo()
        self.commit(repo, {"src/app.ts": "export const app = 2;\n"}, "feature")
        return repo

    def test_quick_receipt_invalid_when_profile_changed(self) -> None:
        repo = self._verified_repo()
        self.assertEqual(vr.check_quick_receipt(str(repo)).reason, "no-receipt")
        proc, data = self.checks(repo)
        self.assertEqual((proc.returncode, data["verdict"]), (0, "PASS"))
        got = vr.check_quick_receipt(str(repo))
        self.assertEqual((got.valid, got.reason, got.receipt["verdict"]), (True, "", "PASS"))
        head_before = self.head(repo)
        self.set_profile(notes="Notes changed; product code untouched.")
        got = vr.check_quick_receipt(str(repo))
        self.assertFalse(got.valid)
        self.assertEqual(got.reason, "stale-config")
        self.assertEqual(self.head(repo), head_before)

    def test_quick_receipt_invalid_when_worktree_dirty_after_verify(self) -> None:
        repo = self._verified_repo()
        self.checks(repo)
        got = vr.check_quick_receipt(str(repo))
        self.assertTrue(got.valid, f"未追蹤的 .verification/ 不得算 dirty：{got.reason}")
        original = (repo / "src/app.ts").read_text(encoding="utf-8")
        (repo / "src/app.ts").write_text("export const app = 3;\n", encoding="utf-8")
        self.assertEqual(vr.check_quick_receipt(str(repo)).reason, "dirty-now")
        (repo / "src/app.ts").write_text(original, encoding="utf-8")
        self.assertTrue(vr.check_quick_receipt(str(repo)).valid)
        (repo / "src/new_file.ts").write_text("export {};\n", encoding="utf-8")
        self.assertEqual(vr.check_quick_receipt(str(repo)).reason, "dirty-now")

    def test_quick_receipt_invalid_when_base_changed(self) -> None:
        repo = self._verified_repo()
        self.checks(repo)
        self.assertTrue(vr.check_quick_receipt(str(repo)).valid)
        tree = self.head(repo, "HEAD^{tree}")
        self.git(repo, "checkout", "-q", "main")
        new_main = self.commit(repo, {"src/other.ts": "export const other = 1;\n"}, "main moves")
        self.git(repo, "checkout", "-q", FEATURE)
        moved = self.git(repo, "commit-tree", tree, "-p", new_main, "-m", "same tree, new base")
        self.git(repo, "reset", "-q", "--hard", moved)
        self.assertEqual(self.head(repo, "HEAD^{tree}"), tree, "前提：tree 不變，只換 base")
        got = vr.check_quick_receipt(str(repo))
        self.assertFalse(got.valid)
        self.assertEqual(got.reason, "stale-base")

    def test_quick_receipt_invalid_when_engine_version_changed(self) -> None:
        repo = self._verified_repo()
        _, data = self.checks(repo)
        path = Path(data["receipt"])
        receipt = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["engine_version"], vr.ENGINE_VERSION)
        self.assertTrue(vr.check_quick_receipt(str(repo)).valid)
        receipt["engine_version"] = "0.0.0-older"
        path.write_text(json.dumps(receipt), encoding="utf-8")
        got = vr.check_quick_receipt(str(repo))
        self.assertFalse(got.valid)
        self.assertEqual(got.reason, "engine-changed")

    def test_quick_receipt_invalid_when_required_item_not_passed(self) -> None:
        ok = ("QC-001 — ok", {"Argv": "python3 tools/ok.py", "When": "always"})
        fail = {"Argv": "python3 tools/fail.py", "When": "always"}
        with self.subTest("required check fails"):
            repo = self._verified_repo(qcs=(ok, ("QC-002 — fails", {**fail, "Required": "true"})))
            proc, data = self.checks(repo)
            self.assertEqual((proc.returncode, data["verdict"]), (1, "FAIL"))
            self.assertTrue(Path(data["receipt"]).is_file(), "FAIL 也要寫 receipt")
            self.assertEqual(vr.check_quick_receipt(str(repo)).reason, "required-incomplete")
        with self.subTest("optional check fails"):
            self.fresh_root()
            repo = self._verified_repo(qcs=(ok, ("QC-002 — fails", {**fail, "Required": "false"})))
            proc, data = self.checks(repo)
            self.assertEqual((proc.returncode, data["verdict"]), (0, "PASS"))
            self.assertTrue(vr.check_quick_receipt(str(repo)).valid)
            path = Path(data["receipt"])
            receipt = json.loads(path.read_text(encoding="utf-8"))
            receipt["checks"][1]["required"] = True
            path.write_text(json.dumps(receipt), encoding="utf-8")
            self.assertEqual(vr.check_quick_receipt(str(repo)).reason, "required-incomplete",
                             "verdict 是 PASS 但有 required 的快檢沒過，仍須判無效")

    def test_quick_receipt_invalid_when_verified_on_dirty_tree(self) -> None:
        """S3.3 補充：快檢跑的是工作樹，驗證當下不乾淨的 receipt 不能代表 HEAD 的 tree。"""
        repo = self._verified_repo()
        original = (repo / "src/app.ts").read_text(encoding="utf-8")
        (repo / "src/app.ts").write_text("export const app = 3;\n", encoding="utf-8")
        proc, data = self.checks(repo)
        self.assertEqual((proc.returncode, data["verdict"]), (0, "PASS"))
        self.assertIs(json.loads(Path(data["receipt"]).read_text(encoding="utf-8"))["dirty"], True)
        text = self.engine("checks", "--repo", str(repo))
        self.assertIn("不能當作開 PR 的憑證", text.stdout)
        (repo / "src/app.ts").write_text(original, encoding="utf-8")
        got = vr.check_quick_receipt(str(repo))
        self.assertEqual((got.valid, got.reason), (False, "dirty-at-verify"))
        self.checks(repo)
        self.assertTrue(vr.check_quick_receipt(str(repo)).valid, "乾淨時重跑即恢復有效")

    def test_quick_receipt_valid_when_required_check_skipped_by_when(self) -> None:
        """S5.1 H4：必要快檢因 When 不命中而 SKIPPED 不算未完成；其他原因的非 PASS 仍算。"""
        ok = ("QC-001 — ok", {"Argv": "python3 tools/ok.py", "When": "always"})
        migrations = ("QC-002 — migrations", {"Argv": "python3 tools/ok.py",
                                              "When": "paths: migrations/**"})
        repo = self._verified_repo(qcs=(ok, migrations))
        proc, data = self.checks(repo)
        self.assertEqual((proc.returncode, data["verdict"]), (0, "PASS"))
        path = Path(data["receipt"])
        receipt = json.loads(path.read_text(encoding="utf-8"))
        skipped = receipt["checks"][1]
        self.assertEqual((skipped["status"], skipped["required"], skipped["skipped_by"]),
                         ("SKIPPED", True, "when"))
        self.assertTrue(vr.check_quick_receipt(str(repo)).valid)
        receipt["checks"][1]["skipped_by"] = "dry-run"
        path.write_text(json.dumps(receipt), encoding="utf-8")
        self.assertEqual(vr.check_quick_receipt(str(repo)).reason, "required-incomplete")

    def test_quick_receipt_invalid_when_when_skip_used_non_default_base(self) -> None:
        """S5.1 N1：`checks --base` 用較新的 ref 讓 diff 變空時，被跳過的條件式必要快檢不算通過。"""
        ok = ("QC-001 — ok", {"Argv": "python3 tools/ok.py", "When": "always"})
        migrations = ("QC-002 — migrations", {"Argv": "python3 tools/fail.py",
                                              "When": "paths: migrations/**"})
        repo = self._verified_repo(qcs=(ok, migrations))
        self.commit(repo, {"migrations/1.sql": "alter table t add c int;\n"}, "migration")
        proc, data = self.checks(repo)
        self.assertEqual((proc.returncode, data["verdict"], data["checks"][1]["status"]),
                         (1, "FAIL", "FAIL"))
        proc, data = self.checks(repo, "--base", "HEAD")
        self.assertEqual((proc.returncode, data["verdict"]), (0, "PASS"))
        self.assertEqual((data["checks"][1]["status"], data["checks"][1]["skipped_by"]),
                         ("SKIPPED", "when"))
        self.assertTrue(any("不能當開 PR 的憑證" in n for n in data["notices"]))
        got = vr.check_quick_receipt(str(repo))
        self.assertEqual((got.valid, got.reason), (False, "required-incomplete"))

    def test_quick_receipt_when_skip_trusted_with_origin_default_base(self) -> None:
        """S5.1 R3-5：本機 main 落後 origin/main 時，--base origin/main 的 When 跳過仍採信。"""
        ok = ("QC-001 — ok", {"Argv": "python3 tools/ok.py", "When": "always"})
        migrations = ("QC-002 — migrations", {"Argv": "python3 tools/ok.py",
                                              "When": "paths: migrations/**"})
        self.use_kb(self.make_kb(eng.profile_text(qcs=(ok, migrations))))
        repo = self.make_repo()
        self.git(repo, "checkout", "-q", "-b", "upstream-main", "main")
        upstream = self.commit(repo, {"migrations/0002.sql": "alter table t add c int;\n"},
                               "main moved on the remote")
        self.git(repo, "update-ref", "refs/remotes/origin/main", upstream)
        self.git(repo, "checkout", "-q", FEATURE)
        self.git(repo, "branch", "-q", "-D", "upstream-main")
        self.git(repo, "reset", "-q", "--hard", upstream)
        self.commit(repo, {"src/app.ts": "export const app = 2;\n"}, "feature on origin/main")
        self.assertNotEqual(self.head(repo, "main"), upstream, "前提：本機 main 落後")
        cases = ((None, "PASS", ""), ("origin/main", "SKIPPED", ""),
                 ("HEAD", "SKIPPED", "required-incomplete"))
        for base, qc2, expected in cases:
            with self.subTest(base=base):
                proc, data = self.checks(repo, *(("--base", base) if base else ()))
                self.assertEqual((proc.returncode, data["verdict"]), (0, "PASS"))
                self.assertEqual(data["checks"][1]["status"], qc2)
                self.assertEqual(vr.check_quick_receipt(str(repo)).reason, expected)


class CiReceiptTests(ReceiptCase):
    def test_ci_receipt_rejects_evidence_for_old_head(self) -> None:
        repo = self.setup_pr()
        old = self.head(repo)
        new = self.commit(repo, {"src/app.ts": "export const app = 9;\n"}, "pushed later")
        with self.subTest("evidence entirely for the old head"):
            self.assert_rejected(*self.ci(repo, self.evidence("green-old-head.json", old)),
                                 "head-mismatch")
        with self.subTest("PR head is new but the runs are for the old head"):
            self.assert_rejected(*self.ci(repo, self.evidence("green-head.json", new, run_head=old)),
                                 "head-mismatch")
        with self.subTest("--from-gh reports the old head"):
            env = self.install_fake_gh(self.evidence_data("green-old-head.json", old))
            proc, data = self.engine_json("receipt", "ci", "--repo", str(repo), "--from-gh",
                                          "--pr", "42", env_extra=env)
            self.assert_rejected(proc, data, "head-mismatch")
            self.assert_gh_read_only()
        self.assertEqual(self.ci_receipts(repo), [])
        self.assertEqual(vr.check_ci_receipt(str(repo), FEATURE).reason, "no-receipt")
        proc, data = self.ci(repo, self.evidence("green-head.json", new))
        receipt = self.assert_written(proc, data, "green")
        self.assertEqual(self.ci_receipts(repo), [f"ci-pr-42-{new}.json"])
        self.assertEqual((receipt["schema"], receipt["repo"], receipt["pr"], receipt["head_sha"]),
                         ("verify-ci-receipt/1", EXAMPLE_KEY, 42, new))
        self.assertEqual(sorted(receipt["required_jobs"]), ["quality", "unit-tests"])
        self.assertRegex(receipt["evidence_sha256"], SHA_RE)
        self.assertRegex(receipt["ci_config_digest"], SHA_RE)
        self.assertTrue(receipt["evidence_path"])
        got = vr.check_ci_receipt(str(repo), FEATURE)
        self.assertEqual((got.valid, got.reason, got.receipt["conclusion"]), (True, "", "green"))

    def test_ci_receipt_rejects_when_required_job_missing(self) -> None:
        repo = self.setup_pr()
        ev = self.evidence("missing-required-job.json", self.head(repo))
        self.assert_rejected(*self.ci(repo, ev), "required-job-missing")
        self.assertEqual(self.ci_receipts(repo), [])

    def test_ci_receipt_pending_only_as_pending_declared(self) -> None:
        repo = self.setup_pr()
        ev = self.evidence("pending.json", self.head(repo))
        self.assert_rejected(*self.ci(repo, ev), "ci-pending")
        self.assertEqual(self.ci_receipts(repo), [])
        receipt = self.assert_written(*self.ci(repo, ev, "--pending-declared"), "pending-declared")
        self.assertEqual(receipt["conclusion"], "pending-declared")
        self.assertEqual(vr.check_ci_receipt(str(repo), FEATURE).receipt["conclusion"],
                         "pending-declared")

    def test_ci_receipt_rejects_repo_or_pr_mismatch(self) -> None:
        repo = self.setup_pr()
        head = self.head(repo)
        cases = {
            "other repo": {"repo": "github.com/example-org/other-repo"},
            "unregistered PR": {"pr": 999},
        }
        for label, override in cases.items():
            with self.subTest(label):
                ev = self.evidence("green-head.json", head, **override)
                self.assert_rejected(*self.ci(repo, ev), "repo-or-pr-mismatch")
        self.assertEqual(self.ci_receipts(repo), [])

    def test_ci_receipt_uses_latest_attempt_per_run(self) -> None:
        repo = self.setup_pr()
        head = self.head(repo)
        ok = {"name": "unit-tests", "status": "completed", "conclusion": "success"}
        bad = {**ok, "conclusion": "failure"}
        quality = {"name": "quality", "status": "completed", "conclusion": "success"}

        def run(attempt: int, unit: dict) -> dict:
            return {"run_id": 1000000001, "attempt": attempt, "workflow": "CI",
                    "head_sha": head, "status": "completed",
                    "conclusion": unit["conclusion"], "jobs": [unit, quality]}

        cases = (("rerun fixed it", [run(2, ok), run(1, bad)], "green", 0),
                 ("rerun broke it", [run(2, bad), run(1, ok)], "red", 1))
        for label, runs, conclusion, rc in cases:
            with self.subTest(label):
                ev = self.evidence("green-head.json", head, runs=runs)
                receipt = self.assert_written(*self.ci(repo, ev), conclusion, rc)
                attempts = [r["attempt"] for r in receipt["runs"] if r["run_id"] == 1000000001]
                self.assertEqual(attempts, [2])

    def test_ci_receipt_rejects_when_pr_closed(self) -> None:
        repo = self.setup_pr()
        for state in ("closed", "merged"):
            with self.subTest(state):
                ev = self.evidence("green-head.json", self.head(repo), pr_state=state)
                proc, data = self.ci(repo, ev)
                self.assert_rejected(proc, data, "pr-not-open")
                self.assertIn("pr register", proc.stdout + proc.stderr)
        self.assertEqual(self.ci_receipts(repo), [])

    def test_ci_receipt_unverifiable_when_evidence_has_no_runs_or_jobs(self) -> None:
        repo = self.setup_pr()
        head = self.head(repo)
        no_jobs = self.evidence_data("green-head.json", head)
        no_jobs["runs"][0]["jobs"] = []
        cases = {"no runs": self.evidence("empty-runs.json", head),
                 "run without jobs": self.write_json(no_jobs)}
        for label, ev in cases.items():
            with self.subTest(label):
                proc, data = self.ci(repo, ev)
                self.assertEqual(data["result"], "unverifiable")
                self.assertEqual(data["reason"], "evidence-empty")
                self.assertIsNone(data["conclusion"])
                self.assertIsNone(data["receipt"])
                self.assertEqual(proc.returncode, 0)
        self.assertEqual(self.ci_receipts(repo), [])

    def test_ci_receipt_unverifiable_when_required_jobs_not_declared(self) -> None:
        repo = self.setup_pr(required_jobs=None)
        ev = self.evidence("green-head.json", self.head(repo))
        proc, data = self.ci(repo, ev)
        self.assertEqual((data["result"], data["reason"]), ("unverifiable", "ci-not-configured"))
        self.assertIsNone(data["receipt"])
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(self.ci_receipts(repo), [], "必要 job 為空集合時不得寫出 green")
        proc, _ = self.ci(repo, ev, "--exempt")
        self.assertEqual(proc.returncode, 2, "--exempt 缺 --reason 屬用法錯誤")
        receipt = self.assert_written(*self.ci(repo, ev, "--exempt", "--reason", "no CI here"),
                                      "exempt")
        self.assertEqual(receipt["reason"], "no CI here")

    def test_ci_receipt_invalid_when_required_job_added_to_profile(self) -> None:
        repo = self.setup_pr(required_jobs="unit-tests")
        self.assert_written(*self.ci(repo, self.evidence("green-head.json", self.head(repo))),
                            "green")
        self.assertTrue(vr.check_ci_receipt(str(repo), FEATURE).valid)
        self.set_profile(required_jobs="unit-tests", notes="Only the notes changed.")
        self.assertTrue(vr.check_ci_receipt(str(repo), FEATURE).valid,
                        "CI 設定 digest 只涵蓋 CI 區，改其他區不應失效")
        self.set_profile(required_jobs="unit-tests, quality")
        got = vr.check_ci_receipt(str(repo), FEATURE)
        self.assertFalse(got.valid)
        self.assertEqual(got.reason, "stale-ci-config")

    def test_ci_receipt_ignores_failure_of_non_required_job(self) -> None:
        """S3.3 補充：Required-jobs 以外的 job 失敗不影響結論。"""
        repo = self.setup_pr()
        data = self.evidence_data("green-head.json", self.head(repo))
        data["runs"][0]["jobs"].append({"name": "docs-preview", "status": "completed",
                                        "conclusion": "failure"})
        data["runs"][0]["conclusion"] = "failure"
        receipt = self.assert_written(*self.ci(repo, self.write_json(data)), "green")
        self.assertEqual(sorted(receipt["required_jobs"]), ["quality", "unit-tests"])

    def test_ci_receipt_required_jobs_span_multiple_runs(self) -> None:
        """S3.3 補充：必要 job 分屬不同 workflow 的 run 時跨 run 合起來看。

        同一 run 多個 attempt 只認最大者，已由 test_ci_receipt_uses_latest_attempt_per_run
        涵蓋（含 attempt 順序顛倒的兩個情境）。
        """
        repo = self.setup_pr()
        head = self.head(repo)

        def split_runs(second_head: str) -> Path:
            data = self.evidence_data("green-head.json", head)
            first = data["runs"][0]
            unit, quality = first["jobs"]
            data["runs"] = [{**first, "jobs": [unit]},
                            {**first, "run_id": 1000000002, "workflow": "Quality",
                             "head_sha": second_head, "jobs": [quality]}]
            return self.write_json(data)

        with self.subTest("both runs on the branch head"):
            receipt = self.assert_written(*self.ci(repo, split_runs(head)), "green")
            self.assertEqual(sorted(r["run_id"] for r in receipt["runs"]),
                             [1000000001, 1000000002])
        with self.subTest("one run is for another head"):
            self.assert_rejected(*self.ci(repo, split_runs(self.head(repo, "main"))),
                                 "head-mismatch")

    def test_ci_receipt_red_takes_precedence_over_pending(self) -> None:
        """已知有必要 job 失敗時判 red，--pending-declared 不得把已知紅燈蓋成 pending。"""
        repo = self.setup_pr()
        head = self.head(repo)
        data = self.evidence_data("pending.json", head)
        data["runs"][0]["jobs"][0]["conclusion"] = "failure"
        ev = self.write_json(data)
        self.assert_written(*self.ci(repo, ev), "red", 1)
        receipt = self.assert_written(*self.ci(repo, ev, "--pending-declared"), "red", 1)
        self.assertEqual(receipt["conclusion"], "red")
        self.assertEqual(vr.check_ci_receipt(str(repo), FEATURE).receipt["conclusion"], "red")

    def test_ci_receipt_unverifiable_when_gh_fails(self) -> None:
        repo = self.setup_pr()
        env = self.install_fake_gh(self.evidence_data("green-head.json", self.head(repo)))
        env["FAKE_GH_DATA"] = str(self.write_json({"run view": {}}))
        proc, data = self.engine_json("receipt", "ci", "--repo", str(repo), "--from-gh",
                                      "--pr", "42", env_extra=env)
        self.assertEqual((data["result"], data["reason"], proc.returncode),
                         ("unverifiable", "evidence-unavailable", 0))
        self.assertIsNone(data["receipt"])
        self.assert_gh_read_only()
        self.assertEqual(self.ci_receipts(repo), [])

    def test_ci_superseded_cancelled_run_does_not_force_red(self) -> None:
        """S5.1 M5：同一 workflow 較舊 run_id 的取消／失敗 run 被較新的 run 取代。"""
        repo = self.setup_pr()
        head = self.head(repo)
        data = self.evidence_data("green-head.json", head)
        newer = data["runs"][0]
        cancelled = [{**job, "conclusion": "cancelled"} for job in newer["jobs"]]
        data["runs"] = [{**newer, "run_id": 999999999, "conclusion": "cancelled",
                         "jobs": cancelled}, newer]
        self.assert_written(*self.ci(repo, self.write_json(data)), "green")
        data["runs"] = [{**newer, "run_id": 2000000000, "conclusion": "failure",
                         "jobs": [{**newer["jobs"][0], "conclusion": "failure"},
                                  newer["jobs"][1]]}, newer]
        self.assert_written(*self.ci(repo, self.write_json(data)), "red", 1)

    def test_ci_receipt_rejects_when_registry_entry_not_open(self) -> None:
        """S5.1 L6：註冊表條目不是 open 時不寫 CI receipt（plan 決策 4）。"""
        repo = self.setup_pr(pr_state="closed")
        proc, data = self.ci(repo, self.evidence("green-head.json", self.head(repo)))
        self.assert_rejected(proc, data, "repo-or-pr-mismatch")
        self.assertIn("pr register", proc.stdout)
        self.assertEqual(self.ci_receipts(repo), [])

    def test_ci_same_named_jobs_in_one_run_must_all_succeed(self) -> None:
        """CodeRabbit R06／M6：同一 run 內同名的必要 job 每一筆都要成功，與順序無關。"""
        repo = self.setup_pr()
        head = self.head(repo)
        ok = {"name": "quality", "status": "completed", "conclusion": "success"}
        bad = {**ok, "conclusion": "failure"}
        unit = {"name": "unit-tests", "status": "completed", "conclusion": "success"}
        for label, jobs in (("success first", [unit, ok, bad]), ("failure first", [unit, bad, ok])):
            with self.subTest(label):
                data = self.evidence_data("green-head.json", head)
                data["runs"][0]["jobs"] = jobs
                self.assert_written(*self.ci(repo, self.write_json(data)), "red", 1)

    def test_ci_evidence_with_non_object_job_is_rejected(self) -> None:
        """CodeRabbit C04：`jobs: [null]` 要被當成格式錯誤，不得丟 traceback。"""
        repo = self.setup_pr()
        data = self.evidence_data("green-head.json", self.head(repo))
        data["runs"][0]["jobs"] = [None]
        proc, out = self.ci(repo, self.write_json(data))
        self.assertEqual((out["result"], out["reason"], proc.returncode),
                         ("rejected", "evidence-invalid", 1))
        self.assertNotIn("Traceback", proc.stderr)

    def test_ci_evidence_with_non_string_job_name_is_rejected(self) -> None:
        """第二批 B：job 的 name 不是字串時回 evidence-invalid，不得丟 traceback。"""
        repo = self.setup_pr()
        data = self.evidence_data("green-head.json", self.head(repo))
        data["runs"][0]["jobs"][0]["name"] = []
        proc, out = self.ci(repo, self.write_json(data))
        self.assertEqual((out["result"], out["reason"], proc.returncode),
                         ("rejected", "evidence-invalid", 1))
        self.assertNotIn("Traceback", proc.stderr)

    def test_ci_latest_attempt_wins_regardless_of_list_order(self) -> None:
        """CodeRabbit R26：較大的 attempt 排在清單後面時也要採用它。"""
        repo = self.setup_pr()
        head = self.head(repo)
        quality = {"name": "quality", "status": "completed", "conclusion": "success"}

        def run(attempt: int, conclusion: str) -> dict:
            unit = {"name": "unit-tests", "status": "completed", "conclusion": conclusion}
            return {"run_id": 1000000001, "attempt": attempt, "workflow": "CI", "head_sha": head,
                    "status": "completed", "conclusion": conclusion, "jobs": [unit, quality]}

        for runs, conclusion, rc in (([run(1, "failure"), run(2, "success")], "green", 0),
                                     ([run(1, "success"), run(2, "failure")], "red", 1)):
            with self.subTest(conclusion):
                ev = self.evidence("green-head.json", head, runs=runs)
                receipt = self.assert_written(*self.ci(repo, ev), conclusion, rc)
                self.assertEqual([r["attempt"] for r in receipt["runs"]], [2])


class RegistryTests(ReceiptCase):
    def test_registry_exemption_invalid_after_head_change(self) -> None:
        with self.subTest("closed, then a new commit"):
            repo = self.setup_pr(pr_state="merged")
            self.assertEqual(self.entry(repo)["state"], "closed")
            got = vr.check_exemption(str(repo), FEATURE)
            self.assertEqual((got.valid, got.reason), (True, "exempt-closed"))
            self.commit(repo, {"src/app.ts": "export const app = 5;\n"}, "after close")
            self.assertEqual(vr.check_exemption(str(repo), FEATURE).reason, "registration-stale")
        with self.subTest("--none, then a new commit"):
            self.fresh_root()
            self.use_kb(self.make_kb(eng.profile_text()))
            repo = self.make_repo()
            self.commit(repo, {"src/app.ts": "export const app = 2;\n"}, "feature")
            proc, data = self.engine_json("pr", "register", "--repo", str(repo), "--none")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            entry = self.entry(repo)
            self.assertEqual((entry["pr"], entry["state"]), (None, "none"))
            self.assertEqual(vr.check_exemption(str(repo), FEATURE).reason, "exempt-none")
            self.commit(repo, {"src/app.ts": "export const app = 6;\n"}, "after none")
            got = vr.check_exemption(str(repo), FEATURE)
            self.assertEqual((got.valid, got.reason), (False, "registration-stale"))
        with self.subTest("closed, branch deleted, no new push"):
            self.fresh_root()
            repo = self.setup_pr(pr_state="closed")
            self.delete_branch(repo)
            got = vr.check_exemption(str(repo), FEATURE)
            self.assertEqual((got.valid, got.reason), (True, "exempt-closed"))

    def test_head_resolution_uses_last_pushed_head_when_ref_missing(self) -> None:
        with self.subTest("push is later than registration"):
            repo = self.setup_pr()
            registered = self.head(repo)
            pushed = self.commit(repo, {"src/app.ts": "export const app = 7;\n"}, "pushed")
            entry = vr.record_push(str(repo), FEATURE)
            self.assertEqual(entry["last_pushed_head"]["sha"], pushed)
            self.assertEqual(vr.resolve_head(str(repo), FEATURE).source, "local-ref")
            self.delete_branch(repo)
            got = vr.resolve_head(str(repo), FEATURE)
            self.assertEqual((got.sha, got.source), (pushed, "last-pushed"))
            self.assertNotEqual(got.sha, registered)
        with self.subTest("registration is later than push"):
            self.fresh_root()
            repo = self.setup_pr()
            registered = self.head(repo)
            vr.record_push(str(repo), FEATURE, head_sha=self.head(repo, "main"),
                           pushed_at=OLD_PUSH_TIME)
            self.delete_branch(repo)
            got = vr.resolve_head(str(repo), FEATURE)
            self.assertEqual((got.sha, got.source), (registered, "registration"))

    def test_head_unresolved_when_ref_missing_and_no_push_record(self) -> None:
        with self.subTest("never registered, never pushed"):
            self.use_kb(self.make_kb(eng.profile_text()))
            repo = self.make_repo()
            self.commit(repo, {"src/app.ts": "export const app = 2;\n"}, "feature")
            self.delete_branch(repo)
            got = vr.resolve_head(str(repo), FEATURE)
            self.assertEqual((got.sha, got.source, got.reason),
                             (None, "unresolved", "head-unresolved"))
        with self.subTest("push target could not be resolved after registration"):
            self.fresh_root()
            repo = self.setup_pr()
            registered = self.head(repo)
            self.assert_written(*self.ci(repo, self.evidence("green-head.json", registered)),
                                "green")
            vr.mark_push_unresolved(str(repo))
            self.delete_branch(repo)
            got = vr.resolve_head(str(repo), FEATURE)
            self.assertEqual((got.sha, got.reason), (None, "head-unresolved"))
            ci = vr.check_ci_receipt(str(repo), FEATURE)
            self.assertEqual((ci.valid, ci.reason), (False, "head-unresolved"),
                             "不得退回註冊時的 head 沿用舊 receipt")

    def test_pr_register_writes_registry_entry(self) -> None:
        self.use_kb(self.make_kb(eng.profile_text()))
        repo = self.make_repo()
        local = self.commit(repo, {"src/app.ts": "export const app = 2;\n"}, "feature")
        gh_view = self.evidence_data("green-head.json", "1" * 40)
        with self.subTest("--from-gh"):
            env = self.install_fake_gh(gh_view)
            proc, data = self.engine_json("pr", "register", "--repo", str(repo), "--from-gh",
                                          env_extra=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(data["result"], "registered")
            self.assert_gh_read_only()
        entry = self.entry(repo)
        self.assertEqual(entry["schema"], "verify-pr-registry/1")
        self.assertEqual((entry["repo"], entry["branch"], entry["pr"], entry["state"]),
                         (EXAMPLE_KEY, FEATURE, 42, "open"))
        self.assertEqual(entry["head_sha"], local, "head_sha 取本機 ref，不取 gh 回報的值")
        self.assertTrue(entry["registered_at"] and entry["updated_at"])
        self.assertEqual(entry["history"], [])
        self.assertIsNone(entry["last_pushed_head"])
        self.assertEqual(data["entry"], entry)
        self.assertTrue(any(self.state_dir.rglob("*")), "註冊表要寫在注入的狀態目錄")
        self.assertFalse((self.home / ".claude").exists())
        with self.subTest("--evidence gives the same entry"):
            proc, data = self.register(repo, self.write_json(gh_view))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            again = self.entry(repo)
            self.assertEqual((again["pr"], again["state"], again["head_sha"], again["history"]),
                             (42, "open", local, []))

    def test_pr_register_closed_pr_marks_entry_closed(self) -> None:
        for state in ("closed", "merged"):
            with self.subTest(state):
                self.fresh_root()
                repo = self.setup_pr(pr_state=state)
                entry = self.entry(repo)
                self.assertEqual((entry["pr"], entry["state"]), (42, "closed"))
                self.assertEqual(entry["head_sha"], self.head(repo))

    def test_pr_register_branch_reuse_replaces_entry_and_old_receipts_do_not_apply(self) -> None:
        repo = self.setup_pr()
        head = self.head(repo)
        self.assert_written(*self.ci(repo, self.evidence("green-head.json", head)), "green")
        self.assert_written(*self.engine_json("receipt", "acceptance", "--repo", str(repo),
                                              "--pr", "42", "--attachments", "1"), None)
        self.assertTrue(vr.check_ci_receipt(str(repo), FEATURE).valid)
        self.assertTrue(vr.check_acceptance_receipt(str(repo), FEATURE).valid)
        proc, _ = self.register(repo, self.evidence("green-head.json", head, pr=43))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        entry = self.entry(repo)
        self.assertEqual((entry["pr"], entry["state"], entry["history"]), (43, "open", [42]))
        self.assertEqual(vr.check_ci_receipt(str(repo), FEATURE).reason, "no-receipt")
        self.assertEqual(vr.check_acceptance_receipt(str(repo), FEATURE).reason, "no-receipt")
        self.assertIn(f"ci-pr-42-{head}.json", self.receipt_files(repo), "舊 receipt 保留但不適用")

    def _behind_setup(self) -> tuple:
        """PR 42 with a green CI receipt at H1 and an unpushed-locally H2.

        The commits carry an old committer date, so the branch's reflog time is
        clearly (more than a second) before a push recorded now.
        """
        os.environ["GIT_COMMITTER_DATE"] = "2001-01-01T00:00:00Z"
        repo = self.setup_pr()
        h1 = self.head(repo)
        self.assert_written(*self.ci(repo, self.evidence("green-head.json", h1)), "green")
        self.git(repo, "checkout", "-q", "--detach")
        h2 = self.commit(repo, {"src/app.ts": "export const app = 3;\n"}, "untested")
        self.git(repo, "checkout", "-q", FEATURE)
        del os.environ["GIT_COMMITTER_DATE"]
        return repo, h1, h2

    def test_resolve_head_uses_pushed_head_when_local_ref_is_behind(self) -> None:
        """S5.1 H3 (a)：推上去的內容本機 ref 沒有時，以推送的 head 判定，不沿用舊 receipt。"""
        repo, h1, h2 = self._behind_setup()
        self.assertTrue(vr.check_ci_receipt(str(repo), FEATURE).valid)
        vr.record_push(str(repo), FEATURE, head_sha=h2)
        self.assertEqual(self.head(repo, FEATURE), h1, "前提：本機 ref 仍是 H1")
        got = vr.resolve_head(str(repo), FEATURE)
        self.assertEqual((got.sha, got.source), (h2, "last-pushed"))
        self.assertEqual(vr.check_ci_receipt(str(repo), FEATURE).reason, "no-receipt")

    def test_resolve_head_keeps_local_ref_when_ahead_of_push(self) -> None:
        """S5.1 H3 (a) 對照：本機較新、尚未推送時照舊用本機 ref。"""
        repo = self.setup_pr()
        vr.record_push(str(repo), FEATURE)
        local = self.commit(repo, {"src/app.ts": "export const app = 4;\n"}, "not pushed yet")
        got = vr.resolve_head(str(repo), FEATURE)
        self.assertEqual((got.sha, got.source), (local, "local-ref"))

    def test_resolve_head_unresolved_when_pushed_object_missing(self) -> None:
        """S5.1 H3 (a)：祖先關係算不出來（物件不在本機）就判 head-unresolved。"""
        repo = self.setup_pr()
        vr.record_push(str(repo), FEATURE, head_sha="f" * 40)
        self.assertEqual(vr.resolve_head(str(repo), FEATURE).reason, "head-unresolved")

    def test_resolve_head_unresolved_when_marker_after_records_even_with_local_ref(self) -> None:
        """S5.1 H3 (b)：推送未解析的標記晚於註冊與推送紀錄時，本機 ref 在也不採用。"""
        repo = self.setup_pr()
        self.assert_written(*self.ci(repo, self.evidence("green-head.json", self.head(repo))),
                            "green")
        vr.mark_push_unresolved(str(repo))
        got = vr.resolve_head(str(repo), FEATURE)
        self.assertEqual((got.sha, got.reason), (None, "head-unresolved"))
        self.assertEqual(vr.check_ci_receipt(str(repo), FEATURE).reason, "head-unresolved")
        vr.record_push(str(repo), FEATURE)
        self.assertEqual(vr.resolve_head(str(repo), FEATURE).source, "local-ref",
                         "之後一次可解析的 push 讓它恢復可判定")
        self.assertTrue(vr.check_ci_receipt(str(repo), FEATURE).valid)

    def test_head_uses_local_ref_after_unpushed_amend(self) -> None:
        """S5.1 N3：推送後本機 amend 尚未推送，以本機 ref 判定；推送較晚則以推送的 head 判定。"""
        repo = self.setup_pr()
        pushed = self.head(repo)
        vr.record_push(str(repo), FEATURE, pushed_at="2020-01-01T00:00:00.000000Z")
        self.write_files(repo, {"src/app.ts": "export const app = 8;\n"})
        os.environ["GIT_COMMITTER_DATE"] = "2021-01-01T00:00:00Z"
        self.git(repo, "commit", "-q", "-a", "--amend", "-m", "amended, not pushed")
        del os.environ["GIT_COMMITTER_DATE"]
        amended = self.head(repo)
        got = vr.resolve_head(str(repo), FEATURE)
        self.assertEqual((got.sha, got.source), (amended, "local-ref"))
        vr.record_push(str(repo), FEATURE, head_sha=pushed, pushed_at="2022-01-01T00:00:00.000000Z")
        got = vr.resolve_head(str(repo), FEATURE)
        self.assertEqual((got.sha, got.source), (pushed, "last-pushed"),
                         "推送時間晚於本機最後一次改動時，以推送的 head 為準")

    def test_same_second_rewrite_after_push_is_unresolved(self) -> None:
        """S5.1 R3-3：reflog 只有秒級；同一秒或推送時間在未來（時鐘倒退）時判不出先後。"""
        repo = self.setup_pr()
        pushed = self.head(repo)
        self.write_files(repo, {"src/app.ts": "export const app = 9;\n"})
        os.environ["GIT_COMMITTER_DATE"] = "2021-06-01T00:00:00Z"
        self.git(repo, "commit", "-q", "-a", "--amend", "-m", "rewritten in the push second")
        del os.environ["GIT_COMMITTER_DATE"]
        cases = (("same second as the rewrite", "2021-06-01T00:00:00.500000Z", None),
                 ("push time in the future", "2999-01-01T00:00:00.000000Z", None),
                 ("push one second later", "2021-06-01T00:00:01.000000Z", "last-pushed"))
        for label, pushed_at, source in cases:
            with self.subTest(label):
                vr.record_push(str(repo), FEATURE, head_sha=pushed, pushed_at=pushed_at)
                got = vr.resolve_head(str(repo), FEATURE)
                if source is None:
                    self.assertEqual((got.sha, got.reason), (None, "head-unresolved"))
                else:
                    self.assertEqual((got.sha, got.source), (pushed, source))

    def test_branch_level_push_marker_only_affects_that_branch(self) -> None:
        """S5.1 N2：branch 層級的推送未解析標記只影響該 branch；repo 層級影響全部。"""
        repo = self.setup_pr()
        other = "feat/other"
        self.git(repo, "branch", other)
        vr.register(str(repo), other, None, vr.STATE_NONE)
        self.assertEqual(vr.registered_branches(str(repo)), tuple(sorted((FEATURE, other))))
        vr.mark_push_unresolved(str(repo), branch=other)
        self.assertEqual(vr.resolve_head(str(repo), other).reason, "head-unresolved")
        self.assertEqual(vr.resolve_head(str(repo), FEATURE).source, "local-ref")
        self.assertEqual(vr.check_exemption(str(repo), other).reason, "head-unresolved")
        vr.mark_push_unresolved(str(repo))
        self.assertEqual(vr.resolve_head(str(repo), FEATURE).reason, "head-unresolved")
        vr.record_push(str(repo), FEATURE)
        self.assertEqual(vr.resolve_head(str(repo), FEATURE).source, "local-ref")
        self.assertEqual(vr.resolve_head(str(repo), other).reason, "head-unresolved")
        vr.register(str(repo), other, None, vr.STATE_NONE)
        self.assertNotIn("push_unresolved_at", self.entry(repo, other))
        self.assertEqual(vr.resolve_head(str(repo), other).source, "local-ref")


class AcceptanceReceiptTests(ReceiptCase):
    def test_acceptance_receipt_invalid_after_ui_path_change(self) -> None:
        repo = self.setup_pr()
        accepted = self.head(repo)
        self.assertEqual(vr.check_acceptance_receipt(str(repo), FEATURE).reason, "no-receipt")
        proc, data = self.engine_json("receipt", "acceptance", "--repo", str(repo),
                                      "--pr", "999", "--attachments", "2")
        self.assert_rejected(proc, data, "repo-or-pr-mismatch")
        receipt = self.assert_written(*self.engine_json(
            "receipt", "acceptance", "--repo", str(repo), "--pr", "42", "--attachments", "2"), None)
        self.assertIn(f"acceptance-pr-42-{accepted}.json", self.receipt_files(repo))
        self.assertEqual((receipt["schema"], receipt["pr"], receipt["head_sha"], receipt["kind"],
                          receipt["attachments"]),
                         ("verify-acceptance-receipt/1", 42, accepted, "evidence-comment", 2))
        self.assertTrue(vr.check_acceptance_receipt(str(repo), FEATURE).valid)
        self.commit(repo, {"docs/notes.md": "not a UI path\n"}, "docs only")
        self.assertTrue(vr.check_acceptance_receipt(str(repo), FEATURE).valid,
                        "非 UI 路徑的變更不應讓驗收 receipt 失效")
        self.commit(repo, {"src/components/Button.tsx": "export const Button = () => 1;\n"}, "ui")
        got = vr.check_acceptance_receipt(str(repo), FEATURE)
        self.assertFalse(got.valid)
        self.assertEqual(got.reason, "ui-changed")


class SubcommandReceiptTests(ReceiptCase):
    """S3.3 補充：測試清單沒涵蓋到的子命令與狀態目錄的安全要求。"""

    def test_receipt_show_reports_each_validity(self) -> None:
        repo = self.setup_pr()
        self.checks(repo)
        self.assert_written(*self.ci(repo, self.evidence("green-head.json", self.head(repo))),
                            "green")
        proc, data = self.engine_json("receipt", "show", "--repo", str(repo))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(data["branch"], FEATURE)
        got = {k: (data[k]["valid"], data[k]["reason"])
               for k in ("quick", "ci", "acceptance", "exemption")}
        self.assertEqual(got, {"quick": (True, ""), "ci": (True, ""),
                               "acceptance": (False, "no-receipt"),
                               "exemption": (False, "not-exempt")})

    def test_receipt_record_generic_only_without_profile(self) -> None:
        repo = self.setup_pr()
        proc, data = self.engine_json("receipt", "record-generic", "--repo", str(repo),
                                      "--summary", "ran unit tests by hand")
        self.assertEqual((proc.returncode, data["result"], data["reason"]),
                         (1, "rejected", "profile-present"))
        self.use_kb(eng.EMPTY_KB)
        proc, data = self.engine_json("receipt", "record-generic", "--repo", str(repo),
                                      "--summary", "ran unit tests by hand")
        receipt = self.assert_written(proc, data, "NO-PROFILE")
        self.assertEqual((receipt["method"], receipt["verdict"], receipt["summary"]),
                         ("generic", "NO-PROFILE", "ran unit tests by hand"))
        self.assertEqual(vr.check_quick_receipt(str(repo)).reason, "required-incomplete")

    def test_receipt_exempt_writes_exempt_acceptance_receipt(self) -> None:
        repo = self.setup_pr()
        proc = self.engine("receipt", "exempt", "--repo", str(repo), "--pr", "42")
        self.assertEqual(proc.returncode, 2, "exempt 缺 --reason 屬用法錯誤")
        receipt = self.assert_written(*self.engine_json(
            "receipt", "exempt", "--repo", str(repo), "--pr", "42", "--reason", "copy only"), None)
        self.assertEqual((receipt["kind"], receipt["reason"], receipt["attachments"]),
                         ("exempt", "copy only", 0))
        self.assertTrue(vr.check_acceptance_receipt(str(repo), FEATURE).valid)

    def test_receipt_exempt_never_replaces_real_acceptance(self) -> None:
        def accept(repo: Path, *extra: str):
            return self.engine_json("receipt", *extra, "--repo", str(repo), "--pr", "42")

        with self.subTest("real acceptance first, then exempt"):
            repo = self.setup_pr()
            self.assert_written(*accept(repo, "acceptance", "--attachments", "2"), None)
            text = self.engine("receipt", "acceptance", "--repo", str(repo), "--pr", "42",
                               "--attachments", "2").stdout
            self.assertIn("written: evidence-comment", text)
            proc, data = accept(repo, "exempt", "--reason", "copy only")
            self.assert_rejected(proc, data, "acceptance-exists")
            got = vr.check_acceptance_receipt(str(repo), FEATURE)
            self.assertEqual((got.valid, got.receipt["kind"], got.receipt["attachments"]),
                             (True, "evidence-comment", 2))
        with self.subTest("exempt first, then real acceptance"):
            self.fresh_root()
            repo = self.setup_pr()
            text = self.engine("receipt", "exempt", "--repo", str(repo), "--pr", "42",
                               "--reason", "copy only").stdout
            self.assertIn("written: exempt", text)
            self.assert_written(*accept(repo, "acceptance", "--attachments", "1"), None)
            got = vr.check_acceptance_receipt(str(repo), FEATURE)
            self.assertEqual(got.receipt["kind"], "evidence-comment")

    def test_pr_show_reports_entry_and_head_source(self) -> None:
        repo = self.setup_pr(pr_state="closed")
        registered = self.head(repo)
        proc, data = self.engine_json("pr", "show", "--repo", str(repo))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual((data["entry"]["pr"], data["head"]["source"]), (42, "local-ref"))
        self.delete_branch(repo)
        _, data = self.engine_json("pr", "show", "--repo", str(repo), "--branch", FEATURE)
        self.assertEqual((data["head"]["sha"], data["head"]["source"]), (registered, "registration"))
        self.assertEqual(data["exemption"]["reason"], "exempt-closed")

    def test_state_files_are_private_and_symlinks_are_refused(self) -> None:
        repo = self.setup_pr()
        self.assertEqual(self.state_dir.stat().st_mode & 0o777, 0o700)
        files = [p for p in self.state_dir.rglob("*.json")]
        self.assertTrue(files)
        for path in files:
            self.assertEqual(path.stat().st_mode & 0o777, 0o600, path.name)
        target = self.root / "elsewhere.json"
        target.write_text("{}", encoding="utf-8")
        files[0].unlink()
        files[0].symlink_to(target)
        proc, data = self.register(repo, self.evidence("green-head.json", self.head(repo)))
        self.assertEqual((proc.returncode, data["reason"]), (1, "state-error"))
        self.assertNotIn("Traceback", proc.stderr)
        self.assertEqual(target.read_text(encoding="utf-8"), "{}")
        linked = self.root / "linked-state"
        linked.symlink_to(self._mkdir("real-state"), target_is_directory=True)
        with self.assertRaises(vr.StateError):
            vr.record_push(str(repo), FEATURE, state_dir=str(linked))
        self.assertEqual(list((self.root / "real-state").iterdir()), [])

    def test_session_link_and_push_without_local_ref(self) -> None:
        repo = self.setup_pr()
        session = vr.record_session_link("sess/../1 x", str(repo), FEATURE, pr=42)
        self.assertEqual(session["session_id"], "sess_.._1_x")
        again = vr.read_session("sess/../1 x")
        self.assertEqual([(l["repo"], l["branch"], l["pr"]) for l in again["links"]],
                         [(EXAMPLE_KEY, FEATURE, 42)])
        self.assertFalse(any(p.name.startswith("..") for p in self.state_dir.rglob("*")))
        self.delete_branch(repo)
        vr.record_push(str(repo), FEATURE)
        self.assertEqual(vr.resolve_head(str(repo), FEATURE).reason, "head-unresolved",
                         "解析不出推送的 head 時要標記為推送未解析")


class AcceptanceRenameTests(ReceiptCase):
    def test_acceptance_receipt_invalid_when_ui_file_renamed_out(self) -> None:
        """S5.1 L7：UI 檔整檔搬出 UI glob 也算 UI 變更（檔名 diff 不做 rename 偵測）。"""
        repo = self.setup_pr()
        self.assert_written(*self.engine_json("receipt", "acceptance", "--repo", str(repo),
                                              "--pr", "42", "--attachments", "1"), None)
        self.git(repo, "mv", "src/components/Button.tsx", "src/Button.tsx")
        self.git(repo, "commit", "-q", "-m", "move UI file out")
        self.assertEqual(vr.check_acceptance_receipt(str(repo), FEATURE).reason, "ui-changed")

if __name__ == "__main__":
    unittest.main()
