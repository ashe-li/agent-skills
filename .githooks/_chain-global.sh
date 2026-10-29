#!/usr/bin/env bash
# 串接全域 core.hooksPath 的同名 hook：repo-local core.hooksPath=.githooks 會蓋掉全域設定，
# 由本檔補回。用法：source 後呼叫 `chain_global_hook <hook 名稱> [原始參數...]`，
# 或直接執行 `_chain-global.sh <hook 名稱> [原始參數...]`；stdin 原樣交給全域 hook，
# 回傳（或結束）碼即全域 hook 的碼；沒有全域 hook 時為 0。

chain_global_hook() {
  local name="$1"
  shift
  local self_dir global_path global_dir global_hook rc=0
  self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
  global_path="$(git config --global --get core.hooksPath || true)"
  case "$global_path" in
    \~) global_path="$HOME" ;;
    \~/*) global_path="$HOME/${global_path#\~/}" ;;
  esac
  [ -n "$global_path" ] && [ -d "$global_path" ] || return 0
  global_dir="$(cd "$global_path" && pwd -P)"
  global_hook="$global_dir/$name"
  # 全域目錄就是本 repo 的 .githooks 時不執行，避免遞迴
  [ "$global_dir" != "$self_dir" ] || return 0
  [ -f "$global_hook" ] && [ -x "$global_hook" ] || return 0
  "$global_hook" "$@" || rc=$?
  return "$rc"
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  set -euo pipefail
  chain_global_hook "$@"
fi
