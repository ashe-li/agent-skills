---
name: notion-plan
description: 貼上 Notion URL，自動抓取頁面需求內容，串接 /design 建立實作計畫。
allowed-tools: Bash, Read, AskUserQuestion, Skill
argument-hint: <Notion URL>
---

# /notion-plan — 從 Notion URL 自動建立實作計畫

貼上 Notion URL，自動擷取頁面需求內容並串接 `/design` 建立實作計畫。一條指令完成 Notion 抓取 → 結構化整理 → plans/active/<slug>.md 產出。

> 使用 `playwright-cli`（非 Playwright MCP）：snapshot 存入 `.playwright-cli/*.yml` 按需讀取，避免每次操作自動注入 ~58KB 到 context。

---

## Step 0：登入持久化（首次設定）

使用 `--profile` 持久化 Notion session，避免每次重新登入。Profile 路徑：`~/.playwright-cli/notion-profile`

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
# 以 headed 模式開啟登入頁面（notion.com 為新主網域，notion.so 會轉址至此）
playwright-cli -s=notion open "https://www.notion.com/login" --profile ~/.playwright-cli/notion-profile --headed
```

開啟後提示使用者：「已開啟瀏覽器，請登入 Notion。完成後告訴我。」
使用者確認登入後，用同一 session 導航至目標頁面繼續抓取。登入狀態會持久保存，後續不需重複登入。

> **網域遷移注意：** Notion 已將主網域從 `notion.so` 遷移至 `notion.com`，`notion.so` 會 301 轉址到 `notion.com`。
> 登入 cookie 綁定在實際落地的網域上，因此一律以 `notion.com` 登入，避免舊 `notion.so` profile 的 session 在轉址後失效。

---

## Step 1：解析 URL

從使用者提供的引數中取得 Notion URL。支援以下格式：

| 格式 | 範例 |
|------|------|
| notion.com 頁面（新主網域） | `https://www.notion.com/workspace/Page-Title-abc123` |
| app.notion.com 頁面（workspace app 子網域，含 `/p/` 路徑） | `https://app.notion.com/p/<workspace>/Page-Title-abc123` |
| notion.so 頁面（舊網域，會轉址到 notion.com） | `https://www.notion.so/workspace/Page-Title-abc123` |
| notion.site 公開頁面 | `https://workspace.notion.site/Page-Title-abc123` |
| 資料庫（com / so 皆可） | `https://www.notion.com/workspace/abc123?v=def456` |
| 短網址 | `https://notion.com/abc123`、`https://notion.so/abc123` |

從 URL 提取 `pageId`（最後的 32 字元 hex，可能以 `-` 分隔或連續）— 此邏輯與網域、子網域、路徑前綴皆無關，`notion.com` / `app.notion.com` / `notion.so` / `notion.site` 共用；`app.notion.com` 的 `/p/<workspace>/` 路徑前綴不影響抽取結果。

**有效網域白名單（dot-boundary 比對）：** host **等於** `notion.com` / `notion.so` / `notion.site`，**或以** `.notion.com` / `.notion.so` / `.notion.site` 結尾，才視為有效（涵蓋 `www.`/`app.`/`<workspace>.` 等子網域，同時擋掉 `evilnotion.com` 這類同尾巴假冒網域）。

若 `$ARGUMENTS` 為空，使用 AskUserQuestion 請使用者提供 Notion URL。
若 URL 的 host 不在上述白名單內，提示使用者確認。

---

## Step 2：Playwright CLI 抓取

不使用 WebFetch / Playwright MCP（見全域規則 `webfetch-blocklist.md`：Notion 為 100% client-side rendering，且 MCP 每次操作注入 ~58KB snapshot）。

所有 `playwright-cli` 指令使用 session `-s=notion` 和 persistent profile `--profile ~/.playwright-cli/notion-profile`。

> **引號規則：** `eval` 指令外層用 **single-quote**，內部 JS 用 **double-quote**，避免 `\"` escape 導致 Playwright serialization 錯誤。

> **不穩定性止損（2026-07-10 實測）：** headless Notion session 會反覆自斷（browser 重啟成 in-memory、execution context destroyed、click ref 失效誤導航到 sidebar 其他頁）。守則：**第一次 snapshot 就把 properties 與 comments 全部撈齊**（accessibility tree 含 `innerText` 拿不到的欄位與留言，一次 Read 完）；後續互動展開（toggle、「Show N replies」）失敗**重試上限 2 次**，仍失敗就在輸出中標註缺口（例：「此留言下有 N 則收合回覆未擷取」）交給下游 HITL 補讀，不值得無限重試——缺口標註本身就是合格產出。

### 2a. 導航至頁面

```bash
playwright-cli -s=notion open "<notion_url>" --profile ~/.playwright-cli/notion-profile
```

### 2b. 等待內容載入

使用 `eval` 輪詢等待 Notion 內容 selector 出現：

```bash
playwright-cli -s=notion eval '() => new Promise((resolve, reject) => { const selectors = [".notion-page-content", ".notion-frame", "[data-block-id]", ".layout-content"]; const check = () => { for (const s of selectors) { if (document.querySelector(s)) return resolve(s); } setTimeout(check, 500); }; check(); setTimeout(() => reject("timeout: no Notion content found"), 15000); })'
```

若回傳非零 exit code 或 stderr 包含 error/reject 訊息，視為逾時，進入 Step 2c 檢查是否需要登入。

### 2c. 如需登入

取得 snapshot 檢查頁面狀態（寫入執行時工作目錄下的 `.playwright-cli/`）：

```bash
playwright-cli -s=notion snapshot
ls -t .playwright-cli/*.yml | head -1   # 找到最新 snapshot 檔案（於專案根目錄執行）
```

用 Read 工具讀取該 yml 檔案。判斷條件：snapshot 不含任何 `data-block-id` 相關內容，且包含 `email`、"Sign in"、"Log in"、"Continue with" 等字樣 → 視為登入頁面，使用 AskUserQuestion 提供選項（同 Step 0）。若已有 persistent profile 且 session 有效，直接繼續。

### 2d. 擷取頁面內容

先展開所有 Toggle 區塊（Notion toggle 預設收合，子內容不在 DOM 中）：

```bash
playwright-cli -s=notion eval '() => { const toggles = document.querySelectorAll(".notion-toggle-block > div[role=button]"); toggles.forEach(t => { if (t.getAttribute("aria-expanded") !== "true") t.click(); }); return toggles.length; }'
```

若有 toggle 被展開，`sleep 1` 等子內容載入。使用 `eval` 提取頁面文字內容（主要擷取方式，足以取得完整文字）：

```bash
playwright-cli -s=notion eval '() => document.querySelector(".notion-page-content")?.innerText || "NO CONTENT"'
```

若需要更結構化的提取（標題 + 內容分離）：

```bash
playwright-cli -s=notion eval '() => { const title = document.querySelector(".notion-page-block, .notion-collection_view-block h1, [data-root=true] h1")?.textContent?.trim() || ""; const content = document.querySelector(".notion-page-content")?.innerText || ""; return JSON.stringify({ title, content }); }'
```

> 若使用者的需求提及 Status、負責人、截止日期等屬性欄位，或頁面預期含有留言討論，則在完成 2d 後額外執行 Step 2f（`eval innerText` 無法取得 properties 和 comments，僅存在於 accessibility tree）。

### 2d-verify. 擷取完整性檢查

在繼續前，對照 snapshot YAML 驗證擷取完整性：

1. 取得 snapshot 中含 `data-block-id` 的行數作為 `block_count`
2. 計算 `innerText` 回傳內容的行數（`line_count`）
3. 若 `line_count < block_count * 0.5`，視為擷取不完整，**強制觸發 Step 2e 捲動**
4. 否則記錄比值供 Step 4 使用，繼續執行

### 2e. 處理長頁面

若頁面內容需要捲動才能載入完全：

```bash
playwright-cli -s=notion eval '() => new Promise(resolve => { let lastHeight = document.body.scrollHeight; const distance = 500; const timer = setInterval(() => { window.scrollBy(0, distance); const newHeight = document.body.scrollHeight; if (newHeight === lastHeight) { clearInterval(timer); resolve(true); } lastHeight = newHeight; }, 300); setTimeout(() => { clearInterval(timer); resolve(true); }, 30000); })'
```

> 使用 `scrollHeight` 穩定性檢查（連續兩次高度不變即停止），而非累計距離比較，避免 lazy-load 不斷擴展頁面導致無限捲動。

捲動完成後捲回頂部，重新擷取內容：

```bash
playwright-cli -s=notion eval '() => window.scrollTo(0, 0)'
```

### 2f. 擷取 properties / comments（可選）

若需求包含頁面屬性（如 Status、Assignee、Due Date）或留言討論，從 snapshot YAML 提取：

```bash
playwright-cli -s=notion snapshot
ls -t .playwright-cli/*.yml | head -1
```

用 Read 工具讀取，在 YAML 中搜尋：
- **Page properties：** `table "Page properties"` 區塊，提取各屬性的 key-value
- **Comments：** `Comments` 或 `Discussion` 區塊，提取留言內容與作者

### 2g. 關閉 session

```bash
playwright-cli -s=notion close
```

---

## Step 3：整理為結構化 Markdown

清理 Notion UI 殘留文字，保留語意結構（標題層級、清單、程式碼區塊、表格），輸出：

```markdown
# [頁面標題]

> 來源：[Notion URL]
> 擷取時間：[YYYY-MM-DD HH:MM]

---

[頁面內容，保留原始 Markdown 結構]
```

特殊內容依慣例轉換：表格/Database → Markdown table；Toggle → 展開後以 `<details>` 或縮排呈現；Callout → blockquote；程式碼區塊保留 ` ``` ` 與語言標記；嵌入標記為 `[Embedded: URL]`；圖片用 `![alt](url)` 或 `[Image: description]`。

---

## Step 4：內容品質確認

對整理後的 Markdown 逐條驗證，任一 FAIL 則用 AskUserQuestion 詢問使用者：

| # | 驗證項目 | 結果 |
|---|----------|------|
| 1 | 有標題（非空、非 "Untitled"） | PASS / FAIL |
| 2 | 有段落文字（>50 字） | PASS / FAIL |
| 3 | （若 URL 含 `?v=` database 參數）有表格資料 | PASS / FAIL / N/A |
| 4 | 內容長度與預期相符（Step 2d-verify 的 `line_count / block_count` 比值 ≥ 0.5） | PASS / FAIL |

全部 PASS（或 N/A）→ 進入 Step 5；任何 FAIL → 顯示失敗條目，詢問是否需要登入或改為手動貼上內容。

---

## Step 5：輸出 Notion 內容並觸發 /design

將 Step 3 整理好的結構化 Markdown 完整輸出至對話中，供後續 `/design` 讀取，再觸發：

```
Skill(skill="design", args="[SOURCE: /notion-plan] 根據上方 Notion 頁面的需求內容建立實作計畫")
```

> `[SOURCE: /notion-plan]` 為 traceability 標記，僅標示需求來源；`/design` 不做任何 skip 處理（與 `/update → /pr` 的 `[PIPELINE]` 語意不同），而是從對話脈絡中讀取 Notion 內容，零修改 `/design`。
>
> 觸發後 `/design` 的 Step 2a 會先派輕量分診 subagent（haiku）判定複雜度再決定走快速路徑或完整流程——測試回報/單一 bug 票通常落在快速路徑，不會為一張 P2 票展開完整儀式。

---

## 使用方式

```bash
/notion-plan https://www.notion.com/workspace/Page-Title-abc123
/notion-plan https://www.notion.so/workspace/Page-Title-abc123     # 舊網域，自動轉址至 notion.com
/notion-plan https://workspace.notion.site/public-page-abc123
```

## 限制

- 首次使用需在 headed 模式下手動登入 Notion（之後 session 持久保存）
- Database view 的篩選/排序/分組以頁面當前狀態為準
- 非常長的頁面（100+ 區塊）可能需要多次捲動，擷取時間較長
