"""Preflight checks for plan_runner.py — can this plan actually be run?

The Stop hook hands out the next step's command on every turn. When a tool
that command needs is missing, the model cannot comply, the step never
starts, and the hook repeats itself turn after turn (the S2.3 end-to-end run
burned six turns this way). Preflight catches the environment problems
before the first step is handed out, so they surface as one loud message
instead of a silent loop.

This module only *checks*; it never reads the plan itself. plan_runner.py
parses the plan and passes the per-step `Command:` strings in, so there is
one plan parser, not two.

Tool extraction rule (deliberately narrow — a false "missing" would stop a
healthy plan, which is worse than missing a check):

- Only the `Command:` field is scanned. `Action:` backticks are mostly file
  paths and function names, so they are never treated as tools.
- The command is split on `&&`, `||`, `;` and `|`; each segment is split
  with shlex and its first word (after `NAME=value` assignments) is the tool.
- Skipped: shell builtins, and slash commands such as `/verify` or
  `/code-review` — those are Claude Code skills, not executables.
- Skipped: any head word with shell expansion (`$VAR`, `${VAR}`, `$(...)`,
  backticks) — its value is only known when the shell runs it.
- Skipped: a relative path (`./run.sh`, `bin/x`) that comes after a `cd` /
  `pushd` in the same command — the directory it resolves against is only
  known at run time, and guessing it is how a healthy plan gets stopped.
- Any other tool containing `/` is checked as a path (relative to
  `base_dir`, must be an executable file); the rest is looked up on PATH.
- A segment shlex cannot parse (unbalanced quotes) is skipped, not guessed.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

KIND_RUNNER = "runner"
KIND_PLAN = "plan"
KIND_STATE = "state"
KIND_TOOL = "tool"

_DIR_CHANGERS = frozenset({"cd", "pushd", "popd"})
_EXPANSION_CHARS = ("$", "`")
SHELL_BUILTINS = frozenset({
    ".", ":", "[", "alias", "cd", "command", "eval", "exec", "exit", "export",
    "pushd", "popd", "read", "return", "set", "shift", "source", "test",
    "trap", "type", "ulimit", "umask", "unset",
})
_SLASH_COMMAND_RE = re.compile(r"^/[A-Za-z0-9:_-]+$")
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||;|\|")

Which = Callable[[str], "str | None"]


@dataclass(frozen=True)
class PreflightCheck:
    kind: str
    name: str
    ok: bool
    hint: str = ""


@dataclass(frozen=True)
class PreflightResult:
    checks: tuple[PreflightCheck, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def failures(self) -> tuple[PreflightCheck, ...]:
        return tuple(check for check in self.checks if not check.ok)


def _segment_head(segment: str) -> str | None:
    """First word of one segment after `NAME=value` assignments, or None
    when there is none or shlex cannot parse the segment."""
    try:
        words = [w for w in shlex.split(segment) if not _ENV_ASSIGN_RE.match(w)]
    except ValueError:
        return None
    return words[0] if words else None


def _is_relative_path(word: str) -> bool:
    return "/" in word and not word.startswith(("/", "~"))


def _checkable(head: str, after_cd: bool) -> bool:
    if head in SHELL_BUILTINS or _SLASH_COMMAND_RE.match(head):
        return False
    if any(ch in head for ch in _EXPANSION_CHARS):
        return False
    return not (after_cd and _is_relative_path(head))


def extract_tools(command: str | None) -> tuple[str, ...]:
    """Tools one `Command:` value needs, in first-seen order, de-duplicated."""
    tools: list[str] = []
    after_cd = False
    for segment in _SEGMENT_SPLIT_RE.split(command or ""):
        head = _segment_head(segment)
        if head is None:
            continue
        if _checkable(head, after_cd) and head not in tools:
            tools.append(head)
        after_cd = after_cd or head in _DIR_CHANGERS
    return tuple(tools)


def check_tool(name: str, base_dir: Path, which: Which = shutil.which) -> PreflightCheck:
    if "/" in name:
        path = Path(os.path.expanduser(name))
        if not path.is_absolute():
            path = base_dir / path
        ok = path.is_file() and os.access(path, os.X_OK)
        hint = f"`{path}` 不存在或不可執行：確認路徑，或 `chmod +x` 後重跑 preflight"
    else:
        ok = which(name) is not None
        hint = f"PATH 上找不到 `{name}`：安裝它或把所在目錄加進 PATH，再重跑 preflight"
    return PreflightCheck(KIND_TOOL, name, ok, "" if ok else hint)


def _file_check(kind: str, path: Path, hint: str, need: int = os.R_OK) -> PreflightCheck:
    ok = path.is_file() and os.access(path, need)
    return PreflightCheck(kind, str(path), ok, "" if ok else hint)


def run_preflight(
    *,
    runner_path: Path,
    plan_path: Path,
    state_path: Path,
    commands: Iterable[str | None],
    base_dir: Path,
    which: Which = shutil.which,
) -> PreflightResult:
    """Check the runner, the plan, its state, and every tool the plan's
    `Command:` fields name. Never raises for a missing file — that is a
    failed check, not an error.
    """
    base = (
        _file_check(KIND_RUNNER, runner_path, f"runner 腳本不存在或不可讀：{runner_path}"),
        _file_check(KIND_PLAN, plan_path, f"plan 檔不存在或不可讀：{plan_path}"),
        _file_check(
            KIND_STATE, state_path,
            f"state 檔不存在：先執行 `plan_runner.py init {plan_path}`",
        ),
    )
    tools: list[str] = []
    for command in commands:
        tools.extend(t for t in extract_tools(command) if t not in tools)
    return PreflightResult(base + tuple(check_tool(t, base_dir, which) for t in tools))


def result_to_dict(result: PreflightResult) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "checks": [
            {"kind": c.kind, "name": c.name, "ok": c.ok, "hint": c.hint}
            for c in result.checks
        ],
    }


def format_md(result: PreflightResult) -> str:
    """One line per check; a failed check carries its fix on the same line."""
    lines = [f"# preflight: {'OK' if result.ok else 'FAIL'}", ""]
    for check in result.checks:
        mark = "ok  " if check.ok else "FAIL"
        tail = f" — {check.hint}" if check.hint else ""
        lines.append(f"- [{mark}] {check.kind}: {check.name}{tail}")
    return "\n".join(lines)
