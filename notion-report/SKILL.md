---
name: notion-report
description: 把工作成果寫回 Notion 頁面 — 優先走官方 REST API（不開瀏覽器），無 API token 時自動退回 playwright-cli 沿用既有登入 session，依收件對象（PM／設計／營運／工程，未指定時以 AskUserQuestion 詢問、可多選）自動調整內容深度與用詞，寫入前一律 dry-run 過目、寫入後讀回驗證。觸發：「把這次結果記到 Notion」「同步進度給 PM」「更新這張票的狀態」「寫個交付摘要給設計」，或 /notion-report <Notion URL>。
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, AskUserQuestion
argument-hint: <Notion URL> [--to pm|design|ops|eng（可逗號分隔多選）] [--mode append|comment] [--via api|browser] [--dry-run]
---

# /notion-report — 把成果寫回 Notion

`/notion-plan` 的反向：那個是**從** Notion 讀需求進來，這個是**往** Notion 寫結果出去。

## 兩條寫入路徑

| 路徑 | 何時用 | 代價 | 前提 |
|---|---|---|---|
| **API**（優先） | 有 Notion integration token | 不開瀏覽器、不注入 snapshot、幾乎不吃 context | 需自建 internal integration，**且該頁面要加進 Connections** |
| **browser**（退路） | 無 token、或無權建 integration | 要開瀏覽器，較慢且吃一些 context | 沿用 `/notion-plan` 建立的 `~/.playwright-cli/notion-profile` 登入 session |

**自動選路**：`NOTION_TOKEN` 有值就走 API，沒有就走 browser。
`--via api|browser` 可強制指定。

> 公司型 workspace 常需管理員核准才能建 integration，所以 **browser 路徑不是次等品，
> 而是很多人的唯一可行路徑**。兩條路的 Step 1～3（定對象、組稿、dry-run）完全共用，
> 只有 Step 4 的寫入動作不同。

> `/notion-plan` 之所以一定要開瀏覽器，是因為它要**讀** Notion 的 client-side rendered
> 畫面（見全域規則 `webfetch-blocklist.md`）。本 skill 的 API 路徑之所以能免開瀏覽器，
> 是因為**寫入不需要看畫面**——「Notion 要用瀏覽器」這個結論只對讀成立。

## 設定

### 走 API：需一次性建立 integration（可選）

Notion API 需要 internal integration token，**必須由使用者本人建立**，Claude 無法代勞。
**公司型 workspace 常需管理員核准**——沒權限就直接走 browser 路徑，不必卡在這裡。

1. 到 <https://www.notion.so/my-integrations> → New integration → 選 workspace → 建立
2. 複製 Internal Integration Secret（`ntn_` 開頭）
3. 存檔並鎖權限：

```bash
mkdir -p ~/.config/notion && chmod 700 ~/.config/notion
# 貼上 token 後 Ctrl-D（不要用 echo，避免進入 shell history）
cat > ~/.config/notion/token && chmod 600 ~/.config/notion/token
```

4. **把目標頁面分享給 integration**（最常被漏掉的一步）：
   Notion 頁面右上 `···` → `Connections` → 加入該 integration。
   沒做這步 API 會回 `object_not_found`，看起來像 URL 錯，其實是權限沒開。

載入（每次執行前）：

```bash
export NOTION_TOKEN="$(tr -d '[:space:]' < ~/.config/notion/token)"
```

> **紅線：永不輸出 token 值。** 不 echo、不寫進報告、不放進指令參數
> （參數會進 shell history 與 process list）。一律走環境變數。

### 走 browser：沿用既有登入，通常不需設定

`/notion-plan` 已建立 persistent profile `~/.playwright-cli/notion-profile`。
只要那個 session 還有效就能直接寫，不需要 token、不需要管理員核准。

session 過期時（Step 0 會偵測到）重新登入一次：

```bash
playwright-cli -s=notion close
playwright-cli -s=notion open "https://www.notion.com/login" \
  --profile ~/.playwright-cli/notion-profile --headed
# 使用者手動完成登入後，profile 會保存，之後不必再登
```

## Step 0：前置檢查與選路

```bash
API=~/.claude/skills/notion-report/scripts/notion_api.py
BROWSER=~/.claude/skills/notion-report/scripts/notion_browser.py
export NOTION_TOKEN="$(tr -d '[:space:]' < ~/.config/notion/token 2>/dev/null)"
```

`NOTION_TOKEN` 非空 → 走 API；空 → 走 browser。使用者用 `--via` 指定則以他為準。

### API 路徑前置檢查

```bash
python3 "$API" probe "<notion_url>"
```

預期輸出頁面標題、`archived: False`、既有 block 數。

| 症狀 | 意義 | 處置 |
|---|---|---|
| `NOTION_TOKEN 未設定` | 沒建 integration | **改走 browser 路徑**，不要卡住 |
| `unauthorized` | token 無效／已撤銷 | 請使用者重新產生，或改走 browser |
| `object_not_found` | **頁面沒分享給 integration**（不是 URL 錯） | 請使用者做設定第 4 步，或改走 browser |
| `archived: True` | 頁面在垃圾桶 | 停止，向使用者確認是否找錯頁面 |

### browser 路徑前置檢查

```bash
playwright-cli -s=notion close        # 清掉可能殘留的 session
playwright-cli -s=notion open "<notion_url>" --profile ~/.playwright-cli/notion-profile

# 等 Notion 的 CSR 內容真的畫出來，不要用固定 sleep
playwright-cli -s=notion eval '() => new Promise((res, rej) => { const t = Date.now(); const c = () => { if (document.querySelector(".notion-page-content") || document.querySelector("[data-content-editable-root=\"true\"]")) return res("loaded"); if (Date.now() - t > 20000) return rej("timeout"); setTimeout(c, 500); }; c(); })'

playwright-cli -s=notion eval "$(python3 "$BROWSER" probe-js)"
```

`probe-js` 回傳 JSON，逐欄判讀：

| 欄位 | 期望 | 不符時 |
|---|---|---|
| `ok` | `true` | 見下面各欄 |
| `loginRedirect` | `false` | `true` = session 過期，依上面「走 browser」重新登入 |
| `editables` | `> 0` | `0` = **對該頁沒有編輯權限**，停止並告知使用者 |
| `blocks` | 合理數字 | `0` 多半是還沒載完，重跑等待那一步 |
| `title` / `tailText` | 與預期頁面相符 | 不符 = 開錯頁，停止 |

`editables: 0` 要當成硬停止條件——那代表帳號只有 view 權限，
硬寫下去不會成功，卻可能在頁面上留下半截操作。

## Step 1：確定收件對象

**這一步不可省略。**同一份工作寫給不同人，該留下的內容完全不同。

使用者已用 `--to` 指定就直接採用。**沒指定時一律用 `AskUserQuestion` 問，不要自行推測**——
從對話脈絡猜對象很容易猜錯（同一個修復，你以為要給工程存查，其實是要給 PM 交代進度），
而寫進共用頁面的東西不好撤回。照下面這題問，不要每次自己重編選項：

```javascript
AskUserQuestion({ questions: [{
  question: "這份要寫給誰看？決定內容深度與用詞。",
  header: "收件對象",
  multiSelect: true,
  options: [
    { label: "PM / 專案經理",
      description: "結論、對時程的影響、風險、需要他裁決的事項、待辦歸屬。不寫技術根因與指令。" },
    { label: "設計師",
      description: "視覺／互動有什麼變化、before-after、哪些點需要設計決策、走查連結。不寫後端架構與監控數字。" },
    { label: "營運 / 客服",
      description: "使用者可感知的變化、對外怎麼說、要不要公告、客訴應對。不寫內部代號、PR 編號、技術術語。" },
    { label: "工程團隊",
      description: "根因、變更內容、驗證證據、回滾方式、後續技術債。細節照留。" },
  ],
}] })
```

`multiSelect: true` 是刻意的——一張票常常同時要交代給 PM 和設計。
**選了多個對象時，每個對象各自成一個獨立區段**（`### 給 PM`、`### 給設計`），
各自套用自己那一列的規則；**不要合併成一段共用文字**，那會讓每個人都讀到一半跟自己無關的內容，
「刻意不寫」的紀律也跟著失效。

同理，`--mode` 沒指定時預設 `append`，不需要問；
只有在使用者說「不要動到頁面內容」「留個言就好」時才切 `comment`。

| `--to` | 對象 | 他要的是 | 刻意不寫 |
|---|---|---|---|
| `pm` | 專案經理 / PM | 結論、對時程的影響、風險、**需要他裁決的事項**、待辦歸屬 | 技術根因、指令、堆疊細節 |
| `design` | 設計師 | 視覺／互動有什麼變化、before-after、**哪些點需要設計決策**、走查連結 | 後端架構、監控數字 |
| `ops` | 營運 / 客服 | 使用者可感知的變化、**對外怎麼說**、要不要公告、客訴應對話術、影響範圍與時間 | 內部代號、PR 編號、技術術語 |
| `eng` | 工程團隊 | 根因、變更內容、驗證方式與證據、**回滾方式**、後續技術債 | — （細節照留） |

寫作規則（全對象共用）：

- **繁體中文（台灣用語）**；技術術語保留英文原詞，不硬翻
- **結論先行**：第一段就講結果，不要把結論埋在敘事後面
- 對 `pm` / `ops` / `design`：技術細節過於複雜時，先給白話類比再展開
- **對外文件禁用「白話版」「人話版」這類標籤**
- 需要對方動作的事項獨立成段，標明**是誰、要做什麼**，不要混在敘述裡

## Step 2：組稿

寫成 Markdown 暫存檔（不要直接手刻 Notion block JSON，容易出錯）：

```bash
mkdir -p .verification/$(date +%F)/notion-report
DRAFT=.verification/$(date +%F)/notion-report/draft.md
```

支援的 Markdown 子集：`##`／`###` 標題、`-` 項目、`1.` 編號、`> callout`、
```` ``` ```` code block、`---` 分隔線、行內 `**粗體**`／`` `code` ``／`[文字](url)`。

> **不支援表格。** Notion 的 table block 結構複雜且容易寫壞，
> 要呈現對照資料請改用項目列表（`- 指標：before → after`）。

建議骨架（依對象調整深度）：

單一對象：

```markdown
## <YYYY-MM-DD> <一句話標題>（給 <對象>）

**結論**：<一句話講完結果>

### 影響
- <對他而言重要的變化>

### 需要你確認 / 待辦
- <對象>：<具體動作>

### 證據
- [<連結名>](<url>)

---
> 由 Claude Code 於 <時間> 寫入 · 來源：<PR / report 路徑>
```

多個對象（Step 1 複選）：**共用一個結論，之後各自分區**，不要把不同對象的內容混在同一段。

```markdown
## <YYYY-MM-DD> <一句話標題>

**結論**：<一句話講完結果，這句所有人都該看>

### 給 PM
- <時程影響 / 待裁決事項>

### 給設計
- <視覺互動變化 / 待設計決策點>

---
> 由 Claude Code 於 <時間> 寫入 · 來源：<PR / report 路徑>
```

**寫入前自我檢查**（每一項都要過）：

- [ ] 沒有任何 secret（API key / token / password / JWT / 內部 IP）
- [ ] 沒有他人的個資或信箱
- [ ] 對 `ops` 版本：沒有內部代號、PR 編號、服務名
- [ ] 所有數字都有出處，沒有記憶推測值
- [ ] 連結真的打得開

## Step 3：dry-run 過目（強制關卡）

**Notion 頁面通常是團隊共用的，寫入對同事即時可見且不易撤回**，
所以無論使用者授權與否，一律先預覽再送出。

```bash
cat "$DRAFT"                         # 給使用者看實際文字（兩條路徑都要）
python3 "$API" render "$DRAFT"       # 僅 API 路徑：印出 block JSON 與數量，不送出
```

把草稿內容貼給使用者過目，並明確說出「這會 append 到 `<頁面標題>` 的末端」——
頁面標題用 Step 0 `probe` / `probe-js` 拿到的那個，不要用 URL 代稱。
使用者說可以才進 Step 4。帶 `--dry-run` 時到此為止，不執行寫入。

## Step 4：寫入

### A. API 路徑

`mode=append`（預設）——在頁面末端追加區塊，既有內容完全不動：

```bash
python3 "$API" append "<notion_url>" "$DRAFT"
```

`mode=comment`——發成頁面 comment，侵入性最低但只支援純文字：

```bash
python3 "$API" comment "<notion_url>" "$DRAFT"
```

> comment 模式會把 Markdown 標記原樣送出（Notion comment 不解析 Markdown），
> 所以 comment 用的稿子請寫成不含 `##`／`-` 的純文字段落。

### B. browser 路徑

沿用 Step 0 已開好的 session（**不要重開，重開會回到頁面頂端**）：

```bash
playwright-cli -s=notion eval "$(python3 "$BROWSER" insert-js "$DRAFT")"
```

回傳 JSON 的判讀：

- `handledByNotion: true` → Notion 的貼上處理器接走了事件，正常
- `handledByNotion: false` → 事件沒被接管，內容多半沒進去，**不要重試**，先做 Step 5 確認實際狀態
- `FAIL: ...` → 照訊息處理，多半是頁面沒載完或無編輯權限

> **為什麼用 synthetic paste 而不是逐字打字**：Notion 的貼上處理器會把 text/plain
> 的 Markdown 直接解析成對應 block（標題、清單、code block 都認得）。逐字打字則要
> 依賴即時 Markdown 快捷鍵，遇到清單換行、離開 code block 要額外送 Escape／Backspace，
> 狀態機容易走歪。paste 是一次性、原子的，且我們自建 `DataTransfer`，
> **不碰系統剪貼簿也不需要 clipboard 權限**。

browser 路徑**不支援 `mode=comment`**（Notion 的 comment 介面需要多步互動，
不值得為它維護一條脆弱的 UI 流程）。使用者指定 comment 又只有 browser 可用時，
說明限制並問他要不要改用 append。

寫完讓 Notion 有時間送出：

```bash
playwright-cli -s=notion eval '() => new Promise(r => setTimeout(() => r("waited"), 2500))'
```

## Step 5：讀回驗證（不可省略）

寫完不能只憑 API 回 200 或 paste 事件被接走就宣稱完成——沿用既有紀律：**write-then-verify**。

API 路徑：

```bash
python3 "$API" readback "<notion_url>" 15
```

browser 路徑（挑草稿裡一段夠獨特的字串當 needle，例如標題那一行）：

```bash
playwright-cli -s=notion eval "$(python3 "$BROWSER" verify-js "<草稿標題那一行>")"
```

`found: true` 且 **`occurrences: 1`** 才算過。
`occurrences` 大於 1 代表**送出了兩次**（常見於誤判失敗後重試），
要告知使用者並請他手動刪掉重複的那份——不要自己再操作頁面去刪。

確認最後幾個 block 就是剛寫入的內容、順序正確、沒有重複送出。
把 readback 輸出存進 `.verification/<date>/notion-report/readback.txt` 當證據。

回報給使用者時附上：頁面標題、寫入的 block 數、頁面 URL。

## 失敗處置

| 情況 | 處置 |
|---|---|
| append 中途失敗（分批送出時） | **不要重跑整份**，先 `readback` 看已寫入多少，只補剩下的 |
| 寫錯內容 | API 無法整段復原，告知使用者需在 Notion 手動刪除該區塊，並附上要刪的標題 |
| 頁面很長導致 readback 看不到 | 正常，`readback` 只印最後 n 個；用 `probe` 對照 block 總數變化 |
| `validation_error` 提到 rich_text 長度 | 單段超過 2000 字，腳本會自動切段；若仍失敗代表單一 block 內容過長，拆成多段 |
| **（browser）`handledByNotion: false`** | **先做 Step 5 確認實際狀態再說**。盲目重試是這條路徑最容易造成重複寫入的原因 |
| （browser）`editables: 0` | 帳號對該頁只有 view 權限。停止，不要嘗試繞道 |
| （browser）`loginRedirect: true` | profile session 過期，依「走 browser」段落重新登入一次 |
| （browser）內容進去了但排版跑掉 | Notion 貼上解析與 Markdown 有落差。**不要在頁面上手動修**，改草稿後請使用者刪掉舊的重寫一次 |
| （browser）中途瀏覽器被關掉 | Step 5 verify 確認寫入了多少；`occurrences` >1 表示重複，請使用者手動刪 |

## 紅線

1. **永不輸出 token 值**，一律走 `NOTION_TOKEN` 環境變數。
2. **永不寫入 secrets 或個資**到 Notion——那是團隊共用且會被索引的空間。
3. **不刪除、不覆寫既有內容。** 本 skill 只做 append 與 comment；
   要修改既有區塊請人工處理，不要用 API 或瀏覽器代改別人寫的東西。
4. **共用頁面的寫入視為對外動作**，一律經 Step 3 過目才送出。
5. 目標頁面歸屬不明時先問使用者，不要憑 URL 自行判斷可不可以寫。
6. **browser 路徑只做 Step 0／4／5 所列的那幾個 eval，不要即興操作頁面。**
   那是使用者本人的登入 session，在上面做的每個動作都以他的名義留在編輯歷史裡。
7. **寫入失敗時預設不重試。** 先驗證實際狀態——重複寫入比沒寫入更難收拾。
