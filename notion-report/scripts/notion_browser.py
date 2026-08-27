#!/usr/bin/env python3
"""產生要餵給 `playwright-cli eval` 的 JS —— 瀏覽器寫入路徑（無需 API token）。

當使用者無權建立 Notion internal integration 時走這條。沿用 /notion-plan 已建立的
persistent profile（~/.playwright-cli/notion-profile），不需要重新登入。

用法：

  python3 notion_browser.py probe-js                  # 唯讀：確認頁面載入與可編輯區
  python3 notion_browser.py insert-js  <markdown_file> # 產生插入用 JS
  python3 notion_browser.py verify-js  <needle>        # 讀回驗證：確認頁面含某段字

搭配：

  playwright-cli -s=notion eval "$(python3 notion_browser.py probe-js)"

為什麼用 synthetic paste event 而不是逐字打字：
Notion 的貼上處理器會把 text/plain 的 Markdown 解析成對應 block（標題、清單、
code block 都認得）。逐字打字則要依賴 Notion 的即時 Markdown 快捷鍵，遇到清單
換行、離開 code block 等情境需要額外送 Escape / Backspace，狀態機容易走歪。
paste 是一次性、原子的，且不需要 clipboard 權限——我們自建 DataTransfer，
不碰系統剪貼簿。
"""
import json
import sys

# Notion 頁面內容容器的候選 selector（不同版面／頁型不一樣）
CONTENT_SELECTORS = [
    ".notion-page-content",
    ".notion-frame .notion-scroller [data-block-id]",
    "[data-content-editable-root='true']",
]

_PRELUDE = """
  const SELECTORS = %s;
  const findContent = () => {
    for (const s of SELECTORS) {
      const el = document.querySelector(s);
      if (el) return el;
    }
    return null;
  };
""" % json.dumps(CONTENT_SELECTORS)


def probe_js():
    return (
        "() => {"
        + _PRELUDE
        + r"""
  const content = findContent();
  if (!content) return "FAIL: 找不到 Notion 內容容器——可能頁面還沒載完，或未登入";
  const editables = content.querySelectorAll('[contenteditable="true"]');
  const titleEl = document.querySelector('[data-root="true"] [contenteditable="true"], .notion-page-block [contenteditable="true"], h1[contenteditable]');
  const titleText = (titleEl && titleEl.innerText) || document.title.replace(/^\(\d+\)\s*/, "").replace(/\s*\|\s*Notion\s*$/, "");
  const login = /login|signin/i.test(location.pathname);
  return JSON.stringify({
    ok: !login && editables.length > 0,
    loginRedirect: login,
    url: location.href,
    title: (titleText || "").trim().slice(0, 80),
    blocks: content.querySelectorAll("[data-block-id]").length,
    editables: editables.length,
    tailText: (content.innerText || "").slice(-160)
  });
}"""
    )


def insert_js(md_file):
    md = open(md_file, encoding="utf-8").read().rstrip()
    return (
        "() => {"
        + _PRELUDE
        + """
  const MD = %s;
  const content = findContent();
  if (!content) return "FAIL: 找不到 Notion 內容容器";
  const editables = content.querySelectorAll('[contenteditable="true"]');
  if (!editables.length) return "FAIL: 沒有可編輯區塊——可能無編輯權限或未登入";
  const last = editables[editables.length - 1];
  last.scrollIntoView({block: "end"});
  last.focus();
  // 游標移到最後一個區塊的結尾，避免插進既有文字中間
  const sel = window.getSelection();
  const range = document.createRange();
  range.selectNodeContents(last);
  range.collapse(false);
  sel.removeAllRanges();
  sel.addRange(range);
  const before = (content.innerText || "").length;
  const dt = new DataTransfer();
  dt.setData("text/plain", MD);
  const notCancelled = last.dispatchEvent(new ClipboardEvent("paste", {
    clipboardData: dt, bubbles: true, cancelable: true
  }));
  return JSON.stringify({
    dispatched: true,
    handledByNotion: !notCancelled,
    lengthBefore: before,
    chars: MD.length
  });
}"""
        % json.dumps(md)
    )


def verify_js(needle):
    return (
        "() => {"
        + _PRELUDE
        + """
  const NEEDLE = %s;
  const content = findContent();
  if (!content) return "FAIL: 找不到 Notion 內容容器";
  const text = content.innerText || "";
  return JSON.stringify({
    found: text.includes(NEEDLE),
    occurrences: text.split(NEEDLE).length - 1,
    totalChars: text.length,
    tail: text.slice(-300)
  });
}"""
        % json.dumps(needle)
    )


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]
    fn = {"probe-js": probe_js, "insert-js": insert_js, "verify-js": verify_js}.get(cmd)
    if not fn:
        sys.exit(f"unknown command: {cmd}\n{__doc__}")
    print(fn(*args))


if __name__ == "__main__":
    main()
