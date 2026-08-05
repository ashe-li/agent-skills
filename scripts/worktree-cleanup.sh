#!/usr/bin/env bash
# worktree-cleanup.sh — 跨 repo git worktree 盤點與清理（預設 dry-run）
#
# 把反覆手刻的 worktree 清理指令鏈固化下來。每個判斷條件都對應一次實際踩過的坑，
# 來源標在該段註解（KB ~/Documents/knowledge-base/wiki/learned/<檔名>.md）。
#
# 用法：
#   worktree-cleanup.sh                                  # 唯讀盤點（預設）
#   worktree-cleanup.sh --du                             # 加算磁碟用量（慢，node_modules 很大）
#   worktree-cleanup.sh --fetch                          # 先 fetch origin 再判斷（--apply 必要）
#   worktree-cleanup.sh --fetch --apply                  # 實際移除判定為 remove 的 worktree
#   worktree-cleanup.sh --fetch --apply --kill-blockers  # 連佔用行程一起 kill
#   worktree-cleanup.sh --root <dir>                     # 覆寫掃描根目錄（預設 ~/Documents）
#
# 硬規則（違反過會出事，不要改）：
#   1. 只移除 worktree 目錄，永遠不刪 branch。2026-08-04 全機 sweep 誤清了一個含未推送
#      commit 的 worktree，救回來全靠 branch ref 還在（`git worktree remove` 不刪 branch）。
#      `git branch -D` 等於把唯一的救命索砍斷。這條同時涵蓋 long-lived branch
#      （develop / dev-vN / release-*）本來就不該刪的情況。
#      來源：worktree-cleanup-verify-merged-by-revlist-not-pr-query.md
#            worktree-cleanup-skip-long-lived-branch-delete.md
#   2.「PR MERGED + 工作區乾淨」不足以判定可刪。**已 commit 但未 push** 的變更在這兩關都
#      長得像乾淨（status --porcelain 是空的），必須加驗 rev-list。來源：同 1。
#   3. rev-list > 0 不等於有遺失。squash / rebase merge 的 repo 上每個正常 merge 的 PR 都會
#      >0（SHA 被改寫）。決定性判準是 `gh pr view <n> --json headRefOid` 是否等於 worktree
#      HEAD：相同＝被 merge 的正是這條 branch 的 tip，無遺失。來源：同 1。
#   4. 整排回同一個 fallback 值（全部 unknown）＝管線壞了，不是資料真相。偵測到就中止，
#      不把壞掉的清單交給人確認。來源：uniform-result-value-means-broken-pipeline-not-data.md
#   5. 刪除前逐一列出具體路徑，不用萬用字元（使用者 rules：刪除用具體路徑、禁萬用字元）。
#
# 退出碼：0 正常 / 1 參數錯 / 2 前置條件不足（gh 未安裝等）/ 3 偵測到管線故障
# 相容性：macOS 內建 bash 3.2 —— 不可用 associative array、mapfile、BSD sed 不支援的語法。

set -euo pipefail

ROOT="${HOME}/Documents"
APPLY=0; DO_FETCH=0; DO_DU=0; KILL_BLOCKERS=0

# 算 rev-list 要列舉所有可能的 base：base 不是 master/main 的 repo 存在（vocus-trends 用 dev-v2）。
# 漏列會把已 merge 的誤判成未 merge（保守方向，可接受），但只列一條就會整個判不準。
# 來源：worktree-cleanup-base-branch-detection-fallback.md
BASE_CANDIDATES="master main hotfix develop develop-v2 develop-v3 develop-v4 dev-v2 dev"

while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --fetch) DO_FETCH=1 ;;
    --du) DO_DU=1 ;;
    --kill-blockers) KILL_BLOCKERS=1 ;;
    --root)
      [ -n "${2:-}" ] || { echo "--root 需要一個目錄路徑" >&2; exit 1; }
      ROOT="$2"; shift ;;
    -h|--help) sed -n '2,/^set -euo/p' "$0" | sed 's/^#\{1,\} \{0,1\}//'; exit 0 ;;
    *) echo "未知參數：$1" >&2; exit 1 ;;
  esac
  shift
done

command -v git >/dev/null || { echo "需要 git" >&2; exit 2; }
command -v gh  >/dev/null || { echo "需要 gh CLI" >&2; exit 2; }

# --apply 沒 fetch 就是拿 stale ref 判斷「已合併 / 無未推送」，等於在錯的資料上做不可逆動作。
if [ "$APPLY" -eq 1 ] && [ "$DO_FETCH" -eq 0 ]; then
  echo "拒絕執行：--apply 必須搭配 --fetch，否則是用 stale 的 origin ref 做不可逆判斷。" >&2
  exit 1
fi

TMP=$(mktemp -d "${TMPDIR:-/tmp}/wtcleanup.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

# ── 一次掃出所有行程的 cwd，供後面比對誰佔住 worktree ────────────────────────────
# 為什麼不逐個 worktree 跑 `lsof +D <path>`：目錄幾乎空的時候它回報為空，看起來沒人佔用。
# 為什麼不用 `pkill -f`：dev server 的 port 是 env var、cmdline 裡沒有 worktree 路徑，pattern
# 要嘛 under-match（殺不到）要嘛 over-match（殺到別 session 的 server）。cwd 才是完整判別依據。
# 來源：worktree-remove-blocked-by-longlived-dev-server-cwd.md
#       background-dev-server-kill-by-port-not-pkill.md
scan_processes() {
  lsof -d cwd -Fpn 2>/dev/null | awk '
    /^p/ { pid = substr($0, 2); next }
    /^n/ { if (pid != "") { print pid "\t" substr($0, 2); pid = "" } }
  ' > "$TMP/cwd.tsv" || true
  [ -s "$TMP/cwd.tsv" ] || echo "警告：lsof 掃不到任何 cwd，佔用行程偵測失效（結果會低報）" >&2
}

# 印出 cwd 落在 $1 底下的行程：PID / 存活時間 / 指令。
# 判準：etime 以「天」計的 dev server 一律可疑 —— 沒人會讓 dev server 開 6 天。
blockers_for() {
  local wt="$1" pid cwd
  while IFS=$'\t' read -r pid cwd; do
    case "$cwd" in
      "$wt"|"$wt"/*) ps -o pid=,etime=,command= -p "$pid" 2>/dev/null | sed 's/^ *//' || true ;;
    esac
  done < "$TMP/cwd.tsv"
}

# ── 每個 repo 只跑一次 gh pr list，之後全部本地 join ──────────────────────────────
# 逐 branch 跑 `gh pr list --head` 慢，而且在 `... | while` 子 shell 裡 PATH 容易壞掉，
# 失敗會靜默回空字串，與「真的沒有 PR」長得一模一樣。
# 來源：bulk-worktree-audit-single-pr-dump-local-match.md
dump_prs() {
  local parent="$1" slug dumpfile rc url nwo
  slug=$(printf '%s' "$parent" | tr '/' '_')
  dumpfile="$TMP/pr_$slug.tsv"
  [ -f "$dumpfile" ] && { printf '%s' "$dumpfile"; return 0; }

  url=$(git -C "$parent" remote get-url origin 2>/dev/null) || url=""
  # 用 python3 解析 remote URL：BSD sed 不支援 `+?` 惰性量詞，用 sed 寫這段會整排噴
  # "repetition-operator operand invalid" 然後靜默回空。來源：同硬規則 4。
  nwo=""
  if [ -n "$url" ]; then
    nwo=$(printf '%s' "$url" | python3 -c \
      'import re,sys;m=re.search(r"github\.com[:/](.+?)(?:\.git)?$",sys.stdin.read().strip());print(m.group(1) if m else "")')
  fi
  if [ -z "$nwo" ]; then
    # 本機 only 的 repo（如 knowledge-base）沒有 PR 可查，不算查詢失敗。
    : > "$dumpfile"; echo "NO_REMOTE" > "$dumpfile.status"
    printf '%s' "$dumpfile"; return 0
  fi

  set +e
  gh pr list --repo "$nwo" --state all --limit 2000 \
     --json number,state,headRefName,headRefOid \
     --jq '.[] | [.headRefName, .number, .state, .headRefOid] | @tsv' > "$dumpfile" 2>"$dumpfile.err"
  rc=$?
  set -e
  # rc != 0（查詢失敗）與 rc == 0 但輸出為空（真的沒 PR）必須分開，否則故障會偽裝成資料。
  if [ $rc -ne 0 ]; then
    echo "QUERY_FAILED" > "$dumpfile.status"; : > "$dumpfile"
  else
    echo "OK" > "$dumpfile.status"
  fi
  printf '%s' "$dumpfile"
}

# ── 盤點 ──────────────────────────────────────────────────────────────────────────
scan_processes

repos=""
while IFS= read -r gitfile; do
  wt=$(dirname "$gitfile")
  parent=$(git -C "$wt" rev-parse --git-common-dir 2>/dev/null | sed 's|/\.git$||') || parent=""
  [ -n "$parent" ] || continue
  [ "$parent" = "$wt" ] && continue           # 主 worktree 不列入候選
  case " $repos " in *" $parent "*) ;; *) repos="$repos $parent" ;; esac
done < <(find "$ROOT" -maxdepth 2 -name ".git" -type f 2>/dev/null)

if [ "$DO_FETCH" -eq 1 ]; then
  for parent in $repos; do
    echo "fetch: $parent"
    git -C "$parent" fetch --quiet --prune origin || echo "  ! fetch 失敗（後續判斷會用 stale ref）" >&2
  done
fi

printf '\n%-13s %-9s %-56s %-34s %-6s %s\n' 動作 PR狀態 路徑 分支 未推送 標記
printf '%s\n' "$(printf -- '-%.0s' $(seq 1 150))"

removable_list="$TMP/removable.txt"; : > "$removable_list"
total=0; unknown=0; n_remove=0; n_keep=0

while IFS= read -r gitfile; do
  wt=$(dirname "$gitfile")
  branch=$(git -C "$wt" symbolic-ref --short HEAD 2>/dev/null) || branch=""
  parent=$(git -C "$wt" rev-parse --git-common-dir 2>/dev/null | sed 's|/\.git$||') || parent=""
  [ -n "$branch" ] && [ -n "$parent" ] && [ "$parent" != "$wt" ] || continue
  total=$((total + 1))

  head_sha=$(git -C "$wt" rev-parse HEAD 2>/dev/null) || head_sha=""
  last_commit=$(git -C "$wt" log -1 --format=%cs 2>/dev/null) || last_commit="?"
  dirty=""; [ -n "$(git -C "$wt" status --porcelain 2>/dev/null)" ] && dirty="dirty"

  # 檢查 1：PR 狀態（本地 join）
  dumpfile=$(dump_prs "$parent")
  dstatus=$(cat "$dumpfile.status" 2>/dev/null || echo UNKNOWN)
  state="no-pr"; pr_num="-"; pr_oid=""
  if [ "$dstatus" = "OK" ]; then
    line=$(awk -F'\t' -v b="$branch" '$1==b {print; exit}' "$dumpfile" || true)
    if [ -n "$line" ]; then
      pr_num=$(printf '%s' "$line" | cut -f2)
      state=$(printf '%s' "$line" | cut -f3)
      pr_oid=$(printf '%s' "$line" | cut -f4)
    fi
  elif [ "$dstatus" = "NO_REMOTE" ]; then
    state="local"
  else
    state="unknown"; unknown=$((unknown + 1))
  fi

  # 檢查 2（權威）：有沒有 commit 不在任何一條 base 上
  bases=""
  for b in $BASE_CANDIDATES; do
    git -C "$wt" rev-parse --verify --quiet "origin/$b" >/dev/null 2>&1 && bases="$bases origin/$b"
  done
  unpushed="?"
  if [ -n "$bases" ] && [ -n "$head_sha" ]; then
    set +e
    unpushed=$(git -C "$wt" rev-list --count "$head_sha" --not $bases 2>/dev/null)
    [ $? -ne 0 ] && unpushed="?"
    set -e
    [ -z "$unpushed" ] && unpushed="?"
  fi
  # squash / rebase 假陽性排除：PR 的 headRefOid == worktree HEAD 代表被 merge 的正是這條
  # branch 的 tip，rev-list >0 純粹是 SHA 改寫。不比這個會讓每個正常 merge 都判成不可刪。
  squash_fp=""
  if [ "$unpushed" != "0" ] && [ "$unpushed" != "?" ] && [ -n "$pr_oid" ] && [ "$pr_oid" = "$head_sha" ]; then
    squash_fp="squash-fp"; unpushed="0*"
  fi

  # 檢查 3：有沒有行程以它為 cwd
  blockers=$(blockers_for "$wt")
  blocked=""; [ -n "$blockers" ] && blocked="blocked"

  size="-"
  [ "$DO_DU" -eq 1 ] && size=$(du -sh "$wt" 2>/dev/null | cut -f1 || echo "?")

  # 三關全過才算可移除
  action="keep"
  case "$state" in MERGED|CLOSED) action="remove" ;; esac
  [ "$action" = "remove" ] && [ -n "$dirty" ] && action="skip-dirty"
  [ "$action" = "remove" ] && [ "$unpushed" != "0" ] && [ "$unpushed" != "0*" ] && action="skip-unpushed"
  [ "$action" = "remove" ] && [ -n "$blocked" ] && [ "$KILL_BLOCKERS" -eq 0 ] && action="skip-blocked"

  flags=$(printf '%s %s %s pr=%s last=%s size=%s' \
          "$dirty" "$blocked" "$squash_fp" "$pr_num" "$last_commit" "$size" | tr -s ' ')
  printf '%-13s %-9s %-56s %-34s %-6s %s\n' "$action" "$state" "$wt" "$branch" "$unpushed" "$flags"

  if [ "$action" = "skip-unpushed" ] && [ "$unpushed" != "?" ]; then
    echo "    不可刪的理由 —— 以下 commit 不在任何 base 上（最多列 5 筆）："
    git -C "$wt" log --oneline -n 5 "$head_sha" --not $bases 2>/dev/null | sed 's/^/      /' || true
  fi
  if [ -n "$blocked" ]; then
    echo "    佔用行程（etime 以天計＝某次 session 沒收乾淨的遺留）："
    printf '%s\n' "$blockers" | sed 's/^/      /'
  fi

  if [ "$action" = "remove" ]; then
    n_remove=$((n_remove + 1))
    printf '%s\t%s\t%s\n' "$wt" "$parent" "$branch" >> "$removable_list"
  else
    n_keep=$((n_keep + 1))
  fi
done < <(find "$ROOT" -maxdepth 2 -name ".git" -type f 2>/dev/null)

echo
[ "$total" -eq 0 ] && { echo "$ROOT 底下找不到 sibling worktree"; exit 0; }

# 硬規則 4：整排 fallback 值＝管線壞了。把壞掉的清單交給人確認，人無從得知它是壞的。
if [ "$unknown" -gt 0 ] && [ "$unknown" -ge $((total * 2 / 3)) ]; then
  echo "中止：$total 個 worktree 有 $unknown 個 PR 狀態查不到。整排 fallback 值代表 gh 查詢壞掉" >&2
  echo "      （認證過期 / PATH / repo 解析失敗），不是真的沒有 PR。先修查詢再跑一次。" >&2
  exit 3
fi

# 變數一律用 ${} 包起來：bash 3.2 是 byte-oriented，`$var、` 會把「、」的位元組吃進變數名。
echo "盤點：共 ${total} 個，判定可移除 ${n_remove}、保留 ${n_keep}"

if [ "$APPLY" -eq 0 ]; then
  echo
  echo "以上為 dry-run。要實際移除請加 --fetch --apply（會逐一列出具體路徑，不用萬用字元）。"
  exit 0
fi

[ "$n_remove" -eq 0 ] && { echo "沒有可移除的項目。"; exit 0; }

echo
echo "即將移除下列 worktree 目錄（branch 一律保留 —— 見檔頭硬規則 1）："
cut -f1 "$removable_list" | sed 's/^/  - /'
echo

removed=0; failed=0
while IFS=$'\t' read -r wt parent branch; do
  # 先 kill 行程再刪目錄。反過來做的話，行程的 cwd 已是 unlinked inode，它照樣 mkdir .next
  # 把目錄重建出來，會浪費一輪「刪掉了嗎？怎麼又在？」。
  if [ "$KILL_BLOCKERS" -eq 1 ]; then
    blockers=$(blockers_for "$wt")
    if [ -n "$blockers" ]; then
      echo "  kill 佔用行程於 ${wt}："
      printf '%s\n' "$blockers" | sed 's/^/    /'
      for p in $(printf '%s\n' "$blockers" | awk '{print $1}'); do kill "$p" 2>/dev/null || true; done
      sleep 2
    fi
  fi
  # 冪等：目錄已不存在就只補 prune metadata，重跑不會造成額外損害。
  if [ ! -d "$wt" ]; then
    git -C "$parent" worktree prune || true
    echo "  [已不存在] $wt"
    continue
  fi
  if git -C "$parent" worktree remove "$wt" 2>/dev/null; then
    echo "  [OK] $wt"
    removed=$((removed + 1))
  else
    echo "  [失敗] $wt —— 若是 'Directory not empty'，通常有行程仍以它為 cwd（加 --kill-blockers）" >&2
    failed=$((failed + 1))
  fi
done < "$removable_list"

echo
echo "已移除：$removed   失敗：$failed   （branch 全數保留）"
echo "提醒：磁碟空間要等佔用已刪除 inode 的行程結束才會真的釋放，df 的差額不是估算誤差。"
