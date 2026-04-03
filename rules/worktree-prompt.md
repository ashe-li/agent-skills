# Worktree Prompt

When the user asks to **implement a plan** (especially from `plans/active/`), or asks to execute a large-scale change (migration, refactoring across 5+ files, infrastructure changes), **proactively ask about worktree** before starting any implementation.

Use AskUserQuestion:

> This task modifies multiple infrastructure files. Would you like to work in a worktree for isolation?
>
> 1. **Yes** — create worktree via `/worktree create <name>`
> 2. **No** — work in the current directory

## Worktree 路徑慣例

**禁止使用 `.claude/worktrees/`（EnterWorktree 預設路徑）。**

Worktree 必須建在專案的共同母目錄下作為 sibling，命名格式：`<project>-<slug>/`

```
~/Documents/
├── vocus-web-ui/              ← 主 repo
├── vocus-web-ui-preview/      ← worktree（preview-environments）
├── vocus-web-ui-sw-optimization/  ← worktree（sw-cache）
└── vocus-trends-submission/   ← worktree（content-submission）
```

建立方式：使用 `git worktree add` 而非 `EnterWorktree` tool，確保路徑正確：

```bash
# 從主 repo 執行
git worktree add ~/Documents/<project>-<slug> -b <branch-name>
```

Skip the prompt if:
- The user already explicitly said to use (or not use) a worktree
- The current directory is already a worktree (`.git` is a file, not a directory — worktrees use a `.git` file pointing to the main repo's `.git/worktrees/`)
- The task is small (single file, quick fix)
