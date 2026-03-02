---
name: assist
description: 萬用助手 — 自動分析情境、盤點 ECC 資源、智慧路由至最佳 agent pipeline，一鍵完成複雜工作流。
allowed-tools: Bash, Read, Glob, Grep, Edit, Write, Agent, AskUserQuestion
argument-hint: [任務描述，留空則自動偵測情境]
---

# /assist — 萬用助手

自動分析當前情境，盤點可用 ECC 資源，智慧選擇最佳 agent pipeline 並執行。適合不確定該用哪個工具時使用。

## Step 1: 情境分析

收集當前工作環境的完整資訊：

### 1a. Git 狀態

```bash
git status
git diff --stat
git log --oneline -5
```

- 是否有未 commit 的變更？變更了哪些檔案？
- 最近的 commit 在做什麼？
- 當前 branch 名稱和狀態

### 1b. 專案偵測

```bash
ls -la
```

- 專案語言/框架（package.json → Node.js、go.mod → Go、requirements.txt → Python 等）
- 是否有測試框架設定
- 是否有 CI/CD 設定

### 1c. 使用者指令

- 分析 `$ARGUMENTS` 中的任務描述
- 回顧對話脈絡中的需求和上下文

### 1d. Build 狀態

- 如果有相關指令（`npm run build`、`go build`、`python -m py_compile`），快速檢查 build 是否正常
- 只在偵測到明確的 build 設定時才執行

## Step 2: 盤點 ECC 資源

快速掃描可用的 agents、skills、commands：

**可用 Agents（根據專案類型篩選）：**

| Agent | 適用情境 |
|-------|----------|
| planner | 需要規劃的複雜任務 |
| architect | 架構設計決策 |
| tdd-guide | 新功能、bug 修復 |
| code-reviewer | 程式碼品質審查 |
| security-reviewer | 安全性分析 |
| build-error-resolver | build 失敗 |
| e2e-runner | E2E 測試 |
| refactor-cleaner | 重構、清理 dead code |
| doc-updater | 文件更新 |
| go-reviewer | Go 專案專用 |
| go-build-resolver | Go build 錯誤 |
| python-reviewer | Python 專案專用 |
| database-reviewer | 資料庫相關 |

## Step 3: 智慧路由

根據 Step 1 的情境分析結果，自動選擇最佳 pipeline：

### 路由規則

| 偵測到的情境 | 選擇的 Pipeline | 說明 |
|---|---|---|
| 有明確的新功能需求 | planner -> architect -> tdd-guide -> code-reviewer | 完整功能開發流程 |
| 有 bug 描述或錯誤訊息 | planner -> tdd-guide -> code-reviewer | bug 修復流程 |
| 有未 commit 變更需 review | code-reviewer -> security-reviewer | 快速品質審查 |
| build 失敗 | build-error-resolver | 直接修復 build |
| 需要重構（使用者明確要求或偵測到 code smell） | architect -> refactor-cleaner -> code-reviewer | 安全重構流程 |
| 需要寫文件 | doc-updater -> code-reviewer | 文件更新流程 |
| Go 專案 | 在 pipeline 中加入 go-reviewer | 自動附加語言專用 reviewer |
| Python 專案 | 在 pipeline 中加入 python-reviewer | 自動附加語言專用 reviewer |
| 涉及資料庫 schema 或 query | 在 pipeline 中加入 database-reviewer | 自動附加資料庫 reviewer |
| 不確定 / 多種可能 | 列出建議 pipeline，讓使用者選擇 | 透過 AskUserQuestion 互動 |

### 路由判斷邏輯

1. **優先使用 `$ARGUMENTS`**：如果使用者有明確指令，以指令為準
2. **其次分析 git 狀態**：未 commit 變更 → review pipeline；build 失敗 → build-fix pipeline
3. **再看對話脈絡**：從對話中推斷使用者的意圖
4. **無法判斷時詢問**：使用 AskUserQuestion 提供 2-4 個建議選項

### 不確定時的互動

使用 AskUserQuestion 提供選項：

```
情境分析結果：[簡述偵測到的狀態]

建議的工作流程：
1. [Pipeline A] — [適用原因]
2. [Pipeline B] — [適用原因]
3. [Pipeline C] — [適用原因]
```

## Step 4: 執行 Pipeline

按選定的 pipeline 依序執行 agents，使用 handoff protocol 傳遞 context。

### Handoff Protocol

每個 agent 完成後，整理交接資訊傳給下一個 agent：

```markdown
## HANDOFF: [previous-agent] -> [next-agent]

### Context
<!-- 任務背景和目標 -->

### Findings
<!-- 上一個 agent 的發現和產出 -->

### Files Modified
<!-- 被修改的檔案清單 -->

### Open Questions
<!-- 未解決的問題 -->

### Recommendations
<!-- 給下一個 agent 的建議 -->
```

### 執行原則

- 每個 agent 依序執行，不跳過
- 如果某個 agent 發現 CRITICAL 問題，暫停 pipeline 並使用 AskUserQuestion 詢問使用者
- 語言專用 reviewer（go-reviewer、python-reviewer）在 code-reviewer 之後執行
- database-reviewer 在涉及 DB 變更的步驟之後執行

## Step 5: 輸出報告

所有 agents 執行完畢後，輸出最終報告：

```markdown
## /assist 執行報告

### 情境分析
- 專案類型: [語言/框架]
- 偵測到的情境: [情境描述]
- 選擇的 Pipeline: [pipeline 名稱]

### Agent 執行結果

#### 1. [Agent 名稱]
- 狀態: 完成 / 有問題
- 摘要: [1-2 句話]
- 重要發現: [列表]

#### 2. [Agent 名稱]
- ...

### 變更的檔案
| 檔案 | 動作 | 說明 |
|------|------|------|
| ... | 新增/修改/刪除 | ... |

### 未解決的問題
<!-- 如果有的話 -->

### 建議下一步
- [ ] ...
- [ ] ...
```
