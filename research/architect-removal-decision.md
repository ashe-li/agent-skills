# 決策：移除 ECC architect agent，改用 subagent 隔離審查

**日期：** 2026-04-01
**狀態：** 已實施（v1.18.0）

## 背景

消融實驗（616 runs）顯示 `everything-claude-code:architect` agent delta=-0.50，為所有 agent 中表現最差。

## 決策

移除 architect agent，將其 8 項審查職責重新分配：

| 職責 | 新歸屬 |
|------|--------|
| 架構合理性、可擴展性 | Step 3 planner（架構決策必填項）+ Step 4a subagent 審查 |
| 替代方案比較 | Step 3 planner（至少列 1 個排除方案） |
| 現有程式碼相容性 | Step 3 planner + Step 4a 相容性維度 |
| 效能和安全性考量 | Step 3 planner + Step 4a 效能/安全維度 |
| 業界/學術參照可靠性 | Step 4a subagent 主動驗證 |
| ECC 資源選用驗證 | Step 4a 基礎品質（對照 Step 1 盤點） |
| 文件影響評估 | Step 3 planner + Step 4a 文件影響維度 |
| 回饋迭代（最多 2 次） | Step 4a FAIL → 回饋 Step 3 planner → 重新審查 |

## 排除的替代方案

1. **保留 architect agent** — 排除原因：消融實驗確認為負向效果
2. **只刪除不補** — 排除原因：8 項審查職責無人承擔，品質會下降
3. **換用其他 ECC agent（如 code-reviewer）做架構審查** — 排除原因：code-reviewer 設計用途是程式碼品質而非架構決策

## 關鍵設計決策

- 使用 `general-purpose` subagent（sonnet）而非 ECC architect，避免依賴已知退化的 agent
- subagent 隔離 context 執行審查，避免受 planner 偏見影響
- 低複雜度任務跳過架構審查，保持效率
- 審查結果為結構化 PASS/FAIL 格式，可操作性高於原 architect 的自由格式輸出
