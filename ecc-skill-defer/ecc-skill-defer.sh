#!/usr/bin/env bash
# ECC Skill Defer — progressive loading for ECC skills
# Renames SKILL.md <-> SKILL.deferred.md to control init token cost.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONF="$SCRIPT_DIR/ecc-skill-defer.conf"
MARKETPLACE_DIR="$HOME/.claude/plugins/marketplaces/everything-claude-code"
CACHE_BASE="$HOME/.claude/plugins/cache/everything-claude-code/everything-claude-code"

if [ -d "$MARKETPLACE_DIR/skills" ]; then
  SKILLS_DIR="$MARKETPLACE_DIR/skills"
else
  SKILLS_DIR="$(ls -td "$CACHE_BASE"/*/ 2>/dev/null | head -1)skills"
fi

if [ ! -d "$SKILLS_DIR" ]; then
  echo "Error: ECC skills dir not found" >&2
  exit 1
fi

read_conf() {
  grep -v '^\s*#' "$CONF" | grep -v '^\s*$' | tr -d ' '
}

cmd_apply() {
  local count=0
  while IFS= read -r skill; do
    local f="$SKILLS_DIR/$skill/SKILL.md"
    if [ -f "$f" ]; then
      mv "$f" "$SKILLS_DIR/$skill/SKILL.deferred.md"
      count=$((count + 1))
    fi
  done < <(read_conf)
  echo "Deferred $count skills"
}

cmd_restore() {
  local skill="$1"
  local f="$SKILLS_DIR/$skill/SKILL.deferred.md"
  if [ -f "$f" ]; then
    mv "$f" "$SKILLS_DIR/$skill/SKILL.md"
    echo "Restored: $skill"
  elif [ -f "$SKILLS_DIR/$skill/SKILL.md" ]; then
    echo "Already active: $skill"
  else
    echo "Not found: $skill" >&2
    exit 1
  fi
}

cmd_restore_all() {
  local count=0
  for dir in "$SKILLS_DIR"/*/; do
    local f="$dir/SKILL.deferred.md"
    if [ -f "$f" ]; then
      mv "$f" "$dir/SKILL.md"
      count=$((count + 1))
    fi
  done
  echo "Restored $count skills"
}

cmd_status() {
  local active=0 deferred=0
  for dir in "$SKILLS_DIR"/*/; do
    if [ -f "$dir/SKILL.md" ]; then
      active=$((active + 1))
    elif [ -f "$dir/SKILL.deferred.md" ]; then
      deferred=$((deferred + 1))
    fi
  done
  echo "Active: $active | Deferred: $deferred | Total: $((active + deferred))"
}

cmd_list() {
  printf "%-40s %s\n" "SKILL" "STATUS"
  printf "%-40s %s\n" "-----" "------"
  for dir in "$SKILLS_DIR"/*/; do
    local name
    name=$(basename "$dir")
    if [ -f "$dir/SKILL.md" ]; then
      printf "%-40s %s\n" "$name" "active"
    elif [ -f "$dir/SKILL.deferred.md" ]; then
      printf "%-40s %s\n" "$name" "deferred"
    fi
  done
}

case "${1:-help}" in
  apply)       cmd_apply ;;
  restore)
    if [ "${2:-}" = "--all" ]; then
      cmd_restore_all
    elif [ -n "${2:-}" ]; then
      cmd_restore "$2"
    else
      echo "Usage: $0 restore <skill-name|--all>" >&2
      exit 1
    fi
    ;;
  status)      cmd_status ;;
  list)        cmd_list ;;
  *)
    echo "Usage: $0 {apply|restore <name|--all>|status|list}"
    echo ""
    echo "  apply              Defer skills listed in ecc-skill-defer.conf"
    echo "  restore <name>     Restore a single deferred skill"
    echo "  restore --all      Restore all deferred skills"
    echo "  status             Show active/deferred counts"
    echo "  list               Show all skills with status"
    ;;
esac
