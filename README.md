# agent-skills

My personal [Agent Skills](https://agentskills.io/) collection for Claude Code.

## Skills

### `/pr` — PR 自動化

總結當前工作、commit、推送並建立或更新 PR。自動將對話脈絡寫入 PR description，確保 reviewer 能快速理解背景。

**Features:**
- Git 分析 + 對話脈絡雙重分析，確保 PR description 同時涵蓋 what 和 why
- 自動 code review（安全性、正確性、debug code、TypeScript 型別）
- 自動偵測是否已有 open PR，決定建立或更新
- PR description 包含 Summary、Context、Changes、Test plan 四個區塊

## Install

```bash
npx skills add ashe-li/agent-skills --global
```

## Usage

```
/pr          # 自動偵測是否有 open PR，沒有就建新的
/pr 1234     # 更新指定 PR 的 description
```
