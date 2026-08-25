---
name: pr-evidence-comment
description: 對 PR 做 headed 驗收 → 截圖存證 → 主對話目檢抽驗 → 把逐項 PASS/FAIL 與截圖一起發成 PR comment。截圖上傳走 stock Chrome + CDP（gh CLI 與 GitHub API 都不支援 comment 附圖）。觸發：「headed 驗收這個 PR」「把驗收結果貼上去」「驗收 comment 附截圖」「preview env 驗收」，或 UI 變更 ship 前的視覺驗收；亦由 `/pr` Step 5.5 的截圖驗收 gate 串接。
allowed-tools: Bash, Read, Grep, Glob, Write, Edit, AskUserQuestion
argument-hint: <PR 編號 / preview 或 staging URL / 驗收項目清單>
---

# /pr-evidence-comment — headed 驗收 → 截圖 → PR comment 附圖

把一次 headed 驗收變成 PR 上**別人打得開**的證據。

## 為什麼一定要上傳，不能只留本機路徑

驗證產物目錄（慣例 `.verification/`）通常被 `.gitignore` 或 `.git/info/exclude` 排除——因為裡面常含 node id／instance type／成本等內部資訊，不該進版控。因此：

- 截圖**只存在你這台機器**
- report 或 comment 裡寫 `.verification/2026-08-20-xxx/C1.png` 對 reviewer 是**死連結**
- **PR comment 的 `user-attachments` 是這些截圖唯一的持久化與共享管道**

所以「上傳」不是加分項，是這條流程成立的必要條件。

## Step 0 — 先判斷這次變更該不該用截圖驗收

**移除類變更拍不出來。** 「某個 slice 下架了」「某段 dead code 移除了」——截圖只能顯示「看起來正常」，證不了「東西真的不在了」。這類要**量測**：grep 殘留、bundle size diff、reducer key 列表、`ls` 目錄不存在。

| 變更類型 | 驗收手段 |
|---|---|
| UI 視覺／互動行為 | ✅ headed 截圖 |
| 狀態遷移後「行為不變」 | ✅ headed 截圖（證明沒壞）＋ 量測（證明真的遷了） |
| 純移除／重構 | ❌ 改用量測，別追截圖 |
| API／資料正確性 | ❌ 改用 curl／測試輸出 |

混合型（常見）：截圖證「沒壞」、量測證「真的移除」，兩者都要，**別只交截圖**。

> 由 `/pr` Step 5.5 串接進來時，該步驟已做過同一張表的分類並取得使用者授權；此處只需覆核分類是否仍成立（例如 PR 範圍在 gate 後又擴大），不必重問。

## Step 1 — 定驗收項目並編號

**先列清單再開瀏覽器**，不要邊看邊想要驗什麼——臨場發揮會漏掉 regression 面。

編號慣例：

```
B1, B2, B3 …   未登入態（baseline）
C1, C2, C3 …   登入態（changed / core flows）
C8b            同一項的補充視角（scrolled / 展開後）
```

每個編號對應**一個具體斷言**，不是「看一下頁面」。例：

> `C6-statistics-week-toggle` — 日/週/月 toggle 點「週」後 active 狀態切換、百分比同步更新，且**無頁面重整**

斷言要寫得能被判 PASS/FAIL，「無頁面重整」這種才是重點（它證明 local state 生效）。

## Step 2 — headed 驗收與截圖

存放路徑：`<專案或知識庫>/.verification/<YYYY-MM-DD>-<slug>/<env>/`
（`<env>` 例：`staging-v2`、`preview-8051`、`hotfix`）

檔名 = `<編號>-<kebab-描述>.png`，與 Step 1 的清單一一對應。

```bash
agent-browser open <url> --headed     # headless 起過的 session 要先 close 再重開才會套用
agent-browser snapshot -i             # 互動元素清單（~3.5K token）
agent-browser screenshot <path>
```

### 已知會卡住的三種元素

| 症狀 | 成因 | 解法 |
|---|---|---|
| Radix UI Tabs 點了沒反應 | `role=tab` + `tabindex=-1`，`click @ref` 與 `element.click()` 都不觸發 | eval 分派完整序列 `pointerdown → mousedown → focus → pointerup → mouseup → click` |
| 下拉選單項目不在 snapshot 裡 | 可點擊項是**無 role 的巢狀 `<div>`** | eval 找 leaf text node 往上找有 onclick 語意的祖先，直接對該 div 分派事件 |
| 登入表單 email 驗證失敗 | `.env.local` 的值帶雙引號 | `awk -F= '...' \| tr -d '"'` 再餵給 `fill` |

⚠️ **不要把帶引號的值直接印進 snapshot 輸出**——曾意外把 email 印出來一次。密碼欄位 agent-browser 會 mask，email 不會。

### 範圍外的錯誤怎麼處理

console 出現與本次變更無關的 error（第三方元件、既有 API 403/400、第三方登入元件行為），**如實記錄但不列為 FAIL**，並寫明判讀理由與「建議另案追查」。把範圍外問題算進 FAIL 會讓驗收失去裁決力。

## Step 2.5 — 主對話目檢抽驗（發文前的最後一道人眼關卡）

截圖會**以使用者本人身分公開發到 PR**，且發出去就進 GitHub 的 `user-attachments`（刪 comment 不一定收得回連結）。所以送出前，**主對話必須實際 Read 幾張 PNG 看過**，不能只信派工 agent 的文字回報。

理由：視覺判讀（截圖比對）是主模型的職責，且「驗證不自驗」——產截圖的 agent 不能自己判定截圖合格。

抽驗範圍：**至少每個編號段各一張**（B 段一張、C 段一張），加上所有被判 FAIL／WARN 的那幾張全看。

看三件事：

| 檢查 | 為什麼 |
|---|---|
| **畫面是不是真的拍到那個斷言** | 常見失敗是拍到 loading 態、空白頁、或滾動位置不對，agent 卻回報 PASS |
| **有沒有不該外流的東西** | email／測試帳號／內部 URL／token。密碼欄 agent-browser 會 mask，**email 不會** |
| **編號與內容是否對得上** | `C6-statistics-week-toggle.png` 裡真的有 toggle 切到「週」嗎 |

任一項不過 → 退回 Step 2 重拍，不要「先發了再說」。

## Step 3 — 寫驗收 comment（繁體中文）

```markdown
## <驗收名稱>（<情境>, <日期>）：PASS / PASS（附 N 個範圍外 WARN）/ FAIL

於 <base/commit> fresh-context 驗收：

**未登入態**
- B1 首頁：PASS。<觀察到什麼>
- B2 文章頁：PASS。…

**登入態**
- C1 登入 → header 不重整立即變會員態：PASS。點擊登入後 URL 未變，header 由「註冊/登入」變成頭像＋通知鈴。
- …

**範圍外（不列 FAIL）**
- `<某 endpoint>` 403：判讀為帳號權限層，非本次變更引入。建議另案追查。

**量測項**（移除類必附）
- `redux/<module>/`、`saga/<module>/` 目錄已不存在；四份 root reducer 無該條目
```

原則：**逐項對應 Step 1 的編號**，一項一行一結論。`gh pr comment` 只發文字，圖在 Step 4 補上去。

## Step 4 — 上傳截圖（★ 本 skill 的核心，其他步驟都可替代，這步不行）

### 為什麼要繞這麼大圈

1. **`gh` CLI 與 GitHub REST API 都不支援 comment 附圖**——拖拉上傳是 web UI 專屬，`user-attachments` 上傳端點只吃 session auth。
2. **不能改用外部圖床**：private repo 的 staging UI／測試帳號截圖公開曝光有風險；且匿名圖床實測多半已關閉（2026-08 實測：vgy.me 關閉匿名上傳、duk.tw 要 API key、urusai.cc 無公開端點）。
3. **不能用 agent-browser 內建 Chromium**：走 GitHub org 的 Google Workspace SSO 會被 Google 擋（「這個瀏覽器或應用程式可能有安全疑慮」）——這是對 automation 指紋的封鎖，**headed 也一樣擋**。

結論：只有 **stock Chrome（正版 build）+ CDP** 這條路走得通。

### 三步

**① 起 stock Chrome + 獨立 profile + CDP 埠**

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --user-data-dir="$HOME/.agent-chrome-profile" \
  --remote-debugging-port=9222 --no-first-run \
  "https://github.com/login?return_to=<PR路徑>" &
```

- 使用者在這個視窗**人工登入一次**；profile 持久化，之後免重登
- ⚠️ **Chrome 136+ 禁止對「預設 profile」開 CDP** —— `--user-data-dir` 指到獨立目錄是必要的，不是可選

**② 接上去**

```bash
agent-browser connect 9222
```

**③ 模擬人工附圖**（classic comment 表單）

| 目標 | selector |
|---|---|
| 新增 comment textarea | `#new_comment_field` |
| 對應的隱藏 file input | `#fc-new_comment_field` |
| 編輯既有 comment 的 file input | `#fc-issuecomment-<id>-body` |

```bash
agent-browser fill "#new_comment_field" "<Step 3 的文字>"
agent-browser upload "#fc-new_comment_field" <檔案1> <檔案2> ...
```

`upload` 到 file input 等同拖拉：GitHub 自動傳到 `user-attachments`，並把 `![...](...)` markdown **append 進 textarea**。

**送出前必等上傳完**——eval 檢查 textarea value：

- `Uploading` 佔位字串數量歸零
- `user-attachments` 連結數 **=** 檔案數

兩者都成立才可送出。送出鈕用 eval `btn.click()`（form 內找 textContent 為 `Comment` 的 button）。

⚠️ **附件會以使用者本人身分發文**（session 是他的）——文字方向要先給使用者確認過，圖要先過 Step 2.5 的目檢。

## Step 5 — 驗證落地（不可省）

```bash
gh pr view <num> --json comments --jq '.comments[-1].body' | grep -c "user-attachments"
```

數字要等於上傳的檔案數。**不要用「沒有報錯」當成功**——沒有壞消息不是好消息（`git status` 空輸出同理，可能是已 commit，也可能是被 ignore）。

同時把截圖清單與判定寫進知識庫 session 檔，標明證據路徑為**本機限定**。

## 不重造的既有資源

- 設計稿對齊（Figma vs local）：`/figma-verify`——與本 skill 分工：figma-verify 比「有沒有照設計做」，本 skill 證「PR 上的東西真的動起來」
- 事實查核：`/evidence-gate`（comment 定稿前過一次）
- live 狀態查驗：`/verify-live-state`
- 開／更新 PR 本身：`/pr`（其 Step 5.5 會判定是否需要跑本 skill）
