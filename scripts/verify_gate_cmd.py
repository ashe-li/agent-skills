#!/usr/bin/env python3
"""Conservative shell command scanner for the verify gate hook (stdlib only).

The gate hook must tell whether `gh pr create`, `git push` or
`verify_engine.py pr register` appears in *command position* of a Bash tool
command. A match inside a comment, a heredoc body or a quoted argument
(`echo "gh pr create"`, `git commit -m "... gh pr create ..."`) does not count.
Commands inside `$(...)`, backticks and `( ... )` subshells do run, so they are
scanned too (with their own cwd), as are commands behind the wrappers
`command`, `env`, `timeout`, `nohup`, `exec` and `time`.

The scanner understands quotes, backslash escapes, comments, `${...}`,
heredocs (including heredocs inside `"$(cat <<'EOF' ...)"`), redirections and
the list operators `;`, `&&`, `||`, `|`, `&` and newline. A simple assignment
on its own line (`E="python3 .../verify_engine.py"`) is remembered so that a
later `$E pr register` in the same command is recognised. Anything it does not
understand (brace groups, process substitution, `;;`, unterminated quotes)
makes the whole command unparseable; an unparseable command matches nothing,
and `mentions_pr_create` lets the hook record that coverage gap. Nothing here
runs a process.
"""
from __future__ import annotations

import os
import re
from typing import NamedTuple, Optional

from verify_gate_refs import *  # noqa: F401,F403  (push refspec parsing, re-exported)
from verify_gate_refs import _branch_name

MAX_COMMAND_CHARS = 200_000
KIND_PR_CREATE, KIND_PUSH, KIND_REGISTER = "gh-pr-create", "git-push", "pr-register"
KIND_PR_CREATE_NOOP = "gh-pr-create-noop"  # --help / -h / --dry-run: creates nothing
KIND_HEAD_MOVE = "git-head-move"  # internal: a later checkout / switch in the same command
DYNAMIC_VALUE = "\x00dynamic"  # option_value: the option is present but its value is not static

_OPERATORS = ("&&", "||", "|&", ";", "|", "&")
_CWD_KEEPING_OPS = frozenset({"&&", ";", "\n", ""})
_RESERVED = frozenset({"fi", "for", "done", "case", "esac", "select", "function", "[[", "]]",
                       "}", "coproc", "in"})
# The next word after these is in command position (`if gh ...`, `then gh ...`, `{ gh ...`).
_COMMAND_KEYWORDS = frozenset({"if", "then", "else", "elif", "while", "until", "do", "{", "!"})
_CONTROL_START = _COMMAND_KEYWORDS | frozenset({"for", "case", "select"})
_CONDITIONAL_OPS = frozenset({"&&", "||", "|"})
_VALUE_TOUCHERS = frozenset({"unset", "read", "for", "export", "local", "declare", "typeset",
                             "readonly", "mapfile", "readarray", "getopts"})
_SUDO_NOARG = frozenset({"-E", "-H", "-n", "-S", "-k", "-A", "-P", "-b", "--preserve-env",
                         "--non-interactive", "--stdin", "--askpass", "--set-home"})
_SUDO_VALUE = frozenset({"-u", "-g", "-U", "-C", "-D", "-p", "-r", "-t", "-T", "--user",
                         "--group", "--other-user", "--chdir", "--prompt"})
_NICE_VALUE = frozenset({"-n", "--adjustment"})
_NICE_LEGACY = re.compile(r"^-\d+$")
_PR_CREATE_NOOP = frozenset({"--help", "-h", "--dry-run"})
# Only these change which branch is checked out; commit, reset, stash ... keep the name.
_GIT_HEAD_MOVERS = frozenset({"checkout", "switch"})
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")
_NAME_CHAR = re.compile(r"[A-Za-z0-9_]")
_NAME_ONLY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PYTHON = re.compile(r"^python(?:3(?:\.\d+)?)?$")
_PYTHON_SAFE_FLAGS = frozenset({"-I", "-u", "-B", "-E", "-s", "-S", "-O", "-OO", "-q"})
_GIT_NOARG_OPTS = frozenset({"--no-pager", "--paginate", "-p", "-P", "--no-replace-objects",
                             "--literal-pathspecs", "--no-optional-locks", "--no-advice"})
_WORD_VAR = re.compile(r'^"?\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))"?$')
_ENV_NOARG = frozenset({"-i", "-", "--ignore-environment", "-0", "--null"})
_ENV_VALUE = frozenset({"-u", "--unset"})
_TIMEOUT_NOARG = frozenset({"--preserve-status", "--foreground", "-v", "--verbose"})
_TIMEOUT_VALUE = frozenset({"-s", "--signal", "-k", "--kill-after"})
_EXEC_NOARG = frozenset({"-c", "-l"})
_PR_CREATE_TEXT = re.compile(r"(?<![\w./-])gh\s+pr\s+create(?![\w-])")
_HEREDOC_BODY = re.compile(r"<<-?\s*(['\"]?)([A-Za-z0-9_]+)\1[^\n]*\n.*?^\s*\2[ \t]*$", re.S | re.M)


class Unparseable(Exception):
    """The command uses a construct this scanner does not model."""


class Word(NamedTuple):
    text: str
    dynamic: bool  # contains an expansion whose value is unknown statically
    var: Optional[str] = None  # set when the whole word is `$NAME` or `${NAME}`


class Segment(NamedTuple):
    words: tuple
    op: str  # operator that ends the segment; "" at the end of input
    nested: tuple = ()  # segment tuples of `$(...)`, backticks and subshells in it


class Invocation(NamedTuple):
    kind: str
    cwd: Optional[str]  # directory the command runs in; None when unknown
    args: tuple         # Words after the recognised subcommand
    moved: bool = False  # KIND_PUSH: a later segment may switch the checked-out branch





# --- scanner ----------------------------------------------------------------------

class _Scanner:
    """Single-use lexer; only `segments()` is meant to be called from outside."""

    def __init__(self, text: str, assigned: Optional[dict] = None,
                 in_control: bool = False) -> None:
        self.s, self.i, self.n = text, 0, len(text)
        self.assigned: dict = dict(assigned or {})  # NAME -> static value, or None if unknown
        self.in_control, self.prev_op = in_control, ""
        self.heredocs: list = []
        self.word: Optional[list] = None
        self.dynamic = False
        self.pending = ""  # "drop": next word is a redirect target; "heredoc[-]": delimiter
        self.words: list = []
        self.segments_out: list = []
        self.nested: list = []
        self.mark = self.word_start = 0

    # word assembly
    def _start(self) -> None:
        if self.word is None:
            self.word, self.dynamic, self.word_start = [], False, self.mark

    def _add(self, text: str, dynamic: bool = False) -> None:
        self._start()
        self.word.append(text)
        self.dynamic = self.dynamic or dynamic

    def _finish_word(self) -> None:
        if self.word is None:
            return
        found = _WORD_VAR.match(self.s[self.word_start:self.i].strip())
        var = (found.group(1) or found.group(2)) if found else None
        word = Word("".join(self.word), self.dynamic, var)
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
        segment = Segment(tuple(self.words), op, tuple(self.nested))
        self.segments_out.append(segment)
        for name, value in assignment_effects(segment.words, self.prev_op, op, self.in_control):
            self.assigned[name] = None if value is None or value.dynamic else value.text
        self.in_control = self.in_control or starts_control(segment.words)
        self.prev_op = op
        self.words, self.nested = [], []

    def _forget_changed(self, inner: "_Scanner") -> None:
        """Assignments made inside a subshell or substitution make the name unknown."""
        for name in set(inner.assigned) | set(self.assigned):
            if inner.assigned.get(name) != self.assigned.get(name):
                self.assigned[name] = None

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
        inner = _Scanner(self.s, self.assigned, self.in_control)
        inner.i = self.i
        self.i = inner.scan(nested=True)
        self.nested.append(tuple(inner.segments_out))
        self._forget_changed(inner)

    def _skip_backtick(self) -> None:
        j = self.i
        while j < self.n and self.s[j] != "`":
            j += 2 if self.s[j] == "\\" else 1
        if j >= self.n:
            raise Unparseable("unterminated backtick")
        inner = _Scanner(self.s[self.i:j].replace("\\`", "`"), self.assigned, self.in_control)
        try:
            inner.scan(nested=False)
        except (Unparseable, IndexError, StopIteration):
            inner.segments_out = []
        if inner.segments_out:
            self.nested.append(tuple(inner.segments_out))
        self._forget_changed(inner)
        self.i = j + 1

    def _skip_braces(self) -> str:
        """Skip a `${...}` body (self.i just past `${`); return the body text."""
        depth, j = 1, self.i
        while j < self.n and depth:
            depth += {"{": 1, "}": -1}.get(self.s[j], 0)
            j += 1
        if depth:
            raise Unparseable("unterminated ${")
        body, self.i = self.s[self.i:j - 1], j
        return body

    def _expansion(self, name: Optional[str], quoted: bool) -> None:
        """Add a variable's static value when it was assigned earlier, else mark dynamic."""
        value = self.assigned.get(name) if name else None
        if value is not None and (quoted or not any(ch.isspace() for ch in value)):
            self._add(value)
        else:
            self._add("", dynamic=True)

    def _dollar(self, quoted: bool = False) -> bool:
        """Handle `$...` at self.i; return False when `$` is a literal."""
        nxt, name = self.s[self.i + 1:self.i + 2], None
        if nxt == "(":
            self.i += 2
            self._skip_nested()
        elif nxt == "{":
            self.i += 2
            body = self._skip_braces()
            name = body if _NAME_ONLY.match(body) else None
        elif nxt and (_NAME_CHAR.match(nxt) or nxt in "@*#?$!-"):
            start = self.i + 1
            self.i += 2
            while nxt not in "@*#?$!-" and self.i < self.n and _NAME_CHAR.match(self.s[self.i]):
                self.i += 1
            name = self.s[start:self.i] if _NAME_ONLY.match(self.s[start:self.i]) else None
        else:
            return False
        self._expansion(name, quoted)
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
            elif c == "$" and self._dollar(quoted=True):
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
            c, self.mark = self.s[self.i], self.i
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
            elif c == "(" and self.word is None and not self.words:
                self.i += 1
                self._skip_nested()  # a `( ... )` subshell at command start
            elif c in "()":
                if not nested:
                    raise Unparseable("parenthesis outside command position")
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
    return tuple(seg for seg in scanner.segments_out
                 if seg.words or seg.nested or seg.op not in ("\n", ""))


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
        elif text in _GIT_HEAD_MOVERS:
            return Invocation(KIND_HEAD_MOVE, cwd, words[index + 1:])
        else:
            return None  # another subcommand, or an option such as --git-dir
    return None


def _engine_script(word: Word) -> bool:
    """The engine script, possibly behind `${...}` (only the static tail is known)."""
    return word.text.endswith("verify_engine.py") and (
        word.dynamic or os.path.basename(word.text) == "verify_engine.py")


def _engine_invocation(words: tuple, cwd: Optional[str]) -> Optional[Invocation]:
    """`python3 <...>/verify_engine.py pr register`, or `$E pr register`.

    A bare variable as the command name is accepted here; the hook then requires
    the engine's own success output before it links anything.
    """
    index = 0
    if _PYTHON.match(os.path.basename(words[0].text)):
        index = 1
        while index < len(words) and words[index].text in _PYTHON_SAFE_FLAGS:
            index += 1
        if index >= len(words) or not _engine_script(words[index]):
            return None
    elif not (_engine_script(words[0]) or (words[0].dynamic and not words[0].text)):
        return None
    rest = words[index + 1:]
    if len(rest) >= 2 and not rest[0].dynamic and not rest[1].dynamic \
            and rest[0].text == "pr" and rest[1].text == "register":
        return Invocation(KIND_REGISTER, cwd, rest[2:])
    return None


def _skip_options(words: tuple, noarg: frozenset, valued: frozenset) -> Optional[tuple]:
    """Drop leading options of a wrapper; None on an option it does not know."""
    index = 0
    while index < len(words) and words[index].text.startswith("-") and words[index].text != "-":
        text = words[index].text
        if words[index].dynamic:
            return None
        if text in valued:
            index += 2
        elif text in noarg or any(text.startswith(v + "=") for v in valued if v.startswith("--")):
            index += 1
        else:
            return None
    return words[index:]


def _unwrap_once(words: tuple) -> Optional[tuple]:
    """Words after one wrapper; the same words when there is none; None to give up."""
    name = words[0].text
    rest = words[1:]
    if name in _COMMAND_KEYWORDS or name in ("nohup", "builtin"):
        return rest
    if name == "sudo":
        return _skip_options(rest, _SUDO_NOARG, _SUDO_VALUE)
    if name == "nice":
        rest = rest[1:] if rest and _NICE_LEGACY.match(rest[0].text) else rest
        return _skip_options(rest, frozenset(), _NICE_VALUE)
    if name == "time":
        return _skip_options(rest, frozenset({"-p"}), frozenset())
    if name == "exec":
        return _skip_options(rest, _EXEC_NOARG, frozenset({"-a"}))
    if name == "command":
        if rest and rest[0].text in ("-v", "-V"):
            return None  # a lookup, not an execution
        return _skip_options(rest, frozenset({"-p"}), frozenset())
    if name == "env":
        rest = _skip_options(rest, _ENV_NOARG, _ENV_VALUE)
        return None if rest is None else _strip_assignments(rest)
    if name == "timeout":
        rest = _skip_options(rest, _TIMEOUT_NOARG, _TIMEOUT_VALUE)
        return rest[1:] if rest else None
    return words


def unwrap(words: tuple) -> tuple:
    """Strip assignments and wrapper commands in front of the real command."""
    words = _strip_assignments(words)
    while words and not words[0].dynamic:
        unwrapped = _unwrap_once(words)
        if unwrapped is None:
            return ()
        if unwrapped is words:
            return words
        words = _strip_assignments(unwrapped)
    return words


def recognise(words: tuple, cwd: Optional[str], home: Optional[str]) -> Optional[Invocation]:
    """Invocation for one simple command, or None when it is none of the three."""
    words = unwrap(words)
    if not words or words[0].text in _RESERVED and not words[0].dynamic:
        return None
    if words[0].dynamic:
        return _engine_invocation(words, cwd)
    name = os.path.basename(words[0].text)
    if name == "gh":
        if (len(words) >= 3 and not words[1].dynamic and not words[2].dynamic
                and words[1].text == "pr" and words[2].text == "create"):
            noop = any(not w.dynamic and w.text in _PR_CREATE_NOOP for w in words[3:])
            return Invocation(KIND_PR_CREATE_NOOP if noop else KIND_PR_CREATE, cwd, words[3:])
        return None
    if name == "git":
        return _git_invocation(words, cwd, home)
    return _engine_invocation(words, cwd)


def _next_cwd(seg: Segment, cwd: Optional[str], home: Optional[str]) -> Optional[str]:
    """Directory after this segment. A `cd` to a directory that does not exist
    fails: after `&&` the rest does not run; after `;` or a newline it runs in a
    directory we cannot know, so it becomes None."""
    words = unwrap(seg.words)
    if not words or words[0].dynamic or words[0].text != "cd":
        return cwd
    target = _resolve_dir(cwd, words[1], home) if len(words) == 2 else None
    if seg.op not in _CWD_KEEPING_OPS or target is None:
        return None
    return target if seg.op == "&&" or os.path.isdir(target) else None


def _expand(words: tuple, assigned: dict) -> tuple:
    """Replace a leading `$NAME` by the words of `NAME=...` seen earlier."""
    value = assigned.get(words[0].var) if words and words[0].var else None
    if value is None:
        return words
    tokens = tuple(Word(t, value.dynamic) for t in value.text.split())
    return tokens + words[1:] if tokens else words


def assignment_words(words: tuple) -> tuple:
    """The assignments of a segment that only assigns (optionally after `export`)."""
    body = words[1:] if words and not words[0].dynamic and words[0].text == "export" else words
    if not body or _strip_assignments(body):
        return ()
    return body


def starts_control(words: tuple) -> bool:
    """The segment opens or continues a compound command (`if`, `then`, `for`, `{` ...)."""
    words = _strip_assignments(words)
    return bool(words) and not words[0].dynamic and words[0].text in _CONTROL_START


def _touched_names(words: tuple) -> tuple:
    """Names whose value `unset`, `read`, `for X in`, `export -n`, `local` ... may change."""
    if not words or words[0].dynamic or words[0].text not in _VALUE_TOUCHERS:
        return ()
    if words[0].text == "for":
        candidates = words[1:2]
    elif words[0].text == "export" and not any(w.text == "-n" for w in words[1:]):
        return ()
    else:
        candidates = tuple(w for w in words[1:] if not w.text.startswith("-"))
    names = (w.text.partition("=")[0].rstrip("+") for w in candidates if not w.dynamic)
    return tuple(n for n in names if _NAME_ONLY.match(n))


def assignment_effects(words: tuple, prev_op: str, op: str, in_control: bool) -> tuple:
    """(name, Word or None) per variable the segment sets; None means "now unknown".

    Only unconditional assignments keep a value: not after a keyword, not inside
    a compound command, not right of `&&`, `||` or `|`, not left of `|`.
    """
    body, keyword = words, False
    while body and not body[0].dynamic and body[0].text in _COMMAND_KEYWORDS:
        body, keyword = body[1:], True
    assigns = assignment_words(body)
    if not assigns:
        return tuple((name, None) for name in _touched_names(body))
    conditional = keyword or in_control or prev_op in _CONDITIONAL_OPS or op == "|"
    effects = []
    for word in assigns:
        name, _, value = word.text.partition("=")
        known = not (conditional or name.endswith("+"))
        effects.append((name.rstrip("+"), Word(value, word.dynamic) if known else None))
    return tuple(effects)


def _is_cd(seg: Segment) -> bool:
    words = unwrap(seg.words)
    return bool(words) and not words[0].dynamic and words[0].text == "cd"


def _walk(segments: tuple, cwd: Optional[str], home: Optional[str], assigned: dict,
          found: list, in_control: bool = False) -> None:
    """Recognise invocations in order; a `cd` inside a compound command makes every
    later directory unknown (its scope is not modelled)."""
    current, prev_op, lost = cwd, "", False
    for seg in segments:
        here = None if lost else current
        for group in seg.nested:
            inner = dict(assigned)
            _walk(group, here, home, inner, found, in_control)
            assigned.update({k: None for k in inner if inner[k] is not assigned.get(k)})
        invocation = recognise(_expand(seg.words, assigned), here, home)
        if invocation is not None:
            found.append(invocation)
        for name, value in assignment_effects(seg.words, prev_op, seg.op, in_control):
            assigned[name] = value
        control = in_control or starts_control(seg.words)
        lost = lost or (_is_cd(seg) and control)
        in_control, prev_op = control, seg.op
        current = _next_cwd(seg, current, home)


def find_invocations(command: str, cwd: Optional[str], home: Optional[str]) -> tuple:
    """Every recognised invocation, including those inside `$(...)` and subshells."""
    segments = parse_segments(command)
    if segments is None:
        return ()
    found: list = []
    _walk(segments, cwd, home, {}, found)
    marked = []
    for index, invocation in enumerate(found):
        if invocation.kind == KIND_HEAD_MOVE:
            continue
        later = any(i.kind == KIND_HEAD_MOVE for i in found[index + 1:])
        marked.append(invocation._replace(moved=later) if invocation.kind == KIND_PUSH
                      else invocation)
    return tuple(marked)


def mentions_pr_create(command: str) -> bool:
    """`gh pr create` outside quotes, comments and heredoc bodies (best effort)."""
    if not isinstance(command, str):
        return False
    text = _HEREDOC_BODY.sub(" ", command)
    text = re.sub(r"'[^']*'", " ", text)
    text = re.sub(r'"(?:\\.|[^"\\])*"', " ", text)
    text = re.sub(r"(?m)(^|[ \t;&|])#[^\n]*", " ", text)
    return bool(_PR_CREATE_TEXT.search(text))


# --- argument helpers --------------------------------------------------------------





def option_value(args: tuple, names: frozenset) -> Optional[str]:
    """Value of the first `--name value` / `--name=value` option.

    Returns DYNAMIC_VALUE when the option is present but its value contains an
    expansion, so callers can refuse to guess instead of falling back.
    """
    for index, word in enumerate(args):
        for name in names:
            if word.text == name and not word.dynamic and index + 1 < len(args):
                value = args[index + 1]
                return DYNAMIC_VALUE if value.dynamic else value.text
            if name.startswith("--") and word.text.startswith(name + "="):
                return DYNAMIC_VALUE if word.dynamic else word.text[len(name) + 1:]
    return None
