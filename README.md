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

### `/update` — 更新知識庫

將本次 session 的工作沉澱為文件與可復用知識。依序執行文件更新、品質審查、模式提取三個階段。

**Pipeline:** doc-updater → code-reviewer → learn-eval

**Features:**
- 自動掃描變更並更新 docs/codemaps/README
- code-reviewer 審查文件品質，確保文件與程式碼一致
- learn-eval 提取可復用 patterns，自動評估品質後寫入知識庫
- 與 `/pr` 分離設計：`/update` 負責知識沉澱，`/pr` 負責 git 輸出

### `/design` — 開發設計

接收功能需求，自動盤點 ECC 資源，透過多個 agent 協作建立完整實作計畫。輸出 plan.md 供使用者確認後才進入實作。

**Pipeline:** ECC 資源盤點 → planner → architect → doc-updater → code-reviewer → plan.md

**Features:**
- 自動盤點可用的 agents/skills/commands，將合適的資源整合進計畫步驟
- planner 建立實作計畫，architect 審查架構設計
- 輸出結構化 plan.md，包含 ECC 資源對照表、實作步驟、風險評估
- 不自動執行實作，使用者確認後才能開始

### `/assist` — 萬用助手

自動分析情境、盤點 ECC 資源、智慧路由至最佳 agent pipeline。適合不確定該用哪個工具時使用。

**智慧路由規則：**

| 情境 | Pipeline |
|------|----------|
| 新功能需求 | planner → architect → tdd-guide → code-reviewer |
| Bug 修復 | planner → tdd-guide → code-reviewer |
| 未 commit 變更需 review | code-reviewer → security-reviewer |
| Build 失敗 | build-error-resolver |
| 重構 | architect → refactor-cleaner → code-reviewer |
| 文件更新 | doc-updater → code-reviewer |
| 不確定 | 互動式選擇 |

**Features:**
- 自動偵測專案類型（Go/Python/Node.js），附加語言專用 reviewer
- 使用 handoff protocol 在 agents 間傳遞 context
- 無法判斷時透過互動式選單讓使用者選擇 pipeline

## Install

```bash
npx skills add ashe-li/agent-skills --global
```

## Usage

```
/pr              # 自動偵測是否有 open PR，沒有就建新的
/pr 1234         # 更新指定 PR 的 description
/update          # 更新知識庫（docs + patterns）
/design <需求>   # 建立實作計畫
/assist          # 自動偵測情境並執行最佳 pipeline
/assist <任務>   # 指定任務描述
```
