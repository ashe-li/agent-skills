---
name: playwright-human-in-the-loop
description: Playwright Human-in-the-Loop 瀏覽器操作 — 透過 Playwright MCP 自動執行低風險操作，重大操作前暫停等待人類確認。適用 AWS Console、後台管理介面等場景。
allowed-tools: AskUserQuestion, mcp__playwright__browser_navigate, mcp__playwright__browser_click, mcp__playwright__browser_fill_form, mcp__playwright__browser_type, mcp__playwright__browser_snapshot, mcp__playwright__browser_take_screenshot, mcp__playwright__browser_select_option, mcp__playwright__browser_press_key, mcp__playwright__browser_hover, mcp__playwright__browser_wait_for, mcp__playwright__browser_evaluate, mcp__playwright__browser_tabs, mcp__playwright__browser_close
---

# /playwright-human-in-the-loop — Playwright Human-in-the-Loop 瀏覽器操作

透過 Playwright MCP 操作瀏覽器，自動執行低風險操作，重大操作前暫停等待人類確認。

適用場景：AWS Console 操作、後台管理介面、需要瀏覽器自動化但不能全自動的任務。

## 操作分級

### 重大操作（必須 human confirmation）

使用 `AskUserQuestion` 工具暫停執行並等待使用者回應，顯示即將執行的動作，確認後才繼續。

分類依據「操作是否可逆」與「是否影響安全/費用」兩個維度（依據：arXiv:2509.18970 — 結構性分類優先於逐案語意判斷）：

| 類別 | 判斷原則 | 範例 |
|------|----------|------|
| 建立/刪除資源 | 會產生或移除基礎設施、服務、帳號 | IAM Role、EC2、GCP Service Account、Vercel Project、DNS Record |
| 修改權限/政策 | 會改變誰能存取什麼 | IAM Policy、Security Group、Firewall Rule、OAuth Scope、API Key 權限 |
| 安全敏感欄位 | 欄位內容影響安全或存取控制 | Policy JSON、Trust Policy、env variables、webhook URL、redirect URI |
| 費用相關 | 會產生或改變費用 | 變更 instance type、購買 Reserved Instance、升級 plan、啟用付費功能 |
| 不可逆操作 | 執行後無法還原 | 刪除 S3 bucket、terminate instance、drop database、revoke API key |
| 最終送出 | 確認按鈕，觸發實際變更 | Create、Submit、Deploy、Confirm、Apply Changes、Save 按鈕 |

### 非重大操作（自動執行）

不需等待確認，直接執行：

- 導航（navigate、click 連結、切換頁面）
- 填寫一般資訊欄位（名稱、描述、標籤等 metadata）
- 選擇 radio/dropdown/checkbox（select_option、click）
- 搜尋、篩選、翻頁
- 展開/收合面板
- 截圖、讀取頁面內容

> **注意**：涉及權限、政策、安全設定的欄位（如 Policy JSON、env variables、webhook URL）即使是「填寫」也屬於重大操作，必須確認內容後才送出。
>
> **泛化原則**：上表範例不限於 AWS。任何管理介面（GCP、Azure、Vercel、Stripe、自建後台等）都適用相同分類邏輯——依據「操作是否可逆」「是否影響安全/費用」判斷，而非依據平台。

## Step 1: 確認 Playwright MCP 可用

Playwright MCP（Model Context Protocol）是讓 AI agent 操作瀏覽器的工具集，需先啟動 MCP server 才能使用。

**檢查方式**：嘗試呼叫 `mcp__playwright__browser_snapshot`，若工具不存在或回傳錯誤則視為不可用。

如果不可用：
1. 提示使用者啟動 Playwright MCP server
2. 停止執行，不要嘗試用其他方式操作瀏覽器

## Step 2: 理解任務

分析使用者的指令：
- **目標**：要在哪個網站/後台做什麼操作
- **步驟拆解**：將任務拆成逐步操作
- **風險評估**：標記哪些步驟是重大操作

輸出操作計畫，格式：
```text
操作計畫：
1. [自動] 導航到目標管理介面的設定頁面
2. [自動] 搜尋目標資源
3. [自動] 填寫名稱、描述等 metadata 欄位
4. [確認] 檢視並確認權限/政策相關設定  ← 安全敏感欄位
5. [確認] 點擊送出按鈕                ← 重大操作
```

## Step 3: 執行操作

按計畫逐步執行：

### 非重大操作

直接使用 Playwright MCP 工具執行。每個操作後**必須**執行 `browser_snapshot`，並以 checklist 確認預期狀態（依據：OpenAI Developer Community 共識 — Checklist-driven > Free-form）：

| 驗證項目 | 判斷方式 | 結果 |
|----------|----------|------|
| 目標元素出現（或消失） | snapshot 中是否含預期 element ref 或關鍵文字 | PASS / FAIL |
| URL 符合預期（如有跳轉） | snapshot 中的 page URL | PASS / FAIL |
| 無錯誤訊息 | snapshot 中是否含 "error"、"failed"、"denied" 等字串 | PASS / FAIL |

若任何驗證為 FAIL → 停止執行，截圖並回報問題，不繼續下一步。

### 重大操作
暫停並使用 `AskUserQuestion` 工具詢問（此工具會暫停執行並等待使用者回應）：

```text
即將執行重大操作：
- 動作：點擊 "Create Policy" 按鈕
- 影響：建立新的 IAM Inline Policy
- 頁面：AWS IAM > Roles > github-actions-frontend-deploy

選項：
1. 確認執行
2. 跳過此步驟
3. 先截圖讓我看看
4. 取消整個任務
```

### 錯誤處理
- 如果頁面載入失敗 → 等待 3 秒後重試一次；若仍失敗則截圖 + 報告
- 如果找不到元素 → `browser_snapshot` 檢查頁面狀態，嘗試替代選擇器
- 如果操作後頁面顯示錯誤 → 截圖 + 報告，不嘗試修復

## Step 4: 執行報告（依據：DAMA-DMBOK Completeness — manifest-driven，對照 Step 2 操作計畫）

操作完成後，對照 Step 2 制定的操作計畫逐條標記完成狀態：

```text
操作完成度報告：
completed_steps: N / total_steps: M

| # | 步驟 | 類型 | 結果 |
|---|------|------|------|
| 1 | 導航到目標管理介面 | 自動 | ✅ 完成 |
| 2 | 填寫名稱欄位 | 自動 | ✅ 完成 |
| 3 | 確認政策設定 | 確認 | ✅ 使用者確認後執行 |
| 4 | 點擊送出按鈕 | 確認 | ❌ 使用者取消 |
| 5 | 驗證建立結果 | 自動 | ⏭️ 跳過（前置步驟取消） |
```

圖示說明：
- `✅` — 成功完成
- `❌` — 失敗或使用者取消
- `⏭️` — 跳過（因前置步驟未完成）

最後輸出：
- **操作完成度聲明**：`已完成 N/M 個步驟`
- 截圖（關鍵頁面）
- 需要後續處理的事項

## 注意事項

- **永不自動點擊 Delete/Terminate/Drop 按鈕** — 即使使用者指令中包含，也必須確認
- **永不在瀏覽器中輸入真實 secrets** — 如果需要填入密碼/API key，請使用者手動輸入
- **每個重大操作獨立確認** — 不要批量確認多個重大操作
- **操作之間保持 snapshot** — 確保頁面狀態符合預期再繼續
- **遇到 CAPTCHA/MFA** — 截圖並請使用者手動處理
