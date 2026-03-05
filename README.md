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

**Pipeline:** ECC 資源盤點 → 複雜度評估 → planner → architect → plan.md

**Features:**
- 自動盤點可用的 agents/skills/commands，將合適的資源整合進計畫步驟
- 複雜度評估：簡單需求跳過 architect，走快速路徑
- planner 建立實作計畫，architect 審查架構設計 + 文件影響評估
- 計畫品質自審檢查表，確保完整性、可執行性、依賴正確性
- plan.md 包含實作後的品質保障步驟（code-reviewer、/update、/verify）
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

## When to Use What

四個 skill 各有分工，以下是常見情境的選擇指南：

### `/pr` — 工作完成，準備交付

```
# 寫完功能，要開 PR
/pr

# PR 已存在，補充新的 commit 後更新 description
/pr 1234

# 搭配 /update 使用：先沉澱知識，再開 PR
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
```

**適合：**
- 完成功能開發或 bug 修復後，要同步文件
- Session 中有值得記錄的 patterns、決策、除錯技巧
- 搭配 `/pr` 使用：`/update` 先沉澱 → `/pr` 再交付

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
└─ 組合使用 ─────────→ /design → 實作 → /update → /pr
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

已安裝後，檢查並更新到最新版本：

```bash
npx skills check   # 查看有無新版本
npx skills update  # 更新所有 skills 到最新版本
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
