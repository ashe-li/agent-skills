---
name: notion-plan
description: 貼上 Notion URL，自動抓取頁面需求內容，串接 /design 建立實作計畫。
allowed-tools: Bash, Read, AskUserQuestion, Skill, mcp__playwright__browser_navigate, mcp__playwright__browser_snapshot, mcp__playwright__browser_evaluate, mcp__playwright__browser_wait_for, mcp__playwright__browser_click
argument-hint: <Notion URL>
---

<!-- 瀏覽器工具選擇：優先 agent-browser（Bash），次選 playwright-cli（Bash），最後 Playwright MCP。偵測邏輯見 Step 2a。 -->

# /notion-plan — 從 Notion URL 自動建立實作計畫

貼上 Notion URL，自動擷取頁面需求內容並串接 `/design` 建立實作計畫。一條指令完成 Notion 抓取 → 結構化整理 → plans/active/<slug>.md 產出。

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

## Step 2：瀏覽器抓取

> **不使用 WebFetch**：Notion 100% client-side rendering，WebFetch 永遠只能拿到載入骨架。

### 2a. 偵測瀏覽器工具

依優先順序偵測，選中第一個可用的工具：

| 優先順序 | 工具 | 偵測方式 | 優勢 |
|---------|------|---------|------|
| 1 | Claude in Chrome | 檢查 `mcp__claude-in-chrome__*` 工具是否可用 | 直接用使用者已登入的 Chrome，auth 零設定（動態工具，需 `--chrome` 啟動） |
| 2 | `agent-browser` | `Bash: agent-browser --version` | Token 效率最高（`snapshot -i` ~3.5K tokens） |
| 3 | `playwright-cli` | `Bash: playwright-cli --help` | Snapshot 存檔案，不灌 context |
| 4 | Playwright MCP | 嘗試 `mcp__playwright__browser_snapshot` | Fallback（每次 action ~58KB） |

> **Notion 需要登入時**：優先使用 Claude in Chrome（天然有 auth）或 agent-browser `connect <port>`（接管已登入的 Chrome，需 CDP）。

以下步驟以偵測到的工具執行。指令對照：

| 操作 | Claude in Chrome | agent-browser | playwright-cli | Playwright MCP |
|------|-----------------|--------------|---------------|----------------|
| 導航 | 透過 extension 導航 | `open <url>` | `open <url>` (首次) / `goto <url>` (已開啟) | `browser_navigate(url)` |
| Snapshot | extension 提供 DOM 存取 | `snapshot` | `snapshot` | `browser_snapshot()` |
| 執行 JS | extension eval | `eval "() => ..."` | `eval "() => ..."` | `browser_evaluate(expression)` |
| 等待 | — | `wait ".selector"` | — | `browser_wait_for(selector)` |
| 點擊 | extension click | `click @ref` | `click ref` | `browser_click(ref)` |

### 2b. 導航至頁面

使用偵測到的工具導航至 Notion URL。

### 2c. 等待內容載入

等待 `.notion-page-content` 出現（timeout 10s）。

agent-browser / playwright-cli 使用 eval 檢查：
```javascript
() => !!document.querySelector('.notion-page-content')
```

若等待逾時，嘗試備用 selector：`.notion-frame`、`[data-block-id]`、`.layout-content`。

### 2d. 如需登入

使用 snapshot 檢查頁面狀態。判斷是否為登入頁面：
- snapshot 不含任何 `[data-block-id]` 區塊
- 且包含 `input[type="email"]` 或文字 "Sign in"、"Log in"、"Continue with"

若 Step 2a 選中 Claude in Chrome，**不會遇到此問題**——直接使用使用者已登入的 Chrome session，跳過整個 2d。

若使用其他工具且確認為登入頁面，使用 AskUserQuestion 提供以下選項：

```text
此頁面需要登入。請選擇登入方式：

1. [Claude in Chrome] 啟用 /chrome，直接用你已登入的 Chrome（推薦）
2. [headed 模式] 開啟瀏覽器視窗，你手動登入後我接手
3. [接管 Chrome（CDP）] 連接你已登入的 Chrome（需以 --remote-debugging-port 啟動）
4. [已有 profile] 使用之前儲存的登入狀態（playwright-cli state-load）
5. [手動貼上] 跳過抓取，直接貼上頁面內容
6. [設為公開] 請先將頁面設為公開（Share → Publish to web）再重試
```

> **Cloudflare Access / SSO 場景**：headless 瀏覽器會被企業防護擋住。使用選項 2（headed + persistent）讓使用者在可見視窗中完成登入，或選項 5 手動貼上。

根據使用者選擇執行對應的登入流程：

#### 選項 1：Claude in Chrome — 直接使用已登入 session

若 Step 2a 選中 Claude in Chrome，此步驟自動跳過——extension 已提供完整 auth，無需額外登入。

#### 選項 2：headed 模式 — 人工登入後接手

開啟 headed 瀏覽器，使用者在視窗中完成登入（含 CAPTCHA/MFA），完成後 CLI 接管。

```bash
# agent-browser
agent-browser open <notion_url> --headed
# → 使用者在視窗中登入 Notion
# → 登入完成後，使用者回到對話確認

# playwright-cli（--persistent 保留登入狀態供下次使用）
playwright-cli open --headed --persistent <notion_url>
```

使用 AskUserQuestion 等待使用者確認登入完成，再繼續 snapshot。

**可選：儲存登入狀態供下次使用**（playwright-cli 登入成功後）：
```bash
playwright-cli state-save notion-auth.json
```

#### 選項 3：接管已登入的 Chrome（CDP）

適合使用者的 Chrome 已安裝密碼管理器、VPN 等 extension 的場景。

使用者需先以 remote-debugging 啟動 Chrome：
```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222
```

然後接管：
```bash
# agent-browser — 連接指定 CDP port
agent-browser connect 9222
agent-browser open <notion_url>
```

> **注意：** CDP 連接會接管使用者的 Chrome 視窗。操作完成後應使用 `agent-browser close` 釋放連接，不要關閉使用者的 Chrome。

#### 選項 4：使用已儲存的 profile

若之前已透過 playwright-cli 儲存過 auth state：

```bash
# playwright-cli — 載入 storage state
playwright-cli open --headed <notion_url>
playwright-cli state-load notion-auth.json
playwright-cli reload
```

#### 選項 5/6：跳過瀏覽器

- **選項 5**：請使用者手動貼上頁面 Markdown 內容，直接進入 Step 3
- **選項 6**：結束流程，請使用者調整頁面權限後重新執行 `/notion-plan`

#### Playwright MCP fallback

若使用的是 Playwright MCP（Step 2a 選中），登入處理回退為原始流程：

> 此頁面需要登入。請在 Playwright MCP 開啟的瀏覽器視窗中手動登入後回覆確認。
> 或將頁面設為公開（Share → Publish to web）。

### 2e. 擷取頁面內容

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

嘗試使用 eval 執行 DOM 提取（比 snapshot 更精確，且避免 Notion 大頁面的 snapshot token 爆炸）：

```javascript
() => {
  const title = document.querySelector('.notion-page-block, .notion-collection_view-block h1, [data-root="true"] h1')?.textContent?.trim() || '';
  const pageContent = document.querySelector('.notion-page-content');
  const topLevelBlocks = pageContent
    ? Array.from(pageContent.children).filter(el => el.dataset.blockId)
    : Array.from(document.querySelectorAll('[data-block-id]'));
  const content = [];
  topLevelBlocks.forEach(block => {
    const text = block.innerText?.trim();
    if (text && text.length > 0) content.push(text);
  });
  return JSON.stringify({ title, content: content.join('\n\n') });
}
```

> **注意：** 使用 `pageContent.children`（直接子元素）而非 `querySelectorAll('[data-block-id]')`（所有後代），
> 避免嵌套區塊（如 toggle 內的段落）被重複擷取。
>
> **已知問題：** `agent-browser eval` 在某些 Notion 頁面回傳空物件 `{}`（可能與 Notion 的 CSP 或 iframe sandbox 有關）。
> 若 eval 回傳空值，**fallback 到 snapshot**：使用 `snapshot -i`（agent-browser）或 `snapshot`（playwright-cli）取得頁面內容，
> 再從 snapshot 的 textbox / StaticText 節點中提取文字。返回值務必用 `JSON.stringify()` 包裝以確保序列化。

### 2f. 處理長頁面

若頁面內容需要捲動才能載入完全：

```javascript
() => {
  return new Promise(resolve => {
    let lastHeight = document.body.scrollHeight;
    const distance = 500;
    const timer = setInterval(() => {
      window.scrollBy(0, distance);
      const newHeight = document.body.scrollHeight;
      if (newHeight === lastHeight) { clearInterval(timer); resolve(true); }
      lastHeight = newHeight;
    }, 300);
    setTimeout(() => { clearInterval(timer); resolve(true); }, 30000);
  });
}
```

> 使用 `scrollHeight` 穩定性檢查（連續兩次高度不變即停止），而非累計距離比較，
> 避免 lazy-load 不斷擴展頁面導致無限捲動。

捲動完成後捲回頂部，重新擷取內容。

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

- ✅ 有實質內容（標題 + 至少一段文字或清單）→ 進入 Step 5
- ❌ 內容為空、僅有標題、或明顯不完整 → 使用 AskUserQuestion 詢問使用者：

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

## 限制

- 私人頁面需要登入：優先用 Claude in Chrome（零設定）或 `playwright-cli open --headed --persistent`（手動登入）
- Cloudflare Access 等企業防護會擋 headless 瀏覽器 — 需用 headed 模式或 macOS `open` 指令讓使用者在自己的瀏覽器登入
- Claude in Chrome 需要 claude.ai 付費方案（Pro/Max/Team/Enterprise）
- Database view 的篩選/排序/分組以頁面當前狀態為準
- 非常長的頁面（100+ 區塊）可能需要多次捲動，擷取時間較長
