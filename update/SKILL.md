---
name: update
description: 更新知識庫 — 依序執行文件更新、審查、context 整理、pattern 提取，將本次 session 的變更沉澱為文件與知識。
allowed-tools: Bash, Read, Glob, Grep, Edit, Write, Agent, Skill, AskUserQuestion
---

# /update — 更新知識庫

將本次 session 的工作沉澱為文件與可復用知識。依序執行文件更新、品質審查、context 整理、pattern 提取四個階段。

> 與 `/pr` 的分工：`/update` 負責知識沉澱，`/pr` 負責 git 輸出。需要時可組合使用：先 `/update` 再 `/pr`。

## Manifest 格式（Step 1-5 共用）

各 Step 間用兩份 manifest 交接，用計數比對取代語意判斷「應該沒問題」。

**變更 Manifest**（Step 1 建立、Step 2 核對）：

| # | 預期更新文件 | 狀態 | 說明 |
|---|------------|------|------|
| 1 | docs/api.md | ✅ PASS | 已正確反映 X API 新增 |

**知識寫入 Manifest**（Step 3 建立、Step 4 更新、Step 5 驗證）：

| # | 項目摘要 | 目標知識庫 | 狀態 |
|---|---------|-----------|------|
| 1 | [決策] 選 A 不選 B 的原因 | docs/decisions/ | 待寫入 |

兩份 manifest 都以 `expected_count - verified_count` 的差集判斷遺漏；verified 一律用實際 grep/glob 查證結果，不可只憑印象認定「應該都做了」。

## Step 1: 更新文件

掃描本次 session 的變更，更新相關文件。

1. 執行 `git diff --name-only HEAD` 取得本次變更的檔案清單
2. 若無未 commit 變更，使用 `git diff --name-only HEAD~3..HEAD` 取得近期 commit 涉及的檔案
3. 若仍無任何變更，AskUserQuestion 詢問：指定檔案 / 全面掃描 / 只做知識提取（跳到 Step 3）/ 結束
4. 根據變更更新 `docs/`、`docs/CODEMAPS/`、`README.md` 及其他受影響文件

**HITL（必做）：** 更新前先列出「計畫更新的檔案清單」，AskUserQuestion 確認：確認繼續 / 調整清單 / 跳過。

**文件庫/知識庫歧義：** 上下文提到「文件庫」或「知識庫」時，先確認指的是 `docs/`/`research/`（專案文件）、`~/.claude/projects/.../memory/`（Claude 記憶庫）還是 `~/.claude/skills/`（技能庫），不得自行假設。

```
Agent(subagent_type="general-purpose")
```

prompt 需明確要求：依上述 fallback 邏輯掃描本次 session 的檔案變更、更新對應文件、輸出「變更 Manifest」（格式見上）。

**主模型驗證（必做，不可省略）：** agent 回報完成後，主模型必須親眼 `git diff` 逐條比對其宣稱的每一項變更 —— 這類 agent 有編造修改記錄的前科，不能只憑其自述採信。

Step 1 結束時把「變更 Manifest」交給 Step 2。

## Step 2: 審查文件品質 + 安全閘門

用 fresh-context agent 審查 Step 1 的更新，交叉比對 manifest。

```
Agent(subagent_type="general-purpose")
```

prompt 需要求找碴而非背書：逐條給 PASS/FAIL + 出處，檢查內容是否準確反映程式碼變更、有無過時或不一致描述、格式是否符合專案慣例、有無遺漏的重要資訊；並對照「變更 Manifest」逐條核對 expected vs actual，計算差集。

**安全閘門（並行委派，觸發閘依 [`rules/security-guidance/skill-integration.md`](../rules/security-guidance/skill-integration.md)）：**

判斷本次 session 變更是否觸及安全敏感面（認證/輸入/endpoint/DB/反序列化/檔案/shell/SSRF/DOM/加密）：

- **觸及** → 與上述審查 agent **並行**委派內建 `/security-review`（機制 A），並讓審查 prompt 附上 `~/.claude/claude-security-guidance.md` 當判準（機制 B）：
  ```
  Skill(skill="security-review")
  ```
  findings 併入下方輸出表；CRITICAL/HIGH 走既有 AskUserQuestion 處理流程
- **未觸及** → 明示「無安全敏感面，跳過安全審查」，不空跑

**輸出格式（必填）：**

| Severity | 項目 | 說明 |
|----------|------|------|
| CRITICAL | 內容錯誤 | 文件描述與實際程式碼行為不符 |
| HIGH | 重要遺漏 | 缺少關鍵功能或 API 的說明 |
| MEDIUM | 格式問題 | 結構不一致、用語不統一 |

有 CRITICAL 或 HIGH 時 AskUserQuestion 詢問：修正後繼續（修正 → 展示結果 → 確認 → 進入 Step 3）/ 跳過直接繼續。

## Step 3: 對話 context 整理

回顧整段對話，提取有價值的 context：

| 提取項目 | 目標 |
|----------|------|
| 技術決策脈絡、放棄的方案、調查/比較結果、架構演進、bug 根因分析、業界標準引用 | 知識庫 |
| 跨專案可重用的技術 pattern | learned skills（交給 Step 4） |
| 使用者偏好修正（「不要這樣做」） | MEMORY.md |

**提取觸發訊號：**「考慮過 X 但...」「最終選擇 Y 因為...」「根據 RFC/OWASP/...」、明確的 before/after 比較、使用者糾正行為。

**知識庫位置不固定：** 目錄因專案而異（`research/`、`docs/`、`notes/` 等），不硬編碼路徑；提取完成後用 AskUserQuestion 確認寫入位置。若對話中無有價值的 context，直接進入 Step 4。

Step 3 結束時輸出「知識寫入 Manifest」初版（格式見上），交給 Step 4。

## Step 4: 提取可復用模式

從本次 session 提取跨專案可復用的 pattern：錯誤解決模式、偵錯技巧、跨專案架構決策、工具使用技巧、業界標準應用、標準化方案選型。Step 3 已把專案特定 context 分流出去，這裡只聚焦跨專案可重用的部分。

**寫入格式強制規範：** 新寫入的 learned skill 必須包含以下 frontmatter：

```yaml
---
name: <kebab-case-slug>
description: "<適用場景一句話>"
user-invocable: false
origin: auto-extracted
---
```

**品質評估（inline 5 維度評分，至少 3 分才保存）：**

| 維度 | 分數 (1-5) | 說明 |
|------|-----------|------|
| specificity | | 知識的具體程度 |
| actionability | | 可直接應用的程度 |
| scope fit | | 適用範圍是否恰當 |
| non-redundancy | | 與現有 skills 的重複度 |
| coverage | | 涵蓋場景的完整度 |

廢棄 `specificity=4, actionability=5, ...` 單行格式。需要更嚴謹的交叉驗證時，可另跑 `/learn-eval-deep` 做三系統客觀評分。

> 既有 learned skills 的格式修復 → `/curation`（批量 remediation）。

Step 4 完成後，把提取到的 pattern 回寫進「知識寫入 Manifest」（新增條目、更新狀態）。

## Step 5: 知識庫交叉比對

Step 4 完成後，對「知識寫入 Manifest」做最終驗證 —— 不依賴 LLM 語意判斷「應該沒有遺漏」，一律用 grep/glob/count 逐條驗證。

**本次 session 可能涉及的知識庫位置：**

| 知識庫 | 路徑 |
|--------|------|
| Claude 記憶庫 | `~/.claude/projects/<project-hash>/memory/MEMORY.md` |
| 全域技能庫 | `~/.claude/skills/learned/` |
| 全域記憶 | `~/.claude/MEMORY.md`（如有） |
| 專案知識庫 | Step 3 確認的目錄 |
| 專案文件 | `docs/`、`research/`、`README.md` |

逐條枚舉 manifest 每一項，用 grep/glob 確認是否已寫入對應知識庫（learned skill 用 Glob + 讀取內容確認內容一致），計算 `expected_count - verified_count` 的差集。差集 > 0 時，為每個遺漏項主動起草內容，AskUserQuestion 確認後寫入；無遺漏則列出逐條驗證證據（grep 命中行號等）並結束。

**MEMORY.md 路徑定位：**

- 路徑規則：`~/.claude/projects/<project-hash>/memory/`
- project-hash = 專案絕對路徑中 `/` 替換為 `-`，去除開頭 `-`
- 用 Glob 驗證路徑存在
- 若目錄/檔案不存在 → 用 AskUserQuestion 確認後建立

## Step 6: 總結報告

輸出：文件更新清單與變更摘要、Step 2 審查結果（含安全閘門 findings，已修正/未修正項目）、Step 3 提取的 context 及寫入位置、Step 4 提取的 pattern 與保存位置、Step 5 交叉比對結果、建議下一步（未修正問題的處理方式；適合的話建議執行 `/pr`）。

## Step 7: Pipeline 串接（選擇性）

檢查 `$ARGUMENTS` 是否包含其他 skill 名稱（如 `/pr`）。若有，`/update` 所有步驟（含使用者確認）完成後才用 Skill tool 觸發（傳入該 skill 後面的參數），不會中途跳轉。

串接 `/pr` 時可跳過其 Step 2（Quick Review）——已由本 skill Step 2 的審查 agent 做過更深度審查；其餘步驟正常執行。觸發時在 prompt 前加註 `[PIPELINE: from /update]` 並列出已完成、可跳過的步驟。

用法：`/update /pr`（先更新知識庫，再 commit + push + 建立/更新 PR）、`/update /pr 7238`（更新 PR #7238）。
