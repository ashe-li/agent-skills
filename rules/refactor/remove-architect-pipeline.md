# Remove Architect Pipeline

## 禁用規則

**禁止使用 `everything-claude-code:architect` agent。**

消融實驗（2026-03）證實該 agent 在 plan mode 中不增加價值，反而退化輸出品質。

## 替代方案

需要架構分析時，使用以下替代：

| 場景 | 替代方案 |
|------|---------|
| 實作計畫 | `everything-claude-code:planner` agent |
| 快速架構問題 | 直接在 plan 中分析 |
| 複雜系統設計 | Plan Mode + planner agent |

## 適用範圍

- `/design` skill 的 Step 3 使用 `planner`（非 `architect`）
- `/assist` skill 的路由不可導向 `architect`
- 任何 Agent tool 呼叫不可指定 `subagent_type="everything-claude-code:architect"` 或 `subagent_type="architect"`
