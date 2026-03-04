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

Trigger evaluation using [Anthropic skill-creator](https://github.com/anthropics/skills/tree/main/skills/skill-creator). Tests whether Claude correctly triggers each skill based on user prompts.

### Trigger Eval Results

#### Sonnet (2026-03-04)

**Config:** 10 queries per skill (5 should-trigger + 5 should-not-trigger), 1 run per query, model: `sonnet`

| Skill | Pass Rate | Should-Trigger | Should-Not-Trigger | Verdict |
|-------|-----------|:--------------:|:------------------:|---------|
| `/assist` | 5/10 (50%) | 0/5 | 5/5 | Undertriggered |
| `/design` | 5/10 (50%) | 0/5 | 5/5 | Undertriggered |
| `/pr` | 5/10 (50%) | 0/5 | 5/5 | Undertriggered |
| `/update` | 5/10 (50%) | 0/5 | 5/5 | Undertriggered |

#### Haiku Baseline (2026-03-04)

**Config:** 6 queries per skill (3 should-trigger + 3 should-not-trigger), 1 run per query, model: `haiku`

| Skill | Pass Rate | Should-Trigger | Should-Not-Trigger | Verdict |
|-------|-----------|:--------------:|:------------------:|---------|
| `/assist` | 3/6 (50%) | 0/3 | 3/3 | Undertriggered |
| `/design` | 3/6 (50%) | 0/3 | 3/3 | Undertriggered |
| `/pr` | 3/6 (50%) | 0/3 | 3/3 | Undertriggered |
| `/update` | 3/6 (50%) | 0/3 | 3/3 | Undertriggered |

### Structure Validation

| Skill | Valid | Issue |
|-------|:-----:|-------|
| `/assist` | WARN | `argument-hint` not in allowed frontmatter keys |
| `/design` | WARN | `argument-hint` not in allowed frontmatter keys |
| `/pr` | WARN | `argument-hint` not in allowed frontmatter keys |
| `/update` | PASS | -- |

### Learned Skills Audit

**50 learned skills** (auto-extracted patterns, `user-invocable: false`)

| Metric | Before | After |
|--------|--------|-------|
| Critical (missing frontmatter) | 10 | 0 |
| Warnings only | 3 | 10 (structural) |
| Clean | 37 | 40 |
| Avg Score | 6.3 | 7.3 |

Remaining 10 warnings are structural (missing `## Problem` / `## Solution` headings) -- not blocking.

### Analysis

**Negative discrimination: perfect.** Both Haiku and Sonnet correctly avoid triggering on unrelated prompts (100% across 40 negative queries). Cross-skill discrimination also works -- e.g., `/assist` negative set includes `/pr` and `/design` queries, none triggered.

**Positive triggering: 0%.** Identical across Haiku and Sonnet. This confirms the issue is **not model sensitivity**, but rather:

1. **`claude -p` eval mode**: Single-prompt mode may behave differently from interactive sessions where skills are consulted more readily.
2. **Skill count saturation**: 70+ visible skills in the system may cause the model to deprioritize individual skill matching.
3. **Description pushiness**: Per [Anthropic's guidance](https://claude.com/blog/improving-skill-creator-test-measure-and-refine-agent-skills), descriptions should be "a little bit pushy" to combat undertriggering.

### Next Steps

- [x] Re-run with Sonnet model for realistic trigger rates
- [x] Expand eval set to 10 queries per skill (5 positive + 5 negative)
- [x] Audit and fix learned skills (50 skills, 0 critical remaining)
- [ ] Optimize descriptions using `run_loop.py` (description optimization)
- [ ] Test with reduced skill count (disable ECC skills during eval)
- [ ] Add benchmark tracking across iterations

### Eval Queries

<details>
<summary>Click to expand Sonnet test queries</summary>

#### `/assist`

| Query | Should Trigger | Triggered | Result |
|-------|:--------------:|:---------:|:------:|
| 剛進入這個專案，不太確定現在該做什麼，幫我看一下狀況然後建議下一步 | Yes | No | FAIL |
| 登入頁面一直報 500 錯誤，牽涉到好幾個檔案，幫我搞定 | Yes | No | FAIL |
| 這段程式碼需要重構，太多重複邏輯，我不確定該走 TDD 還是直接改還是先 plan | Yes | No | FAIL |
| 我不確定該用哪個工具來重構這個 module，有很多 agent 可以選，幫我分析一下情境然後決定最好的做法 | Yes | No | FAIL |
| 幫我處理目前的狀況，我知道目標但不確定該用哪個工具最有效率 | Yes | No | FAIL |
| git log --oneline -5 給我看最近五筆 commit | No | No | PASS |
| 把 README.md 裡面的 typo 修一下，第 15 行 'recieve' 改成 'receive' | No | No | PASS |
| 幫我寫一個 Python function 計算費氏數列第 n 項 | No | No | PASS |
| 我想加一個 user authentication 功能，先幫我規劃整個實作計畫不要直接寫 code | No | No | PASS |
| 工作做完了，幫我 commit 然後開 PR 推上去 | No | No | PASS |

#### `/design`

| Query | Should Trigger | Triggered | Result |
|-------|:--------------:|:---------:|:------:|
| 這個功能跨很多檔案，我不確定怎麼實作，先幫我想清楚建立完整計畫 | Yes | No | FAIL |
| 想把 monolith API 拆分為獨立的 auth 和 payment 模組，幫我評估影響範圍規劃安全的重構步驟 | Yes | No | FAIL |
| 要新增 notification 系統，支援 email 和 push notification，先出 plan 讓我看看再說 | Yes | No | FAIL |
| 幫我設計一下 cache layer 的架構，Redis 還是 in-memory 比較適合這個 use case，需要架構審查再動手 | Yes | No | FAIL |
| 我想加一個 user authentication 功能，用 JWT 和 OAuth，先幫我規劃整個實作計畫不要直接寫 code | Yes | No | FAIL |
| 跑一下 test suite 看有沒有 fail 的 | No | No | PASS |
| 幫我 deploy 到 production，按照之前的流程 | No | No | PASS |
| 工作都做完了，總結一下然後幫我開 PR 推上去讓 reviewer 看 | No | No | PASS |
| 把這個 div 的 padding 從 16px 改成 24px | No | No | PASS |
| 這個 session 做了不少事，幫我更新文件和知識庫把學到的東西沉澱下來 | No | No | PASS |

#### `/pr`

| Query | Should Trigger | Triggered | Result |
|-------|:--------------:|:---------:|:------:|
| 工作做完了，幫我 commit 然後開 PR 推上去，自動寫好 description 讓 reviewer 看得懂 | Yes | No | FAIL |
| 我要更新 PR #42 的 description，加上這次新的改動說明 | Yes | No | FAIL |
| 程式碼已經寫好了，準備交給 reviewer，幫我推上去建立 pull request | Yes | No | FAIL |
| 剛補了幾個 commit，幫我更新現有 PR 的 description 讓它反映最新改動 | Yes | No | FAIL |
| 這個 branch 的功能都寫好測試也過了，幫我總結一下然後開 PR | Yes | No | FAIL |
| run pnpm test and show me the coverage report | No | No | PASS |
| 幫我加一個新的 API endpoint /api/v1/reports/{id} | No | No | PASS |
| 幫我 review 一下這段 code 有沒有 security issue | No | No | PASS |
| 幫我更新文件和知識庫，把這次 session 學到的 patterns 沉澱下來 | No | No | PASS |
| 先幫我規劃怎麼實作這個功能，不要直接寫 code | No | No | PASS |

#### `/update`

| Query | Should Trigger | Triggered | Result |
|-------|:--------------:|:---------:|:------:|
| 這個 session 做了不少事，幫我更新文件和知識庫，把學到的東西沉澱下來 | Yes | No | FAIL |
| 幫我跑一下文件更新流程，包括 code review 和 pattern extraction | Yes | No | FAIL |
| session 結束前幫我做知識沉澱，doc update 加上 learn-eval 那套流程 | Yes | No | FAIL |
| 大型重構剛做完，確保文件跟上程式碼變更，順便提取可復用的 patterns | Yes | No | FAIL |
| 這次討論有不少值得記錄的決策和除錯技巧，幫我沉澱到知識庫 | Yes | No | FAIL |
| 把 Docker compose 的 port mapping 改一下，8001 改成 8002 | No | No | PASS |
| 工作做完了，幫我 commit 然後開 PR 推上去 | No | No | PASS |
| 幫我寫 unit test 覆蓋 utils/search.py 的所有 public function | No | No | PASS |
| explain how the RAG pipeline works in this project | No | No | PASS |
| 我想加一個新功能，先幫我規劃實作計畫 | No | No | PASS |

</details>

## Install

```bash
npx skills add ashe-li/agent-skills --global
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
