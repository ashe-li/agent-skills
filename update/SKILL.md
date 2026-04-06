---
name: update
description: 更新知識庫 — 依序執行 doc-updater、code-reviewer、對話 context 整理、learn-eval，將本次 session 的變更沉澱為文件與知識。
allowed-tools: Bash, Read, Glob, Grep, Edit, Write, Agent, AskUserQuestion
---

# /update — 更新知識庫

將本次 session 的工作沉澱為文件與可復用知識。依序執行文件更新、品質審查、context 整理、模式提取四個階段。

> **ECC 資源前置確認：** 確認 doc-updater agent 和 learn-eval skill 的可用狀態。若已被 defer，提示使用者先 restore 或調整流程。

> 與 `/pr` 的分工：`/update` 負責知識沉澱，`/pr` 負責 git 輸出。需要時可組合使用：先 `/update` 再 `/pr`。

## Step 1: doc-updater — 更新文件

使用 **doc-updater** agent 掃描本次 session 的變更，更新相關文件。

```
Agent(subagent_type="everything-claude-code:doc-updater")
```

**掃描範圍：**

1. 執行 `git diff --name-only HEAD` 取得本次變更的檔案清單
2. 若無未 commit 變更，使用 `git diff --name-only HEAD~3..HEAD` 取得近期 commit 涉及的檔案
3. 若仍無任何變更，使用 AskUserQuestion 詢問：
   - **指定檔案** — 手動指定要檢查的文件
   - **全面掃描** — 掃描所有文件是否有過時內容
   - **只做知識提取** — 跳過 Step 1-2，從 Step 3（對話 context 整理）開始
   - **結束** — 結束 /update 流程
4. 根據變更內容更新：
   - `docs/` 目錄下的相關文件
   - `docs/CODEMAPS/` 目錄下的架構圖
   - `README.md`（如果功能或用法有變動）
   - 其他受影響的文件檔案

**HITL 確認（必做）：** 更新任何文件前，先列出「計畫更新的檔案清單」並使用 AskUserQuestion 請使用者確認：

```
即將更新以下文件，請確認是否正確：
1. path/to/file1.md — 原因：xxx
2. path/to/file2.md — 原因：xxx

[確認繼續] / [調整清單] / [跳過]
```

**文件庫 / 知識庫 歧義處理：** 若上下文提到「文件庫」或「知識庫」，必須先釐清指的是哪個目錄，**不得自行假設**：

- 可能是 `docs/`、`research/`、`README.md`（專案文件）
- 可能是 `~/.claude/projects/.../memory/`（Claude 記憶庫）
- 可能是 `~/.claude/skills/`（技能庫）
- 若無法從上下文判斷，使用 AskUserQuestion 確認後再繼續

**交接資訊：** 記錄更新了哪些文件檔案、變更摘要，作為下一步 code-reviewer 的輸入。

**Step 1 結束時建立「變更 Manifest」（依據：DAMA-DMBOK Completeness）：**

doc-updater 完成後，必須輸出以下格式的 manifest，供 Step 2 逐條比對使用：

```markdown
## 變更 Manifest（Step 1 → Step 2 交接）

### 變更檔案清單（來源：git diff --name-only HEAD）
- [ ] src/foo.ts — 新增 X 功能
- [ ] src/bar.ts — 修改 Y 邏輯
- [ ] ...（共 N 個檔案）

### 預期應更新的文件（含更新原因）
- [ ] docs/api.md — 因 src/foo.ts 新增了 X API
- [ ] README.md — 因功能 Y 有用法變動
- [ ] ...（共 M 份文件）

### 實際已更新的文件
- [x] docs/api.md — ✅ 已更新
- [ ] README.md — ⏳ 待 Step 2 確認
```

expected_count（預期更新）= M；actual_count（已更新）待 Step 2 核實。

## Step 2: code-reviewer — 交叉比對 + 審查文件品質

使用 **code-reviewer** agent 審查 Step 1 更新的文件，並交叉比對變更是否完整正確。

```
Agent(subagent_type="everything-claude-code:code-reviewer")
```

**審查重點：**

- 文件內容是否準確反映程式碼變更
- 是否有過時或不一致的描述
- 格式和結構是否符合專案慣例
- 是否有遺漏的重要資訊

**Manifest-driven 交叉比對（依據：DAMA-DMBOK Completeness + ITIL CMDB Reconciliation）：**

接收 Step 1 的「變更 Manifest」，**逐條執行 set difference 比對**（不可用語意判斷代替計數驗證）：

1. 對照 manifest 的「預期應更新文件清單」（M 份），逐條勾選每份文件是否已更新
2. 計數驗證：`expected_count（M）- actual_count（已確認更新數） = 差集`
3. 差集 > 0 → 標記為「應更新但漏掉的文件」，列在輸出報告中
4. 確認沒有「不在 manifest 但被錯誤修改的文件」（即 manifest 外的意外變更）
5. 若發現遺漏或錯誤，標示在輸出報告中

**逐條比對輸出格式（必填）：**

| # | 預期更新文件 | 狀態 | 說明 |
|---|------------|------|------|
| 1 | docs/api.md | ✅ PASS | 已正確反映 X API 新增 |
| 2 | README.md | ❌ FAIL | 缺少 Y 功能用法說明 |
| ... | ... | ... | ... |
| **計數** | 預期 M 份 | 已確認 K 份 | 差集 = M-K 份遺漏 |

**輸出格式：**

| Severity | 項目 | 說明 |
|----------|------|------|
| CRITICAL | 內容錯誤 | 文件描述與實際程式碼行為不符 |
| HIGH | 重要遺漏 | 缺少關鍵功能或 API 的說明 |
| MEDIUM | 格式問題 | 結構不一致、用語不統一 |

如果有 CRITICAL 或 HIGH 問題，使用 AskUserQuestion 詢問使用者：

- **修正後繼續**：先修正問題 → 展示修正結果 → AskUserQuestion 確認修正無誤 → 進入 Step 3
- **跳過，直接繼續**：忽略問題，繼續執行

## Step 3: 對話 context 整理

回顧整段對話，提取所有有價值的 context，分類到三個目標：

| 提取項目 | 目標 | 說明 |
|----------|------|------|
| 技術決策脈絡（選 A 不選 B + 原因） | 知識庫 | 給人讀的決策紀錄 |
| 放棄的方案及原因 | 知識庫 | 決策紀錄的一部分 |
| 調查/比較結果（框架比較、工具選型） | 知識庫 | 研究成果保留 |
| 架構演進記錄（before/after） | 知識庫 | 專案歷史 |
| Bug 根因分析 | 知識庫 | 專案特定的故障紀錄 |
| 業界標準/學術依據引用 | 知識庫 | 參考資料 |
| 跨專案可重用的技術 pattern | learned skills | 交給 Step 4 learn-eval |
| 使用者偏好修正（「不要這樣做」） | MEMORY.md | 最小化 feedback |

**提取觸發訊號**（對話中出現這些時必須提取）：

- 「考慮過 X 但...」「最終選擇 Y 因為...」「放棄了 Z」
- 「根據 RFC/OWASP/...」「業界做法是...」
- 明確的 before/after 比較
- 調查/比較結果（「比較了 3 個框架，選了 X」）
- 使用者糾正行為（→ MEMORY.md feedback）

**知識庫位置不固定 — HITL 確認：** 知識庫目錄因專案而異（`research/`、`docs/`、`notes/` 等），不硬編碼路徑。提取完成後，用 AskUserQuestion 確認：

```
本次對話提取了以下有價值的 context：

1. [決策] xxx — 選 A 不選 B，因為...
2. [研究] xxx — 比較了 3 個框架...
3. [feedback] xxx — 使用者偏好修正

建議寫入位置：
- #1, #2 → 專案知識庫（請指定目錄，如 docs/decisions/、research/）
- #3 → MEMORY.md feedback

[確認並指定目錄] / [調整內容] / [跳過]
```

若對話中無有價值的 context 可提取，直接進入 Step 4。

**Step 3 結束時建立「知識寫入 Manifest」（依據：DAMA-DMBOK Completeness）：**

context 整理完成、使用者確認後，必須輸出以下格式的 manifest，供 Step 5 最終比對使用：

```markdown
## 知識寫入 Manifest（Step 3 初版）

| # | 項目摘要 | 目標知識庫 | 狀態 |
|---|---------|-----------|------|
| 1 | [決策] 選 A 不選 B 的原因 | docs/decisions/ | 待寫入 |
| 2 | [研究] 3 個框架比較結果 | research/ | 待寫入 |
| 3 | [feedback] 使用者偏好修正 | MEMORY.md | 待寫入 |
| 4 | [pattern] 跨專案可重用模式 | 交給 Step 4 learn-eval | 待提取 |

expected_count（Step 3 識別項目）= N
```

**交接資訊：** 將「跨專案可重用的技術 pattern」清單傳遞給 Step 4 learn-eval 處理。

## Step 4: learn-eval — 提取可復用模式

使用 **learn-eval** 從本次 session 提取跨專案可復用的 patterns，評估品質後寫入知識庫。

```
Skill(skill="everything-claude-code:learn-eval")
```

**提取範圍：**

- 錯誤解決模式（debugging patterns）
- 偵錯技巧（troubleshooting insights）
- 跨專案可重用的架構決策（cross-project architectural decisions）
- 工具使用技巧（tool usage patterns）
- 業界標準應用（industry standard adoptions）
- 標準化方案選型（standardized solution selections）

> Step 3 已將「專案特定 context」分流到知識庫，learn-eval 聚焦在跨專案可重用的 patterns。

**Step 4 結束時更新「知識寫入 Manifest」：**

learn-eval 完成後，將提取到的 patterns 回寫至 manifest（新增條目或更新狀態）：

```markdown
## 知識寫入 Manifest（Step 4 更新版）

| # | 項目摘要 | 目標知識庫 | 狀態 |
|---|---------|-----------|------|
| 1 | [決策] 選 A 不選 B 的原因 | docs/decisions/ | 已寫入 ✅ |
| 2 | [研究] 3 個框架比較結果 | research/ | 已寫入 ✅ |
| 3 | [feedback] 使用者偏好修正 | MEMORY.md | 已寫入 ✅ |
| 4 | [pattern] 跨專案可重用模式 | ~/.claude/skills/learned/ | 已提取 ✅ |
| 5 | [pattern] learn-eval 新發現模式（如有） | ~/.claude/skills/learned/ | 待 Step 5 驗證 |

expected_count（更新後）= N'
```

**品質評估：** learn-eval 會自動進行 5 維度評分，至少達 3 分才會保存。

**寫入格式強制規範：** 新寫入的 learned skill 必須包含以下 frontmatter：

```yaml
---
name: <kebab-case-slug>
description: "<適用場景一句話>"
user-invocable: false
origin: auto-extracted
---
```

品質評分必須使用 5 維度表格格式：

| 維度 | 分數 (1-5) | 說明 |
|------|-----------|------|
| specificity | | 知識的具體程度 |
| actionability | | 可直接應用的程度 |
| scope fit | | 適用範圍是否恰當 |
| non-redundancy | | 與現有 skills 的重複度 |
| coverage | | 涵蓋場景的完整度 |

廢棄 `specificity=4, actionability=5, ...` 單行格式。

> 既有 learned skills 的格式修復 → `/curation`（批量 remediation）。

## Step 5: 知識庫交叉比對（Manifest-driven 驗證 + 主動寫入）

（依據：DAMA-DMBOK Completeness + arXiv:2509.18970 結構性驗證優先於語意驗證）

learn-eval 完成後，執行 **manifest-driven** 最終驗證，確認所有知識庫都已正確更新。**不依賴 LLM 語意判斷「應該沒有遺漏」，必須使用 grep/glob/count 等確定性工具逐條驗證。**

**盤點本次 session 涉及的知識庫位置：**

| 知識庫 | 路徑 | 說明 |
|--------|------|------|
| Claude 記憶庫 | `~/.claude/projects/<project-hash>/memory/MEMORY.md` | 專案級記憶 |
| 全域技能庫 | `~/.claude/skills/learned/` | 全域可復用 patterns |
| 全域記憶 | `~/.claude/MEMORY.md` | 跨專案記憶（如有） |
| 專案知識庫 | Step 3 確認的目錄 | 專案特定的決策/研究紀錄 |
| 專案文件 | `docs/`、`research/`、`README.md` | 專案說明文件 |

**Manifest-driven 驗證步驟（依據：DAMA-DMBOK Completeness）：**

接收 Step 3/4 的「知識寫入 Manifest」（expected_count = N'），執行以下確定性驗證（不可用自由語意判斷代替）：

1. **逐條枚舉 manifest 所有項目**——列出所有 N' 個預期寫入項目
2. **對每個項目使用 grep/glob 驗證是否已寫入對應知識庫：**
   - 決策/研究類 → `Grep(pattern="<關鍵詞>", path="<目標目錄>")`
   - learned skill → `Glob(pattern="~/.claude/skills/learned/*.md")` + 讀取確認內容
   - MEMORY.md feedback → `Grep(pattern="<偏好關鍵詞>", path="<MEMORY.md 路徑>")`
3. **計數比對：** `expected_count（N'）- actual_verified_count（grep 確認存在數）= 差集`
4. **差集 = 遺漏項**，為每個遺漏項主動起草修正內容（不只列出差異）
5. 交叉檢查：是否有跨知識庫的不一致（例如 MEMORY.md 與 learned skill 矛盾）

**Manifest 逐條驗證輸出格式（必填）：**

| # | 項目摘要 | 目標知識庫 | 驗證方法 | 結果 |
|---|---------|-----------|---------|------|
| 1 | [決策] 選 A 不選 B | docs/decisions/xxx.md | grep "選 A" | ✅ PASS（第 12 行） |
| 2 | [pattern] debugging pattern | ~/.claude/skills/learned/ | glob + 讀取 | ❌ FAIL（未找到） |
| 3 | [feedback] 偏好修正 | MEMORY.md | grep "偏好" | ✅ PASS |
| **計數** | 預期 N' 項 | — | — | 已驗證 K 項，差集 = N'-K |

**主動寫入流程：** 差集 > 0 時：

1. 列出「有做但未記錄的決策/知識」
2. 為每個遺漏項**起草修正內容**
3. HITL 確認：

```
知識庫交叉比對發現以下遺漏（Manifest 差集）：

1. [MEMORY.md project] 本次選擇 X 架構的決策未記錄
   驗證方法：grep "X 架構" → 0 結果
   起草內容（memory file `project_architecture_xxx.md`）：
   選擇 X 架構而非 Y，因為...（含 trade-offs 和 rationale）

2. [learned skill] 本次發現的 debugging pattern 未提取
   驗證方法：glob ~/.claude/skills/learned/*.md → 無匹配
   起草內容：...

[確認寫入] / [調整後寫入] / [跳過]
```

4. 確認後直接寫入對應知識庫

**MEMORY.md 路徑定位：**

- 路徑規則：`~/.claude/projects/<project-hash>/memory/`
- project-hash = 專案絕對路徑中 `/` 替換為 `-`，去除開頭 `-`
- 用 Glob 驗證路徑存在
- 若目錄/檔案不存在 → 用 AskUserQuestion 確認後建立

**無遺漏時的 HITL 確認：**

```
知識庫交叉比對結果（Manifest-driven 驗證）：
✅ MEMORY.md — grep 確認已記錄 xxx（第 N 行）
✅ learned/yyy.md — glob + 讀取確認，內容與實作一致
✅ 專案知識庫 — Step 3 識別的 N' 個項目全部 grep 驗證通過

計數比對：expected_count = N'，verified_count = N'，差集 = 0

[確認無誤，結束]
```

## Step 6: 總結報告

所有步驟完成後，輸出最終報告：

```markdown
## /update 執行結果

### 文件更新
- 列出所有更新的文件檔案及變更摘要

### 品質審查
- 列出 code-reviewer 的審查結果
- 標示已修正 / 未修正的項目

### 對話 context 整理
- 列出提取的 context 及寫入位置
- 標示知識庫類型（專案知識庫 / learned skill / MEMORY.md）

### 知識提取
- 列出 learn-eval 提取的 patterns
- 標示保存位置（全域 / 專案級）

### 知識庫交叉比對
- 列出比對結果（✅ 已確認 / ✅ 已修正寫入 / ⏭️ 使用者跳過）

### 建議下一步
- 如果有未修正的問題，建議處理方式
- 如果適合開 PR，建議執行 `/pr`
```

## Step 7: Pipeline 串接（選擇性）

檢查 `$ARGUMENTS` 是否包含其他 skill 名稱（如 `/pr`）。如果有，在 `/update` 完成後自動觸發。

### 串接觸發

```
如果 $ARGUMENTS 包含 "/pr"：
  → 使用 Skill tool 觸發 /pr（傳入 $ARGUMENTS 中 /pr 後面的參數）

如果 $ARGUMENTS 包含其他 /<skill-name>：
  → 使用 Skill tool 觸發對應 skill
```

### 資源去重

串接時，`/update` 已完成的工作會透過環境標記傳遞給下游 skill，避免重複執行：

**串接 `/pr` 時的去重規則：**

| `/pr` 步驟 | 正常執行 | 從 `/update` 串接 | 原因 |
|-------------|---------|-------------------|------|
| Step 1a: Git 分析 | 完整執行 | 完整執行 | 目的不同：`/update` 只需檔案清單，`/pr` 需要完整 diff + commit history 寫 description |
| Step 1b: 對話脈絡分析 | 完整執行 | 完整執行 | `/update` 不做此步驟 |
| Step 2: Quick Review | 完整執行 | **跳過** | `/update` Step 2 已用 code-reviewer agent 做過更深度審查 |
| /pr Step 3-6 | 完整執行 | 完整執行 | 無重疊 |

**實作方式：** 觸發 `/pr` 時，在 prompt 前加上以下指示：

```
[PIPELINE: from /update]
以下步驟已由 /update 完成，請跳過：
- Step 2 (Quick Review) — 已由 code-reviewer agent 完成，無 CRITICAL/HIGH 問題
其餘步驟正常執行。
```

### 用法範例

```bash
/update /pr        # 先更新知識庫，再自動 commit + push + 建立/更新 PR
/update /pr 7238   # 先更新知識庫，再更新 PR #7238
```

**注意：** 只在 `/update` 所有步驟（包含使用者確認）完成後才觸發下一個 skill，不會中途跳轉。
