---
name: verify-fix-loop
description: 透過 local Playwright MCP（headed 模式）執行「verify → fix」迭代迴圈：每輪以 snapshot + console + network 為證據驗證網頁行為，若 FAIL 則修正程式碼後重試。完成 2 輪後（Round 3 起）每輪強制 HITL 詢問是否繼續，避免盲目迭代。適合 UI bug 修復、E2E 行為對齊、前端 regression 排除。
allowed-tools: Read, Edit, Write, Bash, Glob, Grep, Agent, AskUserQuestion, mcp__playwright__browser_navigate, mcp__playwright__browser_click, mcp__playwright__browser_fill_form, mcp__playwright__browser_type, mcp__playwright__browser_snapshot, mcp__playwright__browser_take_screenshot, mcp__playwright__browser_console_messages, mcp__playwright__browser_network_requests, mcp__playwright__browser_evaluate, mcp__playwright__browser_wait_for, mcp__playwright__browser_press_key, mcp__playwright__browser_select_option, mcp__playwright__browser_hover, mcp__playwright__browser_navigate_back, mcp__playwright__browser_tabs, mcp__playwright__browser_close
argument-hint: <要驗證的目標 URL 或行為描述>
redundancy-peers: [playwright-human-in-the-loop, verify-evidence-loop, design]
---

# /verify-fix-loop — Verify-Fix 迭代迴圈（Local Playwright MCP, Headed）

透過 local Playwright MCP（headed 模式）跑「驗證 → 診斷 → 修正 → 重新驗證」迴圈，每輪以 snapshot + console + network 為證據；FAIL 則修正程式碼，下一輪再驗證。Hard cap 與 HITL 觸發時機見 **Step 0b（本文件唯一權威來源，其餘各處只引用不重述）**。

## 與既有 skill 的差異

| Skill | 用途 | 何時用 |
|---|---|---|
| `playwright-human-in-the-loop` | 操作型：每個重大瀏覽器操作前確認 | 在管理介面執行一次性流程 |
| **`verify-fix-loop`** | 修復型：迴圈驗證與程式碼修正 | UI bug 修復、E2E 行為對齊 |
| `verify-evidence-loop` | 證據型：技術主張的 4 維文獻驗證 | 高風險決策前的證據查驗 |
| `design` | 規劃型：架構決策與計畫產出（非執行） | 問題超出 fix 範圍，需重新規劃時轉交 |

本 skill **不重造**：bring-your-own-MCP（local Playwright headed）、復用 `playwright-human-in-the-loop` 的 checklist-driven 驗證模式、復用 `verify-evidence-loop` 的迭代 + HITL gate 模式。超出 fix 範圍時透過 HITL 第 4 選項委派 `/design`（需 `Agent` tool）。

## Step 0: 前置確認

### 0a. Playwright MCP Headed 模式確認

Headed 模式由 Playwright MCP server 啟動參數決定（`--headed` flag），不是 per-call 開關。

**檢查方式：**

1. 嘗試呼叫 `mcp__playwright__browser_snapshot` — 若工具不存在或回傳錯誤，視為 MCP 未啟動
2. 詢問使用者目前 Playwright MCP server 是否以 headed 啟動

若未啟用 headed，提示在 `~/.claude.json` 或專案 `.mcp.json` 加入：

```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["-y", "@playwright/mcp@latest", "--headed"]
    }
  }
}
```

調整後須**重啟 Claude Code** 讓 MCP 重新連線。Headed 模式必要性：使用者可同步觀察瀏覽器、HITL 時能視覺確認、debug 體驗大幅優於 headless。

若 MCP 不可用 → 停止流程，請使用者啟動後重試，**不嘗試用 Bash 替代**。

### 0b. 範圍確認（強制 HITL）— hard cap / HITL 觸發時機的唯一來源

使用 `AskUserQuestion` 收集四項必要資訊（缺一不可）：

> 即將啟動 verify-fix 迭代迴圈：
> - 目標：[$ARGUMENTS 摘要]
> - 預估：每輪 ~5-10k tokens，**hard cap = 5 rounds**（agent 多輪易 drift，故設此上限）
> - 完成 2 輪後（**Round 3 起**）每輪詢問是否繼續
>
> 請提供：
> 1. **驗證目標 URL**（例：`http://localhost:3000/login`）
> 2. **PASS 條件**（具體、可驗證；例：「點擊 Login 後跳轉 `/dashboard` 且 console 無 error」）
> 3. **可修改檔案範圍**（glob；例：`src/components/Login/**`、`src/api/auth.ts`）— 用於限制 Fix 階段的編輯邊界
> 4. **Dev server 狀態**（已運行 / 需先啟動 / 不適用，見 0c）

任一項缺失 → 不得進入 Step 1。

#### PASS 條件 DSL（建議格式）

每條 criterion 一行，型別 + 參數 + 期望值。Phase A 直接機械對照，避免「自由文字 → LLM 解讀」的不確定性。

| 型別 | 語法 | 範例 |
|---|---|---|
| `url` | `url: <regex 或字串>` | `url: /dashboard$` |
| `element` | `element: <ref 或 selector>` | `element: button[name="Sign out"]` |
| `text` | `text: "<字串>"` | `text: "Welcome, Alice"` |
| `not-element` | `not-element: <selector>` | `not-element: .login-form` |
| `console` | `console: no-error` 或 `no-warning` | `console: no-error` |
| `network` | `network: no-4xx` / `no-5xx` / `no-failure` | `network: no-failure` |
| `eval` | `eval: <JS 表達式（須 truthy）>` | `eval: window.localStorage.getItem('token') !== null` |

範例：

```text
url: /dashboard$
element: nav [name="User menu"]
console: no-error
network: no-5xx
```

自由文字輸入 → Step 0b 末尾自動轉換為 DSL，啟動前回讀使用者確認。

### 0c. Dev Server 策略

依 0b 第 4 項回答：

| 狀態 | 行為 |
|---|---|
| **已運行** | 直接進入 Step 1；`browser_navigate` 連線拒絕時退回 0c HITL，不自動啟動 |
| **需先啟動** | `AskUserQuestion` 詢問是否由 skill 用 Bash 啟動。**預設不啟動**，請使用者另開 terminal 跑 `npm run dev` 後再回來 |
| **不適用**（靜態 HTML / production URL） | 跳過 server 檢查 |

**為何預設不自動啟動：** dev server 是 long-running process，自動啟動會佔用 token budget 持續 tail stdout；skill 結束時 process 殘留難以回收；連線重試 / port conflict 邏輯複雜易出錯。

若使用者堅持讓 skill 啟動 → `Bash(run_in_background=true)` + 5 秒後 health check（`browser_navigate` 是否 200）。失敗則終止 process 並報告。Skill 結束時**強制提示**使用者手動 kill。

### 0d. 持久化 round log（自動建立）

啟動 Step 1 前建立檔案：`.claude/verify-fix-loop/<timestamp>-<slug>.md`

- `<timestamp>`：`YYYYMMDD-HHMMSS`
- `<slug>`：URL 末段 path kebab-case，截斷至 30 字元（純 query → `verify-<random>`）
- 每輪結束 append 一個 `## Round N` 區塊（diagnosis、files changed、verdict）；已修改檔案不自動 revert
- HITL 早退 / cap-exceeded / PASS 都寫入 final report 至同一檔案
- 用途：跨 session 接手（搭配 `/handoff`）、PR description 引用、回溯 debug 軌跡
- 不入 git：建議 `.gitignore` 加 `.claude/verify-fix-loop/`

## Step 1: Verify-Fix 迴圈

逐輪執行 Phase A → B → C → D，直到 PASS 或觸及 Step 0b 的 hard cap；第 3 輪起，進入 Phase A 前先過 Step 2 的 HITL Gate。

### Phase A: Verify

對 PASS 條件逐項驗證（checklist-driven，優於自由文字判讀）：

1. `browser_navigate(target_url)` — 進入目標頁
2. `browser_wait_for(...)` — 依 PASS 條件等候 ready selector / text
3. 依 PASS 條件執行操作（click / fill / select 等）
4. `browser_snapshot()` — 取得 ARIA tree
5. `browser_console_messages()` — 蒐集所有 console 訊息
6. `browser_network_requests()` — 蒐集所有 network 請求

對 PASS 條件逐項判定（與 Step 0b PASS 條件 DSL 一一對應）：

| DSL 型別 | 驗證項 | 來源 | PASS 條件 |
|---|---|---|---|
| `url:` | URL 符合預期 | snapshot.url | 字串或 regex 匹配 |
| `element:` | 預期元素出現 | snapshot ARIA | 含預期 ref / selector |
| `not-element:` | 預期元素不存在 | snapshot ARIA | 不含指定 selector |
| `text:` | 預期文字出現 | snapshot ARIA（accessible name / textContent） | 含指定字串 |
| `console:` | 無 console error / warning | `browser_console_messages` | 無 `level: error`（或 warning） |
| `network:` | 無失敗 network 請求 | `browser_network_requests` | 無 4xx / 5xx / `failed` |
| `eval:` | 自訂條件（DOM / 狀態） | `browser_evaluate` | 表達式 truthy |

**全部 PASS** → exit success；**任一 FAIL** → 進入 Phase B。

### Phase B: Diagnose

從失敗證據萃取根因，輸出固定格式：

```text
Round {N} Diagnosis
- Failed criteria:
  - [criterion 1]
  - [criterion 2]
- Evidence:
  - snapshot excerpt: <關鍵節點>
  - console error: <stack trace 首行>
  - network failure: <method> <url> → <status>
- Suspected root cause: <一句話>
- Suspected files (within allowed_paths):
  - <path 1>
  - <path 2>
```

若 suspected files 超出 `allowed_paths` → 標記 `[OUT-OF-SCOPE]`，**不直接編輯**，留待 HITL gate 提醒使用者擴大範圍。

### Phase C: Fix

依 diagnosis 編輯**僅在 `allowed_paths` 內**的檔案：

- 使用 Read → Edit / Write，最小變更原則
- 修改前後在 round_log 記錄 file diff 摘要（`+N -M lines`）
- 一次只針對 1 個 root cause；不順手做不相關的清理

**硬性禁止**：

| 禁止行為 | 原因 |
|---|---|
| 修改測試 / spec 讓 assert 變寬鬆 | 「為過而過」非真修復 |
| 修改 PASS 條件本身 | 同上，污染驗證迴圈 |
| 在 catch 直接 swallow error | 隱藏問題，下輪可能仍 FAIL 但 console 變乾淨 |
| 跨範圍刪檔 / 改架構 | 屬 `/design` 範圍 |
| 寫死 hardcoded value 繞過 | 治標不治本 |

### Phase D: Wait for Reload

- **HMR 場景**：`browser_wait_for(time=2)` → `browser_navigate(target_url)` 強制重新載入
- **需 build / restart**：使用 `AskUserQuestion` 詢問使用者是否已 ready，不自動執行 build（避免長時間佔用）
- **SSR / Next.js**：依專案慣例（hot reload 通常足夠；若涉 server component 變更則需 restart）

## Step 2: HITL Gate（觸發時機見 Step 0b：第 3 輪起每輪）

進入 Phase A 之前用 `AskUserQuestion`：

> Round {N-1} 結束後仍未通過驗證。
>
> **Round Log:**
> | # | Verdict | Diagnosis 摘要 | Files Changed |
> |---|---|---|---|
> | 1 | FAIL | Login button onClick missing | src/Login.tsx |
> | 2 | FAIL | Token header not sent on /api/me | src/api/client.ts |
>
> **目前阻塞 PASS 條件:**
> - {failed criterion 1}
> - {failed criterion 2}
>
> **可選擇:**
> 1. **繼續** — 再跑 1 round（最多到 round 5 hard cap）
> 2. **停止並回報** — 輸出目前狀態，由人類接手
> 3. **改換策略** — 描述新方向後重新進入 Step 0b
> 4. **轉交 /design** — 問題超出 fix 範圍，需重新規劃架構

達 hard cap 上限即使選「繼續」也強制停止並輸出 cap-exceeded 報告。

**選 4 時的處理**：呼叫 `/design` 並把當前 round_log 與失敗證據作為 context 傳遞，避免 /design 從零開始。

## Step 3: Final Report

無論成功 / 早退 / 達 cap，最終輸出統一格式：

```markdown
## Verify-Fix Loop Report

**Status:** PASS | EARLY-EXIT | CAP-EXCEEDED
**Rounds:** N / 5
**Target:** {target_url}
**Allowed paths:** {glob list}

### PASS Criteria
- [x] 預期元素出現
- [x] URL 正確
- [ ] 無 console error  ← 仍 FAIL（早退或 cap-exceeded 時）

### Round Log
| # | Verdict | Diagnosis | Files Changed |
|---|---|---|---|
| 1 | FAIL | Login button missing onClick | src/Login.tsx (+5 -2) |
| 2 | FAIL | API returns 401, token header missing | src/api/client.ts (+3 -0) |
| 3 | PASS | — | — |

### Files Modified（累積）
- src/Login.tsx
- src/api/client.ts

### Next Steps
- {若 PASS：建議跑 lint / test / `/pr`}
- {若 EARLY-EXIT：列出選擇的選項與下一步}
- {若 CAP-EXCEEDED：強烈建議轉 /design 重新規劃，附 round_log}
```

## See Also

- `/playwright-human-in-the-loop` — 單次操作型瀏覽器自動化（非迴圈）
- `/design` — 問題超出 fix 範圍時的架構重新規劃
- `/verify-evidence-loop` — 技術主張的證據驗證（非程式碼修正）
- `/pr` — 迴圈成功後的 commit + PR 自動化
