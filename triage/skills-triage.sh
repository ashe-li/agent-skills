#!/usr/bin/env bash
set -euo pipefail

LEARNED_DIR="$HOME/.claude/skills/learned"
RETIRED_DIR="$HOME/.claude/skills/retired"
LOG_FILE="$RETIRED_DIR/RETIRED_LOG.md"

usage() {
  cat <<EOF
Usage: skills-triage.sh <command> <args>

Commands:
  retire <name> <reason> [tier] [delta]   Move skill from learned to retired
  restore <name>                          Move skill from retired back to learned
  status                                  List all retired skills
EOF
  exit 1
}

retire() {
  local name="$1"
  local reason="$2"
  local tier="${3:-}"
  local delta="${4:-}"
  local src="$LEARNED_DIR/${name}.md"
  local dst="$RETIRED_DIR/${name}.md"

  if [[ ! -f "$src" ]]; then
    echo "ERROR: $src not found" >&2
    exit 1
  fi
  if [[ -f "$dst" ]]; then
    echo "ERROR: $dst already exists (already retired?)" >&2
    exit 1
  fi

  mv "$src" "$dst"

  local date
  date=$(date +%Y-%m-%d)
  printf '| `%s` | %s | %s | %s | %s |\n' \
    "$name" "$date" "$reason" "$tier" "$delta" >> "$LOG_FILE"

  echo "Retired: $name -> $RETIRED_DIR/"
}

restore() {
  local name="$1"
  local src="$RETIRED_DIR/${name}.md"
  local dst="$LEARNED_DIR/${name}.md"

  if [[ ! -f "$src" ]]; then
    echo "ERROR: $src not found in retired" >&2
    exit 1
  fi
  if [[ -f "$dst" ]]; then
    echo "ERROR: $dst already exists in learned" >&2
    exit 1
  fi

  mv "$src" "$dst"
  echo "Restored: $name -> $LEARNED_DIR/"
}

status() {
  local count
  count=$(find "$RETIRED_DIR" -name '*.md' ! -name 'RETIRED_LOG.md' | wc -l | tr -d ' ')
  echo "Retired skills: $count"
  echo ""

  if [[ "$count" -gt 0 ]]; then
    find "$RETIRED_DIR" -name '*.md' ! -name 'RETIRED_LOG.md' -exec basename {} .md \; | sort
  fi

  echo ""
  echo "--- Log ---"
  cat "$LOG_FILE"
}

[[ $# -lt 1 ]] && usage

case "$1" in
  retire)
    [[ $# -lt 3 ]] && { echo "Usage: retire <name> <reason> [tier] [delta]"; exit 1; }
    retire "$2" "$3" "${4:-}" "${5:-}"
    ;;
  restore)
    [[ $# -lt 2 ]] && { echo "Usage: restore <name>"; exit 1; }
    restore "$2"
    ;;
  status)
    status
    ;;
  *)
    usage
    ;;
esac
