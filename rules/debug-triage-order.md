# Debug Triage Order

Scope: Apply when debugging a bug reported from an observable environment (prod/staging URL、線上 console/network 可及)，especially「dev 正常、線上才壞」或回報來自實際使用者的 UI/行為問題。

## 三條順序規則

### 1. Prod-first read-only probe（先撈 live 訊號，再建重現環境）

建任何本地重現環境（dev server、mock、fixture）**之前**，先對回報環境做一次唯讀探測：

- headless 瀏覽器或 curl：console error、關鍵 DOM 狀態、network 指標
- 紅線：不登入、不寫入、導覽 ≤10 頁
- 看什麼：hydration error（React #418/#423/#425）、CSP/第三方腳本錯誤、cache header、payload 完整性

一次探測即可分流「環境差異 bug」（dev 天生重現不了，窮舉導覽條件是浪費）vs「邏輯 bug」（值得建本地重現）。

### 2. Evidence-first before dispatch（派工前先盤點手上證據）

派驗證型 agent/Explore 前，先列已在手的證據；若 1-2 個指令（`git log --grep`、單一 grep、讀一個檔）就能定案，主對話 inline 跑掉，不派整輪 agent 重查已經有答案的問題。

### 3. Verify-via-spec, once（驗收跑既有 spec，只跑一輪 fresh）

regression spec 寫好之後，驗收 = 一個 fresh context（主對話或單一 agent）**實跑該 spec**（UI 用 headed），不要用散文重新推導手動驗證步驟給另一個 agent 執行——重新推導的模擬程序會引入 spec 沒有的 artifact，多花一輪裁決。驗證輪數上限：實作者自測 + 一輪 fresh 驗收；不疊第三輪重複驗證。

## Why

2026-07-16 vocus 投票 RCA session 複盤，三個浪費點合計約佔 session 30-40% 時間與 3 輪可避免的 agent 派工：

1. prod-only hydration 失敗，卻先在 dev 窮舉 5 種導覽條件（整輪 repro agent 註定無法重現）；後補的 prod 唯讀探測第一輪就拿到 React #418×28 的決定性訊號。
2. repro agent 的 grep 已證明 infinity-roll 是 dead code，仍再派一整輪 Explore 重查同一問題（inline `git log --grep` 一分鐘可定案）。
3. 驗收跑了三輪（實作者 E2E + 手動 headed + 主對話重跑），且手動輪照散文指令自創 `cloneNode` 模擬時剝錯節點，噴 `NotFoundError` 假陽性，多花一輪截圖比對裁決——既有 E2E spec headed 跑一次即可覆蓋。

（來源：knowledge-base `reports/2026-07-16-article-poll-csr-portal-detached-rca.md` + 同日 session 檔）
