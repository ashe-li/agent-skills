---
name: assist
description: 萬用助手 — 自動分析情境、盤點 ECC 資源、智慧路由至最佳 agent pipeline，一鍵完成複雜工作流。
allowed-tools: Bash, Read, Glob, Grep, Edit, Write, Agent, AskUserQuestion, TaskCreate, TaskUpdate, TaskList
argument-hint: [任務描述，留空則自動偵測情境]
---

# /assist — 萬用助手

自動分析當前情境，盤點可用 ECC 資源，智慧選擇最佳 agent pipeline 並執行。適合不確定該用哪個工具時使用。

## Step 0: 任務追蹤（條件式 HITL）

評估本次任務是否適合啟用 task tracking。

**判斷依據：**

| 建議啟用 | 建議跳過 |
|----------|----------|
| 預估 4+ 實作步驟 | 1-3 個簡單步驟 |
| 有步驟間依賴關係 | 線性無依賴 |
| 跨多個檔案/模組 | 單一檔案修改 |
| 預計執行時間較長 | 快速完成的任務 |

**若判斷為「建議啟用」：** 使用 AskUserQuestion 詢問使用者：

> 本次任務較為複雜（[簡述原因]），建議啟用任務追蹤。
>
> 啟用後會在每個步驟使用 TaskCreate/TaskUpdate 追蹤進度，
> 支援依賴管理（addBlockedBy）和即時狀態顯示（activeForm）。
> 預估額外 token 消耗：~500-1,000 tokens。
>
> 1. **啟用** — 全程追蹤
> 2. **不啟用** — 直接開始

**若判斷為「建議跳過」：** 不詢問，直接進入 Step 1。

**啟用後的行為：**
- TaskCreate 父任務（subject: `/assist: [任務摘要]`）
- 每個 Step 開始前 TaskCreate 子任務（含 activeForm），**保留回傳的 task ID**，完成後 TaskUpdate 為 completed
- 有依賴的步驟使用 addBlockedBy 標記（填入前序步驟 TaskCreate 回傳的 task ID）
- 不啟用則完全跳過所有 Task 工具呼叫

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
| harness-optimizer | agent harness 設定優化（hooks/evals/routing） |
| loop-operator | 自主迴圈運行與監控 |
| docs-lookup | Context7 文件與 API 查詢 |
| typescript-reviewer | TypeScript/JavaScript 專案型別安全與 best-practice 審查 |

**可用 Commands（根據情境篩選）：**

| Command | 適用情境 |
|---------|----------|
| /orchestrate | 預定義多 agent 工作流（feature/bugfix/refactor/security） |
| /loop-start | 啟動自主迴圈任務 |
| /loop-status | 監控進行中的迴圈狀態 |
| /model-route | 依任務複雜度推薦 model 層級 |
| /checkpoint | 工作流中建立/驗證進度檢查點 |
| /quality-gate | 品質閘——測試覆蓋率、lint、安全掃描 |
| /simplify | code-reviewer 後自動修正（refactor-cleaner：dead code、命名、nesting） |
| /docs | 需要查詢 Context7 文件或 API 行為 |
| /aside | 需要暫時切換話題但不丟失當前任務 context |
| /skill-health | 檢視 skill portfolio 健康狀態與冗餘/缺口分析 |
| /prompt-optimize | 優化 prompt、SKILL.md 描述、system instructions 品質 |
| /blueprint | 需跨 session 持續推進的大型建構計畫 |
| /context-budget | context window 即將超限時稽核用量 |
| /save-session \| /resume-session | 跨 session 保存/恢復工作進度 |

### ECC 資源分配原則

- 盤點結果直接影響 Step 3 路由選擇
- 路由決策前必須確認所選 pipeline 中的所有 agent 均為可用狀態
- 若需要的 agent 已被 defer，提示使用者先 restore 或選擇替代 pipeline

## Step 3: 智慧路由

根據 Step 1 的情境分析結果，自動選擇最佳 pipeline：

### 路由規則

| 偵測到的情境 | 選擇的 Pipeline | 說明 |
|---|---|---|
| 有明確的新功能需求 | planner（含業界/學術方案調研）-> tdd-guide -> code-reviewer -> /simplify | 完整功能開發流程；planner 須附上技術方案的業界標準或學術支撐 |
| 有 bug 描述或錯誤訊息 | planner -> tdd-guide -> code-reviewer -> /simplify | bug 修復流程 |
| 有未 commit 變更需 review | code-reviewer -> /simplify -> security-reviewer | 快速品質審查；先修正再安全審查 |
| build 失敗 | build-error-resolver | 直接修復 build |
| 需要重構（使用者明確要求或偵測到 code smell） | planner -> refactor-cleaner -> code-reviewer | 安全重構流程（已有 refactor-cleaner，不加 /simplify 避免重複） |
| 需要寫文件 | doc-updater -> code-reviewer | 文件更新流程（文件審查不適用程式碼簡化） |
| Go 專案 | 在 pipeline 中加入 go-reviewer | 自動附加語言專用 reviewer |
| Python 專案 | 在 pipeline 中加入 python-reviewer | 自動附加語言專用 reviewer |
| 涉及資料庫 schema 或 query | 在 pipeline 中加入 database-reviewer | 自動附加資料庫 reviewer |
| 需要優化 agent harness 設定 | harness-optimizer | hooks/evals/routing 設定調優 |
| 需要執行自主迴圈任務 | loop-operator | 長時間 autonomous loop 監控 |
| 需要預定義工作流模板 | `/orchestrate` command | 當任務明確符合 feature/bugfix/refactor/security 模板時，委派給 orchestrate 而非手動組裝 pipeline |
| 使用者詢問該用哪個 model | `/model-route` command | 依任務複雜度推薦 Haiku/Sonnet/Opus |
| 長時間迴圈任務需監控 | loop-operator + `/loop-status` | 啟動迴圈後配合狀態查詢 |
| 需要多模型協作（ace-tool MCP 可用時） | `multi-*` commands | 條件：偵測到 ace-tool MCP 已設定時才路由 |
| 需要查詢特定 API 或框架文件 | `/docs` command 或 docs-lookup agent | Context7 MCP 查詢，比 WebFetch 更準確 |
| TypeScript/JavaScript 專案 | 在 pipeline 中加入 typescript-reviewer | 自動附加語言專用 reviewer |
| 需要優化 prompt/SKILL.md 描述品質 | `/prompt-optimize` command | advisory only，不執行實際任務 |
| context 即將超出限制或效能下降 | `/context-budget` command | 稽核各元件用量、建議精簡 |
| 長時間/跨 session 任務需要保存進度 | `/save-session` → 下次 `/resume-session` | 在適當中斷點儲存狀態 |
| 不確定 / 多種可能 | 列出建議 pipeline，讓使用者選擇 | 透過 AskUserQuestion 互動 |

### 路由判斷邏輯

1. **優先使用 `$ARGUMENTS`**：如果使用者有明確指令，以指令為準
2. **其次分析 git 狀態**：未 commit 變更 → review pipeline；build 失敗 → build-fix pipeline
3. **再看對話脈絡**：從對話中推斷使用者的意圖
4. **無法判斷時詢問**：使用 AskUserQuestion 提供 2-4 個建議選項

### 多情境衝突處理

當同時偵測到多個情境時，依以下優先順序決定先處理哪個：

1. Build 失敗 — 必須先修復，否則其他工作無法驗證
2. 安全問題 — 不能延後
3. 使用者明確指令（$ARGUMENTS）— 使用者意圖優先
4. Bug 修復 — 修復比新功能重要
5. 新功能開發 — 標準優先級
6. 重構/清理 — 較低優先級
7. 文件更新 — 可延後或與其他工作並行

若偵測到的情境與 `$ARGUMENTS` 衝突（例如使用者要 review 但 build 失敗），先說明偵測結果，詢問使用者是否仍要繼續原指令。

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

> **若啟用 task tracking：** 每個 agent 執行前 TaskCreate 子任務（含 activeForm，**保留回傳的 task ID**），執行後 TaskUpdate 為 completed。若有 pipeline 依賴順序，使用 addBlockedBy 標記（填入前序 agent TaskCreate 回傳的 task ID）。

按選定的 pipeline 依序執行 agents，使用 handoff protocol 傳遞 context。

### Handoff Protocol

每個 agent 完成後，整理交接資訊傳給下一個 agent：

```markdown
## HANDOFF: [previous-agent] -> [next-agent]

### Status: [COMPLETED | COMPLETED_WITH_ISSUES | FAILED]
<!-- COMPLETED = 正常繼續 | COMPLETED_WITH_ISSUES = 繼續但標記 | FAILED = 暫停詢問使用者 -->

### Context
<!-- 任務背景和目標 -->

### Findings
<!-- 上一個 agent 的發現和產出 -->

### Industry & Standards Referenced
<!-- 本階段引用的業界標準或學術依據 -->

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
