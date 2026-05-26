---
name: figma-verify
description: Figma 對齊 / 視覺比對 / UI ship gate — 任何提到 figma、Figma 對齊、Figma vs local、Figma vs code、設計稿比對、視覺差異、screenshot diff、UI 對齊、UX 對齊、文案對齊、design token 對齊，或 UI / 文案 PR 即將 ship、code 出現 placeholder / follow-up / 待 designer 確認字串時觸發。流程：Figma MCP 抓真規格 → Playwright MCP headed 抓 local → token + 文案逐項對齊表 → /goal 內建 Haiku 評估者做純視覺 gate（不另起 Agent(model=haiku) subagent）。
allowed-tools: Read, Bash, AskUserQuestion, mcp__figma__authenticate, mcp__figma__get_design_context, mcp__figma__get_variable_defs, mcp__figma__get_screenshot, mcp__figma__get_metadata, mcp__playwright__browser_navigate, mcp__playwright__browser_resize, mcp__playwright__browser_take_screenshot, mcp__playwright__browser_snapshot, mcp__playwright__browser_close
argument-hint: <Figma node-id 或 figma.com URL>
redundancy-peers: [verify-fix-loop]
---

# /figma-verify — Figma vs local 對齊與 ship gate

UI / 文案 PR 在 mark ready-for-review、merge、production deploy 之前的最後一道把關。流程結尾用 Claude Code 內建的 `/goal` Haiku 評估者做視覺差異 gate，**不另起** `Agent(model="haiku")` subagent。

## 何時用 / 何時不用

✅ 用：
- 動到 visual token / 文案 / icon / spacing / layout 的 PR
- ticket / PR description 帶 Figma 連結
- code 出現「placeholder」「temp」「follow-up」「沿用 XX」disclaimer
- mark ready-for-review / merge / deploy 之前的最後檢查

❌ 不用：
- 純後端 / 純 logic PR，零視覺/文案改動
- 設計系統明寫「工程可自行調整 X 範圍」
- Bug fix（z-index、布局壓蓋）非設計參數變更，但仍應 PR 註記讓設計師看到

## Step 1：拿到 Figma node-id

從 ticket / PR / Slack thread 找 `figma.com/design/<fileKey>/...?node-id=<id>`：

```
https://figma.com/design/I8foiMEfn9ngF44UGEIrUL/...?node-id=18374-19281
                         └── fileKey ─────┘                 └─ nodeId (- → :) ─┘
                                                              => "18374:19281"
```

**沒有 node-id 不可動 UI / 文案。**

## Step 2：用 Figma MCP 抓真規格（並行四個 call）

```ts
mcp__figma__get_design_context({ nodeId, fileKey })   // Tailwind code + token names
mcp__figma__get_variable_defs({ nodeId, fileKey })    // token → hex map
mcp__figma__get_screenshot({ nodeId, fileKey })       // 視覺 reference（Step 4.5 用）
mcp__figma__get_metadata({ nodeId, fileKey })         // 結構 + x/y/w/h
```

未授權 → 先跑 `mcp__figma__authenticate` 或請使用者跑 OAuth flow。

## Step 3：fallback — Playwright MCP **headed** + 已登入 Figma session

Figma MCP 不可用時：
- **必 headed**（Figma SPA + CSR + bot detection；headless 卡登入頁）
- **必已登入 Figma session**（headless 預設 session 沒登入）
- **不可 WebFetch**（figma.com 在 webfetch-blocklist 黑名單）
- 開頁面 → `browser_resize` ≥1600×1000 → `browser_take_screenshot fullPage:true` → 量距 / 抓 token name

## Step 4：列「Figma → 程式碼」對應表逐項對齊

| 屬性 | Figma 規格 | 程式碼欄位 | 一致？ |
|---|---|---|---|
| bg token | `Color/Success/L-06` (`#eaf5f4`) | `bg-(--Color-Success-L-06)` | ✅ |
| text token | `Color/Success/Base` (`#2e9a8d`) | `text-(--Color-Success-Base)` | ✅ |
| label 文字 | `待曝光` | `label: "待曝光"` | ✅ |
| banner title | `創作者尚未發佈，素材暫時無法使用` | `title="..."` | ✅ |

任何 ❌ 都必須處理 — 不可「先 ship 等 follow-up」。

## Step 4.5：`/goal` 內建 Haiku 評估者做視覺 gate

token / 文案表通過後**不可直接 ship**。token name 對的也可能視覺渲染不同（font weight fallback、icon size、spacing rendered with 1px diff）。本步驟把「修到視覺一致」變成自動續跑的 gate。

### 4.5a — 把兩張 screenshot surface 進 transcript

`/goal` 評估者是小快模型（**預設 Haiku**），但它**不能呼叫工具**，只讀 transcript 既有內容。主 turn 必須先把 Figma + local 兩張圖貼進對話：

```ts
mcp__figma__get_screenshot({ nodeId, fileKey })
mcp__playwright__browser_take_screenshot({ fullPage: true })  // 已 resize ≥1600×1000
```

兩張圖都進 transcript 後，Haiku 評估者就能直接做「純視覺比對」。

### 4.5b — 下 `/goal` 啟動 visual gate

```text
/goal Figma screenshot 與 local headed screenshot 視覺一致
      （font weight / spacing / color 無肉眼可辨偏差），
      且 Step 4 的「Figma → 程式碼」對齊表全部 ✅
      OR stop after 5 turns
```

- 不一致 → 評估者回 reason（例：「Figma 偏粗 vs local 偏細」），自動啟動下一輪修正
- 一致 → 自動 clear goal，進入 Step 5

### 4.5c — 3 欄診斷表

評估者每輪回的 reason 就是診斷表的「視覺發現」欄位。把多輪 reason 整理成下表貼進 PR description：

| 模型 | 視覺發現（評估者 reason） | 推測根因（主 turn diagnose） |
|---|---|---|
| haiku（`/goal` 評估者，純視覺比對 2 張 screenshot） | 視覺差異明顯，Figma 偏粗 vs local 偏細 | weight 設定錯誤（推測）+ 字體 fallback |
| main（token + 字面值對齊） | bg/text token 一致；文案缺「請勿提前轉發」 | unilateral edit |

任一行 ❌ → **不可進入 Step 5**。

### 反模式 ❌

`/goal` 評估者本來就是 Haiku，多開顯式 `Agent(subagent_type="general-purpose", model="haiku")` 做同樣的視覺比對 = 重複造輪子且增加 token：
- 一次性產出表格 → 走 `/goal`，整理評估者 reason
- 需要 Haiku 呼叫工具或多路並行 → 才用 `Agent(model="haiku")` subagent

## Step 5：ship 前最後一道對齊

ready-for-review / merge / production deploy 前 — 開 Figma 字面值對 `git diff`，逐項勾掉。

## 真的不能等的合法選項

設計師「真的」還沒給 spec（極少發生）：

1. **拆 PR**：只 ship 後端邏輯 / status mapping / route gate，**不 ship 視覺/文案**
2. **TODO 常數 + lint gate**：`const TODO_TOKEN = "TODO_DESIGN_TOKEN_AWAITING_SPEC";` + lint rule 擋 production build
3. **block on designer**：PR description 寫「block on designer spec, do not merge」+ 不點 ready-for-review
4. **dev-only flag**：環境 gate 視覺/文案；production 完全不顯示

❌ **禁止**：自選 token / 自編文案 merge 到 main → production。「沿用同類狀態」「placeholder」「之後修」**都不能當免責詞**。

## Audit grep 配方

```bash
# 找 placeholder / follow-up disclaimer（PR 內最可疑訊號）
grep -rnE "placeholder|TODO|待\s*designer|follow-up|沿用.*配色|借\s+.*token" \
  features/ components/ src/ --include="*.tsx" --include="*.ts" \
  | grep -vE "node_modules|\.test\."

# 找 PR description 自承 placeholder
gh pr view <num> --json body | grep -iE "placeholder|follow-up|沿用|借"
```

## See Also

- `/verify-fix-loop` — Step 4.5 visual gate FAIL 後若想用「次數封頂 + HITL」取代 `/goal` 評估者，改用此 skill
- `/plan-run` — 大規模 UI 改造的多 step 計畫推進
- Claude Code `/goal` 官方文件：https://code.claude.com/docs/en/goal

## 設計依據

- **`/goal` 評估者預設 Haiku**：官方原文 "the conversation so far are sent to your configured small fast model, which defaults to Haiku"
- **Designer spec 不可單方修改**：自選 token / 自編文案 = unilateral edit，無論 placeholder 標記是否清楚
- **Token 名對 ≠ 視覺對**：font weight fallback、字體載入失敗、平台渲染差異會讓 token-correct 的程式碼看起來仍與 Figma 不符 — 視覺 gate 必跑
