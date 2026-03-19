---
name: notion-plan
description: 貼上 Notion URL，自動抓取頁面需求內容，串接 /design 建立實作計畫。
allowed-tools: Bash, Read, AskUserQuestion, Skill
argument-hint: <Notion URL>
---

# /notion-plan — 從 Notion URL 自動建立實作計畫

貼上 Notion URL，自動擷取頁面需求內容並串接 `/design` 建立實作計畫。一條指令完成 Notion 抓取 → 結構化整理 → plans/active/<slug>.md 產出。

> **Token 效率：** 使用 `playwright-cli` (CLI) 而非 Playwright MCP。
> snapshot 存入 `.playwright-cli/*.yml` 檔案，按需讀取，避免每次操作自動注入 ~58KB 到 context。

---

## Step 0：登入持久化（首次設定）

使用 `--profile` 持久化 Notion session，避免每次重新登入。

Profile 路徑：`~/.playwright-cli/notion-profile`

**首次登入：** 若偵測到需要登入（Step 2c），使用 AskUserQuestion 提供選項：

> Notion 需要登入才能存取此頁面。
>
> 選項：
> 1. **幫我開啟登入頁面** — 自動執行 headed 瀏覽器，開啟 Notion 登入頁面供手動登入
> 2. **手動貼上內容** — 跳過瀏覽器登入，直接貼上頁面需求內容

若使用者選擇「幫我開啟」：

```bash
# 先關閉現有 session（避免衝突）
playwright-cli -s=notion close
# 以 headed 模式開啟登入頁面
playwright-cli -s=notion open "https://www.notion.so/login" --profile ~/.playwright-cli/notion-profile --headed
```

開啟後提示使用者：「已開啟瀏覽器，請登入 Notion。完成後告訴我。」
使用者確認登入後，用同一 session 導航至目標頁面繼續抓取。登入狀態會持久保存，後續不需重複登入。

---

## Step 1：解析 URL

從使用者提供的引數中取得 Notion URL。支援以下格式：

| 格式 | 範例 |
|------|------|
| notion.so 頁面 | `https://www.notion.so/workspace/Page-Title-abc123` |
| notion.site 公開頁面 | `https://workspace.notion.site/Page-Title-abc123` |
| notion.so 資料庫 | `https://www.notion.so/workspace/abc123?v=def456` |
| 短網址 | `https://notion.so/abc123` |

從 URL 提取 `pageId`（最後的 32 字元 hex，可能以 `-` 分隔或連續）。

若 `$ARGUMENTS` 為空，使用 AskUserQuestion 請使用者提供 Notion URL。
若 URL 不符合 Notion 格式，提示使用者確認。

---

## Step 2：Playwright CLI 抓取

> **不使用 WebFetch**：Notion 100% client-side rendering，WebFetch 永遠只能拿到載入骨架。
> **不使用 Playwright MCP**：MCP 每次操作注入 ~58KB snapshot，改用 CLI 存檔讀取。

所有 `playwright-cli` 指令使用 session `-s=notion` 和 persistent profile `--profile ~/.playwright-cli/notion-profile`。

> **引號規則：** `eval` 指令外層用 **single-quote**，內部 JS 用 **double-quote**。
> 避免 `\"` escape 導致 Playwright serialization 錯誤。

### 2a. 導航至頁面

```bash
playwright-cli -s=notion open "<notion_url>" --profile ~/.playwright-cli/notion-profile
```

### 2b. 等待內容載入

使用 `eval` 輪詢等待 Notion 內容 selector 出現：

```bash
playwright-cli -s=notion eval '() => new Promise((resolve, reject) => { const selectors = [".notion-page-content", ".notion-frame", "[data-block-id]", ".layout-content"]; const check = () => { for (const s of selectors) { if (document.querySelector(s)) return resolve(s); } setTimeout(check, 500); }; check(); setTimeout(() => reject("timeout: no Notion content found"), 15000); })'
```

若 `playwright-cli eval` 回傳非零 exit code 或 stderr 包含 error/reject 訊息，視為逾時，進入 Step 2c 檢查是否需要登入。

### 2c. 如需登入

取得 snapshot 檢查頁面狀態：

```bash
playwright-cli -s=notion snapshot
```

讀取 snapshot 檔案判斷是否為登入頁面。`playwright-cli snapshot` 會將檔案寫入執行時的工作目錄下 `.playwright-cli/` 資料夾：

```bash
# 找到最新的 snapshot 檔案（確保在專案根目錄執行）
ls -t .playwright-cli/*.yml | head -1
```

然後用 Read 工具讀取該 yml 檔案。判斷條件：
- snapshot 不含任何 `data-block-id` 相關內容
- 且包含 `email`、"Sign in"、"Log in"、"Continue with" 等字樣

若確認為登入頁面，使用 AskUserQuestion 提供選項（參見 Step 0）：
- **幫我開啟登入頁面** — 自動執行 headed 瀏覽器
- **手動貼上內容** — 跳過登入，直接貼上需求

若已有 persistent profile 且 session 有效，直接繼續。

### 2d. 擷取頁面內容

先展開所有 Toggle 區塊（Notion toggle 預設收合，子內容不在 DOM 中）：

```bash
playwright-cli -s=notion eval '() => { const toggles = document.querySelectorAll(".notion-toggle-block > div[role=button]"); toggles.forEach(t => { if (t.getAttribute("aria-expanded") !== "true") t.click(); }); return toggles.length; }'
```

若有 toggle 被展開，等待 1 秒讓子內容載入：

```bash
sleep 1
```

使用 `eval` 提取頁面文字內容（主要擷取方式，足以取得完整文字）：

```bash
playwright-cli -s=notion eval '() => document.querySelector(".notion-page-content")?.innerText || "NO CONTENT"'
```

若需要更結構化的提取（標題 + 內容分離）：

```bash
playwright-cli -s=notion eval '() => { const title = document.querySelector(".notion-page-block, .notion-collection_view-block h1, [data-root=true] h1")?.textContent?.trim() || ""; const content = document.querySelector(".notion-page-content")?.innerText || ""; return JSON.stringify({ title, content }); }'
```

> **注意：** `eval innerText` 為主要擷取方式，已足夠取得頁面文字內容。
> 若使用者的需求提及 Status、負責人、截止日期等屬性欄位，或頁面預期含有留言討論，則在完成 2d 後額外執行 Step 2f。

### 2e. 處理長頁面

若頁面內容需要捲動才能載入完全：

```bash
playwright-cli -s=notion eval '() => new Promise(resolve => { let lastHeight = document.body.scrollHeight; const distance = 500; const timer = setInterval(() => { window.scrollBy(0, distance); const newHeight = document.body.scrollHeight; if (newHeight === lastHeight) { clearInterval(timer); resolve(true); } lastHeight = newHeight; }, 300); setTimeout(() => { clearInterval(timer); resolve(true); }, 30000); })'
```

> 使用 `scrollHeight` 穩定性檢查（連續兩次高度不變即停止），而非累計距離比較，
> 避免 lazy-load 不斷擴展頁面導致無限捲動。

捲動完成後捲回頂部，重新擷取內容：

```bash
playwright-cli -s=notion eval '() => window.scrollTo(0, 0)'
```

### 2f. 擷取 properties / comments（可選）

若需求包含頁面屬性（如 Status、Assignee、Due Date）或留言討論，需從 snapshot YAML 提取：

```bash
playwright-cli -s=notion snapshot
```

找到最新 snapshot 檔案並用 Read 工具讀取：

```bash
ls -t .playwright-cli/*.yml | head -1
```

在 YAML 中搜尋以下區塊：

- **Page properties：** 找到 `table "Page properties"` 區塊，提取各屬性的 key-value
- **Comments：** 找到 `Comments` 或 `Discussion` 區塊，提取留言內容與作者

> **注意：** `eval innerText` 無法取得 properties 和 comments，這些資訊僅存在於 accessibility tree（snapshot YAML）。

### 2g. 關閉 session

流程結束後關閉 browser session，釋放資源：

```bash
playwright-cli -s=notion close
```

---

## Step 3：整理為結構化 Markdown

將擷取到的原始內容轉為結構化 Markdown：

### 3a. 內容清理

- 移除 Notion UI 元素（側邊欄、工具列、分享按鈕等的殘留文字）
- 保留語意結構（標題層級、清單、程式碼區塊、表格）
- 移除重複的空行
- 保留圖片 alt text（若有）

### 3b. 輸出格式

```markdown
# [頁面標題]

> 來源：[Notion URL]
> 擷取時間：[YYYY-MM-DD HH:MM]

---

[頁面內容，保留原始 Markdown 結構]
```

### 3c. 特殊內容處理

| 內容類型 | 處理方式 |
|----------|----------|
| 表格 / Database | 轉為 Markdown table |
| Toggle（摺疊區塊） | 展開內容，以 `<details>` 或縮排呈現 |
| Callout | 以 blockquote `>` 呈現 |
| 程式碼區塊 | 保留 ` ``` ` 格式和語言標記 |
| 嵌入（embed） | 保留 URL，標記為 `[Embedded: URL]` |
| 圖片 | `![alt](url)` 或 `[Image: description]` |

---

## Step 4：內容品質確認

檢查整理後的 Markdown 是否包含有效需求內容：

- 有實質內容（標題 + 至少一段文字或清單）→ 進入 Step 5
- 內容為空、僅有標題、或明顯不完整 → 使用 AskUserQuestion 詢問使用者：

> 擷取到的內容似乎不完整。請確認：
> 1. 頁面是否需要登入？
> 2. 是否要手動貼上需求內容？

---

## Step 5：輸出 Notion 內容並觸發 /design

### 5a. 在對話中輸出完整 Markdown

將 Step 3 整理好的結構化 Markdown 完整輸出至對話中，供後續 `/design` 讀取。

### 5b. 觸發 /design 建立實作計畫

```
Skill(skill="design", args="[SOURCE: /notion-plan] 根據上方 Notion 頁面的需求內容建立實作計畫")
```

> **注意：** `[SOURCE: /notion-plan]` 為 traceability 標記，僅標示需求來源。
> `/design` 不做任何 skip 處理（與 `/update → /pr` 的 `[PIPELINE]` 語意不同），
> 而是從對話脈絡中讀取 Notion 內容，零修改 `/design`。

---

## 使用方式

```bash
/notion-plan https://www.notion.so/workspace/Page-Title-abc123
/notion-plan https://workspace.notion.site/public-page-abc123
```

## Token 效率比較

| 項目 | MCP (舊) | CLI (新) |
|------|----------|----------|
| snapshot 注入方式 | 自動注入 context (~58KB/次) | 存檔按需讀取 |
| 典型 5 步操作 | ~290KB context 消耗 | ~0KB（僅讀取需要的檔案） |
| 登入持久化 | 無 | `--profile` 持久 session |
| Session 管理 | 無 | `-s=notion` 命名 session |

## 限制

- 首次使用需在 headed 模式下手動登入 Notion（之後 session 持久保存）
- Database view 的篩選/排序/分組以頁面當前狀態為準
- 非常長的頁面（100+ 區塊）可能需要多次捲動，擷取時間較長
