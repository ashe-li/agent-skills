# Worktree Prompt

Scope: Apply when the user asks to implement a plan (especially from `plans/active/`), or asks to execute a large-scale change (migration, refactor across 5+ files, infrastructure changes).

Action: ask about worktree via AskUserQuestion before starting implementation:

> This task modifies multiple infrastructure files. Would you like to work in a worktree for isolation?
>
> 1. **Yes** — create worktree via `/worktree create <name>`
> 2. **No** — work in the current directory

Skip the prompt when:
- The user has already stated a preference (use or skip worktree) for this task
- The current directory is already a worktree (`.git` is a file, not a directory — worktrees use a `.git` file pointing to the main repo's `.git/worktrees/`)
- The task is small (single file, quick fix)

## Worktree 路徑慣例

Use the project's common parent directory as a sibling, name format: `<project>-<slug>/`. Avoid `.claude/worktrees/` (the `EnterWorktree` default).

```
~/Documents/
├── my-project/                ← 主 repo
├── my-project-preview/        ← worktree（preview-environments）
├── my-project-optimization/   ← worktree（performance）
└── my-other-project/          ← worktree（content-submission）
```

Why: sibling layout keeps worktrees alongside the main checkout, matching the user's existing project organization and avoiding hidden paths.

Create via `git worktree add` from the main repo (ensures correct path):

```bash
git worktree add ~/Documents/<project>-<slug> -b <branch-name>
```
