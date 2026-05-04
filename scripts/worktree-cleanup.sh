#!/usr/bin/env bash
# worktree-cleanup.sh — Cross-repo merged worktree cleanup.
#
# Scans ~/Documents (or $WORKTREE_ROOT) for sibling git worktrees,
# resolves each to its parent repo, queries GitHub PR state via `gh`,
# and removes worktrees whose PR is MERGED or CLOSED.
#
# Usage:
#   worktree-cleanup.sh                # dry-run scan, exit 0
#   worktree-cleanup.sh --apply        # remove merged/closed worktrees (skips dirty)
#   worktree-cleanup.sh --apply --force-dirty   # also remove dirty worktrees (DANGEROUS)
#   worktree-cleanup.sh --root /path   # override scan root (default: ~/Documents)
#
# Exit codes:
#   0  success (or nothing to do)
#   1  invalid arguments
#   2  scan failed (e.g. gh not authenticated)
#
# Safety:
#   - Default mode is dry-run. Destructive actions require --apply.
#   - Worktrees with uncommitted changes are skipped unless --force-dirty.
#   - Skips the main worktree of each repo (only siblings are candidates).
#   - PRs whose state cannot be determined are reported as `unknown` and skipped
#     (require --apply --include-unknown to act on them).

set -euo pipefail

ROOT="${HOME}/Documents"
APPLY=0
FORCE_DIRTY=0
INCLUDE_UNKNOWN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --force-dirty) FORCE_DIRTY=1 ;;
    --include-unknown) INCLUDE_UNKNOWN=1 ;;
    --root) ROOT="$2"; shift ;;
    -h|--help)
      sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//' | head -n -1
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
  shift
done

command -v gh >/dev/null || { echo "gh CLI required" >&2; exit 2; }
command -v git >/dev/null || { echo "git required" >&2; exit 2; }

# tab-separated columns: state, path, branch, pr, parent, dirty
candidates=()

while IFS= read -r gitfile; do
  wt=$(dirname "$gitfile")
  branch=$(git -C "$wt" symbolic-ref --short HEAD 2>/dev/null || echo "")
  parent=$(git -C "$wt" rev-parse --git-common-dir 2>/dev/null | sed 's|/.git$||')
  [[ -z "$branch" || -z "$parent" || "$parent" == "$wt" ]] && continue

  dirty=""
  if [[ -n $(git -C "$wt" status --porcelain 2>/dev/null) ]]; then
    dirty="dirty"
  fi

  # Query PR state from the parent repo (gh uses cwd's remote).
  pr_json=$(gh -R "$(git -C "$parent" remote get-url origin 2>/dev/null | sed -E 's|.*github.com[:/]([^/]+/[^/.]+)(\.git)?|\1|')" \
              pr list --head "$branch" --state all --json number,state,mergedAt --limit 1 2>/dev/null || echo "")

  if [[ -z "$pr_json" || "$pr_json" == "[]" ]]; then
    state="no-pr"
    pr_num="-"
  else
    state=$(printf '%s' "$pr_json" | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d[0]["state"] if d else "no-pr")' 2>/dev/null || echo "unknown")
    pr_num=$(printf '%s' "$pr_json" | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d[0]["number"] if d else "-")' 2>/dev/null || echo "-")
  fi

  candidates+=("$(printf '%s\t%s\t%s\t%s\t%s\t%s' "$state" "$wt" "$branch" "$pr_num" "$parent" "$dirty")")
done < <(find "$ROOT" -maxdepth 2 -name ".git" -type f 2>/dev/null)

if [[ ${#candidates[@]} -eq 0 ]]; then
  echo "No sibling worktrees found under $ROOT"
  exit 0
fi

printf '\n%-7s %-12s %-65s %-45s %-6s %s\n' "ACTION" "STATE" "PATH" "BRANCH" "PR" "FLAGS"
printf '%s\n' "$(printf -- '-%.0s' {1..150})"

removed=0
skipped_dirty=0
skipped_other=0

for line in "${candidates[@]}"; do
  IFS=$'\t' read -r state wt branch pr_num parent dirty <<< "$line"

  action="keep"
  flags=""
  case "$state" in
    MERGED|CLOSED) action="remove" ;;
    unknown) [[ $INCLUDE_UNKNOWN -eq 1 ]] && action="remove" || action="keep" ;;
    *) action="keep" ;;
  esac

  if [[ "$action" == "remove" && -n "$dirty" ]]; then
    flags="dirty"
    [[ $FORCE_DIRTY -eq 0 ]] && action="skip-dirty"
  fi

  printf '%-7s %-12s %-65s %-45s %-6s %s\n' "$action" "$state" "$wt" "$branch" "$pr_num" "$flags"

  [[ $APPLY -eq 0 ]] && continue
  case "$action" in
    remove)
      if git -C "$parent" worktree remove "$wt" --force 2>/dev/null; then
        removed=$((removed+1))
        # Best-effort branch delete; ignore failures (unmerged, not local, etc).
        git -C "$parent" branch -D "$branch" >/dev/null 2>&1 || true
      else
        echo "  ! failed to remove $wt" >&2
      fi
      ;;
    skip-dirty) skipped_dirty=$((skipped_dirty+1)) ;;
    keep) skipped_other=$((skipped_other+1)) ;;
  esac
done

echo
if [[ $APPLY -eq 0 ]]; then
  echo "Dry-run only. Re-run with --apply to remove worktrees marked 'remove'."
else
  echo "Removed: $removed   Skipped(dirty): $skipped_dirty   Kept: $skipped_other"
  echo "Run \`git worktree prune\` in each parent repo if any metadata was left behind."
fi
