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
- Shell comments go first: an unquoted `#` at the start of a word (after
  whitespace, an operator, or at the beginning) drops the rest of its
  line, while `a#b`, `${#arr}`, `$#` and a quoted or escaped `#` stay.
- The whole command is tokenized once with `shlex` (POSIX quoting,
  `punctuation_chars` for operators), so `;`, `&&`, `||`, `|`, `&`, `(` and
  newlines only separate commands when they are unquoted and unescaped:
  `python3 -c "import a; import b"` needs `python3`, never `import`.
- The first word at each command position is the tool, after `NAME=value`
  assignments and the `env` / `time` / `!` prefixes (`env` with an option
  such as `-i` is skipped entirely).
- Shell keywords are not tools: `if then elif else fi for select while until
  do done case esac in function time ! { } [[ ]]`. The body of `[[ ... ]]`
  and of `case ... esac` is skipped, and so is everything inside `$( ... )`,
  `$(( ... ))`, `(( ... ))` and `<( ... )`.
- Skipped: shell builtins, functions the command defines itself (`f() {`,
  `function f`), and slash commands such as `/verify` or `/code-review` —
  those are Claude Code skills, not executables.
- Skipped: any head word with shell expansion (`$VAR`, `${VAR}`, backticks)
  or a glob — its value is only known when the shell runs it.
- Skipped: a relative path (`./run.sh`, `bin/x`) that comes after a `cd` /
  `pushd` in the same command — the directory it resolves against is only
  known at run time, and guessing it is how a healthy plan gets stopped.
- Any other tool containing `/` is checked as a path (relative to
  `base_dir`, must be an executable file); the rest is looked up on PATH.
- A command shlex cannot parse (unbalanced quotes) yields no tools at all:
  the shell would not run it as written, so there is nothing to guess.
"""
from __future__ import annotations

import io
import os
import re
import shlex
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable

KIND_RUNNER = "runner"
KIND_PLAN = "plan"
KIND_STATE = "state"
KIND_TOOL = "tool"

_DIR_CHANGERS = frozenset({"cd", "pushd", "popd"})
_EXPANSION_CHARS = ("$", "`")
_GLOB_CHARS = ("*", "?")
SHELL_BUILTINS = frozenset({
    ".", ":", "[", "alias", "bg", "builtin", "cd", "command", "declare", "echo",
    "eval", "exec", "exit", "export", "false", "fg", "getopts", "hash", "jobs",
    "let", "local", "popd", "printf", "pushd", "pwd", "read", "readonly",
    "return", "set", "shift", "shopt", "source", "test", "trap", "true",
    "type", "typeset", "ulimit", "umask", "unset", "wait",
})
# Keywords after which the next word is still at command position.
_KEYWORDS_KEEP_COMMAND = frozenset({
    "if", "then", "elif", "else", "while", "until", "do", "!", "{",
})
# Keywords that close a construct or introduce non-command words.
_KEYWORDS_END_COMMAND = frozenset({
    "fi", "done", "esac", "}", "]]", "in", "for", "select", "coproc",
})
# Keywords whose whole body is skipped up to the matching closing word.
_KEYWORDS_SKIP_TO = {"[[": "]]", "case": "esac"}
_SLASH_COMMAND_RE = re.compile(r"^/[A-Za-z0-9:_-]+$")
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_OPERATOR_CHARS = "();<>|&\n"
_SEPARATOR_CHARS = frozenset(";&|(\n")
_QUOTING_CHARS = ("'", '"', "\\")
# A `#` starts a comment only at the start of a word: after whitespace, an
# operator, or at the very beginning. `a#b`, `${#arr}` and `$#` are words.
_COMMENT_MAY_FOLLOW = frozenset(" \t\r\n;&|()<>")

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


@dataclass(frozen=True)
class _Token:
    text: str
    is_operator: bool


@dataclass(frozen=True)
class _ScanState:
    """Where the scanner is in the command; replaced, never mutated."""
    tools: tuple[str, ...] = ()
    functions: frozenset[str] = field(default_factory=frozenset)
    command_position: bool = True
    after_cd: bool = False
    redirect_target: bool = False
    prefix: str | None = None
    skip_to: str | None = None
    paren_depth: int = 0
    after_dollar: bool = False


def _is_operator(text: str, raw: str) -> bool:
    """An all-punctuation token is an operator only when it was written
    bare: `\\;` and `';'` are ordinary words to the shell."""
    return (
        bool(text)
        and all(ch in _OPERATOR_CHARS for ch in text)
        and not any(q in raw for q in _QUOTING_CHARS)
    )


def _quote_state(quote: str | None, ch: str) -> str | None:
    """Quote context after reading `ch` (escapes are handled by the caller)."""
    if quote is None:
        return ch if ch in ("'", '"') else None
    return None if ch == quote else quote


def _strip_comments(command: str) -> str:
    """Drop each unquoted, word-initial `#` and the rest of its line.

    shlex's own `commenters` would also cut `a#b` and `${#arr}` mid-word,
    so this follows the shell rule instead (review N1).
    """
    out: list[str] = []
    quote: str | None = None
    word_start = True
    i = 0
    while i < len(command):
        ch = command[i]
        if quote is None and ch == "#" and word_start:
            end = command.find("\n", i)
            i = len(command) if end < 0 else end
            continue
        escapes = ch == "\\" and quote != "'"
        step = 2 if escapes else 1
        out.append(command[i:i + step])
        quote = quote if escapes else _quote_state(quote, ch)
        # An escaped `;` or space is part of the word, so it never opens a
        # comment (review N1-R); neither does a separator inside quotes.
        word_start = not escapes and quote is None and ch in _COMMENT_MAY_FOLLOW
        i += step
    return "".join(out)


def _tokenize(command: str) -> tuple[_Token, ...] | None:
    """shlex tokens tagged operator/word, or None when shlex cannot parse."""
    lexer = shlex.shlex(io.StringIO(command), posix=True, punctuation_chars=_OPERATOR_CHARS)
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    tokens: list[_Token] = []
    start = 0
    try:
        for text in iter(lexer.get_token, None):
            # shlex reads one char ahead around operators; step back over it
            # so `raw` is this token's own source text.
            end = lexer.instream.tell() - len(getattr(lexer, "_pushback_chars", ()))
            tokens.append(_Token(text, _is_operator(text, command[start:end])))
            start = end
    except ValueError:
        return None
    return tuple(tokens)


def _is_relative_path(word: str) -> bool:
    return "/" in word and not word.startswith(("/", "~"))


def _checkable(head: str, after_cd: bool) -> bool:
    if not head or head.startswith("-"):
        return False
    if head in SHELL_BUILTINS or _SLASH_COMMAND_RE.match(head):
        return False
    if any(ch in head for ch in _EXPANSION_CHARS + _GLOB_CHARS):
        return False
    return not (after_cd and _is_relative_path(head))


def _paren_delta(text: str) -> int:
    return text.count("(") - text.count(")")


def _on_paren_body(state: _ScanState, token: _Token) -> _ScanState:
    """Inside `$(...)` / `((...))`: count parens, check nothing."""
    if not token.is_operator:
        return state
    depth = state.paren_depth + _paren_delta(token.text)
    if depth > 0:
        return replace(state, paren_depth=depth)
    separated = any(ch in _SEPARATOR_CHARS for ch in token.text.lstrip(")"))
    return replace(state, paren_depth=0, command_position=separated)


def _on_operator(state: _ScanState, token: _Token) -> _ScanState:
    text = token.text
    base = replace(state, after_dollar=False, prefix=None)
    opens_substitution = "(" in text and (state.after_dollar or text.startswith("(("))
    if ("<" in text or ">" in text) and "(" in text or opens_substitution:
        return replace(base, paren_depth=max(_paren_delta(text), 1), command_position=False)
    if "<" in text or ">" in text:
        return replace(base, redirect_target=True, prefix=state.prefix)
    return replace(base, command_position=any(ch in _SEPARATOR_CHARS for ch in text))


def _on_keyword(state: _ScanState, word: str) -> _ScanState | None:
    """State after a shell keyword at command position, None if not one."""
    if word in _KEYWORDS_SKIP_TO:
        return replace(state, skip_to=_KEYWORDS_SKIP_TO[word], command_position=False)
    if word in _KEYWORDS_KEEP_COMMAND:
        return state
    if word in _KEYWORDS_END_COMMAND:
        return replace(state, command_position=False)
    if word in ("time", "env", "function"):
        return replace(state, prefix=word)
    return None


def _on_prefixed(state: _ScanState, word: str) -> _ScanState | None:
    """Handle the word right after `time` / `env` / `function`; None means
    the word is an ordinary head and scanning should continue with it."""
    prefix = state.prefix
    cleared = replace(state, prefix=None)
    if prefix == "function":
        return replace(cleared, functions=state.functions | {word})
    if prefix == "time" and word == "-p":
        return cleared
    if prefix == "env" and word.startswith("-"):
        return replace(cleared, command_position=False)
    if prefix == "env" and _ENV_ASSIGN_RE.match(word):
        return state
    return None if prefix in ("time", "env") else cleared


def _on_head(state: _ScanState, word: str, next_token: _Token | None) -> _ScanState:
    """A word at command position: keyword, assignment, function name, or tool."""
    if state.prefix is not None:
        handled = _on_prefixed(state, word)
        if handled is not None:
            return handled
        state = replace(state, prefix=None)
    if _ENV_ASSIGN_RE.match(word):
        return state
    keyword = _on_keyword(state, word)
    if keyword is not None:
        return keyword
    done = replace(state, command_position=False, after_cd=state.after_cd or word in _DIR_CHANGERS)
    if next_token is not None and next_token.is_operator and next_token.text.startswith("("):
        return replace(done, functions=state.functions | {word})
    wanted = _checkable(word, state.after_cd) and word not in state.functions
    if not wanted or word in state.tools:
        return done
    return replace(done, tools=state.tools + (word,))


def _on_word(state: _ScanState, token: _Token, next_token: _Token | None) -> _ScanState:
    state = replace(state, after_dollar=token.text.endswith("$"))
    if state.redirect_target:
        return replace(state, redirect_target=False)
    if not state.command_position:
        return state
    return _on_head(state, token.text, next_token)


def _scan(state: _ScanState, token: _Token, next_token: _Token | None) -> _ScanState:
    if state.paren_depth:
        return _on_paren_body(state, token)
    if state.skip_to is not None:
        closed = not token.is_operator and token.text == state.skip_to
        return replace(state, skip_to=None) if closed else state
    if token.is_operator:
        return _on_operator(state, token)
    return _on_word(state, token, next_token)


def extract_tools(command: str | None) -> tuple[str, ...]:
    """Tools one `Command:` value needs, in first-seen order, de-duplicated."""
    tokens = _tokenize(_strip_comments(command or ""))
    if tokens is None:
        return ()
    state = _ScanState()
    for index, token in enumerate(tokens):
        next_token = tokens[index + 1] if index + 1 < len(tokens) else None
        state = _scan(state, token, next_token)
    return state.tools


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
