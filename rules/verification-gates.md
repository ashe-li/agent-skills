# Verification Gates

三個強制驗證閘門，確保每個工作階段有客觀完成證據。宣稱完成不等於完成。

## Gate 1 — Plan Gate（實作前）

**觸發時機：** 要開始實作（修改程式碼）前

必須確認：
- [ ] 計畫存在（`plans/active/<slug>.md` 或對話中有明確計畫）
- [ ] 計畫有驗收標準（Acceptance Criteria）
- [ ] 使用者已確認計畫方向

Gate 失敗 → 停止，提示使用者先執行 `/design` 或明確確認計畫。

## Gate 2 — Implementation Gate（宣告完成前）

**觸發時機：** 即將說「實作完成」或「功能已完成」前

必須提供至少一項完成證據：
- 測試輸出（pass/fail 數量）
- Build 成功輸出（`build succeeded`、`0 errors`）
- Lint 通過紀錄
- 手動驗證步驟和結果

Gate 失敗 → 不得宣稱完成。說明缺少哪項證據，執行對應驗證後再繼續。

## Gate 3 — PR Gate（送出 PR 前）

**觸發時機：** 建立或更新 PR 前

必須確認：
- [ ] 無 secrets / API keys 在 diff 中
- [ ] 無遺留 debug code（`console.log`、`debugger`、未處理的 `TODO:`）
- [ ] Implementation Gate 已通過（有完成證據）

Gate 失敗 → 報告缺失項目，讓使用者選擇修正或繼續。

## 原則

完成證據必須是可觀察的輸出，不是推斷或預估。
