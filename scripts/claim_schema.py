#!/usr/bin/env python3
"""Claim-schema linter and extractor for evidence-gate (stdlib only).

New format: one second-level section per claim, `## <n>. <claim>`; the
evidence command lives in one bash fenced block under it; outside the block
two lines, `關鍵輸出：...` and `判定：PASS|FAIL|UNVERIFIABLE`, both required
(an UNVERIFIABLE claim writes `關鍵輸出：—`). An UNVERIFIABLE claim carries
no block.

`\\|` inside a fenced block is a SCHEMA-DEFECT when the same line runs an
ERE/PCRE tool (grep -E/-P, egrep, rg, awk, sed -E): there it matches a
literal pipe and silently finds nothing. Elsewhere it is only a warning
(ESCAPED-PIPE): a leftover of table escaping, or a valid BRE alternation
such as `grep 'a\\|b'`. In a legacy table cell it stays a SCHEMA-DEFECT.

`extract` wraps each claim's commands in its own `( ... )` subshell so a
`cd`, a variable or `set -e` in one claim cannot leak into the next; the
`echo "=== #n"` marker stays outside.

Neither subcommand runs anything: both are plain text processing.

Usage:
    claim_schema.py lint <file>      issues as `<file>:<line>: <CODE>: <msg>`
    claim_schema.py extract <file>   print every block as one bash script

Exit codes: lint exits 1 when there is an error, 2 on usage error, else 0
(warnings never fail). extract exits 0, or 2 on usage error.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import NamedTuple, Optional, Sequence

VERDICTS = frozenset({"PASS", "FAIL", "UNVERIFIABLE"})
UNVERIFIABLE = "UNVERIFIABLE"
BLOCK_LANGS = frozenset({"bash", "sh"})
HEADING_RE = re.compile(r"^##\s+(?P<n>\d+)\.\s+(?P<title>.+?)\s*$")
H2_RE = re.compile(r"^##(?!#)")
VERDICT_RE = re.compile(r"^判定[：:]\s*(?P<v>.*?)\s*$")
KEY_OUTPUT_RE = re.compile(r"^關鍵輸出[：:]")
FENCE_RE = re.compile(r"^(?P<ch>`{3,}|~{3,})\s*(?P<info>[^`]*)$")
LEGACY_MARK = "證據指令"
ESCAPED_PIPE = "\\|"
EC_ERROR = "error"
EC_WARNING = "warning"
EXIT_OK, EXIT_ERRORS, EXIT_USAGE = 0, 1, 2
ECHO_FMT = 'echo "=== #{n}"'
SUBSHELL_OPEN, SUBSHELL_CLOSE = "(", ")"
ERE_TOOL_RE = re.compile(
    r"\b(?:egrep|rg|awk|gawk|mawk)\b"
    r"|\bgrep\b[^|;&]*?\s(?:-[A-Za-z]*[EP][A-Za-z]*|--extended-regexp|--perl-regexp)\b"
    r"|\bsed\b[^|;&]*?\s(?:-[A-Za-z]*[Er][A-Za-z]*|--regexp-extended)\b")
ERE_PIPE_MSG = ("block 內在 ERE／PCRE 指令（grep -E／-P、egrep、rg、awk、sed -E）裡寫了 `\\|`："
                "這些語法的 `\\|` 比對字面的直線，會靜默回 0 筆；交替請寫 `|`")
ESCAPED_PIPE_MSG = ("block 內出現反斜線加直線：若是表格跳脫的殘留，請拿掉反斜線；"
                    "若是 BRE 的交替（grep 'a\\|b'），可忽略或改用 grep -E 'a|b'")


class Issue(NamedTuple):
    line: int
    code: str
    message: str
    severity: str = EC_ERROR


class Block(NamedTuple):
    lang: str
    start: int
    lines: tuple[str, ...]


class Claim(NamedTuple):
    number: int
    line: int
    blocks: tuple[Block, ...]
    verdict: Optional[str]
    verdict_line: int
    has_key_output: bool = True


class Parsed(NamedTuple):
    claims: tuple[Claim, ...]
    issues: tuple[Issue, ...]
    legacy_rows: int
    legacy_line: int


class LintResult(NamedTuple):
    issues: tuple[Issue, ...]
    claims: int
    errors: int
    warnings: int


def _fence_close(line: str, opener: str) -> bool:
    s = line.strip()
    return bool(s) and set(s) == {opener[0]} and len(s) >= len(opener)


def _split_cells(row: str) -> list[str]:
    body = row.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") and not body.endswith(ESCAPED_PIPE) else body
    return re.split(r"(?<!\\)\|", body)


def _legacy_issues(rows: Sequence[tuple[int, str]]) -> tuple[Issue, ...]:
    """rows[0] is the header; the rest are data rows (separator skipped)."""
    width = len(_split_cells(rows[0][1]))
    found: list[Issue] = []
    for ln, row in rows[1:]:
        if ESCAPED_PIPE in row:
            found.append(Issue(ln, "SCHEMA-DEFECT", "舊表格儲存格含已跳脫的直線字元，指令照字面重跑會失真"))
        elif len(_split_cells(row)) > width:
            found.append(Issue(ln, "SCHEMA-DEFECT", "舊表格儲存格含未跳脫的直線字元，欄位被切多了"))
    return tuple(found)


def _scan_fences(lines: Sequence[str]) -> tuple[list[Block], set[int]]:
    """Return all fenced blocks and the set of line numbers inside/at fences."""
    blocks: list[Block] = []
    covered: set[int] = set()
    opener, lang, start, body = "", "", 0, []
    for i, raw in enumerate(lines, start=1):
        if opener:
            covered.add(i)
            if _fence_close(raw, opener):
                blocks.append(Block(lang, start, tuple(body)))
                opener, body = "", []
            else:
                body.append(raw)
            continue
        m = FENCE_RE.match(raw.lstrip())
        if m:
            opener, start, body = m.group("ch"), i, []
            lang = (m.group("info").split() or [""])[0].lower()
            covered.add(i)
    return blocks, covered


def _scan_legacy(lines: Sequence[str], covered: set[int]) -> tuple[int, int, tuple[Issue, ...]]:
    """Find the first legacy table; return (first line, data rows, defects)."""
    for i, raw in enumerate(lines, start=1):
        if i in covered or not raw.lstrip().startswith("|") or LEGACY_MARK not in raw:
            continue
        rows = [(i, raw)]
        for j in range(i + 1, len(lines) + 1):
            row = lines[j - 1]
            if j in covered or not row.lstrip().startswith("|"):
                break
            if not re.fullmatch(r"[\s|:\-]+", row):
                rows.append((j, row))
        return i, len(rows) - 1, _legacy_issues(rows)
    return 0, 0, ()


def _build_claims(lines: Sequence[str], blocks: Sequence[Block], covered: set[int]):
    heads: list[tuple[int, int]] = []
    issues: list[Issue] = []
    for i, raw in enumerate(lines, start=1):
        if i in covered or not H2_RE.match(raw):
            continue
        m = HEADING_RE.match(raw)
        if m:
            heads.append((i, int(m.group("n"))))
        else:
            issues.append(Issue(i, "BAD-HEADING", "二級標題不是 `## <n>. <宣稱>` 格式"))
    claims = []
    for idx, (ln, n) in enumerate(heads):
        end = heads[idx + 1][0] if idx + 1 < len(heads) else len(lines) + 1
        claims.append(_claim_from_range(lines, blocks, covered, ln, n, end))
    return claims, issues


def _claim_from_range(lines, blocks, covered, ln: int, n: int, end: int) -> Claim:
    mine = tuple(b for b in blocks if ln < b.start < end and b.lang in BLOCK_LANGS)
    verdict, vline, key_output = None, 0, False
    for i in range(ln + 1, end):
        if i in covered:
            continue
        key_output = key_output or bool(KEY_OUTPUT_RE.match(lines[i - 1]))
        m = VERDICT_RE.match(lines[i - 1])
        if m and verdict is None:
            verdict, vline = m.group("v"), i
    return Claim(n, ln, mine, verdict, vline, key_output)


def parse(text: str) -> Parsed:
    lines = text.splitlines()
    blocks, covered = _scan_fences(lines)
    claims, issues = _build_claims(lines, blocks, covered)
    legacy_line, legacy_rows, defects = _scan_legacy(lines, covered)
    return Parsed(tuple(claims), tuple(issues) + defects, legacy_rows, legacy_line)


def _claim_issues(claim: Claim, expected: int) -> list[Issue]:
    out: list[Issue] = []
    if claim.number != expected:
        out.append(Issue(claim.line, "NUMBERING", f"編號應為 {expected}，實際為 {claim.number}"))
    if claim.verdict is None:
        out.append(Issue(claim.line, "NO-VERDICT", "缺少「判定：」行"))
    elif claim.verdict not in VERDICTS:
        out.append(Issue(claim.verdict_line, "BAD-VERDICT", f"判定值 {claim.verdict!r} 不是 PASS／FAIL／UNVERIFIABLE"))
    if not claim.has_key_output:
        out.append(Issue(claim.line, "NO-KEY-OUTPUT", "缺少「關鍵輸出：」行（UNVERIFIABLE 寫「關鍵輸出：—」）"))
    out.extend(_block_issues(claim))
    return out


def _block_issues(claim: Claim) -> list[Issue]:
    out: list[Issue] = []
    if claim.verdict == UNVERIFIABLE:
        if claim.blocks:
            out.append(Issue(claim.blocks[0].start, "UNEXPECTED-BLOCK", "判定為 UNVERIFIABLE 的宣稱不得有 bash block"))
        return out
    if not claim.blocks:
        out.append(Issue(claim.line, "NO-BLOCK", "缺少 bash fenced block"))
    elif len(claim.blocks) > 1:
        out.append(Issue(claim.blocks[1].start, "MULTI-BLOCK", "一條宣稱只能有一個 bash block"))
    for block in claim.blocks:
        for off, text in enumerate(block.lines, start=1):
            if ESCAPED_PIPE in text and ERE_TOOL_RE.search(text):
                out.append(Issue(block.start + off, "SCHEMA-DEFECT", ERE_PIPE_MSG))
            elif ESCAPED_PIPE in text:
                out.append(Issue(block.start + off, "ESCAPED-PIPE", ESCAPED_PIPE_MSG, EC_WARNING))
    return out


def lint_text(text: str, name: str) -> LintResult:
    """`name` is accepted for symmetry with the CLI; issues carry line numbers only."""
    parsed = parse(text)
    issues = list(parsed.issues)
    for idx, claim in enumerate(parsed.claims, start=1):
        issues.extend(_claim_issues(claim, idx))
    count = len(parsed.claims)
    if parsed.legacy_line:
        issues.append(Issue(parsed.legacy_line, "LEGACY-TABLE", "舊表格格式（相容到下一個 MAJOR），請改成每條宣稱一個小節", EC_WARNING))
        count += parsed.legacy_rows
    elif not parsed.claims:
        issues.append(Issue(1, "NO-CLAIMS", "沒有任何宣稱小節", EC_WARNING))
    issues.sort(key=lambda i: i.line)
    errors = sum(1 for i in issues if i.severity == EC_ERROR)
    return LintResult(tuple(issues), count, errors, len(issues) - errors)


def extract_text(text: str) -> str:
    parts: list[str] = []
    for claim in parse(text).claims:
        if claim.verdict == UNVERIFIABLE or not claim.blocks:
            continue
        parts.append(ECHO_FMT.format(n=claim.number))
        parts.append(SUBSHELL_OPEN)
        for block in claim.blocks:
            parts.extend(block.lines)
        parts.append(SUBSHELL_CLOSE)
    return "\n".join(parts) + "\n" if parts else ""


def _read(path: str) -> Optional[str]:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"claim_schema: 無法讀取 {path}: {exc}", file=sys.stderr)
        return None


def _cmd_lint(path: str) -> int:
    text = _read(path)
    if text is None:
        return EXIT_USAGE
    result = lint_text(text, path)
    for i in result.issues:
        print(f"{path}:{i.line}: {i.code}: {i.message}")
    print(f"摘要：{result.claims} 條宣稱，{result.errors} 個錯誤，{result.warnings} 個警告")
    return EXIT_ERRORS if result.errors else EXIT_OK


def _cmd_extract(path: str) -> int:
    text = _read(path)
    if text is None:
        return EXIT_USAGE
    parsed = parse(text)
    if parsed.legacy_line and not parsed.claims:
        print("LEGACY-TABLE: 舊表格格式沒有可抽取的 block，請改成新格式或逐條手跑", file=sys.stderr)
    sys.stdout.write(extract_text(text))
    return EXIT_OK


def main(argv: Sequence[str]) -> int:
    commands = {"lint": _cmd_lint, "extract": _cmd_extract}
    if len(argv) != 2 or argv[0] not in commands:
        print(__doc__, file=sys.stderr)
        return EXIT_USAGE
    return commands[argv[0]](argv[1])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
