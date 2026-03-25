# Subagent Isolation

控制何時使用獨立 subagent，防止探索任務污染主 context。

## 使用 Agent 工具的時機

| 情境 | 做法 | 原因 |
|------|------|------|
| 探索性任務（結果形狀未知） | 委派 Agent | 避免原始輸出污染 context |
| 大型掃描（10+ 檔案、全目錄） | 委派 Agent | 讀取原始內容過度消耗 token |
| 需要多步驟推理的子任務 | 委派 Agent | 獨立 context 避免主流程干擾 |
| 有明確格式的小操作 | 直接執行 | Agent 啟動有 overhead |
| 單一 Read/Grep/Bash 操作 | 直接執行 | 不值得委派 |

## 返回摘要原則

Agent 完成後，只提取結論，不複製原始輸出到主 context：

- 好：「code-reviewer 發現 3 個 HIGH 問題：(1) ... (2) ... (3) ...」
- 壞：把 code-reviewer 完整輸出貼入主 context

## 隔離程度

| 任務規模 | 策略 |
|----------|------|
| 小（1-3 個動作） | 直接執行 |
| 中（5-15 個動作） | Agent，返回摘要 |
| 大（跨 session / 持續監控） | loop-operator 或 worktree 隔離 |

## 禁止事項

- 遞迴觸發相同 Agent（主流程 → agent → 主流程 → 同 agent）
- Agent 返回超過 200 行原始輸出進主 context
- 在 Agent 呼叫中傳入整個對話歷史（傳摘要即可）
