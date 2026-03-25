# Changelog

所有重要變更都記錄在這裡。格式參考 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.0.0/)。

## [v1.18.0] - 2026-03-26

### Added
- `rules/skill-auto-match.md`: 強制 skill 查詢機制 — 回應前自動比對 skill 映射表，匹配就 invoke Skill tool（靈感：obra/superpowers）
- `rules/subagent-isolation.md`: Subagent 隔離原則 — 規範何時用 Agent 工具，防止探索任務污染主 context
- `rules/verification-gates.md`: 驗證門檻 — 3 個強制閘門（Plan / Implementation / PR），完成前必須附客觀證據
- `/systematic-debugging`: 結構化除錯 skill — 4 階段流程 Observe → Hypothesize → Test → Fix，禁止盲目重試，失敗 3 次強制 escalate

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
- `/pr`: PR 標題自動帶入 Notion ticket 資訊（PDT 編號或票名，二擇一），偵測對話中的 `PDT-\d+`、Notion URL 或「Notion Ticket」字樣

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
