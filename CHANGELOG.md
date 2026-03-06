# Changelog

所有重要變更都記錄在這裡。格式參考 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.0.0/)。

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
