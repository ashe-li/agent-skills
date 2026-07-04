---
name: plan-run
description: 依 plan.md 的 Dependencies DAG 推進實作 — Python 狀態機決定下一步（非 LLM 判斷），自動串接 TaskCreate/TaskUpdate。觸發：使用者要求依 plan 推進、要求 task tracking 對齊 DAG、或抱怨 LLM 跳步漏步。
allowed-tools: Bash, Read, Agent, AskUserQuestion, TaskCreate, TaskUpdate, TaskList
argument-hint: <plans/active/xxx.md 路徑>
redundancy-peers: [design]
---

# /plan-run — Plan DAG 推進器（狀態機 by code）

依照 `plan.md` 的 Dependencies DAG 推進實作；判斷邏輯與理由見下方設計原則。

## 設計原則

- **DAG 推進邏輯在 Python**：`scripts/plan_runner.py` 控制 step 順序、依賴檢查、status transition；LLM 不負責「下一步是什麼」的判斷，只負責把 transition 回傳的 step 拿來執行（呼叫 agent、跑命令），完成後回報 `complete` / `fail`
- **State 持久化**：`<plan-dir>/.plan-state/<slug>.state.json` 保存所有 step 狀態 + `previously_reported_ready`（給 delta 模式用）
- **TaskCreate 受控**：state machine 指定 subject / activeForm / addBlockedBy，LLM 照表填入；**全程 best-effort** — TaskCreate/TaskUpdate 允許失敗，失敗即 continue，不中止 DAG 推進（下文不再重述）
- **Output 三層 token 策略**：
  - `next` — full bootstrap（~2.8KB），列出全部 ready 完整模板；只在 session 開始 / 失去 context 時呼叫一次，之後改讀 delta output（下文不再重述）
  - `complete / fail / skip` — delta 模式（150~2KB 視解鎖數而定），**只列「本次新解鎖」的完整模板**，先前已展示過的 ready 只列 ID
  - `index` — 純 trace（~500 chars），ID + status 一覽，給「驗證 trace 完整性」用
  - 預設 markdown，`--format=json` 給 tooling

## Step 0: 格式檢查 — 若為 planner-agent 輸出先 normalize

若 plan 來自 `/design` 的 planner subagent（典型徵兆：`**Step N: title**` 標頭、`- **Field**：value` 全形冒號、Dependencies 含「Phase N 完成」/ 括號註解等自由文字），跑 `init` 會 `No steps found`。先 normalize：

```bash
# 1. 預覽 diff
python3 ~/Documents/agent-skills/scripts/plan_runner.py normalize "$ARGUMENTS" --diff

# 2. diff 合理 → 落地（自動備份至 <plan>.bak）
python3 ~/Documents/agent-skills/scripts/plan_runner.py normalize "$ARGUMENTS" --write
```

Normalize 做的事：
- `**Step N: title**` → `- [ ] **S<phase>.<N>** — title`（依目前 Phase 自動補 S-code）
- `- **Field**：value` → `  - Field: value`（2 空格縮排 + ASCII 冒號）
- Dependencies 自由文字翻譯成 step ID list（cross-phase 先於 same-phase，避免誤翻 cycle）：
  - `Phase N 完成` → 該 Phase 最後一個 step
  - `Phase X Step Y` → `SX.Y`
  - 純 `Step N` → `S<current_phase>.N`
  - 括號註解（`(Step 2 已建立 base)`）丟棄
- 已 canonical 的行 pass-through，**重複跑 idempotent**

Normalize 後 stderr 會列出 `WARN:` 行，重點看 Dependencies 翻不出 ID 的（保留原文留給 user 修）。

## Step 1: 初始化 state

```bash
python3 ~/Documents/agent-skills/scripts/plan_runner.py init "$ARGUMENTS"
```

輸出包含：
- `total_steps`、`phase_order`、`ready_steps`（初始可執行的 step IDs）
- `warnings`（解析警告，例如 dep 用 `~` range 語法）

若 `init` 回傳 `No steps found in plan` → 回 Step 0 跑 normalize。
若已存在 state，先跑 `plan_runner.py status "$ARGUMENTS"` 看狀態再決定；需重新初始化用 `init --force`。

## Step 2: 建立父 task

呼叫 `TaskCreate(subject="<plan title>", activeForm="<plan title> 推進中")`，成功後把 task_id 寫回 state（給 audit + child task 的 `addBlockedBy` 用）：`plan_runner.py set-parent "$ARGUMENTS" --task-id=<parent_task_id>`。

若 `TaskCreate` 不可用（tool deferred、quota 滿、user 沒開 task tracking），記 warning 直接進 Step 3 —— state machine 不依賴 task_id，整個推進流程不會中斷。

## Step 3: 推進迴圈

迴圈直到 transition output 顯示 `Progress: N/N — ALL DONE`。

### 3a. Bootstrap（只在 session 起點 / 失去 context 時呼叫一次）

跑 `python3 ~/Documents/agent-skills/scripts/plan_runner.py next "$ARGUMENTS"` 取得當前所有 ready steps 的完整 instruction 模板；之後改讀 `complete` / `fail` / `skip` 帶的 delta 資訊。

### 3b. 對每個 ready step 執行

1. **Reconcile：先檢查 task list 是否已有對應 pending hint task**（先前 step 的 `## Next hints` 可能已 pre-create）
   - 有 → `TaskUpdate(<hint_task_id>, in_progress)`，不要 TaskCreate 重複
   - 沒 → `TaskCreate`（照搬模板裡的 `task_create.subject / activeForm / addBlockedBy`）→ 拿到 task_id
2. **回寫 task_id**：`plan_runner.py start "$ARGUMENTS" <step_id> --task-id=<task_id>`
3. **讀 `start` 的 `## Next hints` 區塊（如有）→ batch TaskCreate 列出的 next step 為 pending**（addBlockedBy = 當前 task_id），讓 user 看到「已完成 N + 進行中 1 + 下一步 hint」的 sliding window
4. **執行實際工作**：依 step 的 `agent` / `command` / `skill` 欄位
5. **回報結果**：
   - 成功：`plan_runner.py complete "$ARGUMENTS" <step_id>` → output 會自動帶 `## Required sync` 內含 `TaskUpdate(<task_id>, completed)` 指令，跟著做
   - 失敗：`plan_runner.py fail "$ARGUMENTS" <step_id> --reason="<msg>"` → 同上但 status=failed

### 3c. 讀 transition output 決定下一步

每次 `complete / fail / skip` 的 output 依現況附帶對應區塊：`## Required sync`（有 task_id 時，含 `TaskUpdate(...)` 完整指令）、`## Newly unlocked (N)`（新解鎖 step 的完整 instruction 模板，**搭配 3b reconcile 規則**判斷 TaskCreate 或 TaskUpdate）、`## Still ready (M): <ids>`（僅列 ID，模板已給過）、`## In progress (N)`（僅列 ID + task_id）、`## Blocked (N)`（列出 blocking 原因）；全部空則顯示 `(no ready / in_progress / blocked steps)`。`start` 另可能含 `## Next hints`（下一個 pending step 的完整 instruction，3b step 3 用）。

讀到 `Newly unlocked` 套 3b reconcile rule 進入下個 step；`Still ready` 只是提醒（instructions 在更早的 output 裡）。

### 3d. 失敗處理

`fail` 後 downstream 自動 `blocked`。用 `AskUserQuestion` 詢問：

> Step `<id>` 失敗：`<reason>`
> 後續 blocked steps：`<list>`
>
> 1. **重試** — `plan_runner.py reset --step=<id>` + 重新進入 3b
> 2. **跳過** — `plan_runner.py skip <id>`（風險自負）
> 3. **中止** — 停止迴圈

### 3e. context 失去時的 fallback

若 context 被 compaction 砍掉了某 step 的指令、或回到 session 時不確定狀態：跑 `plan_runner.py index "$ARGUMENTS"`（~500 chars）看整體 trace，或跑 `plan_runner.py next "$ARGUMENTS"` 重新拿完整模板（會 reset delta 追蹤）。

### 3f. 自動推進（optional）— `/goal` 包外層

不想每個 step 完成都手動確認，可用 Claude Code 內建 `/goal` 包外層自動續跑：

```text
/goal plan_runner.py status "$ARGUMENTS" 顯示 all_done=true（無 failed/blocked/in_progress）OR stop after 30 turns
```

評估者（預設 Haiku、不呼叫工具）每輪讀 transcript 判斷是否達成；未達成自動續跑，達成自動 clear。3d 的失敗 HITL gate 仍生效（`fail` 後仍須在主 turn 走 `AskUserQuestion`，`/goal` 不會自動跳過），故每次 transition 後跑一次 `plan_runner.py index` 把狀態 surface 給評估者看。一個 session 僅能一個 `/goal`；對 plan 不確定、高風險 step、多 plan 平行跑、或想逐步確認時不要用。

## Step 4: 完成驗證

`summary.all_done == true` 後：

1. 比對 plan 的 Acceptance Criteria，逐項勾選
2. `TaskUpdate(<parent_task_id>, status=completed)`
3. 提示使用者執行 `/plan-archive` 歸檔 plan 至 `plans/completed/`

## DAG 視覺化（debug 用）

`plan_runner.py dag "$ARGUMENTS"`（或加 `--format=dot`）。

## 與其他 skill 的關係

| Skill | 角色 |
|-------|------|
| `/design` | 產生 plan（state machine 的輸入源） |
| `/notion-plan` | 從 Notion 抓需求 → `/design` |
| `/plan-run` | 依 plan 推進（本 skill） |
| `/plan-archive` | 完成後歸檔 |
| `/verify-fix-loop`、`/code-review`、`/simplify` | 在個別 step 中被引用 |
| `/goal`（Claude Code 內建） | optional 包外層，讓 DAG 自動推進至 all_done（見 3f） |

## Plan 格式約束

state machine 依下列規則解析 `plan.md`：

| 元素 | 格式 |
|------|------|
| Phase 標頭 | `### Phase N：<名稱>` 或 `### Phase N: <名稱>` |
| Step 標頭 | `- [ ] **<step_id>** — <title>` 或 `- [ ] <step_id> — <title>`（`**` bold 可省略） |
| Step ID | `S\d+(\.\d+)?[a-z]?`（例：`S0.1`、`S1a`、`S3.1a`、`S12`） |
| Step 欄位 | `- <key>: <value>`（縮排 2 空格） |
| 可辨識欄位 | `Files`、`Action`、`Agent`、`Skill`、`Command`、`Agent/Skill`、`Dependencies`、`Risk` |
| Dependencies 值 | 逗號、斜線、空白分隔的 step ID 清單；支援 range 語法 |

**Range 語法**（自動展開為 plan 內出現順序的完整 ID list）：
- `Dependencies: S4.1 ~ S6` → `[S4.1, S4.2, S4.3, S5, S6]`（依 plan 中出現順序）
- 支援 `~`、`...`、`..`、`–`、`—` 五種分隔符
- 可混用：`Dependencies: S0.1, S1.1 ~ S2, S3` 也合法
- Range 端點不存在時降級為只保留端點 + 發出 warning

`/design` 產出的 plan 已符合格式。手寫 plan 可省略 `**` 並使用 range 簡寫。

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
