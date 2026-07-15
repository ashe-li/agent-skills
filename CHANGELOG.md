# Changelog

所有重要變更都記錄在這裡。格式參考 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.0.0/)。

## [Unreleased]

### Added
- **`rules/debug-triage-order.md`（新規則，預設不 symlink 常駐）**：debug 順序三規則——① prod-first read-only probe（建本地重現環境前先對回報環境做唯讀探測，一次分流環境差異 bug vs 邏輯 bug）；② evidence-first before dispatch（派驗證 agent 前先盤點手上證據，1-2 指令可定案的 inline 跑）；③ verify-via-spec once（regression spec 寫好後驗收＝fresh context 實跑 spec，不散文重推導手動步驟、不疊第三輪驗證）。動機：2026-07-16 vocus 投票 RCA session 複盤，三浪費點合計 ~30-40% session 時間與 3 輪可避免派工。是否 symlink 進 `~/.claude/rules/common/`（常駐載入成本）由使用者 merge 後決定。
- **自持 `agents/` 目錄**（`complexity-triage` / `doc-reviewer` / `doc-updater` / `tdd-guide`）：收斂 v2.0.0 後散落 `design/SKILL.md`、`update/SKILL.md` 各處的裸 `general-purpose` 審查/更新 prompt 為單一權威定義（frontmatter + 檢查清單 + 紅旗），`design/SKILL.md` Step 4a 與 `update/SKILL.md` Step 1-2 均改為引用；維持 ECC 解耦（定義自持於本 repo、無 plugin runtime 依賴）。`planner` → 內建 `Plan` agent、`code-reviewer`（程式碼）→ `/code-review`、`security-reviewer` → `/security-review`、`refactor-cleaner` → `/simplify`、`learn-eval` → inline 5 維 rubric 維持原生替代不重建；`tdd-guide` 是 v2.0.0 唯一未落地明確替代的 agent，本次補回。
- **`/design` Step 2a 複雜度分診 subagent**：進入完整流程前先派 haiku 輕量 agent（Glob/Grep/Read 粗估、固定 JSON 輸出）判定 low/medium/multi-session；主模型保留最終裁決且衝突時取較高複雜度；`/notion-plan` 串接時同樣生效。~5-15K tokens 換掉低複雜度任務誤入完整儀式的成本（2026-07-10 使用者指示）。

### Changed
- **`/design` HITL 合併為單一批次詢問**：Task 追蹤（原 Step 0）、Worktree（原 Step 6）、推進方式（原 Step 7）、編隊授權（原全域規則觸發時散問）四題合併成 plan 寫入後的**一次 AskUserQuestion**（工具單次支援 4 題），答案已知的題目自動剔除、全剔除則跳過；批次後不再為這四類決策二次發問，實作中新裁決發問前先派出不依賴答案的工作。另補：plan 呈現與批次詢問前可發 `PushNotification` 提醒（若 harness 支援）。動機：2026-07-16 PDT-10398 session 實測，3.5h 全程 ~85–100 min 為 HITL 等待（~45%），其中 ~40 min 來自 4 個分散 AskUserQuestion 各自 block、43 min 來自使用者不知 plan 已就緒的 approval 空窗——瀏覽器自斷等技術問題僅損 ~5 min，等待才是主要時間浪費。
- **`/design` 新增低複雜度快速路徑**：單一 bug fix / ≤3 檔 / 無架構決策的任務，計畫只要求需求拆解、技術方案、依賴、風險、驗收（S-code 格式不變，/plan-run 相容）；跳過業界參照表、社群共識表、RTM、逐 step token 預算；Step 4a 改主模型 6 項 self-check、不派 subagent 審查。動機：PDT-10428 前例——P2 顯示 bug 走完整儀式被 61K-token 審查 agent 以格式官僚項目打回，4 個 FAIL 無一改變實作方向。診斷 gate（live 驗證）明文標為不可裁剪。
- **`/notion-plan` 補 headless session 不穩定止損守則**：第一次 snapshot 撈齊 properties/comments；互動展開重試上限 2 次，失敗標註缺口交 HITL，不無限重試。

## [v2.0.0] - 2026-07-08

> ⚠️ **BREAKING CHANGE — 本 repo 首次 major bump。**
> 本版移除對 **everything-claude-code (ECC) plugin** 的所有 hard-runtime 依賴（約 46 處），改用 Claude Code 內建 primitives。skill 對外介面（指令名、plan 格式契約、DSL、安全紅線）不變，但**內部呼叫的 agent 全數更換**。若你的環境靠 ECC agents 被這些 skill 呼叫、或有硬編 `everything-claude-code:*` 名稱的 hook/腳本，見下方 **Migration**。要維持 ECC 行為請 pin `v1.28.0`。

### Removed
- **移除對 everything-claude-code (ECC) plugin 的 hard-runtime 依賴**（約 46 處）：`/design`、`/update`、`/pr`、`/assist` 不再呼叫任何 ECC agent，改用 Claude Code 內建 primitives。對照：
  - `planner` → 內建 `Plan` agent
  - `code-reviewer`（審程式碼）→ `/code-review`；（審文件）→ `general-purpose` fresh 驗收 agent
  - `security-reviewer` → `/security-review`
  - `refactor-cleaner` → `/simplify`
  - `doc-updater` → `general-purpose` + 明確 prompt + 主模型 `git diff` 驗證
  - `/update` Step 4 `learn-eval` → inline 5 維 rubric（格式契約不變；深度交叉驗證仍可走 `/learn-eval-deep`）
  - 無前綴 agent 名稱（`code-reviewer` 等）經查證同屬 ECC plugin 雙重註冊，故解耦改內建 primitives 而非只拿掉前綴

### Changed
- **4.8+ 模型適配精簡**：18 個 SKILL.md 總行數 4,338 → 2,976（-31%）— 刪自建 manifest 完成率儀式（改原生 task tracking）、單檔重複 3-7 次的規則收斂為單一來源、裝飾性學術引用、與 CLAUDE.md 全域規則重複的段落；parser 契約、DSL 表、DOM 腳本、安全紅線（playwright-hitl 三軸分級）逐字保留
- `rules/security-guidance/skill-integration.md` 機制 A 改委派內建 `/security-review`（單點槓桿，design/update/pr/assist 同步生效）
- `rules/refactor/remove-architect-pipeline.md` 替代方案表改指內建 `Plan` agent（原表自身仍推薦 ECC planner 的矛盾修正）
- `plan-archive` 的 Hook 安裝教學移出執行期文件至 `docs/hooks-setup.md`
- `README.md` 全面同步解耦後架構；「ECC Agent 退化警告」改寫為歷史決策記錄
- `plans/active/ecc-190-workflow-integration.md` 作廢歸檔至 `plans/archived/`（依賴 2026-04-01 已禁用的 architect，從未執行）；`plans/active/knowledge-base-quality-optimization.md` 去 ECC 重寫為現況盤點（多數項目已被本次 update/curation 重寫涵蓋）

### Deprecated
- `/ecc-skill-defer` 標記 deprecated：等 harness 端 ECC plugin 處置定案後移除（gateguard 待辦仍引用它，暫保留）

### Fixed
- `verify-evidence-loop` L11 將本 repo 自有的 `evidence-check` 誤標為 ECC primitives 的歸屬錯誤
- `design` 多 session 路徑引用已不存在的 `/blueprint` → 改交 `/plan-run` 跨 session 推進

### Migration（v1.x → v2.0.0）
- **一般使用者**：無需動作。skill 指令名、plan 格式契約、DSL、安全紅線皆不變；內部改用內建 primitives，行為等價或更佳，且不再需要安裝 ECC plugin。
- **想維持 ECC 版行為**：pin 在最後的 ECC 依賴版 `v1.28.0`：
  ```bash
  git clone https://github.com/ashe-li/agent-skills && cd agent-skills && git checkout v1.28.0
  # 依該版 README 的 Install 指示安裝
  ```
  `v1.x` 線維護凍結、不再收新功能（見 [VERSIONING.md](VERSIONING.md)）。
- **有硬編 `everything-claude-code:*` agent 名稱的自建 hook/腳本**：改指上方 **Removed** 對照的內建 primitives。

### Why
Skill 集建於 Opus 4.5 時代：當時以 ECC agents 補足能力、以過細指令與 manifest 儀式補償模型判斷力。模型升級（Opus 4.8+）後兩者都成負債 — ECC 依賴阻礙 plugin 退場，過細指令浪費 context 且造成僵化。無前綴 agent 名稱（`code-reviewer` 等）經查證同屬 ECC plugin 雙重註冊，故解耦必須改內建 primitives 而非只拿掉前綴。環境事實（路徑/API 怪癖/真實踩坑教訓）與模型強弱無關，全數保留。審計與裁決記錄：knowledge-base `reports/2026-07-04-agent-skills-ecc-decoupling-audit.md`；實作計畫：`plans/active/ecc-decoupling-and-model-adaptation.md`（D1-D5 裁決）。

## [v1.28.0] - 2026-06-24

### Changed
- `/notion-plan`：因應 Notion 主網域 `notion.so` → `notion.com` 遷移，更新 URL 辨識與登入
  - 解析表新增 `notion.com`（新主網域）、`app.notion.com`（含 `/p/<workspace>/` 路徑前綴）；`notion.so` 標為舊網域（301 轉址到 `notion.com`）
  - 登入改用 `https://www.notion.com/login`（cookie 綁實際落地網域，避免舊 `notion.so` session 轉址後失效）
  - pageId 抽取（結尾 32 字元 hex）與網域/子網域/路徑前綴無關，核心邏輯不變
- `README.md`：更新 `/notion-plan` 支援網域清單與 Usage 範例

### Security
- `/notion-plan` 網域白名單改 dot-boundary 比對（host 等於或以 `.notion.com` / `.notion.so` / `.notion.site` 結尾），擋掉 `evilnotion.com`、`notion.com.attacker.tld` 等同尾巴/同前綴假冒網域；先前「結尾為 notion.com」描述會誤收 `evilnotion.com`

### Why
Notion 已將主網域遷至 `notion.com` 並新增 `app.notion.com/p/...` 連結格式；硬編 `notion.so` 的工具會漏接新格式 URL。dot-boundary 比對在放寬任意子網域（`www.`/`app.`/`<workspace>.`）的同時，保留網域邊界的安全性。

## [v1.27.0] - 2026-05-27

### Added
- `rules/security-guidance/`：官方 `security-guidance@claude-plugins-official` plugin 的整合設定來源
  - `claude-security-guidance.md`：model-backed review 的威脅模型/檢查清單（secrets 政策 + TS/Python/Go/Swift 規則），symlink 到 `~/.claude/`
  - `security-patterns.json`：per-edit deterministic patterns（硬編 secret 前綴、PEM 私鑰、`subprocess shell=True`）；用 JSON 不用 YAML 避免缺 PyYAML 時靜默忽略
  - `README.md`：三層防線說明、省 token env 設定（`ENABLE_STOP_REVIEW=0` + `SG_AGENTIC_MODEL=sonnet`）、symlink 部署與還原指令
- `README.md`：新增「Rules / 整合設定」段，連向 `rules/security-guidance/`
- `rules/security-guidance/skill-integration.md`：**主動式安全觸發契約** — 定義觸發閘（security-relevance heuristic）+ 兩種機制（委派 security-reviewer agent + 引用同一份 `claude-security-guidance.md`）

### Changed
- `/design`、`/update`、`/pr`、`/assist`：主動把安全這層串進流程（不再只靠 plugin 被動 hook）
  - `/design` Step 3：plan 觸及安全敏感面時必含 Security / Threat Model 章節 + 實作後納入 security-reviewer；Step 4a 品質閘主動驗證安全覆蓋（非只「已評估」打勾）
  - `/update` Step 2：觸及安全敏感面時與 code-reviewer 並行委派 security-reviewer
  - `/pr` Step 2：觸及安全敏感面時委派 security-reviewer（非只 inline quick review）；從 `/update` 串接時去重
  - `/assist` Step 3：路由命中安全敏感面時 pipeline 預設附加 security-reviewer
  - 共同觸發閘：認證/輸入/endpoint/DB/反序列化/檔案/shell/SSRF/DOM/加密；都不觸及則明示跳過，不空跑 agent（省 token）

### Why
此 plugin 是 hook-based、無法經 `npx skills add` 散佈，故 repo 只版本控管「擴充檔 + 設定記錄」，canonical 放 repo 並 symlink 到 `~/.claude/` 達成 config-as-code 與零漂移。plugin 是**被動**事後攔截；主動入口 skill 用**同一份** guidance 在規劃/審查/PR 階段**主動**帶到安全這層，與 plugin 形成 defense-in-depth，不取代。

## [v1.26.0] - 2026-05-22

### Changed
- `/design`: Step 7 推進選項拆為 `1a` / `1b` 子選項
  - `1a`. `/plan-run` 狀態機（手動）— 每 step 完成後手動 enter 繼續
  - `1b`. `/plan-run` + `/goal` 自動推進（強烈推薦於高複雜度）— 用 Claude Code 內建 `/goal` 包外層，自動跑到 `all_done=true` 或 N turns
  - 原 option 2（LLM 自主推進）、option 3（暫不開始）位置不變
- `/design`: 複雜度推薦表第三列改為「`/plan-run` + `/goal` 自動推進（強烈推薦）」對應高複雜度 plan
- `/design`: Step 7 文案明示「1b 仍走 `/plan-run` Step 3d HITL failure gate」`/goal` 不自動跳過 fail

### Fixed
- `/plan-run`: 還原 Step 3f「自動推進（optional）— `/goal` 包外層」及「與其他 skill 的關係」表格中的 `/goal` 列
  - Regression: commit `413fc39`（task_id sync + sliding-window hint + CodeRabbit fixes）誤刪 commit `563b276` 加入的 Step 3f 與 `/goal` 表格列
  - `/design` Step 7 的 1b 文案引用 `/plan-run` Step 3f，若不還原則為 dead link

### Why
解決「plan → 執行的自動推進路徑被埋」：`plan-run/SKILL.md` Step 3f 文件化 `/goal` 整合（commit 563b276，本次還原），但 `/design` 的退出選單原本不含此選項，使用者必須自己翻文件才會知道可以這樣用。把 1b 拉到 Step 7 後，`/design` 完成即可直接接 `/plan-run + /goal` 一鍵自動推進。

## [v1.25.0] - 2026-05-04

### Added
- `scripts/worktree-cleanup.sh`: 跨 repo 批次清理已 merge worktree 的 shell 腳本，補強 `/worktree cleanup` 既有的單一 repo 互動流程
  - 掃描 `~/Documents`（可 `--root` 覆寫）下所有 sibling worktree（`.git` 為檔案而非目錄者）
  - 對每個 worktree 透過 `gh pr list --head <branch>` 查詢 PR 狀態，MERGED/CLOSED 列入清理候選
  - **預設 dry-run**：純列表輸出 ACTION/STATE/PATH/BRANCH/PR/FLAGS，不動任何資源
  - `--apply`：實際執行 `git worktree remove --force` + 嘗試刪除已 merge 的本地分支
  - **髒目錄保護**：未 commit 變更的 worktree 預設 `skip-dirty`，需明確 `--force-dirty` 才會清除
  - **PR 不明處理**：`gh` 查詢失敗或無 PR 時標記 `unknown`/`no-pr`，預設保留，需 `--include-unknown` 才列入清理
  - `worktree/SKILL.md` 補上 cleanup 區塊指引：單 repo 走 skill 互動流程、跨 repo 批次走 script
- 實測 2026-05-04 一次清掉 15 個 MERGED worktree（deployment-eks / vocus-trends / vocus-web-ui），跳過 6 個髒目錄與 3 個 OPEN/no-PR

## [v1.24.0] - 2026-05-04

### Added
- `/verify-fix-loop`: 新增 verify→fix 迭代迴圈 skill — 透過 local Playwright MCP（headed 模式）執行「驗證 → 診斷 → 修正 → 重新驗證」迴圈，每輪以 snapshot + console + network 為證據；**完成 2 輪後（Round 3 起每輪，HITL_AFTER=2）強制 HITL 詢問是否繼續**，避免盲目迭代。
  - **Headed 模式必要**：MCP server 須以 `--headed` 啟動，Step 0a 檢查；使用者同步觀察、HITL 時可視覺確認、debug 體驗大幅優於 headless
  - **PASS 條件 DSL**：`url:` / `element:` / `not-element:` / `text:` / `console: no-error` / `network: no-5xx` / `eval:` 7 種型別機械對照，與 Phase A 驗證項表格一一對應；自由文字輸入會自動轉為 DSL 並回讀使用者確認
  - **每輪 4 階段**：Verify (checklist) → Diagnose（snapshot + console + network 證據三聯）→ Fix（限 allowed_paths 硬邊界）→ Wait reload
  - **HITL Gate**：完成 2 輪後（Round 3 起每輪，`HITL_AFTER=2`，`if n > HITL_AFTER`）`AskUserQuestion`，提供繼續 / 停止 / 改策略 / 轉 `/design` 四選項
  - **Hard cap = 5 rounds**：依 METR 2025 agent degradation 證據；達上限即使選「繼續」也強制停止
  - **Dev server 預設不自動啟動**：避免 long-running process 殘留與 token budget 持續佔用；使用者另開 terminal 為預設策略
  - **持久化 round log**：`.claude/verify-fix-loop/<timestamp>-<slug>.md`，跨 session 接手（搭配 `/handoff`）、PR description 引用、回溯 debug 軌跡
  - **硬性禁止清單**：改測試 assert 放水、改 PASS 條件本身、catch swallow error、跨範圍改架構、hardcoded 繞過 — 防止「為過而過」非真修復
  - **與既有 skill 差異**：`playwright-human-in-the-loop` 為單次操作型，本 skill 為迴圈型修復；`verify-evidence-loop` 為技術主張的文獻驗證，本 skill 為程式碼行為驗證
- `README.md`: 新增 `/verify-fix-loop` 至 Usage、Skills 總覽、決策樹

### 方法論依據
- Self-Refine (arXiv:2303.17651) — 迭代修正模式
- Reflexion (arXiv:2303.11366) — 失敗證據回饋下一輪
- METR 2025 agent degradation — >3 輪 drift 風險，故 hard cap = 5、HITL gate = 2
- OpenAI dev community — checklist-driven verification > free-form
- DAMA-DMBOK Completeness — round_log + final report manifest-driven
- arXiv:2509.18970 — 結構性分類（PASS criteria checklist + DSL）優先於逐案語意判斷

## [v1.23.0] - 2026-04-18

### Added
- `/handoff`: 新增跨 context 接手 prompt skill — 萃取本次對話的目標、進度、決策、未完成項目，輸出可直接貼到新 session 或 `/compact` 之後使用的自包含 prompt。單一 skill 同時涵蓋「新 context 接手」和「compact 前準備」兩種情境（本質都是缺對話記憶）
  - 7 區塊 manifest：任務目標 / 當前進度 / 決策脈絡 / 環境快照 / 重要 context / 待辦項目 / 立即可執行的下一步（對齊 v1.21.0 DAMA-DMBOK Completeness 慣例）
  - HITL 三選項輸出：直接顯示 / 寫入 `.claude/handoff/handoff-<timestamp>.md` / 兩者皆要
  - 完整率驗證：< 70% 阻止輸出，要求補充對話資訊後重跑
  - 環境快照「相關性標註」：stash、`plans/active/*.md`、其他 untracked 檔案逐項標註相關 vs 不相關，避免接手者誤判（誤 pop stash、誤碰其他任務的 plan）
  - 與既有方案差異：純文字 prompt 載體（vs `everything-claude-code:save-session` 的 JSON），跨環境/跨機器/跨 LLM 通用，不需特定 runtime
- `README.md`: 新增 `/handoff` 至 Usage 與 Skills 總覽

## [v1.22.0] - 2026-04-18

### Added
- `/verify-evidence-loop`: 新增迭代式證據驗證 skill — 組合既有 primitive（evidence-check Generator + santa-method Dual Reviewer + iterative-retrieval gap refinement），不重造。4 維蒐集 × 最多 3 輪 iteration × dual Sonnet reviewer 收斂迴圈，適合高風險決策。
  - Haiku × 2 並行蒐證（D1 學術 + D2 標準 / D3 實踐 + D4 社群 + Strong Dissent probe），Sonnet × 2 並行獨立判讀（fresh per iteration），B ∧ C 必須同時 PASS 才 NICE
  - Strong Dissent 為一等公民：要求 source_url + verbatim_quote + argument ≥2 句；reviewer 獨立判定 strength 不信任 subagent 自評標籤；無 dissent 必須明確 `NO-STRONG-DISSENT-FOUND`
  - Hard cap=3（METR 2025 agent degradation 實證），耗盡後輸出 partial report 並要求人工裁決
  - Budget guard：soft 60k / hard 120k，**pre-flight 檢查**（不在 Phase A 啟動後才發現超預算）
  - Prompt injection 結構性防禦：CLAIM 用非 XML `---CLAIM-START---` / `---CLAIM-END---` 分隔 + 確定性剝 `<`/`>`；WebSearch 結果顯式不可信；evidence bundle 包 `<evidence>` tag 且禁 `##` heading 污染 reviewer prompt；耗盡迭代時只輸出 summary-only partial report
  - Verdict 區分：STRONG dissent 存在 ≠ `CONFLICTED`；只有跨維度對主張本身互斥才 CONFLICTED
- `README.md`: 新增 `/verify-evidence-loop` 至 Usage、Skills 總覽、決策樹

### 方法論依據
- Self-Refine (arXiv:2303.17651)、Reflexion (arXiv:2303.11366)、Multi-agent debate (arXiv:2305.14325)、LLM-as-Judge (arXiv:2306.05685)
- IEEE 1012-2016 V&V、NIST SP 800-160、DAMA-DMBOK Completeness
- Anthropic "Building Effective Agents" (2024)
- 反面：Huang et al. (arXiv:2310.01798, LLMs cannot self-correct)、Dziri et al. (arXiv:2305.18654, Faith & Fate)、METR 2025 agent degradation

## [v1.21.1] - 2026-04-16

### Changed
- `/ecc-skill-defer`: conf 依 ECC 1.10.0 `install-modules.json` 結構更新；新增 operator-workflows 模組區塊；defer 60 → 71（+11）
- 新增 defer：`manim-video`、`remotion-video-creation`（media-generation）、`brand-voice`、`social-graph-ranker`（business-content）、`nestjs-patterns`、`laravel-plugin-discovery`（framework-language）、`connections-optimizer`、`customer-billing-ops`、`google-workspace-ops`、`project-flow-ops`、`workspace-surface-audit`（operator-workflows）
- `DEFER_REFERENCE.md`: 同步新增 skills 與 operator-workflows 模組
- `README.md`: defer 數量 61 → 71，結構版本標示 1.9.0 → 1.10.0

## [v1.21.0] - 2026-04-06

### Changed
- 全 12 個 skill 導入 manifest-driven 完整性驗證（依據：DAMA-DMBOK Completeness、ITIL CMDB Reconciliation、arXiv:2509.18970）
- `/update`: Step 1 新增「變更 Manifest」；Step 2 改為逐條 set difference 比對；Step 3 新增「知識寫入 Manifest」；Step 5 改為 manifest-driven + grep/glob 確定性驗證
- `/design`: Step 3 新增需求追蹤矩陣（Requirements Traceability Matrix）；Step 4a 新增「需求覆蓋率」審查維度
- `/assist`: Handoff Protocol 新增 Completeness Declaration 欄位；Industry/Community 欄位升級為強制填寫
- `/pr`: Step 1b 新增 Context Manifest；Changes 新增 commits 計數驗證；Context 新增逐條比對
- `/curation`: Step 1 新增問題 manifest；Step 4 新增修正後驗證（grep -c）；Step 5 新增完成率
- `/learn-eval-deep`: Step 3 新增 Bridge 輸出完整性檢查；Step 4 新增資料來源覆蓋率標註
- `/triage`: Step 2 新增退役前影響分析（grep 依賴搜尋）；新增 Step 5 退役後驗證
- `/plan-archive`: Step 2 新增步驟完成 manifest；Step 3 改為逐條 PASS/FAIL + 完成率閾值
- `/worktree`: status 新增一致性檢查（orphan 偵測）；cleanup 新增操作後驗證
- `/notion-plan`: Step 2d 新增擷取完整性檢查；Step 4 改為 4 條 checklist PASS/FAIL
- `/ecc-skill-defer`: 核心 skill 保護升級為 HITL Guard；apply/restore 新增操作驗證
- `/playwright-human-in-the-loop`: Step 3 新增強制 snapshot checklist；Step 4 改為 manifest-driven 報告

### Fixed
- `/pr`: Step 1a 修正 stale local branch 陷阱 — 所有 `git log/diff <base-branch>..HEAD` 改為 `origin/<base-branch>..HEAD`，新增 `git fetch origin` 前置步驟與 `gh pr diff` 交叉驗證機制

## [v1.20.0] - 2026-04-06

### Added
- `/evidence-check`: 新增獨立證據查驗 skill — 四維度並行調查(D1 學術研究、D2 業界標準、D3 最佳實踐、D4 社群共識+反面意見)，2 個 haiku subagent 並行，跨來源衝突偵測(AGREE/PARTIAL/CONFLICT/NO-DATA)，5 級 verdict，輸出與 /design plan 格式相容
- `README.md`: 新增 `/evidence-check` 至 Usage、Skills 總覽、決策樹

## [v1.19.0] - 2026-04-05

### Changed
- `/design`: Step 3 planner 要求新增「社群共識」和「反面意見與已知陷阱」；Step 4a 品質檢查新增對應維度；plan 模板新增 Community Consensus & Dissenting Views 表格
- `/assist`: 新功能和重構 pipeline 的 planner 標記含社群共識/反面意見；handoff protocol 新增 Community Consensus section
- `/pr`: Step 1b 對話脈絡分析新增「社群共識與反面意見」提取項；PR description Context 模板新增社群共識範例
- `/pr`: 新增 Step 2c plan 歸檔檢查 — commit 前自動掃描 `plans/active/` 已完成的 plan 並歸檔
- `README.md`: 同步 /design（subagent 隔離審查、社群共識）、/assist（路由表社群共識）、/pr（社群共識提取、Step 2c）描述

## [v1.18.1] - 2026-04-04

### Added
- `rules/worktree-prompt.md`: 新增 Worktree 路徑慣例 — 禁止 `.claude/worktrees/`（EnterWorktree 預設路徑），改為 sibling 目錄格式 `<project>-<slug>/`
- `rules/refactor/remove-architect-pipeline.md`: 新增 architect agent 禁用規則，基於消融實驗結果，列出 planner 等替代方案

## [v1.18.0] - 2026-04-01

### Changed
- `/design`: 移除消融實驗表現最差的 ECC architect agent（delta=-0.50），將架構審查職責重新分配至 planner（Step 3 架構決策）和品質審查（Step 4a subagent 隔離審查）
- `/design`: Step 4a 改用 general-purpose subagent 隔離審查，含 PASS/FAIL 結構化回報和回饋迭代（最多 2 次）；新增可擴展性審查維度；業界支撐改為主動驗證
- `/design`: Step 2 複雜度評估恢復低/中等差異化路徑（低複雜度跳過架構審查）
- `/assist`: 新功能和重構 pipeline 標記 planner 含架構決策
- `check_skill.py`: 新增 `redundancy-peers` frontmatter 支援排除 sibling skill 互相扣分；跳過隱藏目錄避免 worktree 干擾

## [v1.17.2] - 2026-03-25

### Added
- `/pr`: Release PR 標題格式 — base branch 為 master/main 時強制使用 `Release vX.Y.Z: <摘要>`
- `/pr`: CHANGELOG 檢查步驟 — Release PR 時自動比對 commits 與 CHANGELOG.md，缺少記錄會提示更新

## [v1.17.1] - 2026-03-23

### Changed
- `/ecc-skill-defer`: conf 依 ECC 1.9.0 `install-modules.json` 模組結構重組；新增 swift-apple（6 skills）和 framework-specific security（4 skills）；113 → 52 active（61 deferred）
- `DEFER_REFERENCE.md`: 改為模組對齊的雙表格格式（Whole Modules / Within-Module）
- `README.md`: defer 數量更新 24 → 61

## [v1.17.0] - 2026-03-21

### Changed
- `/design`: 資源盤點去版本化，新增 docs-lookup/typescript-reviewer agents 和 /docs /aside /skill-health /prompt-optimize /blueprint /context-budget /save-session /resume-session commands；Step 2 新增多 session 複雜度路徑
- `/assist`: agent 表新增 docs-lookup/typescript-reviewer；commands 表新增 7 項 1.9.0 commands；routing 表新增 5 項情境
- `/ecc-skill-defer`: 新增 `--reason` 支援 defer 原因追蹤（DEFER_LOG.md）；新增 /skill-health 整合建議；conf 新增 39 個 1.9.0 語言/領域/媒體 skills
- `/triage`: Step 1 新增 /skill-health 補充視圖建議
- `README.md`: 總覽表補齊 /triage 和 /learn-eval-deep；決策樹新增對應入口

## [v1.16.1] - 2026-03-20

### Fixed
- `pr/SKILL.md`: allowed-tools 補上 `Agent`（Step 2b 委派 refactor-cleaner 需要 Agent tool 權限）
- `pr/SKILL.md`: fenced code block 加上 python 語言標識（MD040）
- `pr/SKILL.md`: 「適用所有修正」→「套用所有修正」錯字修正
- `design/SKILL.md`: ECC Resources 表格與 Phase 2 checklist 補上「重複程式碼合併」，與 pr/README 一致

## [v1.16.0] - 2026-03-19

### Added
- `/simplify` 並行互補整合：code-reviewer（診斷）後自動加入 refactor-cleaner（治療）
  - `/pr`: 新增 Step 2b 自動修正步驟，Quick Review 後委派 refactor-cleaner 修正 dead code、命名、nesting
  - `/assist`: 新功能、Bug 修復、Review pipeline 自動附加 `/simplify`（重構和文件 pipeline 除外）
  - `/design`: Plan 模板 Phase 2 品質保障加入 `/simplify`，ECC Resources 表格加入 refactor-cleaner 範例
  - 所有自動修正步驟含 HITL 確認（套用全部 / 逐一確認 / 跳過）
- `README.md`: 新增 `/simplify` skill 描述、Usage quick-reference、選什麼流程圖條目

### Unchanged
- `/update`: 文件審查不適用程式碼簡化，保持原樣

## [v1.15.0] - 2026-03-17

### Removed
- `plan-rename`: 移除整個 skill（SKILL.md + 3 個 hook 腳本 + v2 實作計畫）— Claude Code 已內建 Plan Mode 自動命名功能，不再需要自訂 hook
- `README.md`: 移除 Background Hooks 段落

## [v1.14.0] - 2026-03-14

### Added
- `/curation`: Learned Skills 品質管控 skill
  - 掃描 `~/.claude/skills/learned/` 格式問題（frontmatter、評分格式、廢棄標記）
  - 自動修正格式問題（從內容推斷 name/description）、HITL 確認後刪除廢棄項目
  - 批次操作模式（全部修正 / 只修格式 / 逐一確認 / 只查看）
- `/update` Step 3: 對話 context 整理（新增步驟，位於 code-reviewer 之後、learn-eval 之前）
  - 從對話中提取決策脈絡、研究成果、架構演進、Bug 根因等有價值的 context
  - 三層分流：專案知識庫（給人讀）、learned skills（給 Claude 學）、MEMORY.md（跨 session 狀態）
  - 知識庫目錄不硬編碼，HITL 確認寫入位置

### Changed
- `/update` Step 4 (原 Step 3, learn-eval): 新增寫入格式強制規範
  - 強制 frontmatter（name/description/user-invocable/origin）
  - 品質評分統一為 5 維度表格格式，廢棄單行格式
- `/update` Step 5 (原 Step 4, 知識庫交叉比對): 從被動報告改為主動寫入
  - 偵測遺漏時起草修正內容，HITL 確認後直接寫入
  - 新增 MEMORY.md 路徑定位規則與自動建立邏輯
  - 新增 Step 3 context 寫入完整性確認
- `/update`: 步驟重新編號（原 Step 3-6 → Step 4-7）

## [v1.13.0] - 2026-03-14

### Added
- `rules/worktree-prompt.md`: 實作 plan 或大範圍變更前，agent 自動詢問是否使用 worktree 隔離開發
  - 觸發條件：實作 `plans/active/` 中的計畫、跨 5+ 檔案的 migration/refactoring、基礎設施變更
  - 跳過條件：使用者已明確表態、當前目錄已是 worktree、單檔小修

## [v1.12.0] - 2026-03-12

### Changed
- `/design`: 新增 Step 0 條件式 HITL — agent 判斷任務複雜度後詢問是否啟用 task tracking
  - frontmatter 新增 `TaskCreate, TaskUpdate, TaskList` 至 allowed-tools
  - 各步驟加入條件式 task tracking 標記（含 activeForm、addBlockedBy）
- `/assist`: 新增 Step 0 條件式 HITL — agent 判斷任務複雜度後詢問是否啟用 task tracking
  - frontmatter 新增 `TaskCreate, TaskUpdate, TaskList` 至 allowed-tools
  - Step 4 Pipeline 執行加入條件式 task tracking 標記（含 activeForm、addBlockedBy）
- `~/.claude/CLAUDE.md`: Task Tracking 規則從「超過 3 步驟主動啟用」改為「agent 判斷 + HITL 詢問，不可自動啟用」

### Fixed
- `/pr`: 移除 frontmatter 中從未使用的 `Task` allowed-tool

## [v1.11.0] - 2026-03-12

### Added
- `plan-rename`: 將 hook 系統從 `~/.claude/scripts/` 遷移至 agent-skills repo，納入版本控制
  - `plan-rename/plan-rename-hook.sh`：PreToolUse ExitPlanMode hook，從 Plan H1 標題自動命名 session
  - `plan-rename/plan-rename-guard.sh`：Stop hook，compaction 後自動重新注入 custom-title
  - `plan-rename/SKILL.md`：完整文件（`user_invocable: false`），含使用前須知、成本分析、穩定性風險、安裝步驟

### Changed
- `README.md`: 新增 Background Hooks 區塊，說明 plan-rename 非使用者呼叫的 hook skill

### Removed
- `~/.claude/scripts/plan-rename-hook.sh`：遷移至 repo，不再為孤兒檔案
- `~/.claude/scripts/plan-rename-guard.sh`：同上
- `~/.claude/skills/learned/claude-code-session-rename-hook.md`：內容已併入 `plan-rename/SKILL.md`

## [v1.10.1] - 2026-03-11

### Changed
- `/pr`: PR 標題自動帶入 Notion ticket 資訊（ticket 編號或票名，二擇一），偵測對話中的 `[A-Z]+-\d+`、Notion URL 或「Notion Ticket」字樣

## [v1.10.0] - 2026-03-10

### Added
- `/notion-plan`: 貼上 Notion URL，自動抓取頁面需求內容並串接 `/design` 建立實作計畫
  - 支援 `notion.so`、`notion.site`、短網址等多種 URL 格式
  - 雙路徑策略：WebFetch（快速）→ Playwright MCP（完整 JS 渲染 fallback）
  - 自動處理長頁面捲動載入、Toggle 展開、登入偵測
  - 擷取內容整理為結構化 Markdown 後，自動觸發 `/design` 建立 plan.md
  - 內容品質確認步驟，空白或不完整時提示使用者

### Changed
- `README.md`: 新增 `/notion-plan` skill 描述、Usage、選擇流程圖

## [v1.9.0] - 2026-03-10

### Added
- `/playwright-human-in-the-loop`: Playwright Human-in-the-Loop 瀏覽器操作 skill
  - 操作分級：重大操作（建立/刪除資源、修改權限、安全敏感欄位、費用、不可逆操作）需 `AskUserQuestion` 確認
  - 非重大操作（導航、填寫 metadata、搜尋、截圖）自動執行
  - 安全敏感欄位（Policy JSON、IAM policy document）即使是填寫也視為重大操作
  - 4 步驟執行流程：確認 MCP → 理解任務 → 執行 → 報告
  - 頁面載入失敗允許一次重試

### Changed
- `README.md`: 新增 `/playwright-human-in-the-loop` skill 描述、Usage、選擇流程圖

## [v1.8.0] - 2026-03-09

### Added
- `/update`: Step 6 Pipeline 串接 — 支援 `/update /pr` 一條指令完成知識沉澱 + PR 交付
  - 檢查 `$ARGUMENTS` 中的 skill 名稱，完成後自動觸發下游 skill
  - `[PIPELINE: from /update]` 標記通知下游跳過已完成步驟
  - 資源去重：`/pr` Step 2 (Quick Review) 自動跳過（`/update` Step 2 已用 code-reviewer agent 完成）
  - 支援傳遞參數（如 `/update /pr 7238`）

### Changed
- `README.md`: 新增 pipeline 串接說明、去重表格、`/update /pr` 用法範例、選擇流程圖更新

## [v1.7.3] - 2026-03-07

### Fixed
- `plan-rename`: 從 PostToolUse Write 改為 PreToolUse ExitPlanMode — Plan Mode 使用 `ExitPlanMode` 存檔而非 `Write`，且 `ExitPlanMode` 不觸發 PostToolUse，導致 hook 永遠不會被 Plan Mode 觸發
- `plan-rename`: 移除 Write fallback 路徑，只處理 `ExitPlanMode` 的 `tool_input.plan`

### Changed
- `plan-rename/README.md`: 改寫為 PreToolUse ExitPlanMode 機制；新增 Prerequisites、Known limitations；移除過時的 Troubleshooting
- `README.md`: plan-rename 區段更新機制說明，新增 claude-hud 依賴提醒和手動設定提醒

## [v1.7.2] - 2026-03-07

### Fixed
- `plan-rename`: `sessionId` → `session_id`（hook stdin 使用 snake_case，非 camelCase）
- `plan-rename`: 移除 `os.getcwd()` slug 路徑拼接，改用 hook stdin 提供的 `transcript_path` 直接定位 session JSONL

## [v1.7.1] - 2026-03-07

### Fixed
- `plan-rename`: path filter 改用 `realpath` + `startswith` 防止 traversal 繞過
- `plan-rename`: sessionId 新增 regex 驗證，防止 `os.path.join` path traversal
- `plan-rename`: exception 分層處理，unexpected error 輸出 stderr 可觀測
- `plan-rename`: `echo` 改為 `printf '%s\n'`，避免 backslash 解析問題

### Changed
- `plan-rename/README.md`: 新增 Troubleshooting 區段、手動重命名覆蓋提醒、截斷描述精確化

## [v1.7.0] - 2026-03-07

### Added
- `plan-rename`: PostToolUse hook，Plan Mode 自動從 H1 標題重命名 session
  - 攔截 Write tool，篩選 `~/.claude/plans/*.md`
  - 擷取 H1 標題，去除 `Plan:` 等前綴，截斷 80 字元
  - 直接 append `custom-title` 到 transcript JSONL（與 `/rename` 相同機制）
  - 從 hook stdin 取 sessionId，多 session 並行安全

## [v1.6.0] - 2026-03-07

### Added
- 全 skill 業界/學術參照機制：規劃階段須附上業界標準（RFC、W3C、OWASP、12-Factor）、學術研究或標準化方案依據
- 全 skill ECC 資源分配介入：核心 skill 深度整合盤點確認，輔助/輕量 skill 加入資源感知 blockquote
- `/design`: plan.md 模板新增 `## Industry & Standards Reference` 表格
- `/assist`: 新功能需求 pipeline 加入業界/學術方案調研；新增 ECC 資源分配原則；Handoff Protocol 新增 Industry & Standards Referenced 欄位
- `/pr`: 對話脈絡分析和 PR Description Context 新增業界/學術依據
- `/update`: learn-eval 提取範圍新增業界標準應用與標準化方案選型；交叉比對新增「業界標準是否已記錄到知識庫」確認項
- `/plan-archive`: 歸檔驗證新增業界/學術參照落實情況
- `/ecc-skill-defer`: 新增 Notes — 核心規劃 skill 保護提醒

## [v1.5.0] - 2026-03-07

### Changed
- `/assist`: 新增 `harness-optimizer`、`loop-operator` 至 agent 表格與路由規則
- `/design`: ECC 資源盤點納入 v1.8 新增 agents 與 commands；計畫品質檢查表新增 Eval 基線維度
- `/ecc-skill-defer`: README 更新數字（23 deferred / 65 total），移除過時的 token 數量描述
- 同步 ECC v1.8.0 的 agent harness 定位與 eval-driven 開發概念

## [v1.4.2] - 2026-03-07

### Fixed
- `/ecc-skill-defer`: 支援 marketplace 安裝路徑（`plugins/marketplaces/`），優先偵測 marketplace 再 fallback 至 cache

### Changed
- `/ecc-skill-defer`: 配合 ECC v1.8.0 更新，v1.8 新增的 9 個 skills 保持 active（42 active / 23 deferred）

## [v1.4.1] - 2026-03-07

### Changed
- `/ecc-skill-defer`: 調整預設 defer 清單 — meta skills 區只保留 `continuous-learning`，其餘 6 個改為 active（33 active / 23 deferred）

## [v1.4.0] - 2026-03-07

### Added
- `/ecc-skill-defer`: ECC Skill 漸進式載入管理，減少 init token 消耗
  - `apply` 一鍵 defer config 中列出的 skills（SKILL.md → SKILL.deferred.md）
  - `restore <name>` / `restore --all` 按需啟用
  - `status` / `list` 檢視目前 active/deferred 狀態
  - 預設 defer 29 skills（Django/Spring Boot/Java/C++/business/meta），省 ~1,800 init tokens
  - 附帶 `ecc-skill-defer.conf` 可自訂 defer 清單

## [v1.3.0] - 2026-03-05

### Added
- `/plan-archive`: 新增 plan 生命週期管理 skill，自動化 active → completed 歸檔流程
  - 自動偵測 `plans/active/` 中待歸檔的 plan
  - 補充「狀態：✅ 完成」標記與驗證結果段落
  - 內建 PostToolUse Hook 設定（ExitPlanMode 自動存 active）
  - 內建 CLAUDE.md Rule 範本（提醒實作後歸檔）
  - 目錄規範：`plans/active/` → `plans/completed/` → `plans/archived/`

## [v1.2.0] - 2026-03-05

### Changed
- `/update` Step 4: 新增知識庫交叉比對（HITL 確認），session 結束前逐一確認 MEMORY.md、learned skills、專案文件是否正確更新
- `/update`: 原 Step 4 總結報告移至 Step 5，新增「知識庫交叉比對」欄位

## [v1.1.0] - 2026-03-05

### Changed
- `/update`: 更新前強制 HITL 確認計畫修改的檔案清單
- `/update`: 偵測「文件庫/知識庫」歧義，不確定時詢問使用者
- `/update` Step 2: 加入 cross-check，確認無遺漏 / 無錯誤修改的文件

## [v1.0.0] - 2026-03-05

### Added
- `/pr`: 自動分析 git diff + 對話脈絡，生成完整 PR description；包含 Quick Review 與 base branch 防護
- `/update`: 依序執行 doc-updater → code-reviewer → learn-eval，將 session 變更沉澱為文件與知識
- `/design`: 透過 planner + architect 建立實作計畫，輸出 plan.md 供確認後才進入實作
- `/assist`: 萬用助手，智慧路由至最佳 agent pipeline

<!-- 版本比較連結（Keep a Changelog 慣例）；補歷史版本連結時比照下方格式沿用即可 -->
[Unreleased]: https://github.com/ashe-li/agent-skills/compare/v2.0.0...HEAD
[v2.0.0]: https://github.com/ashe-li/agent-skills/compare/v1.28.0...v2.0.0
[v1.28.0]: https://github.com/ashe-li/agent-skills/releases/tag/v1.28.0
