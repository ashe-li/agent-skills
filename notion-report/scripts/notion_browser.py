#!/usr/bin/env python3
"""產生要餵給 `playwright-cli eval` 的 JS —— 瀏覽器寫入路徑（無需 API token）。

當使用者無權建立 Notion internal integration 時走這條。沿用 /notion-plan 已建立的
persistent profile（~/.playwright-cli/notion-profile），不需要重新登入。

用法：

  python3 notion_browser.py probe-js                  # 唯讀：確認頁面載入與可編輯區
  python3 notion_browser.py insert-js  <markdown_file> # 產生插入用 JS
  python3 notion_browser.py verify-js  <needle>        # 讀回驗證：確認頁面含某段字
  python3 notion_browser.py stash-image-js <image_file> <index>   # 把一張圖暫存到頁面（每張一次 eval）
  python3 notion_browser.py paste-images-js <count> [anchor]      # 把暫存的圖一次貼到頁面最後
  python3 notion_browser.py scan-js [needle]            # 捲動整頁累計 block：長頁面的權威讀回

搭配：

  playwright-cli -s=notion eval "$(python3 notion_browser.py probe-js)"

為什麼用 synthetic paste event 而不是逐字打字：
Notion 的貼上處理器會把 text/plain 的 Markdown 解析成對應 block（標題、清單、
code block 都認得）。逐字打字則要依賴 Notion 的即時 Markdown 快捷鍵，遇到清單
換行、離開 code block 等情境需要額外送 Escape / Backspace，狀態機容易走歪。
paste 是一次性、原子的，且不需要 clipboard 權限——我們自建 DataTransfer，
不碰系統剪貼簿。
"""
import base64
import json
import mimetypes
import os
import sys

# Notion 頁面內容容器的候選 selector（不同版面／頁型不一樣）
CONTENT_SELECTORS = [
    ".notion-page-content",
    ".notion-frame .notion-scroller [data-block-id]",
    "[data-content-editable-root='true']",
]

# 一張圖會整個放進 eval 的指令參數（base64 約放大 1.33 倍）。macOS 的 ARG_MAX 是
# 1 MiB，所以原始檔上限抓 600 KB；超過請先縮圖，不要硬塞。
MAX_IMAGE_BYTES = 600_000

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
  const charsBefore = (content.innerText || "").length;
  const blocksBefore = content.querySelectorAll("[data-block-id]").length;
  const dt = new DataTransfer();
  dt.setData("text/plain", MD);
  const notCancelled = last.dispatchEvent(new ClipboardEvent("paste", {
    clipboardData: dt, bubbles: true, cancelable: true
  }));
  return JSON.stringify({
    dispatched: true,
    // 純事實記錄：dispatchEvent 是否被 preventDefault 攔下。
    // 【不是成功訊號】——Notion 的貼上處理器不呼叫 preventDefault，
    // 實測寫入成功時這裡仍是 false。權威判定一律靠 Step 5 的 verify-js。
    preventDefaultCalled: !notCancelled,
    blocksBefore: blocksBefore,
    charsBefore: charsBefore,
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


def stash_image_js(image_file, index):
    """把一張圖以 base64 暫存到 window.__nrImgs[index]。只暫存，不動頁面內容。"""
    index = int(index)
    if index < 0:
        sys.exit("index 必須 >= 0")
    size = os.path.getsize(image_file)
    if size > MAX_IMAGE_BYTES:
        sys.exit(
            f"{os.path.basename(image_file)} 有 {size} bytes，超過上限 {MAX_IMAGE_BYTES}。"
            "請先縮圖或裁切再傳（整張圖會放進指令參數，受 ARG_MAX 限制）。"
        )
    with open(image_file, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode("ascii")
    name = os.path.basename(image_file)
    mime = mimetypes.guess_type(name)[0] or "image/png"
    return (
        "() => { window.__nrImgs = window.__nrImgs || [];"
        " window.__nrImgs[%d] = {name: %s, type: %s, b64: %s};"
        " return JSON.stringify({stashed: window.__nrImgs.filter(Boolean).length,"
        " index: %d, bytes: %d}); }"
        % (index, json.dumps(name), json.dumps(mime), json.dumps(b64), index, size)
    )


def paste_images_js(count, anchor=None):
    """把暫存的圖包成 File，用一個 paste 事件貼在最後一個可編輯區塊之後。

    一次貼完才能保證順序：分次貼時每張都插在同一個區塊後面，順序會反過來。
    anchor 有給時，最後一個可編輯區塊的文字必須包含它，否則不貼（防貼錯位置）。
    """
    count = int(count)
    return (
        "() => {"
        + _PRELUDE
        + r"""
  const EXPECTED = %d;
  const ANCHOR = %s;
  const content = findContent();
  if (!content) return "FAIL: 找不到 Notion 內容容器";
  const imgs = (window.__nrImgs || []).filter(Boolean);
  if (imgs.length !== EXPECTED) return "FAIL: 暫存的圖有 " + imgs.length + " 張，預期 " + EXPECTED + "（頁面重載後暫存會消失，要重新 stash）";
  const editables = content.querySelectorAll('[contenteditable="true"]');
  if (!editables.length) return "FAIL: 沒有可編輯區塊——可能無編輯權限或未登入";
  const last = editables[editables.length - 1];
  if (ANCHOR && !(last.innerText || "").includes(ANCHOR)) return "FAIL: 最後一個可編輯區塊不是預期的那一段：" + (last.innerText || "").slice(0, 40);
  last.scrollIntoView({block: "end"});
  last.focus();
  const sel = window.getSelection();
  const range = document.createRange();
  range.selectNodeContents(last);
  range.collapse(false);
  sel.removeAllRanges();
  sel.addRange(range);
  const dt = new DataTransfer();
  for (const im of imgs) {
    const bin = atob(im.b64);
    const u8 = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
    dt.items.add(new File([u8], im.name, {type: im.type}));
  }
  const imagesBefore = content.querySelectorAll(".notion-image-block").length;
  last.dispatchEvent(new ClipboardEvent("paste", {clipboardData: dt, bubbles: true, cancelable: true}));
  window.__nrImgs = [];
  // 【不是成功訊號】——和 insert-js 一樣，權威判定靠讀回（scan-js 的 images）。
  return JSON.stringify({dispatched: true, files: dt.files.length, imagesBefore: imagesBefore});
}"""
        % (count, json.dumps(anchor))
    )


def scan_js(needle=None):
    """捲動整頁累計 block。長頁面只畫畫面附近的區塊，單次 innerText 會少算。"""
    return (
        "() => new Promise(async (resolve) => {"
        + _PRELUDE
        + r"""
  const NEEDLE = %s;
  const content = findContent();
  if (!content) return resolve("FAIL: 找不到 Notion 內容容器");
  const scroller = document.querySelector(".notion-frame .notion-scroller") || document.scrollingElement;
  const seen = new Map();
  const order = [];
  const collect = () => {
    const c = findContent();
    if (!c) return;
    c.querySelectorAll("[data-block-id]").forEach((b) => {
      const id = b.getAttribute("data-block-id");
      if (!seen.has(id)) { order.push(id); seen.set(id, {type: "", text: "", image: ""}); }
      const cls = [...b.classList].find((x) => x.startsWith("notion-") && x.endsWith("-block")) || "other";
      const text = (b.innerText || "").replace(/\n/g, " / ");
      const img = b.classList.contains("notion-image-block") ? b.querySelector("img") : null;
      const prev = seen.get(id);
      seen.set(id, {
        type: cls.replace("notion-", "").replace("-block", ""),
        // 區塊捲出畫面後 innerText 會變空，保留讀到過最長的一次
        text: text.length >= prev.text.length ? text : prev.text,
        image: img && img.complete && img.naturalWidth > 0 ? img.naturalWidth + "x" + img.naturalHeight : prev.image
      });
    });
  };
  // 上限 200 步：再長的頁面也會結束，不會卡住 CLI
  for (let step = 0, y = 0; step < 200; step++, y += 500) {
    scroller.scrollTop = y;
    await new Promise((r) => setTimeout(r, 300));
    collect();
    if (y >= scroller.scrollHeight - scroller.clientHeight) break;
  }
  const rows = order.map((id) => seen.get(id));
  const types = {};
  rows.forEach((r) => { types[r.type] = (types[r.type] || 0) + 1; });
  const images = rows.filter((r) => r.type === "image").map((r) => r.image || "loading");
  const occurrences = NEEDLE ? rows.filter((r) => r.text.includes(NEEDLE)).length : null;
  resolve(JSON.stringify({
    total: rows.length,
    types: types,
    images: images,
    occurrences: occurrences,
    tail: rows.slice(-8).map((r) => r.type + ": " + r.text.slice(0, 40))
  }));
})"""
        % json.dumps(needle)
    )


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]
    fn = {
        "probe-js": probe_js,
        "insert-js": insert_js,
        "verify-js": verify_js,
        "stash-image-js": stash_image_js,
        "paste-images-js": paste_images_js,
        "scan-js": scan_js,
    }.get(cmd)
    if not fn:
        sys.exit(f"unknown command: {cmd}\n{__doc__}")
    print(fn(*args))


if __name__ == "__main__":
    main()
