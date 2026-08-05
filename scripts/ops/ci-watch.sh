#!/usr/bin/env bash
# ci-watch.sh — 輪詢 PR / branch 的 CI 直到收斂，紅的時候只摘關鍵 log（唯讀）
#
# 用法：
#   ci-watch.sh 7970                      # 依 PR 編號輪詢
#   ci-watch.sh --branch hotfix           # 依 branch 最新一次 run 輪詢
#   ci-watch.sh 7970 --timeout 2400       # 自訂上限秒數（預設 2400 = 40 分鐘）
#   ci-watch.sh 7970 --interval 30        # 自訂輪詢間隔（預設 30 秒）
#   ci-watch.sh 7970 --repo owner/name
#
# 全程唯讀：不會 rerun、不會 merge、不改任何狀態。紅了只告訴你怎麼判、要不要 rerun 由人決定。
#
# 退出碼：0 全綠 / 1 參數錯 / 2 前置條件不足 / 3 有 check 紅 / 4 逾時未收斂
# （包在其他腳本裡時注意：`gh pr checks` 自己在「尚未收斂」時 exit 8，那不是失敗，
#   是還沒跑完 —— 本腳本已經把這個語意吃掉，只用上面四個退出碼。
#   來源：KB wiki/learned/gha-pr-check-fail-is-spurious-cancel-not-code.md）
#
# 預設 timeout 為什麼是 40 分鐘：vocus-web-ui 用 ARC（Actions Runner Controller）自架 runner
# scale set，尖峰時段光排隊就可能吃掉 20 分鐘。用 GH-hosted 的 20 分鐘上限會拿一個「timeout」
# 去當結論回報。來源：同上（ARC 排隊變體）。

set -euo pipefail

PR=""; BRANCH=""; REPO=""; TIMEOUT=2400; INTERVAL=30

while [ $# -gt 0 ]; do
  case "$1" in
    --branch)   BRANCH="${2:-}"; [ -n "$BRANCH" ] || { echo "--branch 需要分支名" >&2; exit 1; }; shift ;;
    --repo)     REPO="${2:-}";   [ -n "$REPO" ]   || { echo "--repo 需要 owner/name" >&2; exit 1; }; shift ;;
    --timeout)  TIMEOUT="${2:-}"; shift ;;
    --interval) INTERVAL="${2:-}"; shift ;;
    -h|--help) sed -n '2,/^set -euo/p' "$0" | sed 's/^#\{1,\} \{0,1\}//'; exit 0 ;;
    [0-9]*) PR="$1" ;;
    *) echo "未知參數：$1" >&2; exit 1 ;;
  esac
  shift
done

command -v gh >/dev/null || { echo "需要 gh CLI" >&2; exit 2; }
if [ -z "$REPO" ]; then
  REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null) || REPO=""
fi
[ -n "$REPO" ] || { echo "解析不出 repo，請用 --repo owner/name" >&2; exit 2; }

if [ -z "$PR" ] && [ -z "$BRANCH" ]; then
  BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || BRANCH=""
  [ -n "$BRANCH" ] || { echo "請給 PR 編號或 --branch" >&2; exit 1; }
  echo "未指定目標，改用當前分支：${BRANCH}"
fi

TMP=$(mktemp -d "${TMPDIR:-/tmp}/ciwatch.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

# 取一次 check 狀態。輸出每行：<state>\t<name>\t<link>
# 用 --json 而非解析表格輸出：表格的欄數會隨 check 有無 description 變動，$NF 取到的
# 不一定是 link（實測 CodeRabbit 那類外部 check 沒有 link，欄位會錯位）。
fetch_checks() {
  if [ -n "$PR" ]; then
    gh pr checks "$PR" --repo "$REPO" --json bucket,name,link \
      --jq '.[] | "\(.bucket)\t\(.name)\t\(.link)"' 2>"$TMP/err"
  else
    gh run list --repo "$REPO" --branch "$BRANCH" --limit 15 \
      --json name,status,conclusion,databaseId \
      --jq '.[] | "\(if (.conclusion // "") == "" then .status else .conclusion end)\t\(.name)\t\(.databaseId)"'
  fi
}

deadline=$(( $(date +%s) + TIMEOUT ))
echo "監看 ${REPO} ${PR:+PR #$PR}${BRANCH:+branch $BRANCH}（上限 ${TIMEOUT} 秒，每 ${INTERVAL} 秒一次）"

while :; do
  set +e
  fetch_checks > "$TMP/checks.tsv" 2>/dev/null
  rc=$?
  set -e
  # gh pr checks 在「還有 pending」時 exit 8；rc 非 0 但有輸出不代表壞掉。
  if [ ! -s "$TMP/checks.tsv" ]; then
    if [ "$rc" -ne 0 ] && [ -s "$TMP/err" ]; then
      echo "查詢失敗（rc=${rc}）：$(head -1 "$TMP/err")" >&2
    else
      echo "尚無任何 check（workflow 可能還沒被觸發）"
    fi
  else
    pending=$(awk -F'\t' '$1=="pending"||$1=="queued"||$1=="in_progress"||$1=="waiting"{c++} END{print c+0}' "$TMP/checks.tsv")
    failed=$(awk -F'\t' '$1=="fail"||$1=="failure"||$1=="timed_out"{c++} END{print c+0}' "$TMP/checks.tsv")
    total=$(wc -l < "$TMP/checks.tsv" | tr -d ' ')
    now=$(date +%H:%M:%S)
    echo "[${now}] 共 ${total} 項：pending=${pending} failed=${failed}"

    # 還有 pending 就不下任何結論。混合畫面裡的 fail 可能屬於前一顆 commit 的 run，
    # 那是歷史殘影不是對這次 push 的判決。來源：同檔頭 KB。
    if [ "$pending" -eq 0 ]; then
      echo
      awk -F'\t' '{printf "  %-10s %s\n", $1, $2}' "$TMP/checks.tsv"
      if [ "$failed" -eq 0 ]; then
        echo
        echo "全綠。"
        echo '提醒：skipping 的 job 不算被驗過；CI 綠 != 可以宣稱完成（要有可觀察的驗收證據）。'
        exit 0
      fi
      break
    fi
  fi

  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo
    echo "逾時 ${TIMEOUT} 秒仍未收斂。這通常不是失敗 —— ARC 自架 runner 排隊即可能吃掉數十分鐘。" >&2
    echo "判別：run 是 status=queued 而非 in_progress、job 沒有 startedAt ＝ 還在等 runner，rerun 只會再排一次隊。" >&2
    exit 4
  fi
  sleep "$INTERVAL"
done

# ── 有紅的：只摘關鍵訊號，不整包倒 log ────────────────────────────────────────────
echo
echo "════ 失敗的 check ════"
awk -F'\t' '$1=="fail"||$1=="failure"||$1=="timed_out" {print}' "$TMP/checks.tsv" \
  > "$TMP/failed.tsv"

while IFS=$'\t' read -r state name link; do
  echo
  echo "── ${name} (${state})"
  # 從連結尾巴撈 run id；--branch 模式下第三欄本身就是 databaseId。
  run_id=$(printf '%s' "$link" | sed -n 's|.*/runs/\([0-9]*\).*|\1|p')
  [ -n "$run_id" ] || run_id=$(printf '%s' "$link" | tr -cd '0-9')
  [ -n "$run_id" ] || { echo "  取不到 run id，連結：${link}"; continue; }

  # 判斷 1：這一列是不是屬於舊 commit 的殘影
  meta=$(gh run view "$run_id" --repo "$REPO" --json headSha,event,status,conclusion,createdAt \
         --jq '[.headSha[0:9], .event, .status, (.conclusion // "-"), .createdAt] | @tsv' 2>/dev/null || echo "")
  if [ -n "$meta" ]; then
    echo "  run ${run_id}: ${meta}"
    head_local=$(git rev-parse HEAD 2>/dev/null | cut -c1-9) || head_local=""
    run_sha=$(printf '%s' "$meta" | cut -f1)
    if [ -n "$head_local" ] && [ -n "$run_sha" ] && [ "$run_sha" != "$head_local" ]; then
      echo "  ! 這個 run 的 headSha (${run_sha}) 不等於本地 HEAD (${head_local}) —— 可能是舊 commit 的殘影，先忽略。"
    fi
  fi

  # 判斷 2：失敗在哪個 step。用結構化查詢，不要 --log-failed 撈幾百行 debug。
  # 全部 job 都掛在 Checkout 且耗時都極短 ＝ refs/pull/N/merge 被重算的 stale SHA，不是程式壞了。
  # 來源：KB wiki/learned/pr-ci-all-red-at-checkout-is-stale-merge-ref.md
  echo "  失敗的 step："
  gh run view "$run_id" --repo "$REPO" --json jobs \
     --jq '.jobs[] | select(.conclusion=="failure" or .conclusion=="cancelled") |
           "    \(.name) [\(.conclusion)] steps: " +
           ([.steps[] | select(.conclusion=="failure" or .conclusion=="cancelled") | .name] | join(", "))' \
    2>/dev/null || echo "    （查不到 job 明細）"

  # 判斷 3：真錯誤 vs 併發取消 / runner 被回收。只抓指紋行，不倒全文。
  # pattern 裡的 ✕ 是 jest 標記單一失敗測試用的字元（非裝飾），拿掉會漏掉逐項失敗名稱。
  echo "  log 指紋（最多 12 行）："
  set +e
  gh run view "$run_id" --repo "$REPO" --log 2>/dev/null \
    | grep -E 'The operation was canceled|received a shutdown signal|exit code 130|error TS[0-9]+|✕ |^.*FAIL |Process completed with exit code' \
    | sed 's/^/    /' | sort -u | head -12
  set -e
done < "$TMP/failed.tsv"

cat <<'EOS'

════ 怎麼判 ════
  真失敗          log 有 error TS.../FAIL/斷言細節，且 sibling job 沒被同時砍
  併發取消（假紅） log 尾巴是 "The operation was canceled."、多個無關 job 同一時間戳被砍、
                  或 "Process completed with exit code 130"（130=128+2=SIGINT）
  runner 被回收    log 有 "The runner has received a shutdown signal"；同 sha 的另一個 event
                  （push vs pull_request）若是綠的，即證明與程式碼無關
  merge ref 過期   全部 job 都失敗、耗時都 ≤15 秒、失敗 step 都是 Checkout

假紅的處置是 `gh run rerun <run-id> --failed`，不要回頭找程式碼問題。
本腳本刻意不自動 rerun —— 判定是假紅這件事需要人看過再決定。
EOS
exit 3
