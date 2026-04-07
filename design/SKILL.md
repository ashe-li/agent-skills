---
name: design
description: 開發設計 — 自動盤點 ECC 資源，透過 planner 建立完整實作計畫，輸出至 plans/active/<slug>.md 供使用者確認後才進入實作。
allowed-tools: Bash, Read, Glob, Grep, Edit, Write, Agent, AskUserQuestion, EnterPlanMode, TaskCreate, TaskUpdate, TaskList
argument-hint: <功能描述或需求>
redundancy-peers: [assist]
---

# /design — 開發設計

接收功能需求，自動盤點可用的 ECC 資源（agents/skills/commands），透過 planner 建立完整實作計畫並輸出至 plans/active/<slug>.md。**不會自動執行實作，必須等使用者確認。**

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
- TaskCreate 父任務（subject: `/design: [需求摘要]`）
- 每個 Step 開始前 TaskCreate 子任務（含 activeForm），**保留回傳的 task ID**，完成後 TaskUpdate 為 completed
- 有依賴的步驟使用 addBlockedBy 標記（填入前序步驟 TaskCreate 回傳的 task ID）
- 不啟用則完全跳過所有 Task 工具呼叫

## Step 1: 盤點 ECC 資源

> **若啟用 task tracking：** TaskCreate(subject: "盤點 ECC 資源", activeForm: "掃描 agents/skills/commands 中...") → 完成後 TaskUpdate(status: "completed")

掃描所有可用的 ECC agents、skills 和 commands，作為後續規劃的參考。

**掃描方式：**

1. 列出所有可用的 agent types（從 Agent tool 的 subagent_type 列表），重點關注：`planner`、`tdd-guide`、`code-reviewer`、`security-reviewer`、`build-error-resolver`、`harness-optimizer`、`loop-operator`、`docs-lookup`（Context7 文件查詢）、`typescript-reviewer`
2. 列出所有可用的 skills（從 system-reminder 中的 skill 列表）
3. 列出所有可用的 commands（從 `~/.claude/commands/` 和 plugin commands），重點關注：`/harness-audit`、`/quality-gate`、`/loop-start`、`/docs`（Context7 文件查詢）、`/aside`（側問不丟失 context）、`/skill-health`（skill portfolio 健康儀表板）、`/prompt-optimize`、`/blueprint`（多 session 建構計畫）、`/context-budget`（context window 稽核）、`/save-session`、`/resume-session`
4. 根據使用者的需求（`$ARGUMENTS`），篩選出**相關的資源**

**輸出：** 一份簡潔的資源清單，標記每個資源與當前需求的關聯程度（高/中/低）。

## Step 2: 複雜度評估

根據需求規模，選擇執行路徑：

| 複雜度 | 判斷標準 | 執行路徑 |
|--------|---------|---------|
| **低** | 單一檔案、不涉及架構變更（例：更新 README、加 badge、改設定） | Step 3 → Step 4（跳過架構審查） |
| **中等以上** | 跨檔案或有架構影響 | Step 3（含架構決策）→ Step 4（含架構審查）|
| **多 session** | 需跨 session 持續推進的大型計畫（遷移框架、多天重架構） | 先執行 `/blueprint` 建立多 session 建構計畫，再從 Phase 1 進入標準流程 |

如果需求過於模糊無法判斷複雜度，使用 AskUserQuestion 請使用者補充具體資訊。

> 無論走哪條路徑，都會輸出 plans/active/<slug>.md。

## Step 3: planner — 建立實作計畫

> **若啟用 task tracking：** TaskCreate(subject: "planner 建立實作計畫", activeForm: "planner agent 規劃中...") → 完成後 TaskUpdate(status: "completed")

使用 **planner** agent 建立詳細的實作計畫。

```
Agent(subagent_type="everything-claude-code:planner")
```

**輸入 context：**

- 使用者的需求描述（`$ARGUMENTS` + 對話脈絡）
- Step 1 盤點的 ECC 資源清單
- 當前專案結構（透過 `ls`、`git status` 等了解）

**計畫須包含：**

1. **需求拆解** — 將需求分解為可執行的子任務
2. **技術方案** — 每個子任務的實作方式
3. **業界實踐與標準化方案參照** — 每個技術方案須附上依據
   - 是否有業界標準或慣例（RFC、W3C、OWASP、12-Factor 等）
   - 是否有學術研究支撐（論文、benchmarks）
   - 是否有成熟的標準化解決方案（官方 SDK、知名 library 的 canonical pattern）
   - **社群共識** — 主流社群（GitHub discussions、Stack Overflow、官方 forum、Reddit）對此做法的主流看法
   - **反面意見與已知陷阱** — 針對此方案已知的批評、替代觀點、踩坑經驗或 trade-offs（即使最終仍採用，也須揭露）
   - 若無直接標準，說明為何採用自訂方案，並引述最接近的參考
4. **架構決策**（中等以上複雜度必填）— 每個涉及架構的技術選擇須包含：
   - 選擇的方案及理由
   - 考慮過但排除的替代方案（至少 1 個），附排除原因
   - 與現有程式碼的相容性分析
   - 可擴展性評估 — 能否應對合理範圍的需求增長
   - 效能和安全性影響評估
   - 實作後需要新增或更新的文件（README、API docs、CODEMAPS）
5. **需求追蹤矩陣（Requirements Traceability Matrix）**（依據：DAMA-DMBOK Completeness）— 每個需求必須映射到至少一個實作步驟，格式如下：

   | 需求 ID | 需求描述（來自 $ARGUMENTS） | 對應實作步驟 | 驗收標準 |
   |--------|--------------------------|------------|---------|
   | REQ-1 | 使用者描述的需求 A | Step 2: xxx | 可客觀驗證的標準 |
   | REQ-2 | 使用者描述的需求 B | Step 3, Step 5 | ... |
   | ... | ... | ... | ... |

   此矩陣用於 Step 4a 需求覆蓋率審查的輸入（set difference 比對）。

6. **ECC 資源整合** — 明確指出每個步驟應使用哪個 agent/skill/command。**ECC 資源分配須經盤點確認** — 不可假設資源可用，須回溯 Step 1 的盤點結果
   - 例：「Step 3: 使用 tdd-guide agent 撰寫測試」
   - 例：「Step 5: 使用 /code-review 進行品質審查」
7. **依賴關係** — 哪些步驟必須按順序執行、哪些可以並行
8. **風險評估** — 潛在的技術風險和對策
9. **驗收標準** — 怎樣算完成

## Step 4: 品質檢查與 Plan Mode 呈現

> **若啟用 task tracking：** TaskCreate(subject: "品質檢查與 Plan Mode 呈現", activeForm: "品質檢查與輸出中...", addBlockedBy: [Step 3 TaskCreate 回傳的 task ID]) → 完成後 TaskUpdate(status: "completed")

### Step 4a: 品質審查（subagent 隔離）

啟動 **general-purpose subagent** 在隔離 context 中審查 Step 3 的計畫。不使用 `everything-claude-code:architect`（消融實驗 delta=-0.50），改用通用 agent 搭配明確審查 prompt。

```
Agent(subagent_type="general-purpose", model="sonnet")
```

**傳入 subagent 的 context：**

- Step 3 產出的完整計畫內容
- Step 1 的 ECC 資源盤點結果
- Step 2 判定的複雜度等級
- 下方的審查檢查清單

**subagent 須逐項檢查並回報結果（PASS/FAIL + 說明）。對於「業界支撐」和「社群共識」相關維度，subagent 應主動驗證：**
1. **業界支撐** — 每個技術方案的做法是否有業界支撐（RFC、W3C、OWASP、12-Factor、官方文件、知名 library 的 canonical pattern），若計畫中缺少引述或引述有誤，標記為 FAIL
2. **社群共識與反面意見** — 每個技術方案是否納入社群主流看法和已知反面意見（GitHub discussions、Stack Overflow、Reddit、官方 forum 的討論），若計畫中完全未提及社群觀點或已知陷阱，標記為 FAIL

**基礎品質（所有複雜度）：**

| 維度 | 通過標準 |
|------|---------|
| 需求覆蓋率 | 需求追蹤矩陣的每個 REQ-N 都有對應實作步驟（set difference = 0）（依據：DAMA-DMBOK Completeness） |
| 完整性 | 每個需求都有對應的實作步驟 |
| 可執行性 | 每個步驟有明確的檔案路徑和具體動作 |
| 依賴正確性 | 步驟間依賴無環且順序合理 |
| 粒度適當 | 每個步驟 1-3 個具體動作 |
| ECC 資源合理 | 每個 agent/skill 在其設計用途內使用，且對照 Step 1 盤點結果確認可用 |
| 驗收可測 | 每個驗收標準都可客觀驗證 |
| 業界/學術支撐 | 每個技術方案都有明確的業界標準、學術研究或標準化方案參照 |
| 社群共識與反面意見 | 每個技術方案都納入社群主流看法，並揭露已知的反面意見、批評或陷阱 |
| 實作後工具 | plan 的步驟中包含實作後的品質保障（code-reviewer、/simplify、/update、/verify 等） |
| Eval 基線 | 若涉及行為變更，計畫中包含 eval 基線建立與回歸驗證步驟（參考 `/quality-gate`） |

**需求覆蓋率驗證方法（依據：DAMA-DMBOK Completeness + arXiv:2509.18970）：**

subagent 必須基於需求追蹤矩陣執行 set difference 比對，不可用語意判斷代替計數驗證：

1. 從 $ARGUMENTS 和對話脈絡中，枚舉所有明確需求（REQ-1, REQ-2, ...），總計 N 個
2. 檢查需求追蹤矩陣：每個 REQ-N 是否有對應的實作步驟
3. 計數比對：`total_requirements（N）- requirements_with_steps（K）= uncovered（差集）`
4. 差集 > 0 → 需求覆蓋率 FAIL，列出未覆蓋的需求

| 需求 | 有對應步驟？ | 判定 |
|------|------------|------|
| REQ-1: xxx | Step 2 ✅ | PASS |
| REQ-2: yyy | ——（無） | FAIL |
| **計數** | N 個需求，K 個已覆蓋 | 差集 = N-K |

**架構審查（中等以上複雜度才執行）：**

| 維度 | 通過標準 |
|------|---------|
| 替代方案 | 每個架構決策都列出至少 1 個被排除的替代方案及原因 |
| 簡單性 | 沒有更簡單的方案能達成同樣目標（若有，須說明為何選複雜方案） |
| 可擴展性 | 架構能應對合理範圍的需求增長，不會因規模變化需要重寫 |
| 相容性 | 與現有程式碼的整合方式明確，無破壞性變更或已標記 migration 步驟 |
| 效能/安全 | 已評估效能影響和安全性考量 |
| 參照可靠性 | 引用的業界標準適用於當前情境、為最新版本、無更好的標準被遺漏 |
| 文件影響 | 已列出實作後需新增或更新的文件（README、API docs、CODEMAPS） |

**subagent 回報後的處理：**

- **全部 PASS：** 進入 Step 4b
- **有 FAIL 項目：** 回饋至 Step 3 重新規劃（將 FAIL 項目和修正方向作為額外 context 傳給 planner），修正後重新啟動 Step 4a subagent 檢查。初次檢查 + 最多 2 次重試（共最多 3 輪）
- **經 3 輪仍有 FAIL：** 在 plan 的 Review Notes 中標記待確認項目，交由使用者決定

### Step 4b: EnterPlanMode 呈現計畫

品質檢查通過後，使用 `EnterPlanMode` 將完整計畫內容呈現給使用者。

**為什麼用 Plan Mode：** Claude Code 2.1.77+ 在使用者接受 plan 時會自動命名 session。透過 Plan Mode 呈現計畫，使用者 accept 後觸發 session auto-naming，同時作為正式的計畫確認流程。

**呈現內容：** 將完整 plan（依照下方格式）作為 Plan Mode 的輸出。Plan Mode 中不可使用 Write/Edit，所以**只呈現，不寫檔**。

**使用者 accept 後：** ExitPlanMode 觸發 session auto-naming，接著進入 Step 5 寫檔。

## Step 5: 生成 Slug 並寫入 plan 檔案

使用者在 Plan Mode 中 accept 後（ExitPlanMode），將計畫寫入檔案。

**Slug 生成規則：**

從 plan 的 H1 標題（`# Implementation Plan: <名稱>`）生成 kebab-case slug：

1. 取 `:` 後的標題部分（去除 `Implementation Plan:` 前綴）
2. 保留英文字母、數字、`-`；中文字跳過
3. 空格和特殊字元轉 `-`，連續 `-` 合併，兩端修剪
4. 全部小寫，截斷至最多 60 字元
5. 若結果為空（純中文標題），使用 `plan-YYYYMMDD`

範例：
- `PDT-8953 過濾 abstract 中的 URL` → `pdt-8953-url`
- `Stripe Subscription Billing` → `stripe-subscription-billing`

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

## ECC Resources
<!-- 本次計畫使用的 agents/skills/commands -->

| Resource | Type | Usage |
|----------|------|-------|
| tdd-guide | agent | Step 3: 撰寫測試 |
| code-reviewer | agent | Step 5: 品質審查 |
| refactor-cleaner | agent | Phase 2: /simplify 自動修正（dead code、命名、nesting、重複程式碼合併） |
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

### Phase 1: [實作階段]
- [ ] Step 1: ...
  - Agent: `planner`
  - Input: ...
  - Output: ...

### Phase 2: [品質保障]
- [ ] 執行 code-reviewer 審查程式碼品質
- [ ] 執行 /simplify 自動修正（refactor-cleaner：dead code、命名、nesting、重複程式碼合併）
- [ ] 執行 security-reviewer 檢查安全性
- [ ] 執行 /verify 進行全面驗證

### Phase 3: [文件同步]
- [ ] 執行 /update 更新知識庫（doc-updater + learn-eval）
- [ ] 或手動執行 /update-docs + /update-codemaps

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

plan 寫入後，使用 AskUserQuestion 詢問使用者：

> 是否要在 worktree 中進行隔離開發？
>
> Worktree 可將實作隔離在獨立目錄，不影響主工作區。
> PR merge 後 worktree 會自動回收，不需手動管理。
>
> 1. **進入 worktree** — 建立隔離環境，在新 session 中開發
> 2. **不使用** — 在當前工作區直接開發

若使用者選擇「進入 worktree」：
1. 從 plan slug 推導 worktree 名稱（例：`pdt-8953-url`）
2. 呼叫 `EnterWorktree(name: "<slug>")`
3. 提示使用者：開發完成後執行 `/pr`，worktree 將在 session 結束時自動回收

若使用者選擇「不使用」：跳過，結束 /design 流程。
