#!/usr/bin/env python3
"""Check that changed tests really showed up in a CI log (stdlib only).

"CI is green but the new test never ran" is invisible to a pass/fail light.
`ids` compares the identifiers of the tests a PR changed against the
identifiers the CI log lists; `count` is a weak, informational helper.

Reads only the files it is given: no commands, no network.

Usage:
    ci_collected.py ids --ids-file F --log L [--log L ...]
                        (--pattern REGEX | --runner NAME) [--match MODE]
    ci_collected.py count --before L --after L --pattern REGEX

ids-file: one changed-test identifier per line; blank lines and `#` lines skip.
--pattern: regex with exactly ONE capture group; each log line yields at most
    one identifier. Lines are matched with `search` after ANSI codes, the
    `gh run view --log` `<job>\\t<step>\\t` prefix and the Actions timestamp
    are removed (so `^` anchors work).
--runner: built-in list pattern for unittest (-v), pytest (-v), jest, playwright.
--match: how an ids-file value is compared with a log identifier.
    exact (default)  strictly equal
    suffix           the log identifier ends with the value at a `/` or `.`
                     boundary (ids short, log complete)
    suffix-of-id     the value ends with the log identifier at a `/` or `.`
                     boundary (ids from the repo root, log relative to the
                     runner dir). Misjudges FOUND when the log identifier is
                     short (any package's `TestRefund` matches); use it only
                     when both the ids and the log list paths.
    prefix           the log identifier starts with the value at a `.`/`/`/`:` boundary
Several --log files (matrix jobs) are unioned and deduplicated.

ids verdict: PASS (every id found), FAIL (some NOT-FOUND or SKIPPED),
    UNVERIFIABLE (the pattern lists nothing in any log, or the ids file is
    empty). With --runner unittest/pytest an id listed only as skipped (pytest
    SKIPPED, unittest `... skipped`) prints `SKIPPED <id>`: it was collected
    but did not run. A run in any one log (matrix job) is enough for FOUND.
count verdict: INFO or UNVERIFIABLE (a side has no count). Never PASS: totals
    move for unrelated reasons and cannot prove a specific test ran. A side's
    count is the sum of its matches; `matches=` shows how many there were.

Exit codes: ids FAIL 1; usage error 2; everything else 0.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Mapping, Optional, Sequence

MATCH_EXACT, MATCH_SUFFIX, MATCH_PREFIX = "exact", "suffix", "prefix"
MATCH_SUFFIX_OF_ID = "suffix-of-id"
MATCH_MODES = (MATCH_EXACT, MATCH_SUFFIX, MATCH_SUFFIX_OF_ID, MATCH_PREFIX)
SUFFIX_BOUNDARIES = ("/", ".")
PREFIX_BOUNDARIES = (".", "/", ":")

PASS, FAIL, UNVERIFIABLE, INFO = "PASS", "FAIL", "UNVERIFIABLE", "INFO"
EXIT_OK, EXIT_FAIL, EXIT_USAGE = 0, 1, 2

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
JOB_PREFIX_RE = re.compile(r"^[^\t]*\t[^\t]*\t(?=\d{4}-\d\d-\d\dT)")
TIMESTAMP_RE = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ?")

_STATUS = r"(?:PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)"
RUNNER_PATTERNS: Mapping[str, str] = {
    # `test_x (pkg.mod.Cls.test_x) ... ok`, or the same id alone on its line
    # when a docstring follows on the next line (`-v` with docstrings)
    "unittest": r"^\w+ \(([\w.]+)\)(?: \.\.\. |$)",
    # `tests/a.py::Cls::test_x[param id] PASSED`; the param id may hold spaces.
    # xdist `[gw0] PASSED tests/..` is not caught
    "pytest": rf"^(\S+\.py::.+?)\s+{_STATUS}\b",
    # `PASS src/a.test.ts (1.2 s)`; captures the test FILE, not single tests
    "jest": r"^(?:PASS|FAIL)\s+(\S+\.(?:test|spec)\.[cm]?[jt]sx?)\b",
    # list reporter `✓  1 [chromium] › a.spec.ts:3:5 › title`; captures the FILE
    "playwright": (
        r"^\s*(?:✓|✔|✘|x|-|·)\s+\d+\s+(?:\[[^\]]+\]\s+›\s+)?"
        r"(\S+\.(?:spec|test)\.[cm]?[jt]sx?):\d+:\d+"
    ),
}


# With --runner, a listed id whose status says it was skipped is reported as
# SKIPPED (not run), never FOUND. The status follows the id on the same line,
# or, for unittest -v with a docstring, on the next line.
SKIP_STATUS: Mapping[str, "re.Pattern[str]"] = {
    "unittest": re.compile(r"\.\.\. skipped\b"),
    "pytest": re.compile(r"^\s*SKIPPED\b"),
}


class UsageError(Exception):
    """Bad arguments or unreadable input; maps to exit code 2."""


def normalize_line(line: str) -> str:
    """Drop ANSI codes, the gh job/step prefix and the Actions timestamp."""
    text = ANSI_RE.sub("", line.rstrip("\r\n"))
    text = JOB_PREFIX_RE.sub("", text, count=1)
    return TIMESTAMP_RE.sub("", text, count=1)


def compile_pattern(pattern: str) -> "re.Pattern[str]":
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise UsageError(f"invalid regex: {exc}") from exc
    if compiled.groups != 1:
        raise UsageError(
            f"pattern must have exactly one capture group, has {compiled.groups}"
        )
    return compiled


def _matches(text: str, pattern: "str | re.Pattern[str]") -> list[str]:
    compiled = compile_pattern(pattern) if isinstance(pattern, str) else pattern
    found: list[str] = []
    for raw in text.splitlines():
        hit = compiled.search(normalize_line(raw))
        if hit and hit.group(1) is not None:
            found.append(hit.group(1))
    return found


def extract_listing(text: str, pattern: "re.Pattern[str]",
                    skip: Optional["re.Pattern[str]"]) -> tuple[tuple[str, bool], ...]:
    """(id, skipped) for every listed id, in order; skipped needs a skip pattern."""
    lines = [normalize_line(raw) for raw in text.splitlines()]
    found: list[tuple[str, bool]] = []
    for index, line in enumerate(lines):
        hit = pattern.search(line)
        if not hit or hit.group(1) is None:
            continue
        status = line[hit.end(1):]
        if not line[hit.end():].strip() and index + 1 < len(lines):
            status += " " + lines[index + 1]
        found.append((hit.group(1), bool(skip and skip.search(status))))
    return tuple(found)


def extract_ids(text: str, pattern: "str | re.Pattern[str]") -> tuple[str, ...]:
    """Identifiers listed in `text`, in first-seen order, deduplicated."""
    return tuple(dict.fromkeys(_matches(text, pattern)))


def is_match(wanted: str, seen: str, mode: str = MATCH_EXACT) -> bool:
    if wanted == seen:
        return True
    if mode == MATCH_SUFFIX:
        return any(seen.endswith(b + wanted) for b in SUFFIX_BOUNDARIES)
    if mode == MATCH_SUFFIX_OF_ID:
        return any(wanted.endswith(b + seen) for b in SUFFIX_BOUNDARIES)
    if mode == MATCH_PREFIX:
        return any(seen.startswith(wanted + b) for b in PREFIX_BOUNDARIES)
    return False


def judge_ids(
    wanted: Sequence[str], collected: Sequence[str], mode: str
) -> tuple[tuple[tuple[str, bool], ...], str]:
    """Per-id found flags and the overall verdict."""
    if not wanted or not collected:
        return tuple((w, False) for w in wanted), UNVERIFIABLE
    flags = tuple(
        (w, any(is_match(w, seen, mode) for seen in collected)) for w in wanted
    )
    return flags, PASS if all(ok for _, ok in flags) else FAIL


def _status(ident: str, ok: bool, skipped: Sequence[str], mode: str) -> str:
    if ok:
        return "FOUND"
    return "SKIPPED" if any(is_match(ident, seen, mode) for seen in skipped) else "NOT-FOUND"


def read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise UsageError(f"cannot read {path}: {exc.strerror}") from exc


def read_ids_file(path: str) -> tuple[str, ...]:
    lines = (ln.strip() for ln in read_text(path).splitlines())
    return tuple(dict.fromkeys(ln for ln in lines if ln and not ln.startswith("#")))


def run_ids(args: argparse.Namespace) -> int:
    pattern = compile_pattern(args.pattern or RUNNER_PATTERNS[args.runner])
    skip = SKIP_STATUS.get(args.runner) if args.runner else None
    wanted = read_ids_file(args.ids_file)
    ran: dict[str, None] = {}
    skipped: dict[str, None] = {}
    for log in args.log:
        for ident, was_skipped in extract_listing(read_text(log), pattern, skip):
            (skipped if was_skipped else ran)[ident] = None
    listed = {**ran, **skipped}
    flags, verdict = judge_ids(wanted, tuple(ran), args.match)
    if verdict == UNVERIFIABLE and wanted and skipped:
        verdict = FAIL  # something was listed, but only as skipped
    print(f"MATCH: {args.match}")
    print(f"LISTED: {len(listed)} identifiers across {len(args.log)} log(s)"
          f"{f' ({len(skipped)} skipped)' if skipped else ''}")
    for ident, ok in flags:
        print(f"{_status(ident, ok, tuple(skipped), args.match)} {ident}")
    print(f"VERDICT: {verdict}")
    return EXIT_FAIL if verdict == FAIL else EXIT_OK


def count_in(text: str, pattern: "re.Pattern[str]") -> tuple[Optional[int], int]:
    numbers = [int(m) for m in _matches(text, pattern) if m.isdecimal()]
    return (sum(numbers) if numbers else None), len(numbers)


def run_count(args: argparse.Namespace) -> int:
    pattern = compile_pattern(args.pattern)
    before, n_before = count_in(read_text(args.before), pattern)
    after, n_after = count_in(read_text(args.after), pattern)
    show = lambda v: "none" if v is None else str(v)  # noqa: E731
    print(f"before: {show(before)} (matches={n_before})")
    print(f"after: {show(after)} (matches={n_after})")
    if before is None or after is None:
        print("delta: none")
        print(f"VERDICT: {UNVERIFIABLE}")
    else:
        print(f"delta: {after - before:+d}")
        print(f"VERDICT: {INFO}")
    return EXIT_OK


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # argparse default exits 2 already; keep text
        raise UsageError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(description="Compare changed tests against a CI log.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ids = sub.add_parser("ids", help="main verdict: are the changed test ids in the log")
    ids.add_argument("--ids-file", required=True)
    ids.add_argument("--log", action="append", required=True)
    source = ids.add_mutually_exclusive_group(required=True)
    source.add_argument("--pattern")
    source.add_argument("--runner", choices=sorted(RUNNER_PATTERNS))
    ids.add_argument(
        "--match", choices=MATCH_MODES, default=MATCH_EXACT,
        help="exact (default); suffix: log id ends with the value; suffix-of-id: "
             "value ends with the log id, misjudges FOUND for short log ids, use only "
             "when ids and log are both paths; prefix: log id starts with the value",
    )
    count = sub.add_parser("count", help="auxiliary totals; never PASS")
    count.add_argument("--before", required=True)
    count.add_argument("--after", required=True)
    count.add_argument("--pattern", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        return run_ids(args) if args.cmd == "ids" else run_count(args)
    except UsageError as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except SystemExit as exc:  # --help exits 0 inside argparse
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
