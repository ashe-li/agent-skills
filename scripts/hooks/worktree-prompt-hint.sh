#!/usr/bin/env bash
# UserPromptSubmit hook：偵測「要實作 plan / 大規模變更」，提醒先問要不要開 worktree。
#
# Why:
#   原 rules/worktree-prompt.md 每個 session 預載（≈404 tokens），但只在開工那一刻適用。
#   2026-09-01 降級成觸發式；規則本身仍在 agent-skills repo 發布。
#
# 不觸發：已在 worktree 內（.git 是檔案不是目錄）、小改動、使用者已表態。
input=$(cat)
printf '%s' "$input" | python3 -c '
import sys, json, re, os
try: data=json.load(sys.stdin)
except Exception: sys.exit(0)
p=data.get("prompt","") or ""
if p.lstrip().startswith("/"): sys.exit(0)

# 已在 worktree 裡就不用問
cwd=data.get("cwd") or os.getcwd()
if os.path.isfile(os.path.join(cwd,".git")): sys.exit(0)

TRIGGER=(r"實作(這個|一下)?\s*plan|依 plan|照 plan|plans/active"
         r"|大規模(改動|重構|遷移)|migration|遷移|重構.{0,6}(整個|全部|跨)"
         r"|跨 ?\d{1,2}\+? ?個檔|infra(structure)? (改動|變更)"
         r"|開始實作|動手實作")
if not re.search(TRIGGER, p, re.I): sys.exit(0)

hint=("[worktree hint] 這像是「實作 plan / 大規模變更」。開工前先用 AskUserQuestion 問要不要開 worktree 隔離："
 "1) 開——`git worktree add ~/Documents/<project>-<slug> -b <branch>`（sibling 佈局，"
 "不要用 .claude/worktrees/ 這個預設路徑）；2) 不開，就在當前目錄做。"
 "已表態過、已在 worktree 內、或只是單檔小修就別問。"
 "全文：${AGENT_SKILLS_DIR:-~/Documents/agent-skills}/rules/worktree-prompt.md")
print(json.dumps({"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":hint}}, ensure_ascii=False))
'
exit 0
