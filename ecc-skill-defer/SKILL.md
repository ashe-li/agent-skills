---
name: ecc-skill-defer
description: Manage ECC skill loading — defer unused skills to save init tokens, restore on demand. Use when user wants to check, defer, or restore ECC skills.
user_invocable: true
---

# /ecc-skill-defer — ECC Skill 漸進式載入管理

管理 ECC skills 的 defer/restore 狀態，減少 init token 消耗。

## Instructions

Run the script at this skill's directory: `ecc-skill-defer/ecc-skill-defer.sh` (relative to the skills repo root), or the installed copy at `~/.claude/scripts/ecc-skill-defer.sh`.

### Available commands

| Command | Description |
|---------|-------------|
| `status` | Show active/deferred counts |
| `list` | Show all skills with their status |
| `apply` | Defer skills listed in config (use after ECC update) |
| `restore <name>` | Activate a single deferred skill |
| `restore --all` | Restore all deferred skills |
| `apply --reason "<text>"` | Defer with explicit reason logged to DEFER_LOG.md |

### Default behavior

If no argument is provided by the user, run `status` then `list`.

### Config

The defer list is at `ecc-skill-defer.conf` (same directory as the script). To add/remove skills from the defer list, edit this file then run `apply`.

### After restoring a skill

Tell the user: "Skill restored. It will be available in the **next session** (or after context reload)."

### Notes

- **defer 原因追蹤：** `apply --reason` 會將 defer 原因記錄在 `DEFER_LOG.md`（append-only），格式為 `<date> | <skill> | <reason>`。`list` 指令可選顯示最後一次 defer 原因。
- **Portfolio 健康確認：** defer/restore 操作後，建議執行 `/skill-health` 檢查當前 skill 組合是否有冗餘或缺口。

### 核心 skill 保護（HITL Guard）

**在執行 `apply` 前，必須先掃描即將 defer 的 skill 清單（`ecc-skill-defer.conf`）：**

若清單包含 `design` 或 `assist`，使用 AskUserQuestion 強制確認（此為 **Hard Stop**，不得略過）：

> 即將 defer 的清單包含核心規劃 skill：
> - [列出偵測到的核心 skill]
>
> design / assist 為核心規劃 skill，defer 後將失去業界/學術參照能力與 ECC 資源分配品質。
>
> 1. **確認 defer** — 我了解影響，仍要繼續
> 2. **取消** — 保留這些 skill，不執行 apply

只有使用者明確選擇「確認 defer」後才繼續執行 apply。

### apply 操作驗證（依據：DAMA-DMBOK Completeness — manifest vs. actual）

執行 `apply` 後，必須透過 `list` 重新驗證狀態：

1. 執行 apply 前，記錄預計 defer 的 skill 清單（manifest）
2. apply 完成後，執行 `list` 取得當前狀態
3. 對照 manifest 逐條驗證（依據：OpenAI Developer Community 共識 — Checklist-driven）：

   | Skill | 預期狀態 | 實際狀態 | 結果 |
   |-------|----------|----------|------|
   | foo   | deferred | deferred | PASS |
   | bar   | deferred | active   | FAIL |

4. 若有任何 FAIL → 輸出警告，提示手動檢查 `ecc-skill-defer.conf` 和 script 執行結果
5. 全部 PASS → 輸出當前可用 skill 清單，確保使用者了解現有資源

### restore 操作驗證（依據：DAMA-DMBOK Completeness）

執行 `restore <name>` 或 `restore --all` 後：

1. 執行 `list` 驗證目標 skill 狀態為 `active`
2. 輸出驗證結果：`[PASS] <skill> 已恢復為 active` 或 `[FAIL] <skill> 狀態仍為 deferred`
3. 告知使用者：「Skill restored. It will be available in the **next session** (or after context reload).」
