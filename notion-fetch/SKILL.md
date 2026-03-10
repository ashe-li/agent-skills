---
name: notion-fetch
description: 貼上 Notion URL，自動抓取頁面內容（標題、文字、表格、清單），轉為 Markdown 供後續使用。
allowed-tools: Bash, Read, Write, WebFetch, AskUserQuestion, mcp__playwright__browser_navigate, mcp__playwright__browser_snapshot, mcp__playwright__browser_evaluate, mcp__playwright__browser_wait_for, mcp__playwright__browser_click
argument-hint: <Notion URL>
---

# /notion-fetch — 從 Notion 頁面抓取內容

貼上 Notion URL，自動擷取頁面內容並轉為 Markdown。支援公開頁面和需要登入的私人頁面。

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

## Step 2：嘗試 WebFetch（快速路徑）

先用 WebFetch 嘗試取得頁面內容：

```
WebFetch(url=<notion_url>)
```

**判斷內容是否有效：**
- ✅ 回傳的 HTML/文字包含頁面標題和實際內容 → 進入 Step 4 整理輸出
- ❌ 回傳空白、需登入提示、或僅有 Notion 載入骨架（`<div id="notion-app">`）→ 進入 Step 3

> Notion 使用 client-side rendering，公開頁面大多可透過 WebFetch 取得，
> 但部分頁面或私人頁面需要 JavaScript 執行。

---

## Step 3：Playwright MCP 抓取（備用路徑）

當 WebFetch 無法取得內容時，使用 Playwright 瀏覽器：

### 3a. 導航至頁面

```
mcp__playwright__browser_navigate(url=<notion_url>)
```

### 3b. 等待內容載入

```
mcp__playwright__browser_wait_for(selector=".notion-page-content", timeout=10000)
```

若等待逾時，嘗試備用 selector：
- `.notion-frame`
- `[data-block-id]`
- `.layout-content`

### 3c. 如需登入

使用 `browser_snapshot` 檢查頁面狀態。判斷是否為登入頁面：
- snapshot 不含任何 `[data-block-id]` 區塊
- 且包含 `input[type="email"]` 或文字 "Sign in"、"Log in"、"Continue with"

若確認為登入頁面，使用 AskUserQuestion 告知使用者：

> ⚠️ 此頁面需要登入。請在瀏覽器中登入 Notion 後重試，
> 或將頁面設為公開（Share → Publish to web）。

若使用者的 Playwright 已有 Notion session（persistent profile），則直接繼續。

### 3d. 擷取頁面內容

先展開所有 Toggle 區塊（Notion toggle 預設收合，子內容不在 DOM 中）：

```javascript
() => {
  const toggles = document.querySelectorAll('.notion-toggle-block > div[role="button"]');
  toggles.forEach(t => {
    if (t.getAttribute('aria-expanded') !== 'true') t.click();
  });
  return toggles.length;
}
```

若有 toggle 被展開，等待 1 秒讓子內容載入。

接著使用 `browser_snapshot` 取得 accessibility tree：

```
mcp__playwright__browser_snapshot()
```

若內容過長或需要更精確擷取，使用 `browser_evaluate` 執行 DOM 提取：

```javascript
() => {
  // 擷取頁面標題
  const title = document.querySelector('.notion-page-block, .notion-collection_view-block h1, [data-root="true"] h1')?.textContent?.trim() || '';

  // 只擷取 page content 容器下的頂層區塊，避免嵌套重複
  const pageContent = document.querySelector('.notion-page-content');
  const topLevelBlocks = pageContent
    ? Array.from(pageContent.children).filter(el => el.dataset.blockId)
    : Array.from(document.querySelectorAll('[data-block-id]'));

  const content = [];
  topLevelBlocks.forEach(block => {
    const text = block.innerText?.trim();
    if (text && text.length > 0) {
      content.push(text);
    }
  });

  return { title, content: content.join('\n\n') };
}
```

> **注意：** 使用 `pageContent.children`（直接子元素）而非 `querySelectorAll('[data-block-id]')`（所有後代），
> 避免嵌套區塊（如 toggle 內的段落）被重複擷取。

### 3e. 處理長頁面

若頁面內容需要捲動才能載入完全：

```javascript
() => {
  return new Promise(resolve => {
    let lastHeight = document.body.scrollHeight;
    const distance = 500;
    const timer = setInterval(() => {
      window.scrollBy(0, distance);
      const newHeight = document.body.scrollHeight;
      // 若 scrollHeight 不再增長，表示已載入完畢
      if (newHeight === lastHeight) {
        clearInterval(timer);
        resolve(true);
      }
      lastHeight = newHeight;
    }, 300);
    // 安全上限：最多捲動 30 秒
    setTimeout(() => { clearInterval(timer); resolve(true); }, 30000);
  });
}
```

> 使用 `scrollHeight` 穩定性檢查（連續兩次高度不變即停止），而非累計距離比較，
> 避免 lazy-load 不斷擴展頁面導致無限捲動。

捲動完成後捲回頂部，重新擷取內容。

---

## Step 4：整理輸出

將擷取到的原始內容轉為結構化 Markdown：

### 4a. 內容清理

- 移除 Notion UI 元素（側邊欄、工具列、分享按鈕等的殘留文字）
- 保留語意結構（標題層級、清單、程式碼區塊、表格）
- 移除重複的空行
- 保留圖片 alt text（若有）

### 4b. 輸出格式

```markdown
# [頁面標題]

> 來源：[Notion URL]
> 擷取時間：[YYYY-MM-DD HH:MM]

---

[頁面內容，保留原始 Markdown 結構]
```

### 4c. 特殊內容處理

| 內容類型 | 處理方式 |
|----------|----------|
| 表格 / Database | 轉為 Markdown table |
| Toggle（摺疊區塊） | 展開內容，以 `<details>` 或縮排呈現 |
| Callout | 以 blockquote `>` 呈現 |
| 程式碼區塊 | 保留 ` ``` ` 格式和語言標記 |
| 嵌入（embed） | 保留 URL，標記為 `[Embedded: URL]` |
| 圖片 | `![alt](url)` 或 `[Image: description]` |

---

## Step 5：確認與後續

將整理好的 Markdown 內容直接輸出至對話中。

若使用者需要將內容存檔：
- 提議儲存路徑（如 `research/<topic>.md` 或 `docs/<topic>.md`）
- 等待使用者確認後才寫入檔案

---

## 使用方式

```bash
/notion-fetch https://www.notion.so/workspace/Page-Title-abc123
/notion-fetch https://workspace.notion.site/public-page-abc123
```

## 限制

- Notion 的 client-side rendering 可能導致 WebFetch 取得不完整內容
- 私人頁面需要 Playwright 有 Notion session 或使用者手動登入
- Database view 的篩選/排序/分組以頁面當前狀態為準
- 非常長的頁面（100+ 區塊）可能需要多次捲動，擷取時間較長
