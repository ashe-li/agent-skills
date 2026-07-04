# Plan 自動儲存 Hook 設定

若希望每次 `ExitPlanMode` 後**自動**將 plan 存至 `plans/active/`，
可設定 PostToolUse hook（搭配 `/plan-archive` 使用，一次性安裝，非每次執行都需要）。

## 1. 建立 hook script

`~/.claude/hooks/save-plan-on-exit.sh`：

```bash
#!/bin/bash
# PostToolUse hook for ExitPlanMode
# Reads JSON from stdin, copies plan file to project's plans/active/

INPUT=$(cat)

# Extract plan_file path from tool response
PLAN_FILE=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    # ExitPlanMode tool_response may contain the plan file path
    resp = d.get('tool_response', d)
    print(resp.get('plan_file', resp.get('planFile', '')))
except Exception:
    print('')
" 2>/dev/null)

if [ -z "$PLAN_FILE" ] || [ ! -f "$PLAN_FILE" ]; then
  exit 0
fi

# Only act if we're inside a git repo with a plans/ directory pattern
if [ ! -f "$(pwd)/.git/HEAD" ] && [ ! -d "$(pwd)/.git" ]; then
  exit 0
fi

DEST_DIR="$(pwd)/plans/active"
mkdir -p "$DEST_DIR"

SLUG=$(basename "$PLAN_FILE")
cp "$PLAN_FILE" "$DEST_DIR/$SLUG"
echo "[plan-archive] Plan saved to plans/active/$SLUG" >&2
```

```bash
chmod +x ~/.claude/hooks/save-plan-on-exit.sh
```

## 2. 設定 `~/.claude/settings.json`

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "ExitPlanMode",
        "hooks": [
          {
            "type": "command",
            "command": "bash ~/.claude/hooks/save-plan-on-exit.sh"
          }
        ]
      }
    ]
  }
}
```

## 3. Rule（搭配使用）

在專案 `CLAUDE.md` 加入，確保 Claude 在實作完成後主動呼叫 `/plan-archive`：

```markdown
### Commit 前知識沉澱清單

5. **歸檔 plan** — 若本次工作有對應的 plan，實作完成後執行 `/plan-archive`
   將 `plans/active/<name>.md` 移至 `plans/completed/`，補上驗證結果
```
