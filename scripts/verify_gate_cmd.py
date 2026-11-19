#!/usr/bin/env python3
"""Conservative shell command scanner for the verify gate hook (stdlib only).

The gate hook must tell whether `gh pr create`, `git push` or
`verify_engine.py pr register` appears in *command position* of a Bash tool
command. A match inside a comment, a heredoc body, a quoted argument
(`echo "gh pr create"`, `git commit -m "... gh pr create ..."`) or a command
substitution does not count.

The scanner understands quotes, backslash escapes, comments, `$(...)`,
backticks, `${...}`, heredocs (including heredocs inside `"$(cat <<'EOF' ...)"`),
redirections and the list operators `;`, `&&`, `||`, `|`, `&` and newline.
Anything it does not understand (subshell or brace groups, process
substitution, `;;`, unterminated quotes) makes the whole command unparseable,
and an unparseable command matches nothing. Nothing here runs a process.
"""
from __future__ import annotations

import os
import re
from typing import NamedTuple, Optional

MAX_COMMAND_CHARS = 200_000
KIND_PR_CREATE, KIND_PUSH, KIND_REGISTER = "gh-pr-create", "git-push", "pr-register"
PUSH_CURRENT, PUSH_BRANCH, PUSH_UNRESOLVED, PUSH_NONE = "current", "branch", "unresolved", "none"

_OPERATORS = ("&&", "||", "|&", ";", "|", "&")
_CWD_KEEPING_OPS = frozenset({"&&", ";", "\n", ""})
_RESERVED = frozenset({"if", "then", "else", "elif", "fi", "for", "while", "until", "do",
                       "done", "case", "esac", "select", "function", "!", "[[", "]]",
                       "{", "}", "time", "coproc"})
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")
_NAME_CHAR = re.compile(r"[A-Za-z0-9_]")
_PYTHON = re.compile(r"^python(?:3(?:\.\d+)?)?$")
_PYTHON_SAFE_FLAGS = frozenset({"-I", "-u", "-B", "-E", "-s", "-S", "-O", "-OO", "-q"})
_GIT_NOARG_OPTS = frozenset({"--no-pager", "--paginate", "-p", "-P", "--no-replace-objects",
                             "--literal-pathspecs", "--no-optional-locks", "--no-advice"})
_PUSH_FLAGS = frozenset({"-u", "--set-upstream", "-f", "--force", "--force-with-lease",
                         "--no-force-with-lease", "--force-if-includes", "-q", "--quiet",
                         "-v", "--verbose", "--progress", "--no-progress", "--no-verify",
                         "--verify", "--atomic", "--no-atomic", "--porcelain",
                         "--follow-tags", "--no-follow-tags", "--signed", "--no-signed",
                         "-4", "-6", "--ipv4", "--ipv6", "--thin", "--no-thin"})
_PUSH_VALUE_OPTS = frozenset({"-o", "--push-option", "--repo", "--receive-pack", "--exec"})
_PUSH_VALUE_PREFIXES = ("--force-with-lease=", "--push-option=", "--repo=", "--receive-pack=",
                        "--exec=", "--signed=", "--recurse-submodules=")
_PUSH_DRY_RUN = frozenset({"-n", "--dry-run"})
_HEADS = "refs/heads/"


class Unparseable(Exception):
    """The command uses a construct this scanner does not model."""


class Word(NamedTuple):
    text: str
    dynamic: bool  # contains an expansion whose value is unknown statically


class Segment(NamedTuple):
    words: tuple
    op: str  # operator that ends the segment; "" at the end of input


class Invocation(NamedTuple):
    kind: str
    cwd: Optional[str]  # directory the command runs in; None when unknown
    args: tuple         # Words after the recognised subcommand


class PushTarget(NamedTuple):
    kind: str  # PUSH_CURRENT, PUSH_BRANCH, PUSH_UNRESOLVED or PUSH_NONE (dry run)
    branch: Optional[str]


# --- scanner ----------------------------------------------------------------------

class _Scanner:
    """Single-use lexer; only `segments()` is meant to be called from outside."""

    def __init__(self, text: str) -> None:
        self.s, self.i, self.n = text, 0, len(text)
        self.heredocs: list = []
        self.word: Optional[list] = None
        self.dynamic = False
        self.pending = ""  # "drop": next word is a redirect target; "heredoc[-]": delimiter
        self.words: list = []
        self.segments_out: list = []

    # word assembly
    def _start(self) -> None:
        if self.word is None:
            self.word, self.dynamic = [], False

    def _add(self, text: str, dynamic: bool = False) -> None:
        self._start()
        self.word.append(text)
        self.dynamic = self.dynamic or dynamic

    def _finish_word(self) -> None:
        if self.word is None:
            return
        word = Word("".join(self.word), self.dynamic)
        self.word, self.dynamic = None, False
        if self.pending == "drop":
            self.pending = ""
        elif self.pending.startswith("heredoc"):
            self.heredocs.append((word.text, self.pending.endswith("-")))
            self.pending = ""
        else:
            self.words.append(word)

    def _end_segment(self, op: str) -> None:
        self._finish_word()
        if self.pending:
            raise Unparseable("redirection without a target")
        self.segments_out.append(Segment(tuple(self.words), op))
        self.words = []

    # nested constructs
    def _skip_heredoc_bodies(self) -> None:
        for delimiter, strip_tabs in self.heredocs:
            while self.i < self.n:
                end = self.s.find("\n", self.i)
                end = self.n if end < 0 else end
                line = self.s[self.i:end]
                self.i = end + 1
                if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                    break
        self.heredocs = []

    def _skip_nested(self) -> None:
        """Skip a `$(...)` body; self.i is just past `$(`."""
        inner = _Scanner(self.s)
        inner.i = self.i
        self.i = inner.scan(nested=True)

    def _skip_backtick(self) -> None:
        j = self.i
        while j < self.n and self.s[j] != "`":
            j += 2 if self.s[j] == "\\" else 1
        if j >= self.n:
            raise Unparseable("unterminated backtick")
        self.i = j + 1

    def _skip_braces(self) -> None:
        depth, j = 1, self.i
        while j < self.n and depth:
            depth += {"{": 1, "}": -1}.get(self.s[j], 0)
            j += 1
        if depth:
            raise Unparseable("unterminated ${")
        self.i = j

    def _dollar(self) -> bool:
        """Handle `$...` at self.i; return False when `$` is a literal."""
        nxt = self.s[self.i + 1:self.i + 2]
        if nxt == "(":
            self.i += 2
            self._skip_nested()
        elif nxt == "{":
            self.i += 2
            self._skip_braces()
        elif nxt and (_NAME_CHAR.match(nxt) or nxt in "@*#?$!-"):
            self.i += 2
            while nxt not in "@*#?$!-" and self.i < self.n and _NAME_CHAR.match(self.s[self.i]):
                self.i += 1
        else:
            return False
        self._add("", dynamic=True)
        return True

    def _double_quote(self) -> None:
        self.i += 1
        self._start()
        while self.i < self.n:
            c = self.s[self.i]
            if c == '"':
                self.i += 1
                return
            if c == "\\" and self.i + 1 < self.n:
                nxt = self.s[self.i + 1]
                self._add(nxt if nxt in '$`"\\' else ("" if nxt == "\n" else "\\" + nxt))
                self.i += 2
            elif c == "`":
                self.i += 1
                self._skip_backtick()
                self._add("", dynamic=True)
            elif c == "$" and self._dollar():
                continue
            else:
                self._add(c)
                self.i += 1
        raise Unparseable("unterminated double quote")

    def _single_quote(self, ansi: bool) -> None:
        j = self.i + 1
        while j < self.n and self.s[j] != "'":
            j += 2 if ansi and self.s[j] == "\\" else 1
        if j >= self.n:
            raise Unparseable("unterminated single quote")
        self._add(self.s[self.i + 1:j], dynamic=ansi)
        self.i = j + 1

    def _redirect(self) -> None:
        if self.word is not None and "".join(self.word).isdigit() and not self.dynamic:
            self.word, self.dynamic = None, False  # an fd number such as the 2 in 2>&1
        self._finish_word()
        if self.pending:
            raise Unparseable("two redirections in a row")
        s, i = self.s, self.i
        if s.startswith("<<<", i):
            self.i, self.pending = i + 3, "drop"
        elif s.startswith("<<", i):
            strip = s.startswith("<<-", i)
            self.i, self.pending = i + (3 if strip else 2), "heredoc-" if strip else "heredoc"
        elif s[i + 1:i + 2] == "(":
            raise Unparseable("process substitution")
        else:
            self.i = i + (2 if s[i + 1:i + 2] in ">&|" else 1)
            self.pending = "drop"

    def _operator(self, nested: bool) -> None:
        s, i = self.s, self.i
        if s.startswith(";;", i):
            raise Unparseable("case clause")
        if s.startswith("&>", i):
            self.i += 1
            self._redirect()
            return
        op = next(o for o in _OPERATORS if s.startswith(o, i))
        self.i += len(op)
        self._end_segment(op)

    def _special(self, c: str, nested: bool) -> bool:
        """Handle quoting/expansion characters; False when `c` is not one."""
        if c == "\\":
            nxt = self.s[self.i + 1:self.i + 2]
            if nxt != "\n":
                self._add(nxt)
            self.i += 2
        elif c == "'":
            self._single_quote(ansi=False)
        elif c == '"':
            self._double_quote()
        elif c == "`":
            self.i += 1
            self._skip_backtick()
            self._add("", dynamic=True)
        elif c == "$" and self.s[self.i + 1:self.i + 2] == "'":
            self.i += 1
            self._single_quote(ansi=True)
        elif c == "$" and self._dollar():
            pass
        else:
            return False
        return True

    def scan(self, nested: bool) -> int:
        """Scan to the end (top level) or to the closing `)` (nested)."""
        depth = 0
        while self.i < self.n:
            c = self.s[self.i]
            if c in " \t":
                self._finish_word()
                self.i += 1
            elif c == "\n":
                self.i += 1
                self._end_segment("\n")
                self._skip_heredoc_bodies()
            elif c == "#" and self.word is None:
                end = self.s.find("\n", self.i)
                self.i = self.n if end < 0 else end
            elif c in "()":
                if not nested:
                    raise Unparseable("subshell or grouping")
                if c == ")" and depth == 0:
                    self._end_segment("")
                    return self.i + 1
                depth += 1 if c == "(" else -1
                self._finish_word()
                self.i += 1
            elif c in "<>":
                self._redirect()
            elif c in ";&|":
                self._operator(nested)
            elif not self._special(c, nested):
                self._add(c)
                self.i += 1
        if nested:
            raise Unparseable("unterminated command substitution")
        self._end_segment("")
        return self.i


def parse_segments(command: str) -> Optional[tuple]:
    """Top-level simple commands of `command`, or None when unparseable."""
    if not isinstance(command, str) or len(command) > MAX_COMMAND_CHARS:
        return None
    scanner = _Scanner(command)
    try:
        scanner.scan(nested=False)
    except (Unparseable, IndexError, StopIteration):
        return None
    return tuple(seg for seg in scanner.segments_out if seg.words or seg.op not in ("\n", ""))


# --- command recognition -----------------------------------------------------------

def _strip_assignments(words: tuple) -> tuple:
    index = 0
    while index < len(words) and _ASSIGNMENT.match(words[index].text):
        index += 1
    return words[index:]


def _resolve_dir(base: Optional[str], word: Word, home: Optional[str]) -> Optional[str]:
    if word.dynamic or not word.text or word.text == "-":
        return None
    text = word.text
    if text == "~" or text.startswith("~/"):
        if not home:
            return None
        text = home + text[1:]
    elif text.startswith("~"):
        return None
    if not os.path.isabs(text):
        if base is None:
            return None
        text = os.path.join(base, text)
    return os.path.normpath(text)


def _git_invocation(words: tuple, cwd: Optional[str], home: Optional[str]
                    ) -> Optional[Invocation]:
    index = 1
    while index < len(words):
        text = words[index].text
        if words[index].dynamic:
            return None
        if text == "-C" and index + 1 < len(words):
            cwd = _resolve_dir(cwd, words[index + 1], home)
            index += 2
        elif text == "-c" and index + 1 < len(words):
            index += 2
        elif text in _GIT_NOARG_OPTS:
            index += 1
        elif text == "push":
            return Invocation(KIND_PUSH, cwd, words[index + 1:])
        else:
            return None  # another subcommand, or an option such as --git-dir
    return None


def _engine_invocation(words: tuple, cwd: Optional[str], home: Optional[str]
                       ) -> Optional[Invocation]:
    index = 0
    if _PYTHON.match(os.path.basename(words[0].text)):
        index = 1
        while index < len(words) and words[index].text in _PYTHON_SAFE_FLAGS:
            index += 1
    rest = words[index:]
    if (len(rest) >= 3 and not any(w.dynamic for w in rest[:3])
            and os.path.basename(rest[0].text) == "verify_engine.py"
            and rest[1].text == "pr" and rest[2].text == "register"):
        return Invocation(KIND_REGISTER, cwd, rest[3:])
    return None


def recognise(words: tuple, cwd: Optional[str], home: Optional[str]) -> Optional[Invocation]:
    """Invocation for one simple command, or None when it is none of the three."""
    words = _strip_assignments(words)
    if not words or words[0].dynamic or words[0].text in _RESERVED:
        return None
    name = os.path.basename(words[0].text)
    if name == "gh":
        if (len(words) >= 3 and not words[1].dynamic and not words[2].dynamic
                and words[1].text == "pr" and words[2].text == "create"):
            return Invocation(KIND_PR_CREATE, cwd, words[3:])
        return None
    if name == "git":
        return _git_invocation(words, cwd, home)
    return _engine_invocation(words, cwd, home)


def _next_cwd(seg: Segment, cwd: Optional[str], home: Optional[str]) -> Optional[str]:
    words = _strip_assignments(seg.words)
    if not words or words[0].dynamic or words[0].text != "cd":
        return cwd
    target = _resolve_dir(cwd, words[1], home) if len(words) == 2 else None
    return target if seg.op in _CWD_KEEPING_OPS else None


def find_invocations(command: str, cwd: Optional[str], home: Optional[str]) -> tuple:
    """Every recognised invocation in command position, in order."""
    segments = parse_segments(command)
    if segments is None:
        return ()
    found, current = [], cwd
    for seg in segments:
        invocation = recognise(seg.words, current, home)
        if invocation is not None:
            found.append(invocation)
        current = _next_cwd(seg, current, home)
    return tuple(found)


# --- argument helpers --------------------------------------------------------------

def _branch_name(ref: str) -> Optional[str]:
    if ref.startswith(_HEADS):
        ref = ref[len(_HEADS):]
    if not ref or ref.startswith(("refs/", "-")) or ref in ("HEAD", "@"):
        return None
    return ref


def _refspec_target(spec: str) -> PushTarget:
    spec = spec[1:] if spec.startswith("+") else spec
    if spec in ("HEAD", "@"):
        return PushTarget(PUSH_CURRENT, None)
    if ":" in spec:
        src, dst = spec.split(":", 1)
        src_name, dst_name = _branch_name(src), _branch_name(dst)
        if src_name and src_name == dst_name:
            return PushTarget(PUSH_BRANCH, src_name)
        return PushTarget(PUSH_UNRESOLVED, None)
    name = _branch_name(spec)
    return PushTarget(PUSH_BRANCH, name) if name else PushTarget(PUSH_UNRESOLVED, None)


def push_target(args: tuple) -> PushTarget:
    """Which local branch a `git push <args>` pushes; see the hooks document."""
    positional, index = [], 0
    while index < len(args):
        word = args[index]
        text = word.text
        if word.dynamic:
            return PushTarget(PUSH_UNRESOLVED, None)
        if text in _PUSH_DRY_RUN:
            return PushTarget(PUSH_NONE, None)
        if text in _PUSH_VALUE_OPTS:
            index += 2
            continue
        if text == "--":
            positional.extend(w.text for w in args[index + 1:])
            break
        if text.startswith("-"):
            if text not in _PUSH_FLAGS and not text.startswith(_PUSH_VALUE_PREFIXES):
                return PushTarget(PUSH_UNRESOLVED, None)
        else:
            positional.append(text)
        index += 1
    if any(w.dynamic for w in args):
        return PushTarget(PUSH_UNRESOLVED, None)
    if len(positional) <= 1:
        return PushTarget(PUSH_CURRENT, None)
    if len(positional) == 2:
        return _refspec_target(positional[1])
    return PushTarget(PUSH_UNRESOLVED, None)


def option_value(args: tuple, names: frozenset) -> Optional[str]:
    """Value of the first `--name value` / `--name=value` option, if static."""
    for index, word in enumerate(args):
        if word.dynamic:
            continue
        for name in names:
            if word.text == name and index + 1 < len(args) and not args[index + 1].dynamic:
                return args[index + 1].text
            if name.startswith("--") and word.text.startswith(name + "="):
                return word.text[len(name) + 1:]
    return None


def has_flag(args: tuple, name: str) -> bool:
    return any(not w.dynamic and w.text == name for w in args)
