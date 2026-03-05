---
name: update
description: 更新知識庫 — 依序執行 doc-updater、code-reviewer、learn-eval，將本次 session 的變更沉澱為文件與知識。
allowed-tools: Bash, Read, Glob, Grep, Edit, Write, Agent, AskUserQuestion
---

# /update — 更新知識庫

將本次 session 的工作沉澱為文件與可復用知識。依序執行文件更新、品質審查、模式提取三個階段。

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
   - **只做 learn-eval** — 跳過 Step 1-2，直接進入 Step 3 提取 patterns
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

**交叉比對（Cross-check）：**

1. 對照 `git diff` 的實際變更，逐一確認每份更新的文件是否涵蓋所有重要變更
2. 確認沒有「應該更新但漏掉的文件」
3. 確認沒有「不應該更新但錯誤修改的文件」
4. 若發現遺漏或錯誤，標示在輸出報告中

**輸出格式：**

| Severity | 項目 | 說明 |
|----------|------|------|
| CRITICAL | 內容錯誤 | 文件描述與實際程式碼行為不符 |
| HIGH | 重要遺漏 | 缺少關鍵功能或 API 的說明 |
| MEDIUM | 格式問題 | 結構不一致、用語不統一 |

如果有 CRITICAL 或 HIGH 問題，使用 AskUserQuestion 詢問使用者：

- **修正後繼續**：先修正問題，再進入 Step 3
- **跳過，直接繼續**：忽略問題，繼續執行 learn-eval

## Step 3: learn-eval — 提取可復用模式

使用 **learn-eval** 從本次 session 提取可復用的 patterns，評估品質後寫入知識庫。

```
Skill(skill="everything-claude-code:learn-eval")
```

**提取範圍：**

- 錯誤解決模式（debugging patterns）
- 偵錯技巧（troubleshooting insights）
- 架構決策（architectural decisions）
- 專案特定模式（project-specific patterns）
- 工具使用技巧（tool usage patterns）

**品質評估：** learn-eval 會自動進行 5 維度評分（specificity、actionability、scope fit、non-redundancy、coverage），至少達 3 分才會保存。

## Step 4: 總結報告

所有步驟完成後，輸出最終報告：

```markdown
## /update 執行結果

### 文件更新
- 列出所有更新的文件檔案及變更摘要

### 品質審查
- 列出 code-reviewer 的審查結果
- 標示已修正 / 未修正的項目

### 知識提取
- 列出 learn-eval 提取的 patterns
- 標示保存位置（全域 / 專案級）

### 建議下一步
- 如果有未修正的問題，建議處理方式
- 如果適合開 PR，建議執行 `/pr`
```
