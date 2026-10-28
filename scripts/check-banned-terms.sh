#!/usr/bin/env bash
# check-banned-terms.sh - 擋下不該進公開 repo 的字樣（黑名單由外部提供，不進 repo）。
#
# 用法：
#   scripts/check-banned-terms.sh --staged              掃 git diff --cached 的新增行
#   scripts/check-banned-terms.sh --range <base>..<head> 掃範圍內 diff 新增行與 commit message
#   scripts/check-banned-terms.sh --all                 掃整個 tracked tree
#
# 黑名單來源（Python re 語法，不分大小寫）：
#   1. 環境變數 BANNED_TERMS_REGEX
#   2. 檔案 ${BANNED_TERMS_FILE:-$HOME/.config/agent-skills/banned-terms.regex}
# 兩者都沒有：印一行 notice 並 exit 0（fork／其他使用者不被擋）。
#
# 輸出紀律：repo 與 CI log 都是公開的，命中時只印 `path:line`（commit message 印 sha），
# 絕不印命中內容或 regex 本身。
# Exit code：0 乾淨或未設定黑名單；1 有命中；2 用法錯誤。
set -euo pipefail

usage() {
  echo "usage: $0 --staged | --range <base>..<head> | --all" >&2
  exit 2
}

[ $# -ge 1 ] || usage
MODE="$1"
RANGE=""
case "$MODE" in
  --staged | --all) [ $# -eq 1 ] || usage ;;
  --range)
    [ $# -eq 2 ] || usage
    RANGE="$2"
    ;;
  *) usage ;;
esac

if [ -z "${BANNED_TERMS_REGEX:-}" ]; then
  terms_file="${BANNED_TERMS_FILE:-$HOME/.config/agent-skills/banned-terms.regex}"
  if [ -f "$terms_file" ]; then
    BANNED_TERMS_REGEX="$(cat "$terms_file")"
  fi
fi

if [ -z "${BANNED_TERMS_REGEX:-}" ]; then
  echo "check-banned-terms: 未設定黑名單（BANNED_TERMS_REGEX 或 banned-terms.regex），略過檢查"
  exit 0
fi
export BANNED_TERMS_REGEX

exec python3 -I -c '
import os, re, subprocess, sys

mode, rng = sys.argv[1], sys.argv[2]
try:
    pattern = re.compile(os.environ["BANNED_TERMS_REGEX"].strip(), re.IGNORECASE)
except re.error:
    print("check-banned-terms: 黑名單 regex 無法編譯", file=sys.stderr)
    sys.exit(2)

hits = []  # 只存 (label, lineno_or_None)，不存命中內容


def git(*args):
    out = subprocess.run(
        ["git", "-c", "core.quotepath=off", *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if out.returncode != 0:
        print("check-banned-terms: git 指令失敗", file=sys.stderr)
        sys.exit(2)
    return out.stdout.decode("utf-8", errors="replace")


def scan_diff(text):
    path, lineno = None, 0
    for raw in text.splitlines():
        if raw.startswith("+++ "):
            path = None if raw == "+++ /dev/null" else raw[6:]
        elif raw.startswith("@@"):
            m = re.match(r"@@ -\S+ \+(\d+)", raw)
            lineno = int(m.group(1)) if m else 0
        elif raw.startswith("+") and path is not None:
            if pattern.search(raw[1:]):
                hits.append(("%s:%d" % (path, lineno)))
            lineno += 1


def scan_tree():
    names = git("ls-files", "-z").split("\0")
    for name in filter(None, names):
        try:
            with open(name, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        if b"\0" in data:
            continue
        for i, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            if pattern.search(line):
                hits.append("%s:%d" % (name, i))


def scan_range(spec):
    if "..." in spec:
        a, b = spec.split("...", 1)
        log_spec = a + ".." + b
    else:
        log_spec = spec
    scan_diff(git("diff", "-U0", "--no-color", "--no-ext-diff", spec))
    log = git("log", "--format=%H%x00%B%x00%x01", log_spec)
    for rec in log.split("\x01"):
        rec = rec.strip("\n")
        if not rec:
            continue
        sha, _, body = rec.partition("\0")
        if pattern.search(body):
            hits.append("commit %s" % sha[:12])


if mode == "--all":
    scan_tree()
elif mode == "--staged":
    scan_diff(git("diff", "--cached", "-U0", "--no-color", "--no-ext-diff"))
else:
    scan_range(rng)

for h in hits:
    print(h)
if hits:
    print("check-banned-terms: %d 處命中黑名單（內容不顯示，請依 path:line 自行檢視）" % len(hits), file=sys.stderr)
    sys.exit(1)
' "$MODE" "$RANGE"
