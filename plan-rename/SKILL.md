---
name: plan-rename
description: "Background hook: auto-rename Claude Code sessions from Plan Mode H1 title, with compaction resilience via sidecar + Stop guard"
user_invocable: false
---

# plan-rename — Plan Mode Session Auto-Rename Hook

Plan Mode 結束時自動從計畫的 H1 標題重新命名 session，並透過 sidecar 機制在 compaction 後自動修復名稱。

## How It Works

兩個 shell 腳本協作：

1. **`plan-rename-hook.sh`**（PreToolUse `ExitPlanMode`）：擷取計畫 H1 標題，寫入 JSONL `custom-title` 條目 + sidecar 備份檔
2. **`plan-rename-guard.sh`**（Stop hook）：每次 Claude 回應後檢查 — 若 sidecar 存在但 JSONL 缺少 `custom-title`（被 compaction 清除），自動重新注入

### 流程圖

```
Plan Mode exit
  → ExitPlanMode hook
    → 擷取 H1 標題（去除 "Plan:" 前綴，截斷 80 字元）
    → append custom-title 到 session JSONL
    → 寫入 sidecar 備份（~/.claude/session-titles/<session_id>.json）

每次 Claude 回應後
  → Stop guard
    → bash 快速檢查：有 sidecar 嗎？
      → 沒有 → exit 0（~30ms，不啟動 Python）
      → 有 → Python tail-scan JSONL 尾部 50 行
        → 有 custom-title → exit 0
        → 沒有 → 重新注入 custom-title
```

## Hook Registration

安裝後需**手動**在 `~/.claude/settings.json` 的 `hooks` 區塊加入以下兩條設定：

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "ExitPlanMode",
        "hooks": [
          {
            "type": "command",
            "command": "bash ~/.claude/skills/plan-rename/plan-rename-hook.sh"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash ~/.claude/skills/plan-rename/plan-rename-guard.sh"
          }
        ]
      }
    ]
  }
}
```

> **注意：** `npx skills add` 只安裝檔案，不會自動修改 `settings.json`。

## Hook stdin Schemas

**PreToolUse ExitPlanMode:**
```json
{
  "session_id": "uuid-string",
  "transcript_path": "/Users/.../.claude/projects/<slug>/<session_id>.jsonl",
  "hook_event_name": "PreToolUse",
  "tool_name": "ExitPlanMode",
  "tool_input": { "plan": "# Plan Title\n...", "allowedPrompts": [] }
}
```

**Stop hook:**
```json
{
  "session_id": "uuid-string",
  "transcript_path": "/Users/.../.claude/projects/<slug>/<session_id>.jsonl"
}
```

## JSONL custom-title Format

與 Claude Code `/rename` 指令使用相同格式：

```json
{"type":"custom-title","customTitle":"<title>","sessionId":"<session_id>"}
```

## 使用前須知

### 優點

- Plan Mode exit 自動命名 session — 不需手動 `/rename`
- Compaction 後自動修復 — sidecar 機制確保名稱不會丟失
- 不消耗 API token — 兩個 hook 都是本機 shell 指令

### 缺點 / 限制

- 需手動設定 `settings.json` hook（`npx skills add` 只裝檔案不改 settings）
- Stop hook 每次回應都執行（雖然 99% 快速退出）
- 依賴 `jq`

### 潛在問題

- **Sidecar 累積**：`~/.claude/session-titles/` 會持續累積，每個 ~100 bytes。建議定期清理：
  ```bash
  find ~/.claude/session-titles/ -mtime +30 -delete
  ```
- **Compaction 行為**：compaction 會重寫 JSONL 丟棄非 message 條目，這是觀察推測而非官方文件記載。Claude Code 更新可能改變行為
- **`/clear` 限制**：`/clear` 建新 session 時 title 不會 carry forward

### Token / IO 成本

| 項目 | 成本 |
|------|------|
| **API Token** | 零消耗 — 純本機 shell 指令 |
| **Stop hook (guard)** | 無 sidecar：bash+jq 快速退出 (~30ms)；有 sidecar：Python JSONL 尾部掃描 (~100-150ms) |
| **PreToolUse hook** | 僅 ExitPlanMode 時觸發，Python 執行一次 (~50-80ms) |
| **磁碟 IO** | guard 讀 sidecar (~100 bytes) + JSONL 尾部 50 行；hook 寫 JSONL 一行 + sidecar 一檔 (atomic temp+rename) |
| **總結** | 未用 plan rename 的 session 幾乎無感；有 sidecar 的 session 每次回應多 ~100-150ms |

### Script 穩定性風險

- JSONL `custom-title` 格式是透過觀察 `/rename` 行為逆向工程取得，非官方 API — Claude Code 更新可能改變格式
- Hook stdin JSON schema（`session_id`、`transcript_path` 等欄位）同樣無官方保證
- `ExitPlanMode` 只觸發 `PreToolUse`（不觸發 `PostToolUse`）是觀察結果
- **建議**：Claude Code 大版本更新後，執行整合測試確認 hook 仍正常運作

### 系統需求

- `python3`
- `jq`（macOS 內建或 `brew install jq`）
- `bash`

## Security

- Session ID 驗證：`[a-zA-Z0-9_-]{8,128}` regex
- Path containment：transcript_path 必須在 `~/.claude/` 內
- Control character 清理：title 去除 Unicode C 類別字元
- Sidecar 權限：目錄 700、檔案 600
- Atomic write：temp file + rename 防止 partial write

## Common Pitfalls

- **Compaction drops custom-title**：JSONL 由 Claude Code 管理，非 message 條目可能被 compaction 清除。必須用 sidecar 備份
- **Stop hook Python cold-start**：bash+jq pre-check 跳過 Python 是關鍵效能優化
- **PostToolUse 無法攔截 Plan Mode**：必須用 PreToolUse ExitPlanMode
- **欄位命名**：hook stdin 使用 `session_id`（snake_case），非 `sessionId`（camelCase）
- **路徑來源**：使用 stdin 提供的 `transcript_path`，不要手動拼接
