---
name: design
description: 開發設計 — 自動盤點可用資源，透過 Plan agent 建立完整實作計畫，輸出至 plans/active/<slug>.md 供使用者確認後才進入實作。
allowed-tools: Bash, Read, Glob, Grep, Edit, Write, Agent, AskUserQuestion, EnterPlanMode, TaskCreate, TaskUpdate, TaskList
argument-hint: <功能描述或需求>
redundancy-peers: [assist]
---

# /design — 開發設計

接收功能需求，自動盤點可用資源（agents/skills），透過 Plan agent 建立完整實作計畫並輸出至 plans/active/<slug>.md。**不會自動執行實作，必須等使用者確認。**

## Step 0: 任務追蹤（條件式 HITL）

依 CLAUDE.md 全域規則詢問是否啟用 task tracking（複雜任務用 AskUserQuestion 附 token 預估，簡單任務不問逕行）。啟用後於每個 Step 開始前 TaskCreate 子任務（含 activeForm、必要時 addBlockedBy），完成後 TaskUpdate 為 completed；不啟用則跳過所有 Task 工具呼叫。

## Step 1: 盤點可用資源

> **若啟用 task tracking：** TaskCreate(subject: "盤點可用資源", activeForm: "掃描 agent/skill 中...") → 完成後 TaskUpdate(status: "completed")

盤點 Claude Code 內建資源作為規劃參考：

1. **Agent types**：`Plan`（規劃，見 Step 3）、`Explore`（唯讀搜尋定位）、`general-purpose`（多步驟研究/審查，見 Step 4a）
2. **內建 skills**：`/code-review`（品質審查）、`/security-review`（安全審查）、`/simplify`（reuse/簡化/效率清理）、`/verify`（端對端行為驗證）
3. 根據使用者需求（`$ARGUMENTS`）篩選出**相關的資源**

**輸出：** 一份簡潔的資源清單，標記每個資源與當前需求的關聯程度（高/中/低）。

## Step 2: 複雜度評估

### Step 2a: 分診 subagent（先於一切重流程）

**進入完整流程前，先派一個輕量分診 agent 判定複雜度**——不讓主模型憑印象判斷，也不讓可走快速路徑的任務直接掉進完整儀式（2026-07-10 使用者指示）：

```
Agent(subagent_type="general-purpose", model="haiku", effort="low")
```

- **輸入**：`$ARGUMENTS` + 對話中的需求脈絡（`/notion-plan` 來源含整理後的 Markdown）+ 專案根目錄路徑
- **動作**：只允許 Glob/Grep/Read 粗估影響面（找出可能要改的檔案、判斷有無方案取捨），**不深讀、不設計方案**；回傳固定格式：

```json
{"complexity": "low | medium | multi-session", "estimated_files": N, "has_architecture_decision": bool, "ambiguous_requirements": bool, "rationale": "一句話"}
```

- **主模型裁決**：對照下表採納或否決——分診結果與主模型判斷衝突時，**取較高複雜度**（分診只准降級失敗、不准升級失敗）；`ambiguous_requirements=true` → 先 AskUserQuestion 補需求再重分診
- 分診 agent 約 5-15K tokens，換到的是：低複雜度任務跳過 Plan agent 全儀式 + 61K-token 審查 agent 的成本

### Step 2b: 路徑判定

根據分診結果，選擇執行路徑：

| 複雜度 | 判斷標準 | 執行路徑 |
|--------|---------|---------|
| **低（快速路徑）** | 單一 bug fix 或小型變更：≤3 檔、無架構決策、修法方向明確（例：顯示邏輯 bug、更新 README、改設定） | Step 3（精簡計畫，見下）→ Step 4a 改主模型 self-check（不派 subagent）→ Step 4b |
| **中等以上** | 跨檔案且有架構影響、或修法方向需要取捨 | Step 3（含架構決策）→ Step 4（含架構審查）|
| **多 session** | 需跨 session 持續推進的大型計畫（遷移框架、多天重架構） | 走標準流程產出 plan，跨 session 推進交給 `/plan-run`（state 持久化於 `plans/active/.plan-state/`） |

> **快速路徑的存在理由**（2026-07-10 PDT-10428 前例）：一個 P2 顯示 bug 走完整儀式，產出業界參照表、社群共識表、RTM、threat model 後，再被 61K-token 審查 agent 以「缺效能評估段落」「缺文件影響章節」等格式官僚項目打回重修——4 個 FAIL 沒有一個改變實作方向。儀式成本要與風險×不可逆性成比例，不與流程完整度成比例。**檔案數不是唯一判準**：bug fix 常跨 2-3 檔（util + 元件 + 測試）但仍是單一決策，屬快速路徑；反之單檔但涉及方案取捨（快取策略、狀態管理選型）走中等。

如果需求過於模糊無法判斷複雜度，使用 AskUserQuestion 請使用者補充具體資訊。

> 無論走哪條路徑，都會輸出 plans/active/<slug>.md。

## Step 3: Plan agent — 建立實作計畫

> **若啟用 task tracking：** TaskCreate(subject: "Plan agent 建立實作計畫", activeForm: "Plan agent 規劃中...") → 完成後 TaskUpdate(status: "completed")

使用內建 **Plan** agent 建立詳細的實作計畫。

```
Agent(subagent_type="Plan")
```

**輸入 context：**

- 使用者的需求描述（`$ARGUMENTS` + 對話脈絡）
- Step 1 盤點的可用資源清單
- 當前專案結構（透過 `ls`、`git status` 等了解）

**計畫須包含：**

1. **需求拆解** — 將需求分解為可執行的子任務
2. **技術方案** — 每個子任務的實作方式
3. **業界實踐與標準化方案參照** — 每個技術方案須附上依據：業界標準/慣例（RFC、W3C、OWASP、12-Factor 等）、學術研究或成熟標準化方案（官方 SDK、知名 library 的 canonical pattern）、**社群共識**（GitHub discussions、Stack Overflow、官方 forum、Reddit 的主流看法）、**反面意見與已知陷阱**（即使最終仍採用也須揭露）；無直接標準時說明理由並引述最接近的參考
4. **架構決策**（中等以上複雜度必填）— 每個涉及架構的技術選擇須包含：選擇方案及理由、至少 1 個被排除的替代方案及原因、與現有程式碼的相容性分析、可擴展性評估、效能與安全性影響評估、實作後需新增或更新的文件（README、API docs、CODEMAPS）
5. **需求追蹤矩陣（Requirements Traceability Matrix）**（依據：DAMA-DMBOK Completeness）— 每個需求（REQ-1、REQ-2...）須映射到至少一個實作步驟與驗收標準，欄位：需求 ID / 需求描述 / 對應實作步驟 / 驗收標準；此矩陣用於 Step 4a 需求覆蓋率審查的輸入（set difference 比對）
6. **可用資源整合** — 明確指出每個步驟應使用哪個 agent/skill（例：「Step 3: 使用 Plan agent」「Step 5: 使用 /code-review」）。**資源分配須經 Step 1 盤點確認** — 不可假設資源可用
7. **依賴關係** — 哪些步驟必須按順序執行、哪些可以並行
8. **風險評估** — 潛在的技術風險和對策
9. **Security / Threat Model**（主動觸發，依 [`rules/security-guidance/skill-integration.md`](../rules/security-guidance/skill-integration.md) 的觸發閘）— 先判斷 plan 是否觸及安全敏感面（認證/輸入/endpoint/DB/反序列化/檔案/shell/SSRF/DOM/加密）：**觸及** → 必含「Security / Threat Model」章節，逐條對照 `~/.claude/claude-security-guidance.md`（與 plugin 同一份判準），且實作後步驟須納入 `/security-review`；**未觸及** → 明示「無安全敏感面，跳過」，不空列
10. **驗收標準** — 怎樣算完成

> **快速路徑（低複雜度）只要求 1、2、7、8、10 + Implementation Steps（S-code 格式不變，維持 /plan-run 相容）**：跳過 3（業界參照/社群共識表）、4（架構決策表）、5（RTM）；9 縮為一句話判定（觸及安全敏感面則自動升級回完整路徑）；token 預算縮為一行總估算（不逐 step 列表）。診斷類 step（live 驗證、payload 擷取）不屬儀式、不可裁剪——票面假設與程式碼矛盾時，診斷 gate 是快速路徑中最有價值的部分。

## Step 4: 品質檢查與 Plan Mode 呈現

> **若啟用 task tracking：** TaskCreate(subject: "品質檢查與 Plan Mode 呈現", activeForm: "品質檢查與輸出中...", addBlockedBy: [Step 3 TaskCreate 回傳的 task ID]) → 完成後 TaskUpdate(status: "completed")

### Step 4a: 品質審查（subagent 隔離）

> **快速路徑（低複雜度）不派 subagent**：主模型以 6 項精簡清單 self-check——可執行性（每步有檔案路徑+動作）、依賴正確性、驗收可測、實作後品保步驟存在、診斷 gate 存在（票面假設未經 live 驗證時）、安全敏感面判定。任一不過直接改 plan，不進 FAIL 迴圈。以下 subagent 審查僅適用中等以上複雜度。

啟動 **general-purpose subagent** 在隔離 context 中審查 Step 3 的計畫。不使用已禁用的 `architect` agent（ECC 版與無前綴版皆禁，消融實驗 delta=-0.50，見 `rules/refactor/remove-architect-pipeline.md`），改用通用 agent 搭配明確審查 prompt。

```
Agent(subagent_type="general-purpose", model="sonnet")
```

**傳入 subagent 的 context：**

- Step 3 產出的完整計畫內容
- Step 1 的資源盤點結果
- Step 2 判定的複雜度等級
- 下方的審查檢查清單

**subagent 須逐項檢查並回報結果（PASS/FAIL + 說明），且對「業界支撐」「社群共識」兩維度主動驗證**（計畫缺引述、引述有誤，或完全未提及社群觀點/已知陷阱 → 標記 FAIL）：

**基礎品質（所有複雜度）：**

| 維度 | 通過標準 |
|------|---------|
| 需求覆蓋率 / 完整性 | 需求追蹤矩陣的每個 REQ-N 都有對應實作步驟，覆蓋率 100%（set difference = 0，依據：DAMA-DMBOK Completeness）。驗證法：枚舉 $ARGUMENTS 與對話脈絡中的需求（共 N 個）→ 逐條核對矩陣 → `N - requirements_with_steps(K) = uncovered`；差集 > 0 即 FAIL 並列出未覆蓋項，不可用語意判斷代替計數 |
| 可執行性 | 每個步驟有明確的檔案路徑和具體動作 |
| 依賴正確性 | 步驟間依賴無環且順序合理 |
| 粒度適當 | 每個步驟 1-3 個具體動作 |
| 資源合理 | 每個 agent/skill 在其設計用途內使用，且對照 Step 1 盤點結果確認可用 |
| 驗收可測 | 每個驗收標準都可客觀驗證 |
| 業界/學術支撐 | 每個技術方案都有明確的業界標準、學術研究或標準化方案參照（依據：arXiv:2509.18970） |
| 社群共識與反面意見 | 每個技術方案都納入社群主流看法，並揭露已知的反面意見、批評或陷阱 |
| 實作後工具 | plan 的步驟中包含實作後的品質保障（`/code-review`、`/simplify`、`/update`、`/verify` 等） |
| Eval 基線 | 若涉及行為變更，計畫中包含 eval 基線建立與回歸驗證步驟 |

**架構審查（中等以上複雜度才執行）：**

| 維度 | 通過標準 |
|------|---------|
| 替代方案 | 每個架構決策都列出至少 1 個被排除的替代方案及原因 |
| 簡單性 | 沒有更簡單的方案能達成同樣目標（若有，須說明為何選複雜方案） |
| 可擴展性 | 架構能應對合理範圍的需求增長，不會因規模變化需要重寫 |
| 相容性 | 與現有程式碼的整合方式明確，無破壞性變更或已標記 migration 步驟 |
| 效能 | 已評估效能影響 |
| 安全覆蓋（主動驗證）| 若 plan 觸及安全敏感面：必含 Security / Threat Model 章節、逐條對照 `claude-security-guidance.md`、實作後步驟含 `/security-review`。**逐項核對存在性，非只看「已評估」打勾**；若觸及卻缺章節 → FAIL。未觸及則標 N/A |
| 參照可靠性 | 引用的業界標準適用於當前情境、為最新版本、無更好的標準被遺漏 |
| 文件影響 | 已列出實作後需新增或更新的文件（README、API docs、CODEMAPS） |

**subagent 回報後的處理：**

- **全部 PASS：** 進入 Step 4b
- **有 FAIL 項目：** 回饋至 Step 3 重新規劃（將 FAIL 項目和修正方向作為額外 context 傳給 Plan agent），修正後重新啟動 Step 4a subagent 檢查。初次檢查 + 最多 2 次重試（共最多 3 輪）
- **經 3 輪仍有 FAIL：** 在 plan 的 Review Notes 中標記待確認項目，交由使用者決定

### Step 4b: EnterPlanMode 呈現計畫

品質檢查通過後，使用 `EnterPlanMode` 將完整計畫內容呈現給使用者。

**為什麼用 Plan Mode：** Claude Code 2.1.77+ 在使用者接受 plan 時會自動命名 session。透過 Plan Mode 呈現計畫，使用者 accept 後觸發 session auto-naming，同時作為正式的計畫確認流程。

**呈現內容：** 將完整 plan（依照下方格式）作為 Plan Mode 的輸出。Plan Mode 中不可使用 Write/Edit，所以**只呈現，不寫檔**。

**使用者 accept 後：** ExitPlanMode 觸發 session auto-naming，接著進入 Step 5 寫檔。

## Step 5: 生成 Slug 並寫入 plan 檔案

使用者在 Plan Mode 中 accept 後（ExitPlanMode），將計畫寫入檔案。

**Slug 生成規則：** 從 plan 的 H1 標題（`# Implementation Plan: <名稱>`）取 `:` 後的部分轉為 kebab-case（僅保留英數字與 `-`，中文與特殊字元轉 `-`、去重複、截斷至 60 字元、全小寫）；純中文標題等無法轉換時用 `plan-YYYYMMDD`。例：`Stripe Subscription Billing` → `stripe-subscription-billing`。

**輸出路徑：** `plans/active/<slug>.md`（相對於專案根目錄）

- 若目錄不存在，自動 `mkdir -p plans/active/`
- 若同名檔案已存在，使用 AskUserQuestion 詢問：覆蓋 / 加日期後綴 / 取消

**plan 格式：**

```markdown
# Implementation Plan: [功能名稱]

> Generated by /design on [日期]
> Plan file: plans/active/<slug>.md
> Status: PENDING APPROVAL

## Overview
<!-- 1-3 句話描述目標 -->

## Resources
<!-- 本次計畫使用的 agents/skills -->

| Resource | Type | Usage |
|----------|------|-------|
| Plan | agent | Step 3: 建立實作計畫 |
| /code-review | skill | Step 5: 品質審查 |
| /simplify | skill | Phase 2: dead code、命名、nesting、重複程式碼合併 |
| ... | ... | ... |

## Industry & Standards Reference
| 技術決策 | 參照依據 | 類型 | 來源 |
|----------|---------|------|------|
| ... | ... | 業界標準/學術研究/標準化方案/最佳實踐 | ... |

## Community Consensus & Dissenting Views
| 技術決策 | 社群共識 | 反面意見/已知陷阱 | 來源 |
|----------|---------|------------------|------|
| ... | ... | ... | GitHub/SO/Reddit/... |

## Implementation Steps

<!--
格式約束（/plan-run state machine 需要）：
- Step 標頭：`- [ ] **S<phase>.<num>** — <title>`（S-code 必填，例 S1.1、S1.2、S2.1）
- 欄位：兩個空格縮排 + ASCII 冒號（`  - Files: ...`，不要 bold、不要全形冒號）
- Dependencies 值：純 step ID list（`S1.1, S1.2`），支援 range（`S1.1 ~ S2.3`），禁止自由文字
-->

### Phase 1: [實作階段]
- [ ] **S1.1** — [Step title]
  - Files: src/foo.ts, src/bar.ts
  - Agent: `planner`
  - Action: ...
  - Dependencies: []
  - Why: ...

> **Dependencies canonical form**: 無 deps 時用 `Dependencies: []`（不要省略整個欄位、不要寫 `None` 或留空白），plan_runner.py parser 對 `[]` 一致處理。有 deps 時用 step ID list：`Dependencies: S1.1, S1.2` 或 range `Dependencies: S1.1 ~ S2.3`。

### Phase 2: [品質保障]
- [ ] 執行 /code-review 審查程式碼品質
- [ ] 執行 /simplify 自動修正（dead code、命名、nesting、重複程式碼合併）
- [ ] 執行 /security-review 檢查安全性
- [ ] 執行 /verify 進行全面驗證

### Phase 3: [文件同步]
- [ ] 執行 /update 更新知識庫（文件更新 + 審查 + inline 5 維知識沉澱）
- [ ] 或手動同步相關文件（README、docs/）

## Architecture Notes
<!-- 中等以上複雜度必填 -->
<!-- 每個架構決策：選擇方案、排除的替代方案、相容性分析、效能/安全影響 -->
<!-- 文件影響評估：實作後需新增或更新哪些文件 -->

## Risks & Mitigations
<!-- 風險評估 -->

## Acceptance Criteria
- [ ] ...
- [ ] ...

## Review Notes
<!-- 計畫品質自審的結果 -->
```

寫入後提示使用者：

> `plans/active/<slug>.md` 已寫入。
> 可以開始實作；實作完成後執行 `/plan-archive` 歸檔。

## Step 6: Worktree 隔離開發（可選）

plan 寫入後，依全域規則 `rules/worktree-prompt.md` 判斷是否詢問使用者進入 worktree（跨檔案或架構變更預設詢問，單一檔案小型任務跳過）。

若使用者選擇進入 worktree：從 plan slug 推導 worktree 名稱（例：`ticket-1234-url`）、呼叫 `EnterWorktree(name: "<slug>")`，並提示開發完成後執行 `/pr`（worktree 於 session 結束時自動回收）。不使用則直接進入 Step 7。

## Step 7: 推進方式選擇（HITL）

依 Step 2 判定的複雜度推薦推進方式，使用 AskUserQuestion 詢問使用者：

| 複雜度（Step 2 判定）| 推薦選項 |
|----------------------|---------|
| 低（1-3 步、無依賴）| LLM 自主推進 |
| 中等（4-10 步、有依賴）| `/plan-run` 狀態機（手動 / 推薦） |
| 高（10+ 步、複雜 DAG、多並行）| `/plan-run` + `/goal` 自動推進（強烈推薦） |

**詢問格式：**

> Plan 已寫入 `plans/active/<slug>.md`。如何推進實作？
>
> 1a. **`/plan-run` 狀態機（手動）** — Python 狀態機決定 DAG 推進順序、TaskCreate `addBlockedBy` 自動串接、session 中斷可續推、不會跳步；每個 step 完成後手動 enter 繼續
> 1b. **`/plan-run` + `/goal` 自動推進**（強烈推薦於高複雜度）— 同 1a 但用 Claude Code 內建 `/goal` 包外層，狀態機 + evaluator 自動跑到 `all_done=true` 或 N turns；**失敗仍走 Step 3d HITL gate**（不會自動跳過 fail）
> 2. **LLM 自主推進** — 直接在當前 session 開始實作；簡單線性 plan 適用，無狀態機 overhead
> 3. **暫不開始** — 結束 `/design`，由使用者另行決定時機

> **1b 何時不適用：** 對 plan 不確定 / 高風險 step、想細看每個 step 的 output、一個 session 已被另一個 `/goal` 佔用。詳見 `/plan-run` Step 3f。

**若選 1a 或 1b（`/plan-run` 手動或自動推進）：**

1. normalize 兜底（canonical 格式跑下去是 no-op）：`python3 "${AGENT_SKILLS_HOME:-$HOME/Documents/agent-skills}/scripts/plan_runner.py" normalize plans/active/<slug>.md --write`。stderr 顯示 `Wrote normalized plan` → 已 normalize（`.bak` 備份同目錄）；顯示 `WARN: Dependencies prose did not yield step IDs` → 該行需手動補 step ID
2. 初始化 state：`python3 "${AGENT_SKILLS_HOME:-$HOME/Documents/agent-skills}/scripts/plan_runner.py" init plans/active/<slug>.md`。若回 `No steps found in plan`，依回傳 payload 的 `hint` 欄位處理；`warnings` 欄位若僅為 range syntax 已自動展開可忽略
3. 提示使用者：
   > State 已初始化於 `<state_path>`。
   > 1a：輸入 `/plan-run plans/active/<slug>.md` 啟動狀態機推進；或執行 `python3 "${AGENT_SKILLS_HOME:-$HOME/Documents/agent-skills}/scripts/plan_runner.py" status plans/active/<slug>.md` 查看進度。
   > 1b：先輸入上一行，再貼入 `/goal plan_runner.py status plans/active/<slug>.md 的輸出顯示 all_done=true（即所有 step 都 completed / skipped，無 failed / blocked / in_progress） OR stop after 30 turns`。
   > 失敗時 Step 3d 仍會以 `AskUserQuestion` 詢問重試 / 跳過 / 中止；`/goal` 不自動跳過失敗，詳見 `/plan-run` Step 3f。

**若選 2（LLM 自主推進）：** 直接進入標準 implementation flow（plan 仍可隨時切換 `/plan-run`，state machine `init` 是冪等的，未來呼叫不影響）。

**若選 3（暫不開始）：** 結束 `/design`，告知：Plan 已存於 `plans/active/<slug>.md`，隨時可執行 `/plan-run plans/active/<slug>.md` 啟動狀態機，或直接在 session 中開始實作。

**跳過 Step 7 的情境：**

- 使用者已在 `$ARGUMENTS` 或對話中明示推進偏好
