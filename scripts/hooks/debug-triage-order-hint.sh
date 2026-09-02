#!/usr/bin/env bash
# UserPromptSubmit hook：偵測「線上/可觀測環境回報的 bug」，注入 debug 分流順序。
#
# Why:
#   這份紀律原本是 rules/debug-triage-order.md，每個 session 全文預載（≈718 tokens），
#   但它只在「debug 一個線上回報的 bug」時適用——其餘 session 純粹付費。
#   2026-09-01 依 KB resident-context-paid-real-estate-route-on-demand 降級成觸發式。
#
#   規則本身仍留在 ${AGENT_SKILLS_DIR:-~/Documents/agent-skills}/rules/debug-triage-order.md（發布給他人用）。
#
# 精度設計：要同時命中「debug 訊號」與「可觀測環境訊號」才觸發。
#   只講「這段程式有 bug」不觸發（那是本地邏輯題，不適用 prod-first probe）。
input=$(cat)
printf '%s' "$input" | python3 -c '
import sys, json, re, os
try: data=json.load(sys.stdin)
except Exception: sys.exit(0)
p=data.get("prompt","") or ""
if p.lstrip().startswith("/"): sys.exit(0)

DEBUG = r"bug|壞掉|壞了|沒反應|不work|不 work|報錯|錯誤|異常|失敗|repro|重現|為什麼會|查一下為何|debug|排查|RCA|root cause"
OBSERVABLE = (r"線上|prod\b|production|staging|stg\b|hotfix"
              r"|使用者回報|用戶回報|客訴|回報說"
              r"|dev (正常|沒事|沒問題)|本地(正常|沒事|沒問題)"
              r"|sentry|grafana|loki|https?://")

if not (re.search(DEBUG, p, re.I) and re.search(OBSERVABLE, p, re.I)): sys.exit(0)

RULE_PATH = os.path.join(
    os.environ.get("AGENT_SKILLS_DIR") or os.path.expanduser("~/Documents/agent-skills"),
    "rules", "debug-triage-order.md")

hint=("[debug-triage hint] 這像是「可觀測環境回報的 bug」。依 debug-triage-order 三條順序："
 "(1) **Prod-first read-only probe**——建任何本地重現環境之前，先對回報環境做一次唯讀探測"
 "（headless 或 curl 撈 console error、關鍵 DOM、network；不登入、不寫入、導覽 ≤10 頁）。"
 "一次探測就能分流「環境差異 bug」（dev 天生重現不了，窮舉導覽條件是浪費）vs「邏輯 bug」。"
 "(2) **Evidence-first before dispatch**——派 agent 前先盤點手上證據；1-2 個指令"
 "（git log --grep、單一 grep、讀一個檔）能定案就 inline 跑掉，不派整輪重查已有答案的問題。"
 "(3) **Verify-via-spec, once**——驗收＝一個 fresh context 實跑既有 spec（UI 用 headed），"
 "不要用散文重新推導手動步驟給另一個 agent；上限是實作者自測 + 一輪 fresh 驗收。"
 "全文：" + RULE_PATH)
print(json.dumps({"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":hint}}, ensure_ascii=False))
'
exit 0
