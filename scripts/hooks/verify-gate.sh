#!/bin/bash
#
# verify-gate.sh — thin wrapper for agent-skills' scripts/verify_gate.py.
#
# Usage (from settings.json hooks):
#   bash ~/.claude/hooks/verify-gate.sh pre-tool    # PreToolUse, matcher Bash
#   bash ~/.claude/hooks/verify-gate.sh post-tool   # PostToolUse, matcher Bash
#   bash ~/.claude/hooks/verify-gate.sh stop        # Stop
#
# The hook JSON on stdin is never parsed here; it is handed byte-for-byte to
# Python. Every failure path exits 0 silently: a broken, missing or old
# checkout must turn this hook into a no-op, never into an error shown on
# every tool call or every turn. The single exception is a deliberate
# pre-tool block, which Python signals with exit 2 and a reason line that
# starts with "[verify-gate]"; only that exact combination is passed on.
#
# The checkout comes from AGENT_SKILLS_DIR, else AGENT_SKILLS_HOME, else
# ~/Documents/agent-skills. It is resolved with `cd -P` (collapsing `..` and
# symlinks) and must lie under $HOME, because this decides which Python file
# runs on every Bash call. `set -e` is deliberately not used.

SUBCOMMAND="${1:-}"
case "$SUBCOMMAND" in
    pre-tool|post-tool|stop) ;;
    *) exit 0 ;;
esac

[ -n "$HOME" ] || exit 0
CHECKOUT="${AGENT_SKILLS_DIR:-${AGENT_SKILLS_HOME:-$HOME/Documents/agent-skills}}"
CHECKOUT=$(cd -P -- "$CHECKOUT" 2>/dev/null && pwd -P) || exit 0
HOME_REAL=$(cd -P -- "$HOME" 2>/dev/null && pwd -P) || exit 0

case "$CHECKOUT" in
    "$HOME_REAL"/*) ;;
    *) exit 0 ;;
esac

GATE="$CHECKOUT/scripts/verify_gate.py"
[ -f "$GATE" ] || exit 0
command -v python3 >/dev/null 2>&1 || exit 0

case "$SUBCOMMAND" in
    pre-tool)
        # stdout is not part of the PreToolUse contract here; stderr carries
        # the block reason and is only forwarded for a deliberate block.
        REASON=$(python3 "$GATE" pre-tool 2>&1 >/dev/null)
        STATUS=$?
        if [ "$STATUS" -eq 2 ]; then
            case "$REASON" in
                "[verify-gate]"*)
                    printf '%s\n' "$REASON" >&2
                    exit 2
                    ;;
            esac
        fi
        exit 0
        ;;
    post-tool)
        python3 "$GATE" post-tool >/dev/null 2>&1
        exit 0
        ;;
    stop)
        # Captured first so a crash after partial output prints nothing.
        OUTPUT=$(python3 "$GATE" stop 2>/dev/null) || exit 0
        [ -n "$OUTPUT" ] && printf '%s\n' "$OUTPUT"
        exit 0
        ;;
esac
exit 0
