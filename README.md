# agent-skills

My personal [Agent Skills](https://agentskills.io/) collection for Claude Code.

## Install

```bash
npx skills add ashe-li/agent-skills --global
```

<details>
<summary>Update / 新增 skill</summary>

```bash
# 更新已安裝的 skills
npx skills update

# 若有新增 skill，需重新 add
npx skills add ashe-li/agent-skills --global
```

</details>

## Usage

```
/pr                        # commit + push + PR（自動偵測 open PR）
/pr 1234                   # 更新指定 PR
/update                    # 更新知識庫（docs + patterns）
/update /pr                # 知識沉澱 + PR 一條龍
/design <需求>              # 建立實作計畫 → plans/active/<slug>.md
/assist                    # 自動偵測情境 → 最佳 pipeline
/assist <任務>              # 指定任務描述
/simplify                  # 自動修正程式碼（dead code、命名、nesting）
/notion-plan <URL>         # Notion 需求 → 實作計畫
/worktree                  # 列出所有 worktree 狀態
/worktree create <name>    # 建立新 worktree
/worktree cleanup          # 清理已 merge 的 worktree
/curation                  # 清理 learned skills 格式問題
/plan-archive              # 歸檔已完成的 plan
/ecc-skill-defer apply     # Defer 不常用的 skills
/playwright-human-in-the-loop  # 安全的瀏覽器自動化
```

## Skills 總覽

| Skill | 說明 |
|-------|------|
| [`/pr`](#pr--pr-自動化) | commit + push + PR，自動寫 description |
| [`/update`](#update--更新知識庫) | 文件更新 + 模式提取，可串接 `/pr` |
| [`/design`](#design--開發設計) | 盤點資源 → planner → architect → plan |
| [`/assist`](#assist--萬用助手) | 自動分析情境，智慧路由至最佳 pipeline |
| [`/simplify`](#simplify--自動修正) | code-reviewer 後自動修正（並行互補模式） |
| [`/notion-plan`](#notion-plan--notion-需求轉計畫) | Notion URL → 自動建立實作計畫 |
| [`/worktree`](#worktree--git-worktree-管理) | Worktree 建立、狀態、清理 |
| [`/curation`](#curation--learned-skills-品質管控) | 掃描 learned skills 格式、標準化、清理廢棄 |
| [`/plan-archive`](#plan-archive--歸檔-plan) | 將完成的 plan 歸檔至 `plans/completed/` |
| [`/ecc-skill-defer`](#ecc-skill-defer--skill-漸進式載入) | Defer/restore skills 減少 init tokens |
| [`/playwright-human-in-the-loop`](#playwright-human-in-the-loop--瀏覽器操作) | 瀏覽器自動化 + 重大操作人類確認 |

---

### `/pr` — PR 自動化

總結當前工作、commit、推送並建立或更新 PR。

<details>
<summary>Features & 串接細節</summary>

- Git 分析 + 對話脈絡雙重分析，PR description 涵蓋 what 和 why
- 自動 code review（安全性、正確性、debug code、TypeScript 型別）
- 自動偵測是否已有 open PR，決定建立或更新
- PR description 包含 Summary、Context、Changes、Test plan

</details>

### `/update` — 更新知識庫

將 session 工作沉澱為文件與可復用知識。Pipeline: doc-updater → code-reviewer → context 整理 → learn-eval

<details>
<summary>Features & 串接細節</summary>

- 自動掃描變更並更新 docs/codemaps/README
- code-reviewer 審查文件品質，確保文件與程式碼一致
- **對話 context 整理**：提取決策脈絡、研究成果、使用者偏好，HITL 確認後寫入知識庫
- learn-eval 提取可復用 patterns，強制 frontmatter + 5 維度評分表格
- **知識庫交叉比對強化**：偵測遺漏時主動起草修正內容並寫入（非僅報告差異）
- **Pipeline 串接**：`/update /pr` 一條指令完成知識沉澱 + PR 交付，自動去重

**`/update /pr` 資源去重：**

| `/pr` 步驟 | 單獨執行 | 從 `/update` 串接 | 原因 |
|-------------|:-------:|:-----------------:|------|
| Step 1a: Git 分析 | 完整 | 完整 | 目的不同 |
| Step 1b: 對話脈絡 | 完整 | 完整 | `/update` 不做此步驟 |
| Step 2: Quick Review | 完整 | **跳過** | `/update` 已做更深度審查 |
| /pr Step 3-6 | 完整 | 完整 | 無重疊 |

</details>

### `/design` — 開發設計

接收功能需求，盤點 ECC 資源，透過 planner + architect 建立實作計畫。輸出至 `plans/active/<slug>.md`。

<details>
<summary>Features</summary>

**Pipeline:** ECC 資源盤點 → 複雜度評估 → planner → architect → plan

- 自動盤點 agents/skills/commands，資源分配須經盤點確認
- 複雜度評估：簡單需求跳過 architect，走快速路徑
- planner 含業界實踐與標準化方案參照（RFC、OWASP、12-Factor 等）
- architect 審查架構 + 文件影響評估 + 參照可靠性
- 計畫品質自審檢查表（完整性、可執行性、依賴正確性、業界支撐）
- plan 含 Industry & Standards Reference 表格和品質保障步驟
- Step 0 條件式 HITL：評估複雜度後詢問是否啟用 TaskCreate/TaskUpdate
- Step 6 可選進入 worktree 隔離開發
- 不自動執行實作，使用者確認後才開始

</details>

### `/assist` — 萬用助手

自動分析情境、盤點 ECC 資源、智慧路由至最佳 agent pipeline。

<details>
<summary>路由規則 & Features</summary>

| 情境 | Pipeline |
|------|----------|
| 新功能需求 | planner → architect → tdd-guide → code-reviewer → /simplify |
| Bug 修復 | planner → tdd-guide → code-reviewer → /simplify |
| 未 commit 變更需 review | code-reviewer → /simplify → security-reviewer |
| Build 失敗 | build-error-resolver |
| 重構 | architect → refactor-cleaner → code-reviewer（已有 refactor-cleaner，不加 /simplify） |
| 文件更新 | doc-updater → code-reviewer（文件審查不適用） |
| Harness 設定優化 | harness-optimizer |
| 自主迴圈任務 | loop-operator |
| 預定義工作流模板 | `/orchestrate` command |
| 詢問該用哪個 model | `/model-route` command |
| 迴圈任務需監控 | loop-operator + `/loop-status` |
| 多模型協作（ace-tool MCP） | `multi-*` commands |
| 不確定 | 互動式選擇 |

- 自動偵測專案類型（Go/Python/Node.js），附加語言專用 reviewer
- 使用 handoff protocol 在 agents 間傳遞 context
- Step 0 條件式 HITL：評估複雜度後詢問是否啟用任務追蹤
- 無法判斷時透過互動式選單選擇 pipeline

</details>

### `/simplify` — 自動修正

code-reviewer 後自動修正程式碼品質問題，形成「診斷 → 治療」並行互補 pipeline。

<details>
<summary>Features & 並行互補模式</summary>

**Pipeline:** code-reviewer（只讀、診斷）→ /simplify（可寫、治療）

- 委派 refactor-cleaner agent（model: sonnet）執行自動修正
- 修正範圍：dead code 移除、命名改善、nesting 降低、重複程式碼合併
- HITL 確認：修正完成後讓使用者選擇適用全部 / 逐一確認 / 跳過
- 無可修正問題時自動跳過，不增加額外 token 消耗

**整合位置：**

| Skill | 整合方式 |
|-------|---------|
| `/pr` | Step 2b，Quick Review 後自動修正 |
| `/assist` | 新功能、Bug 修復、Review pipeline 自動附加 |
| `/design` | Plan 模板 Phase 2 品質保障步驟 |
| `/update` | 不整合（文件審查不適用） |

**不加 /simplify 的情況：**
- 重構 pipeline（已有 refactor-cleaner，避免重複）
- 文件審查 pipeline（code-reviewer 審查文件品質，非程式碼）

</details>

### `/notion-plan` — Notion 需求轉計畫

貼上 Notion URL，自動擷取內容並串接 `/design` 建立實作計畫。

<details>
<summary>Features</summary>

- 支援 `notion.so`、`notion.site`、短網址等多種 URL 格式
- Playwright MCP 抓取（Notion 為 CSR，不用 WebFetch）
- 自動處理長頁面捲動載入、Toggle 展開、登入偵測
- 擷取內容整理為結構化 Markdown 後，自動觸發 `/design`

</details>

### `/worktree` — Git Worktree 管理

Worktree 生命週期管理。統一存放至 `~/Documents/<repo>-<name>`。

<details>
<summary>子指令 & Features</summary>

| 子指令 | 說明 |
|--------|------|
| `status`（預設） | 列出所有 worktree + PR 狀態 + 磁碟用量 |
| `create <name>` | 建立新 worktree |
| `cleanup` | 清理已 merge/closed PR 的 worktree |
| `prune` | 清理孤立 metadata |

- Repo-agnostic：自動偵測 repo 名稱
- PR 偵測：顯示每個 worktree 對應的 PR 狀態
- 確認閘門：cleanup 前顯示清單等待確認
- Legacy 標記：標記非標準路徑（`.claude/worktrees/` → `[legacy]`）

</details>

### `/curation` — Learned Skills 品質管控

按需執行的維護工具，掃描 `~/.claude/skills/learned/` 的格式問題並標準化。

<details>
<summary>Features</summary>

- 掃描 learned skills：frontmatter 有無/完整度、評分格式、檔案大小
- 分類問題：無 frontmatter、不完整 frontmatter、評分格式不統一、已廢棄
- 格式問題自動修正（從內容推斷 name/description）
- 廢棄項目需 HITL 確認後才刪除
- 批次操作：全部修正 / 只修格式 / 逐一確認 / 只查看
- **明確不做**：不自動合併相似 skills、不重新評分

</details>

### `/plan-archive` — 歸檔 Plan

將 `plans/active/` 中完成的 plan 移至 `plans/completed/`，補上驗證結果與完成時間。

<details>
<summary>Features</summary>

- 自動偵測待歸檔的 plan（或手動指定檔名）
- 補充「狀態：完成」標記與驗證結果段落
- 內建 Hook 設定：`ExitPlanMode` PostToolUse hook 自動存至 `plans/active/`
- 內建 Rule 範本：加入 CLAUDE.md 確保主動歸檔

**目錄規範：** `plans/active/` → 進行中 ｜ `plans/completed/` → 已完成 ｜ `plans/archived/` → 長期封存

</details>

### `/ecc-skill-defer` — Skill 漸進式載入

管理 ECC skills 的 defer/restore 狀態，減少 init token 消耗。

<details>
<summary>Features</summary>

- `apply` 一鍵 defer config 中列出的 skills（rename → `.deferred.md`）
- `restore <name>` 臨時啟用 / `restore --all` 全部恢復
- `status` / `list` 檢視目前狀態
- 可自訂 `ecc-skill-defer.conf` 控制 defer 清單
- 預設 defer 24 skills，支援 marketplace 與 cache 雙路徑

</details>

### `/playwright-human-in-the-loop` — 瀏覽器操作

透過 Playwright MCP 操作瀏覽器，重大操作前暫停等待人類確認。

<details>
<summary>Features</summary>

- 操作分級：重大操作（建立/刪除、權限、費用、安全敏感欄位）需確認
- 安全敏感欄位即使是填寫也視為重大操作
- 每步驟後 `browser_snapshot` 確認頁面狀態
- 永不自動 Delete/Terminate，永不輸入 secrets，CAPTCHA/MFA 交由人類

</details>

<details>
<summary>Quality Management</summary>

### 品質評估框架

每個 skill 用 5 維度評估（來自 [skills-ecosystem-eval](https://github.com/ashe-li/skills-ecosystem-eval) 研究）：

| 維度 | 說明 | 來源 |
|------|------|------|
| Specificity | 指令和範例的具體程度 | 結構分析 |
| Actionability | 載入後是否真的改善輸出 | 消融實驗 |
| Scope Fit | 描述是否精準觸發 | 描述品質 |
| Non-redundancy | 與其他 skills 的重疊度 | Jaccard 比對 |
| Coverage | 章節和內容的完整度 | 結構分析 |

### 研究發現

- **靜態品質 != 動態效用**（r = -0.04）：結構漂亮不代表有幫助
- **82% 的 skills 確實有幫助**：平均提升 44 百分點 pass rate
- **33% 是「沉默浪費」**：結構好但消融測試無效果

### Triage 結果（2026-03-15）

基於 616 ablation runs 的四層分流處理：

| 動作 | 數量 | 效果 |
|------|------|------|
| 退役 degraded (T1) | 5 learned | delta -0.13~-0.25，幫倒忙 |
| 退役 Q3 delta=0 (T2) | 5 learned | 無效 context token |
| 補 frontmatter (T3) | 7 learned | delta +0.50~+1.00 的高效用 skills |
| Q4 審查清單 | 10 learned | 待人工判斷 |

Learned skills：144 → 134（-6.9%）。所有操作可逆（`skills-triage.sh restore`）。

#### ECC Agent 退化警告

| Agent | Delta | 說明 |
|-------|-------|------|
| `architect` | **-0.50** | 消融實驗中表現最差，建議 ECC 上游調查 |
| `build-error-resolver` | **-0.13** | 同為負向效果 |

這兩個 agents 位於 ECC plugin cache（`~/.claude/plugins/cache/everything-claude-code/`），
不適合直接退役（ECC 更新會覆蓋）。已在 `RETIRED_LOG.md` 標記 documented-only。

### CI 品質閘門

PR 修改 `SKILL.md` 時自動觸發 structural eval（0 tokens）。
完整評估（含消融）可在本地執行 bridge：

```bash
python ~/Documents/skills-ecosystem-eval/src/learn_eval_bridge.py <skill>.md --mode full
```

</details>

---

## 選什麼？

```
你現在要做什麼？
│
├─ 程式碼寫完了 ──────→ /pr
├─ Session 要收尾 ───→ /update（或 /update /pr）
├─ 準備開始新工作 ───→ /design <需求>
├─ 不確定 ──────────→ /assist
├─ 程式碼要簡化 ────→ /simplify
├─ 組合使用 ─────────→ /design → 實作 → /update /pr
├─ 需要操作瀏覽器 ──→ /playwright-human-in-the-loop
├─ Learned skills 要整理 → /curation
├─ Plan 要收尾 ─────→ /plan-archive
├─ 需要 worktree ───→ /worktree create <name>
├─ Worktree 要清理 ─→ /worktree cleanup
├─ 有 Notion ticket ─→ /notion-plan <URL>
└─ 優化 init tokens ─→ /ecc-skill-defer apply
```

<details>
<summary>各 skill 使用範例</summary>

### `/pr`

```
/pr                  # 自動偵測 open PR
/pr 1234             # 更新指定 PR
/update /pr          # 搭配 /update（推薦）
```

**適合：** 程式碼寫好、測試通過、準備交給 reviewer

### `/update`

```
/update              # 更新文件和提取 patterns
/update /pr          # 知識沉澱 + PR 一條龍（自動去重）
/update /pr 7238     # 知識沉澱 + 更新指定 PR
```

**適合：** 完成開發後同步文件、提取 patterns
**不適合：** 開發中途、程式碼尚未穩定

### `/design`

```
/design 實作使用者認證系統，支援 OAuth 和 JWT
/design 將 monolith API 拆分為獨立模組
/design 在 README 加上 CI badge          # 低複雜度快速路徑
```

**適合：** 不確定怎麼實作、跨多檔案變更、需要架構審查
**不適合：** 已知道怎麼做的小修改

### `/assist`

```
/assist                             # 自動分析環境
/assist 登入頁面一直報 500 錯誤
/assist 這段程式碼需要重構
/assist 幫我 review 目前的變更
```

**適合：** 不確定該用哪個工具
**不適合：** 已知道要用哪個 skill

### `/notion-plan`

```
/notion-plan https://www.notion.so/workspace/PDT-8590-abc123
/notion-plan https://workspace.notion.site/public-page-abc123
```

**適合：** 需求在 Notion，想一鍵轉為實作計畫

</details>

---

<details>
<summary>Benchmark</summary>

Output quality evaluation using [Anthropic skill-creator](https://github.com/anthropics/skills/tree/main/skills/skill-creator).

### `/design` — Output Quality Eval (2026-03-04)

**Method:** 2 test cases (with-skill vs without-skill baseline), model: `opus`

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

- **結構化格式** — Skill 嚴格遵循 plan 模板，baseline 自由格式
- **ECC 資源整合** — Skill 盤點 agents 並分配到步驟，baseline 不知有哪些 agent
- **複雜度路由** — Skill 正確識別低複雜度並跳過 architect
- **產出精簡** — Skill 產出更短（256 vs 451 行）但涵蓋所有區塊
- **安全閘門** — Skill 強制 PENDING APPROVAL，baseline 無此控制

</details>

### Other Commands

| Command | Eval Status | Note |
|---------|:-----------:|------|
| `/pr` | Pending | 涉及 git push + GitHub API，需 mock |
| `/update` | Pending | nested agents (doc-updater, code-reviewer, learn-eval) |
| `/assist` | Pending | 情境分析可測，pipeline 執行會修改專案 |
| `/notion-plan` | Pending | 依賴外部 Notion 頁面 |

</details>
