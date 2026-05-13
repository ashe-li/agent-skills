---
name: plan-run
description: 依 plan.md 的 Dependencies DAG 推進實作 — Python 狀態機決定下一步（非 LLM 判斷），自動串接 TaskCreate/TaskUpdate。觸發：使用者要求依 plan 推進、要求 task tracking 對齊 DAG、或抱怨 LLM 跳步漏步。
allowed-tools: Bash, Read, Agent, AskUserQuestion, TaskCreate, TaskUpdate, TaskList
argument-hint: <plans/active/xxx.md 路徑>
redundancy-peers: []
---

# /plan-run — Plan DAG 推進器（狀態機 by code）

依照 `plan.md` 的 Dependencies DAG，由 Python 狀態機決定下一步該執行哪些 step、TaskCreate 該如何串接，避免 LLM 自主推進造成漏步、亂序、`addBlockedBy` 串錯。

## 設計原則

- **DAG 推進邏輯在 Python**：`scripts/plan_runner.py` 控制 step 順序、依賴檢查、status transition；LLM 不負責「下一步是什麼」的判斷
- **LLM 只負責執行**：把 transition 回傳的 step 拿來執行（呼叫 agent、跑命令），完成後回報 `complete` / `fail`
- **State 持久化**：`<plan-dir>/.plan-state/<slug>.state.json` 保存所有 step 狀態 + `previously_reported_ready`（給 delta 模式用）
- **TaskCreate 受控**：state machine 指定 subject / activeForm / addBlockedBy，LLM 照表填入
- **Output 三層 token 策略**：
  - `next` — full bootstrap（~2.8KB），列出全部 ready 完整模板；只在 session 開始 / 失去 context 時呼叫
  - `complete / fail / skip` — delta 模式（150~2KB 視解鎖數而定），**只列「本次新解鎖」的完整模板**，先前已展示過的 ready 只列 ID
  - `index` — 純 trace（~500 chars），ID + status 一覽，給「驗證 trace 完整性」用
  - 預設 markdown，`--format=json` 給 tooling

## Step 1: 初始化 state

```bash
python3 ~/Documents/agent-skills/scripts/plan_runner.py init "$ARGUMENTS"
```

輸出包含：
- `total_steps`、`phase_order`、`ready_steps`（初始可執行的 step IDs）
- `warnings`（解析警告，例如 dep 用 `~` range 語法）

若已存在 state，先看狀態再決定：

```bash
python3 ~/Documents/agent-skills/scripts/plan_runner.py status "$ARGUMENTS"
```

需重新初始化用 `init --force`。

## Step 2: 建立父 task

```
TaskCreate(subject="<plan title>", activeForm="<plan title> 推進中")
```

把回傳的 task_id 寫回 state（後續 child 不依賴此 id，但用於 audit）：

```bash
python3 ~/Documents/agent-skills/scripts/plan_runner.py set-parent "$ARGUMENTS" --task-id=<parent_task_id>
```

## Step 3: 推進迴圈（delta-driven，不要每次都呼叫 next）

迴圈直到 transition output 顯示 `Progress: N/N — ALL DONE`。

### 3a. Bootstrap（只在 session 起點 / 失去 context 時呼叫一次）

```bash
python3 ~/Documents/agent-skills/scripts/plan_runner.py next "$ARGUMENTS"
```

取得當前所有 ready steps 的完整 instruction 模板。之後**不要再頻繁呼叫 `next`**——`complete` / `fail` / `skip` 的 output 會自動帶 delta 資訊。

### 3b. 對每個 ready step 執行

1. **TaskCreate**（照搬模板裡的 `task_create.subject / activeForm / addBlockedBy`）→ 拿到 task_id
2. **回寫 task_id**：`plan_runner.py start "$ARGUMENTS" <step_id> --task-id=<task_id>`
3. **執行實際工作**：依 step 的 `agent` / `command` / `skill` 欄位
4. **回報結果**：
   - 成功：`TaskUpdate(<task_id>, completed)` + `plan_runner.py complete "$ARGUMENTS" <step_id>`
   - 失敗：`TaskUpdate(<task_id>, failed)` + `plan_runner.py fail "$ARGUMENTS" <step_id> --reason="<msg>"`

### 3c. 讀 transition output 決定下一步（不要重複呼叫 next）

每次 `complete / fail / skip` 的 output 都含：

| 區塊 | 何時出現 | 內容 |
|------|---------|------|
| `## Newly unlocked (N)` | 本次解鎖了新 step | 完整 instruction 模板，直接用 |
| `## Still ready (M): <ids>` | 之前已 ready 但未啟動 | 只列 ID，先前 output 已給過模板 |
| `## In progress (N)` | 有 in_progress | 只列 ID + task_id |
| `## Blocked (N)` | 有 dep 失敗 | 列出 blocking 原因 |
| 全部空 | done | 顯示 `(no ready / in_progress / blocked steps)` |

讀到 `Newly unlocked` 直接拿模板進入 3b。`Still ready` 是提醒（instructions 在更早的 output 裡，context 還在的話 LLM 應記得）。

### 3d. 失敗處理

`fail` 後 downstream 自動 `blocked`。用 `AskUserQuestion` 詢問：

> Step `<id>` 失敗：`<reason>`
> 後續 blocked steps：`<list>`
>
> 1. **重試** — `plan_runner.py reset --step=<id>` + 重新進入 3b
> 2. **跳過** — `plan_runner.py skip <id>`（風險自負）
> 3. **中止** — 停止迴圈

### 3e. context 失去時的 fallback

若 context 被 compaction 砍掉了某 step 的指令、或回到 session 時不確定狀態：

- 跑 `plan_runner.py index "$ARGUMENTS"`（~500 chars）看整體 trace
- 跑 `plan_runner.py next "$ARGUMENTS"` 重新拿完整模板（會 reset delta 追蹤）

## Step 4: 完成驗證

`summary.all_done == true` 後：

1. 比對 plan 的 Acceptance Criteria，逐項勾選
2. `TaskUpdate(<parent_task_id>, status=completed)`
3. 提示使用者執行 `/plan-archive` 歸檔 plan 至 `plans/completed/`

## DAG 視覺化（debug 用）

```bash
python3 ~/Documents/agent-skills/scripts/plan_runner.py dag "$ARGUMENTS"
python3 ~/Documents/agent-skills/scripts/plan_runner.py dag "$ARGUMENTS" --format=dot
```

## 與其他 skill 的關係

| Skill | 角色 |
|-------|------|
| `/design` | 產生 plan（state machine 的輸入源） |
| `/notion-plan` | 從 Notion 抓需求 → `/design` |
| `/plan-run` | 依 plan 推進（本 skill） |
| `/plan-archive` | 完成後歸檔 |
| `/verify-fix-loop`、`/code-review`、`/simplify` | 在個別 step 中被引用 |

## Plan 格式約束

state machine 依下列規則解析 `plan.md`：

| 元素 | 格式 |
|------|------|
| Phase 標頭 | `### Phase N：<名稱>` 或 `### Phase N: <名稱>` |
| Step 標頭 | `- [ ] **<step_id>** — <title>` |
| Step ID | `S\d+(\.\d+)?[a-z]?`（例：`S0.1`、`S1a`、`S3.1a`、`S12`） |
| Step 欄位 | `- <key>: <value>`（縮排 2 空格） |
| 可辨識欄位 | `Files`、`Action`、`Agent`、`Skill`、`Command`、`Agent/Skill`、`Dependencies`、`Risk` |
| Dependencies 值 | 逗號、斜線、空白分隔的 step ID 清單 |

**禁止語法**：
- `Dependencies: S4.1 ~ S6`（range 不展開，會 warning 並只抓首尾兩個）→ 改為 `Dependencies: S4.1, S4.2, S4.3, S5, S6`
- `Dependencies: ...`（省略號）→ 顯式列出

`/design` 產出的 plan 已符合格式。手寫 plan 注意 step id 與 Dependencies 列法。

## State 機制

```
   pending ──start──> in_progress ──complete──> completed
      │                    │
      │                    └──fail──> failed
      │
      ├──(dep 失敗自動)──> blocked
      │
      └──skip──> skipped
```

| Status | 意義 |
|--------|------|
| `pending` | 等待中（deps 未滿足或未啟動） |
| `in_progress` | 執行中（已 TaskCreate 並回寫 task_id） |
| `completed` | 完成 |
| `failed` | 失敗（需使用者決定後續） |
| `blocked` | 因 dep 失敗而 block；dep reset 後自動回 pending |
| `skipped` | 使用者主動跳過；後續 deps 視同 completed 解 block |

state transition 由 Python 強制驗證，不允許 `completed → pending` 等非法轉移（避免覆寫已完成工作）。

## 約束

- **狀態機不執行實際工作**：只決定 DAG 順序；agent 呼叫、build/test、檔案修改皆由 LLM 完成
- **失敗不自動重試**：避免吃 token，必經 user 決定
- **並行 step 由 LLM 自行決定是否真的並行**：state machine 只告訴你「這些 step 可以開始」
- **task_id 完全由 LLM 提供**：state machine 不會自動生成；若 LLM 沒呼叫 TaskCreate，task_id 為 null（不影響 DAG 推進，僅 audit）
