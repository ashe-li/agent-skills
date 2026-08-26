# Task Tracking Tool Availability

Scope: Apply when writing or maintaining any skill / rule / prompt that calls `TodoWrite`, `TaskCreate`, `TaskGet`, `TaskUpdate`, or `TaskList`, or that treats a task list as a completion-tracking substrate.

## 事實（官方，已 live 驗證）

Claude Code **v2.1.233 起**，下列工具在 **Opus 4.8、Sonnet 5、Fable 5、Mythos 5 及這些家族的更新版本**上**預設不註冊**：`TodoWrite`、`TaskCreate`、`TaskGet`、`TaskUpdate`、`TaskList`。

官方理由（[tools-reference](https://code.claude.com/docs/en/tools-reference#task-tool-availability) 原文）：「Those models keep track of multi-step work without a written checklist, and the tools' definitions and reminders take up context, so Claude Code leaves them out.」——**官方建議的因應方式是「什麼都不做」**，讓模型自我追蹤；沒有另一套替代機制。只有「應用端要靠 `tool_use` 事件渲染進度面板」的場景才需要 opt back in。

Agent SDK 對應版本：TypeScript `0.3.233+` / Python `0.2.139+`。

不受影響 / 易混淆的項目：

| 項目 | 現況 |
|---|---|
| `Task` / `TaskOutput` / `TaskStop`（子代理家族） | **不受影響**，照常提供。與 task-tracking 家族同名不同物 |
| background sessions、Claude Code on the web | **一律提供**所有模型，不受此變更影響 |
| `CLAUDE_CODE_ENABLE_TASKS=0` | 是**舊模型**上「改拿 legacy `TodoWrite` 而非四個 Task 工具」的選擇器，**不是**本變更的開關，別混用 |
| subagent | 繼承**你 session 的工具集**，即使跑不同模型。in-process teammate 同理；split-pane teammate 是獨立 process，由它自己的模型決定 |

## Opt-in 途徑（三條有效、一條無效）

有效：

- `CLAUDE_CODE_ENABLE_TODO_TOOLS=1`（env var，v2.1.233+）——**已實測有效**
- `claude --allowedTools TaskCreate`——**已實測有效**
- `claude --tools ...` 指名（會把 session 的內建工具限縮成所列清單，其他要用的工具得一併列出）
- Agent SDK：`allowedTools` / `tools` option，或 `env` 帶 `CLAUDE_CODE_ENABLE_TODO_TOOLS=1`（TS 的 `env` 會**整組取代** subprocess 環境，需 `...process.env` 展開；Python 是疊加）

**無效**：SKILL.md frontmatter 的 `allowed-tools: TaskCreate` **不會**把工具帶回來。frontmatter 是「skill 執行期間可用工具的限縮清單」，不是 opt-in 開關——工具沒註冊，列了也不會出現。**這是本 repo 最容易踩的坑**：frontmatter 看起來像宣告了依賴，實際上是死宣告。

## 撰寫守則

1. **主線不得依賴 Task 工具**。任何 skill 的執行步驟，在工具不存在時必須照常跑完。
2. **追蹤預設用文字清單**——在回覆內維護編號 step 清單並逐項標記狀態。精度與 Task 工具相同，差別只在沒有 UI 面板。
3. **Task 呼叫一律條件式且 best-effort**：寫成「若 session 有 Task 工具則…；否則…」，呼叫失敗即 continue，不中止流程。
4. **不得拿 task 數當完成率分母**，也不得拿 `TaskList` 當唯一的完成狀態來源——無工具環境下兩者都不存在。需要分母就用 plan / 檔案清單這類本來就在的東西。
5. **frontmatter 的 `allowed-tools` 保留 Task 工具無妨**（opt-in 環境下才不會被限縮擋掉），但**不得據此假設工具存在**。

## 驗證方式

判斷當前 session 到底有沒有這些工具，用可觀察證據，不要問模型（模型自報工具清單不可信，實測中對照組會答錯）：

```bash
P='Call the TaskCreate tool once with subject "probe" and activeForm "probing". If that tool does not exist for you, reply exactly NOTOOL and do nothing else.'

# baseline（預設）→ 只印 NOTOOL
claude --output-format stream-json --verbose --model opus -p "$P" | grep -o '"name":"Task[A-Za-z]*"\|NOTOOL' | sort -u

# opt-in → 會印出 "name":"TaskCreate"
CLAUDE_CODE_ENABLE_TODO_TOOLS=1 claude --output-format stream-json --verbose --model opus -p "$P" | grep -o '"name":"Task[A-Za-z]*"\|NOTOOL' | sort -u
```

Why: 2026-08-26 查證。本 repo 有 9 個檔（README、`/design`、`/assist`、`/curation`、`/triage`、`/ship-ticket`、`/plan-run`、`/plan-archive`、`rules/teammate-fleet.md`）把 `TaskCreate` 當成可用工具在寫，其中 `/curation`、`/triage`、`/plan-archive` 更把「用 TaskCreate 建 task」當作追蹤基準與完成率分母——在預設模型上這些全是死指令，skill 會在第一個追蹤步驟就落空。四個 SKILL.md 的 frontmatter 列了 Task 工具，實測證明那不構成 opt-in。來源：Claude Code CHANGELOG v2.1.233、[tools-reference](https://code.claude.com/docs/en/tools-reference#task-tool-availability)、[agent-sdk/todo-tracking](https://code.claude.com/docs/en/agent-sdk/todo-tracking#model-availability)、[env-vars](https://code.claude.com/docs/en/env-vars)，加本機 v2.1.246 三組對照實測。
