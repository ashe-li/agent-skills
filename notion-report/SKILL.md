---
name: notion-report
description: 把工作成果寫回 Notion 頁面 — 走官方 REST API（不開瀏覽器），依收件對象（PM／設計／營運／工程）自動調整內容深度與用詞，寫入前一律 dry-run 過目、寫入後讀回驗證。觸發：「把這次結果記到 Notion」「同步進度給 PM」「更新這張票的狀態」「寫個交付摘要給設計」，或 /notion-report <Notion URL>。
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, AskUserQuestion
argument-hint: <Notion URL> [--to pm|design|ops|eng] [--mode append|comment] [--dry-run]
---

# /notion-report — 把成果寫回 Notion（API，不開瀏覽器）

`/notion-plan` 的反向：那個是**從** Notion 讀需求進來，這個是**往** Notion 寫結果出去。

> **與 `/notion-plan` 的關鍵差異：本 skill 不使用 `playwright-cli`。**
> `/notion-plan` 必須開真實瀏覽器，是因為它要讀 Notion 的 client-side rendered 畫面
> （見全域規則 `webfetch-blocklist.md`）。寫入不需要看畫面，官方 REST API 就夠，
> 因此本 skill 全程走 `curl`／`python3`，**不開瀏覽器、不注入 snapshot、不吃 context**。
> 需要「先讀懂一張票再動手」時才用 `/notion-plan`；只是要回報結果就用本 skill。

## 一次性設定（沒有 token 就無法運作）

Notion API 需要 internal integration token，**必須由使用者本人建立**，Claude 無法代勞：

1. 到 <https://www.notion.so/my-integrations> → New integration → 選擇 workspace → 建立
2. 複製 Internal Integration Secret（`ntn_` 開頭）
3. 存檔並鎖權限：

```bash
mkdir -p ~/.config/notion && chmod 700 ~/.config/notion
# 貼上 token 後 Ctrl-D（不要用 echo，避免進入 shell history）
cat > ~/.config/notion/token && chmod 600 ~/.config/notion/token
```

4. **把目標頁面分享給 integration**（最常被漏掉的一步）：
   開啟 Notion 頁面 → 右上 `···` → `Connections` → 加入剛建立的 integration。
   沒做這步，API 會回 `object_not_found`，看起來像 URL 錯，其實是權限沒開。

載入方式（每次執行前）：

```bash
export NOTION_TOKEN="$(tr -d '[:space:]' < ~/.config/notion/token)"
```

> **紅線：永不輸出 token 值。** 不 echo、不寫進報告、不放進指令參數
> （參數會進 shell history 與 process list）。一律走環境變數。

## Step 0：前置檢查

```bash
export NOTION_TOKEN="$(tr -d '[:space:]' < ~/.config/notion/token)"
S=~/Documents/agent-skills/notion-report/scripts/notion_api.py
python3 "$S" probe "<notion_url>"
```

預期輸出頁面標題、`archived: False`、既有 block 數。任一失敗都**停下來**：

| 症狀 | 意義 | 處置 |
|---|---|---|
| `NOTION_TOKEN 未設定` | 沒做一次性設定 | 引導使用者做上面四步，不要自行找替代路徑 |
| `unauthorized` | token 無效／已撤銷 | 請使用者到 my-integrations 重新產生 |
| `object_not_found` | **頁面沒分享給 integration**（不是 URL 錯） | 請使用者做第 4 步 |
| `archived: True` | 頁面在垃圾桶 | 停止，向使用者確認是否找錯頁面 |

## Step 1：確定收件對象

**這一步不可省略。**同一份工作寫給不同人，該留下的內容完全不同。
使用者沒指定時，用 `AskUserQuestion` 問，不要自行假設。

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
python3 "$S" render "$DRAFT"        # 印出 block JSON 與數量，不送出
cat "$DRAFT"                        # 給使用者看實際文字
```

把草稿內容貼給使用者過目，並明確說出「這會 append 到 `<頁面標題>` 的末端」。
使用者說可以才進 Step 4。帶 `--dry-run` 時到此為止，不執行寫入。

## Step 4：寫入

### mode=append（預設）

在頁面末端追加區塊。既有內容完全不動。

```bash
python3 "$S" append "<notion_url>" "$DRAFT"
```

### mode=comment

發成頁面 comment。侵入性最低，但只支援純文字（無標題／清單格式），適合短通知。

```bash
python3 "$S" comment "<notion_url>" "$DRAFT"
```

> `comment` 模式會把 Markdown 標記原樣送出（Notion comment 不解析 Markdown），
> 所以 comment 用的稿子請寫成不含 `##`／`-` 的純文字段落。

## Step 5：讀回驗證（不可省略）

寫完不能只憑 API 回 200 就宣稱完成——沿用既有紀律：**write-then-verify**。

```bash
python3 "$S" readback "<notion_url>" 15
```

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

## 紅線

1. **永不輸出 token 值**，一律走 `NOTION_TOKEN` 環境變數。
2. **永不寫入 secrets 或個資**到 Notion——那是團隊共用且會被索引的空間。
3. **不刪除、不覆寫既有內容。** 本 skill 只做 append 與 comment；
   要修改既有區塊請人工處理，不要用 API 代改別人寫的東西。
4. **共用頁面的寫入視為對外動作**，一律經 Step 3 過目才送出。
5. 目標頁面歸屬不明時先問使用者，不要憑 URL 自行判斷可不可以寫。
