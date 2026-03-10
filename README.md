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
- **Pipeline 串接**：`/update /pr` 一條指令完成知識沉澱 + PR 交付，自動去重避免重複工作

**Pipeline 串接與資源去重：**

`/update` 完成後可自動觸發 `/pr`，透過 `[PIPELINE: from /update]` 標記通知下游 skill 跳過已完成的步驟：

| `/pr` 步驟 | 單獨執行 | 從 `/update` 串接 | 原因 |
|-------------|:-------:|:-----------------:|------|
| Step 1a: Git 分析 | 完整執行 | 完整執行 | 目的不同：`/update` 只需檔案清單，`/pr` 需要完整 diff + commit history |
| Step 1b: 對話脈絡分析 | 完整執行 | 完整執行 | `/update` 不做此步驟 |
| Step 2: Quick Review | 完整執行 | **跳過** | `/update` Step 2 已用 code-reviewer agent 做過更深度審查 |
| Step 3-6 | 完整執行 | 完整執行 | 無重疊 |

### `/design` — 開發設計

接收功能需求，自動盤點 ECC 資源，透過多個 agent 協作建立完整實作計畫。輸出 plan.md 供使用者確認後才進入實作。

**Pipeline:** ECC 資源盤點 → 複雜度評估 → planner → architect → plan.md

**Features:**
- 自動盤點可用的 agents/skills/commands，資源分配須經盤點確認（不可假設可用）
- 複雜度評估：簡單需求跳過 architect，走快速路徑
- planner 建立實作計畫，含業界實踐與標準化方案參照（RFC、OWASP、12-Factor 等）
- architect 審查架構設計 + 文件影響評估 + 業界/學術參照可靠性
- 計畫品質自審檢查表，確保完整性、可執行性、依賴正確性、業界/學術支撐
- plan.md 包含 Industry & Standards Reference 表格和實作後的品質保障步驟
- 不自動執行實作，使用者確認後才能開始

### `/assist` — 萬用助手

自動分析情境、盤點 ECC 資源、智慧路由至最佳 agent pipeline。適合不確定該用哪個工具時使用。

**智慧路由規則：**

| 情境 | Pipeline |
|------|----------|
| 新功能需求 | planner（含業界/學術調研）→ architect → tdd-guide → code-reviewer |
| Bug 修復 | planner → tdd-guide → code-reviewer |
| 未 commit 變更需 review | code-reviewer → security-reviewer |
| Build 失敗 | build-error-resolver |
| 重構 | architect → refactor-cleaner → code-reviewer |
| 文件更新 | doc-updater → code-reviewer |
| Harness 設定優化 | harness-optimizer |
| 自主迴圈任務 | loop-operator |
| 不確定 | 互動式選擇 |

**Features:**
- 自動偵測專案類型（Go/Python/Node.js），附加語言專用 reviewer
- 使用 handoff protocol 在 agents 間傳遞 context（含 Industry & Standards Referenced）
- ECC 資源分配原則：路由前確認 pipeline 中所有 agent 可用，若已被 defer 則提示 restore 或替代 pipeline
- 無法判斷時透過互動式選單讓使用者選擇 pipeline

### `/playwright-hitl` — Playwright Human-in-the-Loop 瀏覽器操作

透過 Playwright MCP 操作瀏覽器，自動執行低風險操作，重大操作前暫停等待人類確認。

**Features:**
- 操作分級：重大操作（建立/刪除資源、修改權限、費用、安全敏感欄位）需人類確認，非重大操作自動執行
- 安全敏感欄位（Policy JSON、IAM policy document）即使是填寫也視為重大操作
- 每步驟後 `browser_snapshot` 確認頁面狀態
- 永不自動 Delete/Terminate，永不輸入 secrets，CAPTCHA/MFA 交由人類處理

### `/ecc-skill-defer` — ECC Skill 漸進式載入

管理 ECC skills 的 defer/restore 狀態，將不常用的 skill 延遲載入以減少 init token 消耗。

**Features:**
- `apply` 一鍵 defer config 中列出的 skills（rename SKILL.md → SKILL.deferred.md）
- `restore <name>` 臨時啟用單一 deferred skill
- `restore --all` 全部恢復
- `status` / `list` 檢視目前狀態
- 可自訂 `ecc-skill-defer.conf` 控制 defer 清單
- 預設 defer 23 skills（Django/Spring Boot/Java/C++/business/meta），支援 marketplace 與 cache 雙路徑

### `/plan-archive` — 歸檔已完成的 Plan

實作完成後，將 `plans/active/` 中的 plan 移至 `plans/completed/`，補上驗證結果與完成時間。

**Features:**
- 自動偵測 `plans/active/` 中待歸檔的 plan（或手動指定檔名）
- 補充「狀態：✅ 完成」標記與驗證結果段落
- 內建 Hook 設定說明：`ExitPlanMode` PostToolUse hook 自動將 plan 存至 `plans/active/`
- 內建 Rule 範本：加入 CLAUDE.md 確保 Claude 主動執行歸檔

**目錄規範：** `plans/active/` → 進行中；`plans/completed/` → 已完成；`plans/archived/` → 長期封存

## When to Use What

四個 skill 各有分工，以下是常見情境的選擇指南：

### `/pr` — 工作完成，準備交付

```
# 寫完功能，要開 PR
/pr

# PR 已存在，補充新的 commit 後更新 description
/pr 1234

# 搭配 /update 使用：一條指令完成（推薦）
/update /pr

# 或分開執行
/update
/pr
```

**適合：** 程式碼已經寫好、測試通過、準備交給 reviewer

### `/update` — Session 結束，沉澱知識

```
# 完成一段工作後，更新文件和提取 patterns
/update

# 這次 session 只有討論沒有改 code（learn-eval 仍可提取 patterns）
/update

# 大型重構後，確保文件跟上程式碼變更
/update

# Pipeline 串接：知識沉澱 + PR 一條龍完成（自動去重）
/update /pr

# Pipeline 串接：知識沉澱 + 更新指定 PR
/update /pr 7238
```

**適合：**
- 完成功能開發或 bug 修復後，要同步文件
- Session 中有值得記錄的 patterns、決策、除錯技巧
- 搭配 `/pr` 使用：`/update /pr` 一條指令完成（推薦），或分開執行

**不適合：** 還在開發中途、程式碼尚未穩定

### `/design` — 動手之前，先想清楚

```
# 新功能：自動盤點可用工具，建立完整計畫
/design 實作使用者認證系統，支援 OAuth 和 JWT

# 重構：評估影響範圍，規劃安全的重構步驟
/design 將 monolith API 拆分為獨立的 auth 和 payment 模組

# 簡單需求：自動偵測為低複雜度，走快速路徑
/design 在 README 加上 CI badge
```

**適合：**
- 不確定怎麼實作，需要先規劃
- 跨多個檔案的變更，想確認影響範圍
- 想知道該用哪些 ECC agents/skills 來完成任務
- 需要架構審查再動手

**不適合：** 已經知道怎麼做的小修改（直接改就好）

### `/assist` — 不確定該用什麼，讓它幫你選

```
# 不帶參數：自動分析 git 狀態和專案環境，選擇最佳 pipeline
/assist

# 帶任務描述：根據描述選擇 pipeline
/assist 登入頁面一直報 500 錯誤
/assist 這段程式碼需要重構，太多重複邏輯
/assist 幫我 review 目前的變更
```

**適合：**
- 剛進入專案，不確定現在該做什麼
- 知道目標但不確定該用哪個工具
- 想要一鍵完成完整的 agent pipeline

**不適合：** 已經知道要用哪個 skill（直接用 `/pr`、`/update`、`/design`）

### 選擇流程圖

```
你現在要做什麼？
│
├─ 程式碼寫完了 ──────→ /pr（commit + push + PR）
│
├─ Session 要收尾 ───→ /update（文件 + patterns）
│
├─ 準備開始新工作 ───→ /design <需求>（規劃 → plan.md）
│
├─ 不確定 ──────────→ /assist（自動分析 + 路由）
│
├─ 組合使用 ─────────→ /design → 實作 → /update /pr（一條龍）
│
├─ 需要操作瀏覽器 ──→ /playwright-hitl（安全的瀏覽器自動化）
│
├─ Plan 寫完要收尾 ─→ /plan-archive（歸檔至 completed/）
│
└─ 優化 init tokens ─→ /ecc-skill-defer apply
```

## Benchmark

Output quality evaluation using [Anthropic skill-creator](https://github.com/anthropics/skills/tree/main/skills/skill-creator). Tests whether each command produces structured, high-quality outputs compared to a baseline (no skill).

### `/design` — Output Quality Eval (2026-03-04)

**Method:** 2 test cases (with-skill vs without-skill baseline), model: `opus`, graded against assertions

| | With Skill | Baseline | Delta |
|---|:---:|:---:|:---:|
| **Pass Rate** | **100%** | 32.5% | **+67.5%** |
| **Avg Time** | 167s | 138s | +29s |
| **Avg Tokens** | 61,294 | 68,903 | -7,609 |

<details>
<summary>Eval details</summary>

#### Eval 1: Webhook Notification System (medium-high complexity)

| Assertion | With Skill | Baseline |
|-----------|:---:|:---:|
| plan.md 被建立 | PASS | PASS |
| 包含 `## Overview` | PASS | FAIL |
| 包含 `## ECC Resources` 表格 | PASS | FAIL |
| 包含 `## Implementation Steps` (分 Phase) | PASS | FAIL |
| 包含 `## Risks & Mitigations` | PASS | FAIL |
| 包含 `## Acceptance Criteria` | PASS | PASS |
| 包含品質保障步驟 (code-reviewer/security-reviewer) | PASS | FAIL |
| 狀態標記 PENDING APPROVAL | PASS | FAIL |

#### Eval 2: CI Badge (low complexity)

| Assertion | With Skill | Baseline |
|-----------|:---:|:---:|
| plan.md 被建立 | PASS | PASS |
| 包含 `## Overview` | PASS | FAIL |
| 包含 `## Implementation Steps` | PASS | FAIL |
| 正確跳過 architect (低複雜度快速路徑) | PASS | PASS |
| 狀態標記 PENDING APPROVAL | PASS | FAIL |

#### Key Findings

- **結構化格式** -- Skill 嚴格遵循 plan.md 模板，baseline 使用自由格式（section 名稱不統一）
- **ECC 資源整合** -- Skill 盤點 agents 並分配到具體步驟，baseline 不知道有哪些 agent 可用
- **複雜度路由** -- Skill 正確識別低複雜度需求並跳過 architect，baseline 無此判斷
- **產出精簡** -- Skill 產出更短（256 vs 451 行）但涵蓋所有必要區塊
- **安全閘門** -- Skill 強制 PENDING APPROVAL 防止未經確認就執行，baseline 無此控制

</details>

### Other Commands

| Command | Eval Status | Note |
|---------|:-----------:|------|
| `/pr` | Pending | 涉及 git push + GitHub API，需 mock 環境 |
| `/update` | Pending | 涉及 nested agents (doc-updater, code-reviewer, learn-eval) |
| `/assist` | Pending | 情境分析可測，但 pipeline 執行會修改專案 |

## Install

```bash
npx skills add ashe-li/agent-skills --global
```

## Update

已安裝後，更新現有 skills 到最新版本：

```bash
npx skills update
```

> **注意：** `skills update` 只更新**已安裝**的 skills。若有新增 skill（如 `/plan-archive`），需重新執行 `add` 才能安裝：
>
> ```bash
> npx skills add ashe-li/agent-skills --global
> ```

## Usage

```
/pr                        # 自動偵測是否有 open PR，沒有就建新的
/pr 1234                   # 更新指定 PR 的 description
/update                    # 更新知識庫（docs + patterns）
/update /pr                # 知識沉澱 + PR 一條龍（自動去重）
/update /pr 7238           # 知識沉澱 + 更新指定 PR
/design <需求>              # 建立實作計畫
/assist                    # 自動偵測情境並執行最佳 pipeline
/assist <任務>              # 指定任務描述
/plan-archive              # 歸檔已完成的 plan（自動偵測）
/plan-archive my-plan.md   # 指定檔名歸檔
/ecc-skill-defer           # 查看 ECC skill defer 狀態
/ecc-skill-defer apply     # Defer 不常用的 skills
/ecc-skill-defer restore X # 臨時啟用某個 skill
/playwright-hitl           # 瀏覽器操作（自動 + 人類確認）
```
