# plan-rename

PostToolUse hook — 當 Claude 在 Plan Mode 建立計畫檔時，自動從 H1 標題擷取名稱並重命名 session。

## How it works

1. Hook 攔截 `Write` tool 呼叫
2. 篩選：只處理 `~/.claude/plans/*.md`
3. 擷取第一個 H1 標題（`# ...`）
4. 去除 `Plan:` / `Implementation Plan:` 等前綴
5. 直接 append `{"type":"custom-title"}` 到 session transcript JSONL
6. Session 名稱立即更新（與 `/rename` 相同機制）

## Install

```bash
# 複製 hook script
mkdir -p ~/.claude/scripts
cp plan-rename-hook.sh ~/.claude/scripts/
chmod +x ~/.claude/scripts/plan-rename-hook.sh
```

在 `~/.claude/settings.json` 的 `hooks.PostToolUse` 陣列中，在 `*` matcher **之前**新增：

```json
{
  "matcher": "Write",
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
2. 寫一個含 H1 標題的 plan
3. stderr 顯示 `[plan-rename] Session renamed to: <title>`
4. `claude -r` 列出 sessions，確認名稱已更新

## Edge cases

- 無 H1 標題：靜默跳過
- 非 plan 檔的 Write：靜默跳過
- 超長標題：截斷至 77 字元並附加 `...`（總長 80）
- 多次更新 plan：每次都會更新 session 名稱（最後一次生效）
- 手動重命名後再編輯 plan：自動重命名會覆蓋手動重命名，基於最新 H1 標題優先
- CWD 含空格：project slug 仍可運作（路徑不含空格的假設由 Claude 保證）
- python3 失敗：`|| true` 確保不影響 hook chain

## Troubleshooting

若沒看到 stderr 訊息，檢查：
- `~/.claude/plans/` 是否存在
- plan 檔案是否含 `# ` 開頭的標題
- `claude -r` 確認 session 存在（sessionId 是否有效）
