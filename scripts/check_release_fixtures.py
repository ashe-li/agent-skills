#!/usr/bin/env python3
"""Mechanical gate for skill golden sets (``<skill>/fixtures/``).

判讀型 golden set 本身不可機械執行——「這份 PR body 的取捨對不對」需要人或
agent 判讀。**本 script 不假裝做得到那件事。** 它守的是外圍那圈可機械化的
完整性條件，也就是實際會腐爛的地方：

1. coverage manifest 存在且可解析
2. manifest 列到的 fixture 檔真的存在（防「表上寫了但檔沒建」）
3. fixtures/ 裡沒有孤兒檔（防「建了 fixture 但忘了掛 manifest」）
4. 每份 fixture 的 frontmatter 齊全，且 fixture_id 與檔名前綴一致
5. 至少有一份 negative-control（防擋門退化成「一律修剪」）
6. SKILL.md 新增 ``## Step`` 標題時，同一個 PR 必須也動到 fixtures/

第 6 條是本 script 存在的主要理由：KB 記過兩次同構失效——
``ci-green-doesnt-mean-your-new-test-ran-check-collected-count``（CI 綠但跑的
是別的）與 ``an-existing-guard-that-always-passes-may-just-not-cover-you``
（守衛 glob 差一格，每次都通過因為沒守到你）。

Usage:
    python scripts/check_release_fixtures.py --repo-dir .
    python scripts/check_release_fixtures.py --repo-dir . --base-ref origin/main
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REQUIRED_FRONTMATTER = ("fixture_id", "kind", "expected_verdict")
VALID_KINDS = frozenset({"regression", "negative-control"})
FIXTURE_RE = re.compile(r"^(\d{2})-.+\.md$")
# manifest 與說明性檔案，不算 fixture
NON_FIXTURE = frozenset({"README.md", "coverage.md", "acceptance_results.md"})


def parse_frontmatter(text: str) -> dict:
    """Parse YAML frontmatter; return {} when absent or malformed."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    try:
        return yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError:
        return {}


def parse_coverage_ids(manifest: Path) -> tuple[set[str], list[str]]:
    """Return (fixture ids referenced in the manifest table, errors)."""
    errors: list[str] = []
    rows = [
        line for line in manifest.read_text(encoding="utf-8").splitlines()
        if line.lstrip().startswith("|")
    ]
    if not rows:
        errors.append(f"{manifest}: 找不到任何 markdown pipe table")
        return set(), errors

    ids: set[str] = set()
    for line in rows:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or set(cells[1]) <= set("-: "):
            continue  # 表頭分隔線
        ids.update(re.findall(r"\d{2}", cells[1]))
    if not ids:
        errors.append(f"{manifest}: 表格第 2 欄沒有任何 fixture 編號")
    return ids, errors


def check_fixture_file(path: Path) -> list[str]:
    """Validate one fixture's frontmatter. Returns error strings."""
    errors: list[str] = []
    meta = parse_frontmatter(path.read_text(encoding="utf-8"))
    if not meta:
        return [f"{path}: 缺 YAML frontmatter 或無法解析"]

    for field in REQUIRED_FRONTMATTER:
        if not meta.get(field):
            errors.append(f"{path}: frontmatter 缺 `{field}`")

    kind = meta.get("kind")
    if kind and kind not in VALID_KINDS:
        errors.append(
            f"{path}: kind=`{kind}` 不合法，只接受 {sorted(VALID_KINDS)}"
        )

    prefix = FIXTURE_RE.match(path.name)
    fid = str(meta.get("fixture_id", "")).strip()
    if prefix and fid and fid != prefix.group(1):
        errors.append(
            f"{path}: fixture_id=`{fid}` 與檔名前綴 `{prefix.group(1)}` 不一致"
        )
    return errors


def check_fixtures_dir(fixtures: Path) -> tuple[list[str], list[str]]:
    """Validate one fixtures/ directory. Returns (errors, notices)."""
    errors: list[str] = []
    manifest = fixtures / "coverage.md"
    if not manifest.exists():
        return [f"{fixtures}: 缺 coverage.md（涵蓋率 manifest）"], []

    declared, errs = parse_coverage_ids(manifest)
    errors += errs

    on_disk = {
        m.group(1): p
        for p in sorted(fixtures.glob("*.md"))
        if p.name not in NON_FIXTURE and (m := FIXTURE_RE.match(p.name))
    }

    for missing in sorted(declared - on_disk.keys()):
        errors.append(f"{manifest}: 列到 fixture `{missing}` 但檔案不存在")
    for orphan in sorted(on_disk.keys() - declared):
        errors.append(
            f"{on_disk[orphan]}: 未掛進 coverage.md —— "
            "建了 fixture 卻沒掛 manifest，涵蓋率表會謊報"
        )

    kinds: list[str] = []
    for path in on_disk.values():
        errors += check_fixture_file(path)
        meta = parse_frontmatter(path.read_text(encoding="utf-8"))
        if meta.get("kind"):
            kinds.append(meta["kind"])

    if on_disk and "negative-control" not in kinds:
        errors.append(
            f"{fixtures}: 沒有任何 negative-control fixture —— "
            "擋門會退化成單向修正，缺少防過度修剪的對照組"
        )
    return errors, [f"{fixtures}: {len(on_disk)} 個 fixture，manifest 對得上"]


def changed_files(repo: Path, base_ref: str) -> list[str]:
    """Files changed vs base_ref; empty list when git is unavailable."""
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
            cwd=repo, capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [line for line in out.stdout.splitlines() if line]


def added_step_headings(repo: Path, base_ref: str, skill_md: str) -> bool:
    """True when this diff ADDS a `## Step` heading to skill_md."""
    try:
        out = subprocess.run(
            ["git", "diff", "-U0", f"{base_ref}...HEAD", "--", skill_md],
            cwd=repo, capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return any(
        line.startswith("+") and line[1:].lstrip().startswith("## Step")
        for line in out.stdout.splitlines()
    )


def check_skill_fixture_sync(repo: Path, base_ref: str) -> list[str]:
    """新增 Step 標題卻沒動 fixtures/ → 擋下。"""
    files = changed_files(repo, base_ref)
    if not files:
        return []

    errors: list[str] = []
    for path in files:
        parts = path.split("/")
        if len(parts) != 2 or parts[1] != "SKILL.md":
            continue
        skill = parts[0]
        if not (repo / skill / "fixtures").is_dir():
            continue  # 這支 skill 還沒有 golden set，不強制
        if not added_step_headings(repo, base_ref, path):
            continue
        if any(f.startswith(f"{skill}/fixtures/") for f in files):
            continue
        errors.append(
            f"{path}: 新增了 `## Step` 擋門步驟，但同一個 PR 沒有動到 "
            f"`{skill}/fixtures/` —— 新步驟必須有對應 fixture，"
            "否則 golden set 會跟 skill 漂移"
        )
    return errors


# ---------------------------------------------------------------------------
# Self-test：證明這道守衛「真的會擋」，不是每次都通過因為沒守到
# ---------------------------------------------------------------------------

BASE_COVERAGE = (
    "| failure mode | fixture | KB | 狀態 |\n|---|---|---|---|\n"
    "| bloat | 01 | kb.md | 已涵蓋 |\n"
)
BASE_FIXTURE = (
    '---\nfixture_id: "01"\nkind: negative-control\n'
    "expected_verdict: KEEP\n---\n\nbody\n"
)


def _seed_repo(root: Path) -> None:
    """Create a minimal skill repo with a valid golden set, committed to main."""
    fixtures = root / "myskill" / "fixtures"
    fixtures.mkdir(parents=True)
    (root / "myskill" / "SKILL.md").write_text("# skill\n\n## Step 1\n\nfoo\n")
    (fixtures / "coverage.md").write_text(BASE_COVERAGE)
    (fixtures / "01-a.md").write_text(BASE_FIXTURE)
    for cmd in (
        ["git", "init", "-q", "."],
        ["git", "config", "user.email", "selftest@local"],
        ["git", "config", "user.name", "selftest"],
        ["git", "add", "-A"],
        ["git", "commit", "-qm", "base"],
        ["git", "branch", "-q", "-M", "main"],
    ):
        subprocess.run(cmd, cwd=root, check=True, capture_output=True)


def _run_gate(root: Path) -> tuple[list[str], list[str]]:
    """Run every check against root; return (errors, notices)."""
    errors: list[str] = []
    notices: list[str] = []
    for fixtures in sorted(root.glob("*/fixtures")):
        errs, notes = check_fixtures_dir(fixtures)
        errors += errs
        notices += notes
    errors += check_skill_fixture_sync(root, "main")
    return errors, notices


def _mutate(root: Path, case: str) -> None:
    """Apply one failure scenario and commit it on a fresh branch."""
    fixtures = root / "myskill" / "fixtures"
    if case == "added_step_without_fixture":
        path = root / "myskill" / "SKILL.md"
        path.write_text(path.read_text() + "\n## Step 2 擋門\n\nbar\n")
    elif case == "orphan_fixture":
        (fixtures / "02-b.md").write_text(
            BASE_FIXTURE.replace('"01"', '"02"').replace(
                "negative-control", "regression"
            )
        )
    elif case == "no_negative_control":
        (fixtures / "01-a.md").write_text(
            BASE_FIXTURE.replace("negative-control", "regression")
        )
    elif case == "manifest_points_at_missing_file":
        (fixtures / "coverage.md").write_text(
            BASE_COVERAGE + "| ghost | 03 | kb.md | 已涵蓋 |\n"
        )
    elif case == "id_prefix_mismatch":
        (fixtures / "01-a.md").write_text(BASE_FIXTURE.replace('"01"', '"99"'))
    subprocess.run(["git", "checkout", "-q", "-b", case], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-qm", case], cwd=root, check=True, capture_output=True
    )


def self_test() -> int:
    """Baseline must pass; every mutation must be caught. Returns exit code."""
    cases = [
        "added_step_without_fixture",
        "orphan_fixture",
        "no_negative_control",
        "manifest_points_at_missing_file",
        "id_prefix_mismatch",
    ]
    failures: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="golden-gate-selftest-"))
    try:
        _seed_repo(tmp)
        errors, _ = _run_gate(tmp)
        if errors:
            failures.append(f"baseline 應該乾淨卻報錯: {errors}")
        else:
            print("  PASS  baseline（合法 golden set）")

        for case in cases:
            subprocess.run(["git", "checkout", "-q", "main"], cwd=tmp, check=True)
            subprocess.run(
                ["git", "branch", "-qD", case], cwd=tmp, capture_output=True
            )
            _mutate(tmp, case)
            errors, _ = _run_gate(tmp)
            if errors:
                print(f"  PASS  {case} → 擋下（{errors[0].split(': ', 1)[-1][:40]}…）")
            else:
                failures.append(f"{case}: 守衛沒擋住，等於沒守到")
            subprocess.run(
                ["git", "checkout", "-q", "--", "."], cwd=tmp, capture_output=True
            )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    for fail in failures:
        print(f"::error::self-test {fail}")
    print(f"\nself-test：{len(cases) + 1} 個情境，{len(failures)} 個失敗")
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mechanical completeness gate for skill golden sets."
    )
    parser.add_argument("--repo-dir", default=".", help="Repository root")
    parser.add_argument(
        "--base-ref",
        default="",
        help="Base ref for the SKILL.md/fixtures sync check (e.g. origin/main)",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Prove the guard actually blocks（跑完即退出，不檢查本 repo）",
    )
    args = parser.parse_args()

    if args.self_test:
        sys.exit(self_test())

    repo = Path(args.repo_dir).resolve()
    errors: list[str] = []
    notices: list[str] = []

    dirs = sorted(repo.glob("*/fixtures"))
    if not dirs:
        print("::notice::沒有任何 */fixtures 目錄，略過")
        sys.exit(0)

    for fixtures in dirs:
        errs, notes = check_fixtures_dir(fixtures)
        errors += errs
        notices += notes

    if args.base_ref:
        errors += check_skill_fixture_sync(repo, args.base_ref)

    for note in notices:
        print(f"::notice::{note}")
    for err in errors:
        print(f"::error::{err}")

    print(
        f"\ngolden set 擋門：{len(dirs)} 個 fixtures 目錄，"
        f"{len(errors)} 個問題"
    )
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
