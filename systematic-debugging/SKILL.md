---
name: systematic-debugging
description: 結構化除錯 — build 失敗、test 錯誤、執行期錯誤、或說「一直修不好」「debug 卡住」「一直報錯」時使用。四階段：Observe → Hypothesize → Test → Fix，禁止盲目重試。
allowed-tools: Bash, Read, Glob, Grep, Edit, Write, AskUserQuestion
---

# /systematic-debugging — 結構化除錯

遇到錯誤時強制執行 4 階段流程，防止盲目重試和猜測性修改。

## Phase 1: Observe（觀察）

收集所有可用資訊，不跳過此步。

1. 完整擷取錯誤訊息（逐字複製，不要重述）
2. 確認觸發環境：
   - 什麼指令觸發？最後一次正常是什麼時候？
   - 最近哪些變更：`git diff`、`git log -5`
3. 讀取錯誤訊息中提到的檔案和行號
4. 判斷錯誤類型：

| 類型 | 信號 |
|------|------|
| 語法/型別 | compile error, type error |
| 執行期 | runtime exception, panic, stack trace |
| 測試失敗 | assertion failed, expected X got Y |
| 環境問題 | not found, permission denied, module not found |
| 邏輯錯誤 | 輸出錯誤但無錯誤訊息 |

**輸出：** 錯誤摘要 + 相關程式碼片段 + 最近變更清單。

## Phase 2: Hypothesize（假設）

明確列出 2-4 個可能根因，**不得跳過直接修改程式碼**。

格式：

```
假設 1：[原因]，依據：[觀察到的證據]
假設 2：[原因]，依據：[觀察到的證據]
```

按可能性排序，最可能的優先測試。

## Phase 3: Test（測試）

針對每個假設設計最小可驗證測試：

1. 設計能「證偽」假設的測試步驟
2. 執行並記錄結果（pass / fail / inconclusive）
3. 根據結果更新假設優先順序

測試方式（按破壞性從小到大）：
- 讀取程式碼、確認程式碼路徑
- 執行子集測試（隔離失敗範圍）
- 插入 debug print，確認執行到哪一步
- 建立最小復現案例

### Escalation 規則

同一假設方向嘗試 3 次仍失敗 → 停止，**回到 Phase 1 重新觀察**。假設可能有誤。

Phase 1 重新觀察 2 次後仍無進展 → AskUserQuestion 說明卡住位置，請使用者補充資訊。

## Phase 4: Fix（修復）

**進入前置條件：** Phase 3 中至少一個假設的測試結果為 pass（找到根因）。

不得在 inconclusive 狀態進入 Fix。

1. 針對確認根因進行最小化修復（不要一次改多處）
2. 修復後執行完整測試套件
3. 確認沒有引入新失敗

| 錯誤類型 | 完成證據 |
|----------|----------|
| Build 失敗 | build 輸出無錯誤 |
| Test 失敗 | n passed, 0 failed |
| 執行期錯誤 | 原觸發路徑不再產生錯誤 |

## 禁止事項

- 看到錯誤直接修改，未先完成 Phase 1-2
- 同時修改多個可能根因（無法判斷哪個有效）
- 「先試試看」的修改（沒有假設依據）
- 宣稱修好但沒有完成證據
- 超過 3 次相同方向失敗不 escalate
