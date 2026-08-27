#!/bin/bash
#
# plan-run-stop.sh — Stop hook wrapper for agent-skills' plan_runner.py.
#
# This script is intentionally minimal: it does not parse or touch the
# Stop hook's stdin JSON payload at all. It is handed through byte-for-byte
# to `plan_runner.py hook-stop`, which owns all reading/writing/output for
# the Stop hook. Keeping this wrapper free of stdin parsing / string
# interpolation avoids any risk of path or argument injection from
# hook input.
#
# AGENT_SKILLS_DIR points at the agent-skills checkout that provides
# scripts/plan_runner.py. It defaults to the primary checkout at
# ~/Documents/agent-skills. Override it only for deliberate, temporary
# testing against another checkout (e.g. a sibling worktree) — see
# scripts/hooks/README.md in that checkout for the install/dev-pointer
# procedure and its risks.
#
# Every failure path below exits 0 (silently) so a missing/broken
# agent-skills checkout never turns a Stop hook into a visible error.
# `set -e` is deliberately NOT used, so a non-zero exit mid-script can't
# be misread as this script's own intended failure exit.
#
# This matters most for a checkout that predates the `hook-stop`
# subcommand: argparse rejects the unknown choice and exits 2, and exit 2
# from a Stop hook is a *blocking* error — every turn of every session on
# this machine would be blocked with an argparse usage dump fed back to
# the model. Hence the `|| exit 0` below, which is a hard requirement and
# not defensive garnish: it makes an old, a broken, or a mid-rebase
# checkout degrade to "this hook does nothing" instead of to a
# machine-wide outage. stderr is discarded for the same reason — an
# unsupported checkout must be quiet, not noisy, on every single turn.

AGENT_SKILLS_DIR="${AGENT_SKILLS_DIR:-$HOME/Documents/agent-skills}"

# The variable is never eval'd or word-split (it is quoted everywhere), so
# this is not an injection guard. It bounds *which* Python file this hook
# will execute every turn of every session: anything that can set this
# process's environment (direnv, settings.json's `env` block) could
# otherwise point it at an arbitrary checkout. Outside $HOME we do nothing,
# matching every other failure path here.
case "$AGENT_SKILLS_DIR" in
    "$HOME"/*) ;;
    *) exit 0 ;;
esac

RUNNER="$AGENT_SKILLS_DIR/scripts/plan_runner.py"

if [ ! -f "$RUNNER" ]; then
    exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
    exit 0
fi

# stdout is passed straight through (no shell variable round-trip, which
# would mangle escape sequences inside the JSON reason string).
python3 "$RUNNER" hook-stop 2>/dev/null || exit 0

exit 0
