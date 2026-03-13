# Worktree Prompt

When the user asks to **implement a plan** (especially from `plans/active/`), or asks to execute a large-scale change (migration, refactoring across 5+ files, infrastructure changes), **proactively ask about worktree** before starting any implementation.

Use AskUserQuestion:

> This task modifies multiple infrastructure files. Would you like to work in a worktree for isolation?
>
> 1. **Yes** — create worktree via `/worktree create <name>`
> 2. **No** — work in the current directory

Skip the prompt if:
- The user already explicitly said to use (or not use) a worktree
- The current directory is already a worktree (`.git` is a file, not a directory — worktrees use a `.git` file pointing to the main repo's `.git/worktrees/`)
- The task is small (single file, quick fix)
