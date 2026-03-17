---
name: playwright-human-in-the-loop
description: 瀏覽器 Human-in-the-Loop 操作 — 自動選擇最佳瀏覽器工具（agent-browser → playwright-cli → Playwright MCP），低風險操作自動執行，重大操作前暫停等待人類確認。適用 AWS Console、後台管理介面等場景。
allowed-tools: AskUserQuestion, Bash, Read, mcp__playwright__browser_navigate, mcp__playwright__browser_click, mcp__playwright__browser_fill_form, mcp__playwright__browser_type, mcp__playwright__browser_snapshot, mcp__playwright__browser_take_screenshot, mcp__playwright__browser_select_option, mcp__playwright__browser_press_key, mcp__playwright__browser_hover, mcp__playwright__browser_wait_for, mcp__playwright__browser_evaluate, mcp__playwright__browser_tabs, mcp__playwright__browser_close
---

# /playwright-human-in-the-loop — 瀏覽器 Human-in-the-Loop 操作

自動選擇最省 token 的瀏覽器工具，執行低風險操作，重大操作前暫停等待人類確認。

適用場景：AWS Console 操作、後台管理介面、需要瀏覽器自動化但不能全自動的任務。

## 操作分級

### 重大操作（必須 human confirmation）

使用 `AskUserQuestion` 工具暫停執行並等待使用者回應，顯示即將執行的動作，確認後才繼續。

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

## Step 1: 偵測可用瀏覽器工具

依優先順序偵測，選中第一個可用的工具：

| 優先順序 | 工具 | 偵測方式 | 優勢 |
|---------|------|---------|------|
| 1 | Claude in Chrome | 檢查 `mcp__claude-in-chrome__*` 工具是否可用 | 用使用者已登入的 Chrome，auth 零設定，支援 extension |
| 2 | `agent-browser` | `Bash: agent-browser --version` | Token 效率最高（`snapshot -i` ~3.5K tokens） |
| 3 | `playwright-cli` | `Bash: playwright-cli --help` | Snapshot 存檔案，按需讀取 |
| 4 | Playwright MCP | 嘗試 `mcp__playwright__browser_snapshot` | Fallback（每次 action ~58KB 灌入 context） |

> **Claude in Chrome** 需要 `claude --chrome` 或 `/chrome` 啟用，且需 claude.ai 付費方案。若管理介面需要登入且有 extension 依賴，這是最佳選擇。

若工具 2-4 皆不可用，提示使用者安裝：`npm install -g agent-browser` 或 `npm install -g @playwright/cli@latest`。

### 指令對照表

選定工具後，以下為各操作的對應指令：

| 操作 | Claude in Chrome | agent-browser | playwright-cli | Playwright MCP |
|------|-----------------|--------------|---------------|----------------|
| 開啟 URL | extension 導航 | `open <url>` | `open <url>` (首次) / `goto <url>` (已開啟) | `browser_navigate(url)` |
| Snapshot（互動元素） | — | `snapshot -i` | — | — |
| Snapshot（完整） | extension DOM 存取 | `snapshot` | `snapshot` | `browser_snapshot()` |
| 點擊 | extension click | `click @e101` | `click e101` | `browser_click(ref="e101")` |
| 填寫 | extension fill | `fill @e101 "text"` | `fill e101 "text"` | `browser_fill_form(ref="e101", value="text")` |
| 選擇 | extension select | `select @e101 "val"` | `select e101 "val"` | `browser_select_option(ref="e101", value="val")` |
| 按鍵 | extension press | `press Enter` | `press Enter` | `browser_press_key(key="Enter")` |
| 截圖 | extension screenshot | `screenshot [path]` | `screenshot` | `browser_take_screenshot()` |
| 執行 JS | extension eval | `eval "() => ..."` | `eval "() => ..."` | `browser_evaluate(expression="() => ...")` |
| 等待 | — | `wait "selector"` | — | `browser_wait_for(selector)` |
| 關閉 | `/chrome` 管理 | `close` | `close` | `browser_close()` |

> **snapshot 策略**：使用 agent-browser 時，一般操作用 `snapshot -i`（只列可互動元素），需要讀取頁面內容時才用完整 `snapshot`。
> 使用 playwright-cli 時，snapshot 存到 `.playwright-cli/*.yml`，用 Read 工具按需讀取。

### 需要登入的管理介面

若目標網站需要登入（AWS Console、GCP、Vercel 等），使用 AskUserQuestion 提供選項：

| 選項 | 指令 | 適用場景 |
|------|------|---------|
| headed 模式 | `agent-browser open <url> --headed` | 單次操作，手動登入後接手 |
| 接管 Chrome（CDP） | `agent-browser connect 9222` | 已登入的 Chrome（需以 `--remote-debugging-port=9222` 啟動） |
| playwright-cli headed + persistent | `playwright-cli open --headed --persistent <url>` | 持久化 profile，下次自動保留登入狀態 |

> **playwright-cli 可儲存登入狀態**：`playwright-cli state-save auth.json`，下次用 `playwright-cli state-load auth.json` 載入。

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
直接使用 Step 1 選定的工具執行，每步驟後用 snapshot 確認頁面狀態（agent-browser 用 `snapshot -i`，其他用完整 snapshot）。

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
- 如果找不到元素 → snapshot 檢查頁面狀態，嘗試替代選擇器
- 如果操作後頁面顯示錯誤 → 截圖 + 報告，不嘗試修復
- 如果 CLI 工具回傳錯誤 → 先確認瀏覽器是否仍在執行，必要時重新 `open`

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
- **優先使用 CLI 工具** — agent-browser/playwright-cli 不會自動灌 snapshot 進 context，長流程更穩定
