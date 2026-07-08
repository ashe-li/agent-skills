---
name: triage
description: Skills 分流管理 — 基於消融實驗數據，退役/復原/查詢 learned skills。搭配 skills-ecosystem-eval 的四象限分析使用。
allowed-tools: Bash, Read, Glob, Grep, AskUserQuestion
---

# /triage — Skills Portfolio 分流管理

基於 skills-ecosystem-eval 的消融實驗結果，管理 learned skills 的生命週期。

## Step 0: 檢查工具

確認 `~/.claude/scripts/skills-triage.sh` 存在且可執行（`ls -la ~/.claude/scripts/skills-triage.sh`）。若不存在，提示先從 agent-skills repo 複製：`cp ~/Documents/agent-skills/triage/skills-triage.sh ~/.claude/scripts/ && chmod +x ~/.claude/scripts/skills-triage.sh`。

## Step 1: 查看狀態

```bash
~/.claude/scripts/skills-triage.sh status
```

顯示已退役數量、清單、及審計日誌。如果 `/skill-health` command 可用，建議先執行以取得 portfolio 全局視圖（重複覆蓋率、gap 分析），再決定退役優先順序。

## Step 2: 退役 Skill

使用者提供 skill 名稱和原因時：

```bash
~/.claude/scripts/skills-triage.sh retire <name> "<reason>" <tier> <delta>
```

- `tier`：T1（degraded）、T2（Q3 delta=0）、T3（其他）
- `delta`：消融實驗的 delta 值

退役前確認：
1. 讀取 skill 內容，摘要給使用者
2. 確認使用者同意退役

**退役前影響分析：** 在使用者確認前，先確認此 skill 是否被其他地方引用——grep `~/.claude/skills/learned/`、`~/.claude/rules/`、`~/.claude/CLAUDE.md` 與專案 `CLAUDE.md`，整理結果：

| 引用來源 | 引用位置 | 影響 |
|---------|---------|------|
| （無引用） | — | 可安全退役 |
| other-skill.md | 第 12 行 | 退役後 other-skill 可能行為改變 |

若有引用存在，向使用者展示影響清單，讓使用者決定是否繼續退役。

## Step 3: 復原 Skill

```bash
~/.claude/scripts/skills-triage.sh restore <name>
```

復原後驗證 skill 檔案回到 `~/.claude/skills/learned/`。

## Step 4: 批次處理

當使用者提供消融數據（如 `full-analysis.json`）時：

1. 讀取數據，篩選 delta < 0 的 skills，按 delta 絕對值排序
2. 用 TaskCreate 為每個待退役 skill 建一個 task，並對每個執行 Step 2 的影響分析（grep 搜尋引用）：

   | Skill | Delta | Tier | 依賴關係 |
   |-------|-------|------|---------|
   | skill-a.md | -0.23 | T1 | 無 |
   | skill-b.md | -0.10 | T2 | skill-c 引用 |

   有依賴關係的 skill 需先與使用者確認才可退役，不可批次自動退役；無依賴關係的可批次逐一確認後退役
3. 逐一確認後退役，每次退役後更新對應 task 狀態

## Step 5: 退役後完整性驗證

批次退役完成後，對每個標記完成的 skill 驗證：
- 已不在 `~/.claude/skills/learned/` 中（`ls ~/.claude/skills/learned/<skill-name>.md` 應撲空）
- skill 名稱已出現在 `~/.claude/skills/retired/RETIRED_LOG.md` 中

任一項不成立即視為退役未完成，需人工介入，不可標記該 task 為完成。

## 關鍵路徑

| 路徑 | 用途 |
|------|------|
| `~/.claude/skills/learned/` | 活躍 skills |
| `~/.claude/skills/retired/` | 退役歸檔 |
| `~/.claude/skills/retired/RETIRED_LOG.md` | 審計日誌 |
| `~/.claude/scripts/skills-triage.sh` | 批次腳本 |
