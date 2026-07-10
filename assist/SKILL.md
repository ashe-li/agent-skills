---
name: assist
description: 萬用助手 — 自動分析情境、盤點可用資源、智慧路由至最佳 agent/skill 組合，一鍵完成複雜工作流。
allowed-tools: Bash, Read, Glob, Grep, Edit, Write, Agent, Skill, AskUserQuestion, TaskCreate, TaskUpdate, TaskList
argument-hint: [任務描述，留空則自動偵測情境]
redundancy-peers: [design]
---

# /assist — 萬用助手

自動分析當前情境，盤點可用資源，智慧選擇最佳 agent/skill 組合並執行。適合不確定該用哪個工具時使用。

> 與 `/design` 的分工：`/assist` 是萬用路由入口，涵蓋審查/文件/研究/build 修復等各類情境；`/design` 專注在完整實作計畫的產出。不確定用哪個時先選 `/assist`——判定為需要完整規劃時會委派 `/design`，而非手動重造一套規劃流程。

## Step 0: 任務追蹤（條件式 HITL）

依 CLAUDE.md 全域規則詢問是否啟用 task tracking（複雜任務用 AskUserQuestion 附 token 預估，簡單任務不問逕行）。啟用後於每個 Step 開始前 TaskCreate 子任務（含 activeForm、必要時 addBlockedBy），完成後 TaskUpdate 為 completed；不啟用則跳過所有 Task 工具呼叫。

## Step 1: 情境分析

收集判斷路由所需的關鍵事實：

- **Git 狀態**：`git status`、`git diff --stat`、`git log --oneline -5` — 有無未 commit 變更、變更了哪些檔案、最近 commit 在做什麼
- **專案偵測**：`ls -la` 判斷語言/框架（package.json/go.mod/requirements.txt 等）、是否有測試/CI 設定
- **使用者指令**：`$ARGUMENTS` 與對話脈絡中的需求
- **Build 狀態**：只在偵測到明確 build 設定時才跑一次快速檢查（`npm run build`、`go build` 等）

## Step 2: 盤點可用資源

盤點 Claude Code 內建資源與本 repo 自有 skills 作為路由候選：

**內建 Agent types：**

| Agent | 適用情境 |
|-------|----------|
| Plan | 需要規劃的複雜任務（見 `/design`） |
| Explore | 唯讀搜尋定位、快速盤點 |
| general-purpose | 多步驟研究/審查/文件更新/TDD 引導等；文件審查/同步/TDD/複雜度分診有自持定義檔（呼叫慣例見 `agents/SKILL.md`），其餘用明確 prompt |

**內建 Skills：**

| Skill | 適用情境 |
|-------|----------|
| `/code-review` | 程式碼品質審查（支援 `--comment`、`--fix`；語言專用審查以 prompt 指定 go/python/ts） |
| `/security-review` | 安全性分析 |
| `/simplify` | reuse/簡化/效率清理（dead code、命名、nesting、重複程式碼） |
| `/verify` | 端對端行為驗證，非只跑測試 |
| `/review` | 審查 GitHub PR |
| `deep-research` | 開放式多來源研究，含 fact-check |

**本 repo 自有 Skills（根據情境篩選相關者）：**

| Skill | 適用情境 |
|-------|----------|
| `/design` | 需要完整實作計畫（新功能/架構變更） |
| `/update` | 知識沉澱（文件更新 + 審查 + context 整理 + pattern 提取） |
| `/pr` | commit/push/建立或更新 PR |
| `/plan-run` | 依既有 plan 的 DAG 狀態機推進實作 |
| `/worktree` | worktree 建立/清理 |
| `/handoff` | 跨 context/session 交接 |
| `/evidence-check`、`/verify-evidence-loop` | 技術決策查證；後者為高風險決策的迭代收斂版 |

若需要的資源不可用（例如某 skill 被 defer），提示使用者先 restore 或改選替代組合。

## Step 3: 智慧路由

根據 Step 1 的情境分析，選擇對應的資源組合：

| 偵測到的情境 | 選擇的組合 | 說明 |
|---|---|---|
| 新功能需求 / bug 修復 | `/design` → 實作 → `/code-review` → `/simplify` | `/design` 已內建業界標準調研、架構決策、需求追蹤矩陣；委派而非手動重造 |
| 有未 commit 變更需 review | `/code-review` → `/simplify` | 快速品質審查，先修正再視需要加安全審查 |
| build 失敗 | general-purpose agent（prompt 要求最小 diff 修復 build，不做架構變更） | 直接修復，優先於其他工作 |
| 需要重構 | `/design`（含架構決策）→ 實作 → `/simplify` → `/code-review` | 安全重構流程 |
| 需要寫文件 | general-purpose agent（依 `agents/doc-updater.md` 定義）或直接 `/update` | 文件更新流程；文件審查不適用 `/simplify` |
| 觸及安全敏感面（認證/輸入/endpoint/DB/反序列化/檔案/shell/SSRF/DOM/加密）| 組合中**預設附加** `/security-review` | 依 [`rules/security-guidance/skill-integration.md`](../rules/security-guidance/skill-integration.md) 觸發閘；都不觸及則不附加 |
| 需要完整實作計畫 | `/design` | 見上方與本 skill 的分工說明 |
| 需要依既有 plan 推進 | `/plan-run` | DAG 狀態機推進，不跳步 |
| 需要 commit/PR | `/pr` | 總結對話脈絡 + commit + push + PR |
| 需要跨 session/context 交接 | `/handoff` | 產生自包含 prompt |
| 需要開放式技術研究 | `deep-research` 或 general-purpose + WebSearch | 多來源研究與 fact-check |
| 需要驗證技術決策 | `/evidence-check`（single-shot）或 `/verify-evidence-loop`（高風險、迭代收斂） | |
| 不確定 / 多種可能 | 列出建議組合，AskUserQuestion 讓使用者選 | |

**判斷優先序：** `$ARGUMENTS` 明確指令 > build 失敗（阻塞後續驗證）> 安全問題 > 未 commit 變更 > 對話脈絡推斷意圖。多個情境衝突時，先說明偵測結果，問使用者是否仍按原指令繼續。

**不確定時**，用 AskUserQuestion 呈現選項：

```
情境分析結果：[簡述偵測到的狀態]

建議的組合：
1. [組合 A] — [適用原因]
2. [組合 B] — [適用原因]
3. [組合 C] — [適用原因]
```

**委派 general-purpose agent 時**（build 失敗、文件更新等無專用 skill 的情境），prompt 需明確界定範圍，例如：

```
Agent(subagent_type="general-purpose")
```
- build 失敗：只修最小 diff 讓 build 通過，不做架構變更、不新增功能
- 文件更新：掃描本次變更、更新對應文件（README、API docs、CODEMAPS），格式比照既有文件慣例

## Step 4: 執行

> 若啟用 task tracking：每個資源執行前 TaskCreate 子任務（含 activeForm，保留回傳的 task ID），執行後 TaskUpdate 為 completed；有依賴順序用 addBlockedBy 標記前序 task ID。

依序執行選定的 agent/skill。每個完成後，把「做了什麼、關鍵發現、修改的檔案、待解問題」整理成一段交給下一步——交接內容須自包含（下一個執行者看不到上一步的完整過程），不需要固定的必填欄位模板。

若某步驟發現 CRITICAL 問題，暫停 pipeline 並用 AskUserQuestion 詢問使用者是否繼續。

## Step 5: 輸出報告

所有步驟完成後輸出總結，涵蓋以下內容即可，不需固定模板：

- 情境分析結果與選擇的組合（含原因）
- 每步驟的結果摘要與重要發現
- 變更的檔案
- 未解決的問題與建議下一步

若啟用 task tracking，TaskList 本身即為完成狀態的來源，不需另建 manifest 表格或完成率計算。
