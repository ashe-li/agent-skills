# /playwright-hitl — Playwright Human-in-the-Loop 瀏覽器操作

透過 Playwright MCP 操作瀏覽器，自動執行低風險操作，重大操作前暫停等待人類確認。

適用場景：AWS Console 操作、後台管理介面、需要瀏覽器自動化但不能全自動的任務。

## 操作分級

### 重大操作（必須 human confirmation）

使用 `AskUserQuestion` 暫停，顯示即將執行的動作，等待使用者確認後才繼續。

| 類別 | 範例 |
|------|------|
| 建立/刪除資源 | Create/Delete IAM Role、OIDC Provider、EC2、RDS、S3 Bucket |
| 修改權限/政策 | Attach/Detach Policy、修改 Trust Policy、修改 Security Group |
| 費用相關 | 啟用付費服務、變更 instance type、購買 Reserved Instance |
| 不可逆操作 | 刪除 S3 bucket、terminate instance、drop database |
| 最終送出 | Create、Submit、Deploy、Confirm、Apply Changes 按鈕 |

### 非重大操作（自動執行）

不需等待確認，直接執行：

- 導航（navigate、click 連結、切換頁面）
- 填寫表單欄位（fill、type）
- 選擇 radio/dropdown/checkbox（select_option、click）
- 搜尋、篩選、翻頁
- 展開/收合面板
- 截圖、讀取頁面內容

## Step 1: 確認 Playwright MCP 可用

檢查 Playwright MCP 工具是否可用（`mcp__playwright__browser_navigate` 等）。

如果不可用：
1. 提示使用者啟動 Playwright MCP server
2. 停止執行，不要嘗試用其他方式操作瀏覽器

## Step 2: 理解任務

分析使用者的指令：
- **目標**：要在哪個網站/後台做什麼操作
- **步驟拆解**：將任務拆成逐步操作
- **風險評估**：標記哪些步驟是重大操作

輸出操作計畫，格式：
```
操作計畫：
1. [自動] 導航到 AWS Console IAM
2. [自動] 搜尋 Role "github-actions-frontend-deploy"
3. [自動] 點擊進入 Role 詳情
4. [自動] 填寫 Policy JSON
5. [確認] 點擊 "Create Policy" 按鈕  ← 重大操作
```

## Step 3: 執行操作

按計畫逐步執行：

### 非重大操作
直接使用 Playwright MCP 工具執行，每步驟後用 `browser_snapshot` 確認頁面狀態。

### 重大操作
暫停並使用 `AskUserQuestion` 詢問：

```
即將執行重大操作：
- 動作：點擊 "Create Policy" 按鈕
- 影響：建立新的 IAM Inline Policy
- 頁面：AWS IAM > Roles > github-actions-frontend-deploy

選項：
1. 確認執行
2. 跳過此步驟
3. 先截圖讓我看看
```

### 錯誤處理
- 如果頁面載入失敗 → 截圖 + 報告，不重試
- 如果找不到元素 → `browser_snapshot` 檢查頁面狀態，嘗試替代選擇器
- 如果操作後頁面顯示錯誤 → 截圖 + 報告，不嘗試修復

## Step 4: 執行報告

操作完成後輸出：
- 執行的步驟和結果
- 截圖（關鍵頁面）
- 需要後續處理的事項

## 注意事項

- **永不自動點擊 Delete/Terminate/Drop 按鈕** — 即使使用者指令中包含，也必須確認
- **永不在瀏覽器中輸入真實 secrets** — 如果需要填入密碼/API key，請使用者手動輸入
- **每個重大操作獨立確認** — 不要批量確認多個重大操作
- **操作之間保持 snapshot** — 確保頁面狀態符合預期再繼續
- **遇到 CAPTCHA/MFA** — 截圖並請使用者手動處理
