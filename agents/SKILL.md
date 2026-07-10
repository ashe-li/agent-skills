---
name: agents
description: 本 repo 自持 agent 定義目錄索引（complexity-triage / doc-reviewer / doc-updater / tdd-guide），供 design、update 等 skill 的 general-purpose subagent 呼叫時引用定義與檢查清單；本身非直接可呼叫的 skill，不會出現在指令清單中。
user-invocable: false
---

# agents/ — 自持 Agent 定義目錄

本目錄存放本 repo 自持的 agent 定義（一 agent 一檔，ECC 式架構：frontmatter `name`/`description`/`tools`/`model` + 輸入防禦基線 / 職責 / 流程 / 輸出格式 / 判準或檢查清單 / 紅旗）。不依賴 everything-claude-code (ECC) plugin，供 `design/SKILL.md`、`update/SKILL.md` 等呼叫方在啟動 `general-purpose` subagent 時引用其定義。本文件是這份索引與呼叫慣例的唯一權威版本，其他文件（README、design/SKILL.md、update/SKILL.md）一律只留一句指標過來，不重複內容。

## Step 1 — Problem：選定義檔

呼叫方在派 subagent 前，先依任務類型從下表選出對應定義檔：

| 定義檔 | 用途 | 何時用 | 被誰引用 |
|--------|------|--------|---------|
| `complexity-triage.md` | 任務複雜度分診（low/medium/multi-session） | 進入完整規劃儀式前，先粗估影響面決定走快速路徑或完整流程 | `/design` Step 2a |
| `doc-reviewer.md` | 計畫／文件審查，逐項 PASS/FAIL 找碴 | Plan agent 產出計畫後、或文件更新完成後，需要隔離 context 找碴而非背書 | `/design` Step 4a、`/update` Step 2 |
| `doc-updater.md` | 文件同步（README/docs/CODEMAPS），輸出變更 Manifest | 程式碼變更後需同步文件，且需要主模型事後逐條驗證 | `/update` Step 1 |
| `tdd-guide.md` | TDD 紅－綠－重構引導，80% 覆蓋率門檻 | 新功能或修 bug 前缺測試覆蓋，需要先寫失敗測試再實作 | `/design` Step 1 資源盤點列出、Step 3 計畫步驟視需要引用 |

選錯定義檔的常見徵兆：把「審查」誤派成「更新」（doc-reviewer 只讀不寫，doc-updater 才會 Edit 檔案）、把「分診」誤派成「審查」（complexity-triage 只做影響面粗估，不做逐項 PASS/FAIL）。

## Step 2 — Solution：呼叫慣例

一律經 `general-purpose` + 明確 prompt 引用定義檔內容執行，不直呼定義檔的 `name` 當 `subagent_type`：

```
Agent(subagent_type="general-purpose", model="sonnet")  # model 取自定義檔 frontmatter（doc-reviewer/doc-updater/tdd-guide=sonnet、complexity-triage=haiku）
```

prompt 需包含：「依 agents/<name>.md 的定義與檢查清單執行 <任務>」+ 呼叫方傳入的 context。例如審查計畫時：「依 agents/doc-reviewer.md 的定義與檢查清單執行審查」+ Step 3 產出的完整計畫內容。

**撞名禁令：禁止 `Agent(subagent_type="tdd-guide")`、`Agent(subagent_type="doc-updater")` 這類直呼。** 環境中存在同名的 ECC 遺留 agent type（`tdd-guide`、`doc-updater` 皆為 ECC plugin 曾註冊過的 agent 名稱），直呼會靜默載入舊的 ECC 定義而非本目錄的定義，且不會有任何錯誤或警告提示——這是本目錄從 ECC 解耦後最容易被繞過的一點，呼叫方必須明確走 `general-purpose` + 定義引用，不可貪圖簡潔改用 `subagent_type=<name>`。

## Step 3 — When to Use：驗收

subagent 回報完成後，呼叫方（主模型）對照定義檔的「輸出格式」章節逐項核對回報是否符合契約（例：doc-reviewer 的回報是否為逐項 PASS/FAIL 表格，而非摘要式印象分）。

- **doc-updater 額外要求**：回報的「變更 Manifest」不可直接採信，主模型必須親眼 `git diff` 逐條比對其宣稱的每一項變更——這類 agent 有編造修改記錄的前科，見 `agents/doc-updater.md` 的紅旗章節
- **doc-reviewer / complexity-triage**：回報本身即為審查/分診結果，主模型的驗收工作是核對「是否逐項給了 PASS/FAIL 或固定 JSON 格式」，而非重新審查一次

## Context — 路徑解析

呼叫方引用 `agents/*.md` 時，實際路徑依執行環境而異：

```
repo checkout：           agents/<name>.md
skills CLI 安裝環境：      ~/.agents/skills/agents/<name>.md
```

找不到對應檔案時視為「`agents/` 目錄不存在（安裝不完整）」：從 https://github.com/ashe-li/agent-skills 的 `agents/<name>.md` 取得，或請使用者重跑 `npx skills update`；`/design` Step 4a 等呼叫方文件內的 fallback 一律指回這一段，不各自重複。
