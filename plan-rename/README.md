# plan-rename

PreToolUse hook — 當 Claude 退出 Plan Mode 時，自動從 H1 標題擷取名稱並重命名 session。

## How it works

1. Hook 攔截 `ExitPlanMode` tool 呼叫（PreToolUse）
2. 從 `tool_input.plan` 擷取第一個 H1 標題（`# ...`）
3. 去除 `Plan:` / `Implementation Plan:` 等前綴
4. 直接 append `{"type":"custom-title"}` 到 session transcript JSONL
5. Session 名稱立即更新（與 `/rename` 相同機制）

> **為什麼是 PreToolUse？** Plan Mode 使用 `ExitPlanMode` 存檔，不是 `Write`。而且 `ExitPlanMode` 只觸發 PreToolUse hook（不觸發 PostToolUse），所以必須掛在 PreToolUse。

## Prerequisites

- **python3** — hook 內部使用 Python 解析 JSON 和擷取標題
- **claude-hud**（推薦）— 完整解析 JSONL，session 名稱永遠可靠。`claude -r` 只 tail 64KB，長 session 下 custom-title 可能被截斷而不顯示

## Install

```bash
# 複製 hook script
mkdir -p ~/.claude/scripts
cp plan-rename-hook.sh ~/.claude/scripts/
chmod +x ~/.claude/scripts/plan-rename-hook.sh
```

在 `~/.claude/settings.json` 的 `hooks.PreToolUse` 陣列中，在 `*` matcher **之前**新增：

```json
{
  "matcher": "ExitPlanMode",
  "hooks": [
    {
      "type": "command",
      "command": "bash ~/.claude/scripts/plan-rename-hook.sh"
    }
  ]
}
```

## Verification

1. 開新 session，進入 Plan Mode（`Shift+Tab` 或 `/design`）
2. 寫一個含 H1 標題的 plan，退出 Plan Mode
3. stderr 顯示 `[plan-rename] Session renamed to: <title>`
4. claude-hud statusline 或 `claude -r` 確認名稱已更新

## Edge cases

- 無 H1 標題：靜默跳過
- 超長標題：截斷至 77 字元並附加 `...`（總長 80）
- 多次退出 Plan Mode：每次都會更新 session 名稱（最後一次生效）
- 手動 `/rename` 後再退出 Plan Mode：自動重命名會覆蓋手動重命名
- python3 失敗：`|| true` 確保不影響 hook chain

## Known limitations

- `claude -r` 只 tail 64KB，長 session 的 custom-title entry 可能不在 tail 範圍內，導致名稱不顯示。使用 claude-hud 可避免此問題
