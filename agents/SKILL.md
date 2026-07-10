---
name: agents
description: 本 repo 自持 agent 定義目錄，供 design/update 等 skill 引用，非直接呼叫的 skill。
user-invocable: false
---

# agents/ — 自持 Agent 定義目錄

本目錄存放本 repo 自持的 agent 定義（一 agent 一檔，ECC 式架構：frontmatter `name`/`description`/`tools`/`model` + 輸入防禦基線 / 職責 / 流程 / 輸出格式 / 判準或檢查清單 / 紅旗）。不依賴 everything-claude-code (ECC) plugin，供 `design/SKILL.md`、`update/SKILL.md` 內的 `general-purpose` subagent 呼叫時引用其定義。

## 索引

| 定義檔 | 用途 | 被誰引用 |
|--------|------|---------|
| `agents/complexity-triage.md` | 任務複雜度分診（low/medium/multi-session） | `/design` Step 2a |
| `agents/doc-reviewer.md` | 計畫／文件審查，逐項 PASS/FAIL 找碴 | `/design` Step 4a、`/update` Step 2 |
| `agents/doc-updater.md` | 文件同步（README/docs/CODEMAPS），輸出變更 Manifest | `/update` Step 1 |
| `agents/tdd-guide.md` | TDD 紅－綠－重構引導，80% 覆蓋率門檻 | `/design` Step 1 資源盤點、Step 3 計畫步驟視需要引用 |

## 路徑解析

- **repo checkout**：`agents/<name>.md`（相對於本 repo 根目錄）
- **skills CLI 安裝環境**（`npx skills add`）：`~/.agents/skills/agents/<name>.md`

呼叫方（`design/SKILL.md`、`update/SKILL.md`）引用 `agents/*.md` 時，須依實際執行環境（repo 內開發 vs 已安裝環境）解析對應路徑；找不到檔案時視為「`agents/` 目錄不存在」，走各 skill 文件內標註的 fallback。

## 呼叫慣例（避免撞名）

本目錄定義一律經 `Agent(subagent_type="general-purpose", model=<定義檔 frontmatter 的 model>)` + prompt 引用定義檔內容執行；**禁止 `Agent(subagent_type="tdd-guide")` 這類直呼**——環境中可能存在同名的 ECC 遺留 agent type，直呼會靜默載入舊定義而非本目錄的定義，且不會有任何錯誤提示。
