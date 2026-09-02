#!/usr/bin/env python3
"""Notion API helper — 解析 URL、Markdown 轉 block、append/comment、讀回驗證。

用法（token 由環境變數 NOTION_TOKEN 提供，永不作為參數傳入以免進入 shell history）：

  python3 notion_api.py resolve  <notion_url>
  python3 notion_api.py probe    <notion_url>
  python3 notion_api.py render   <markdown_file>            # 只印 block JSON，不送出
  python3 notion_api.py append   <notion_url> <markdown_file>
  python3 notion_api.py comment  <notion_url> <text_file>
  python3 notion_api.py readback <notion_url> [n]           # 讀回最後 n 個 block

設計原則：
- 任何錯誤都印出 Notion 回傳的 message，不吞錯（避免 silent failure）。
- append 一次最多 100 個 block，超過自動分批。
- rich_text 單段上限 2000 字，超過自動切段。
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
MAX_BLOCKS_PER_CALL = 100
MAX_RICH_TEXT_CHARS = 2000


def _token():
    tok = os.environ.get("NOTION_TOKEN", "").strip()
    if not tok:
        sys.exit(
            "ERROR: 環境變數 NOTION_TOKEN 未設定。\n"
            "  export NOTION_TOKEN=\"$(tr -d '[:space:]' < ~/.config/notion/token)\""
        )
    return tok


def call(method, path, body=None):
    req = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": "Bearer " + _token(),
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
    )
    try:
        return json.loads(urllib.request.urlopen(req).read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()
        try:
            msg = json.loads(detail)
            hint = ""
            if msg.get("code") == "object_not_found":
                hint = (
                    "\nHINT: 多半不是 URL 錯，而是該頁面尚未分享給 integration。"
                    "\n      到 Notion 頁面右上 ··· → Connections → 加入你的 integration。"
                )
            elif msg.get("code") == "unauthorized":
                hint = "\nHINT: token 無效或已撤銷，到 notion.so/my-integrations 確認。"
            sys.exit(
                f"ERROR {e.code} {msg.get('code')}: {msg.get('message')}{hint}"
            )
        except json.JSONDecodeError:
            sys.exit(f"ERROR {e.code}: {detail[:500]}")


DASHED = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)
BARE = re.compile(r"([0-9a-fA-F]{32})$")


def page_id(url_or_id):
    """從 Notion URL 取出 page id。

    支援 app.notion.com/p/<ws>/<id>、www.notion.so/<Title>-<id>、notion.site、
    以及已經是 id（帶或不帶 dash）的字串。

    id 一律錨定在**最後一段路徑的結尾**。不可以先把 dash 移除再全域搜尋 32 位
    hex——標題裡的字母（a-f）會被當成 id 的一部分，例如
    `Some-Page-3c90...` 的 `Page` 結尾的 `e` 會讓比對整個錯位一格。
    """
    s = url_or_id.strip().split("?")[0].split("#")[0].rstrip("/")
    seg = s.split("/")[-1]
    m = DASHED.search(seg)
    raw = m.group(1).replace("-", "").lower() if m else None
    if raw is None:
        m = BARE.search(seg)
        if not m:
            sys.exit(f"ERROR: 無法從此 URL 取出 page id: {url_or_id}")
        raw = m.group(1).lower()
    return f"{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}"


def _chunks(text):
    """把長文字切成 <=2000 字的 rich_text 片段。"""
    return [
        text[i : i + MAX_RICH_TEXT_CHARS]
        for i in range(0, max(len(text), 1), MAX_RICH_TEXT_CHARS)
    ] or [""]


def rich(text, bold=False, code=False, link=None):
    out = []
    for part in _chunks(text):
        item = {"type": "text", "text": {"content": part}}
        if link:
            item["text"]["link"] = {"url": link}
        ann = {}
        if bold:
            ann["bold"] = True
        if code:
            ann["code"] = True
        if ann:
            item["annotations"] = ann
        out.append(item)
    return out


_INLINE = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))")


def rich_inline(text):
    """處理行內 **粗體**、`code`、[文字](連結)。"""
    out = []
    for tok in _INLINE.split(text):
        if not tok:
            continue
        if tok.startswith("**") and tok.endswith("**") and len(tok) > 4:
            out += rich(tok[2:-2], bold=True)
        elif tok.startswith("`") and tok.endswith("`") and len(tok) > 2:
            out += rich(tok[1:-1], code=True)
        elif tok.startswith("[") and "](" in tok:
            label, url = tok[1:-1].split("](", 1)
            out += rich(label, link=url)
        else:
            out += rich(tok)
    return out or rich("")


def md_to_blocks(md):
    """把受限子集的 Markdown 轉成 Notion block。

    支援：## / ### 標題、- 項目、1. 編號、> callout、``` code、--- 分隔線、段落。
    刻意不支援表格——Notion table block 結構複雜且容易失敗，改用項目列表表達。
    """
    blocks = []
    lines = md.split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if not s:
            i += 1
            continue
        if s.startswith("```"):
            lang = s[3:].strip() or "plain text"
            i += 1
            buf = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            blocks.append(
                {
                    "object": "block",
                    "type": "code",
                    "code": {
                        "rich_text": rich("\n".join(buf)),
                        "language": lang,
                    },
                }
            )
            continue
        if s in ("---", "***", "___"):
            blocks.append({"object": "block", "type": "divider", "divider": {}})
        elif s.startswith("#### "):
            blocks.append(_h("heading_3", s[5:]))
        elif s.startswith("### "):
            blocks.append(_h("heading_3", s[4:]))
        elif s.startswith("## "):
            blocks.append(_h("heading_2", s[3:]))
        elif s.startswith("# "):
            blocks.append(_h("heading_1", s[2:]))
        elif s.startswith("> "):
            blocks.append(
                {
                    "object": "block",
                    "type": "callout",
                    "callout": {
                        "rich_text": rich_inline(s[2:]),
                        "icon": {"type": "emoji", "emoji": "💡"},
                    },
                }
            )
        elif re.match(r"^[-*]\s+", s):
            blocks.append(
                {
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": rich_inline(re.sub(r"^[-*]\s+", "", s))
                    },
                }
            )
        elif re.match(r"^\d+\.\s+", s):
            blocks.append(
                {
                    "object": "block",
                    "type": "numbered_list_item",
                    "numbered_list_item": {
                        "rich_text": rich_inline(re.sub(r"^\d+\.\s+", "", s))
                    },
                }
            )
        else:
            blocks.append(
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": rich_inline(s)},
                }
            )
        i += 1
    return blocks


def _h(kind, text):
    return {"object": "block", "type": kind, kind: {"rich_text": rich_inline(text)}}


def cmd_resolve(url):
    print(page_id(url))


def cmd_probe(url):
    pid = page_id(url)
    p = call("GET", f"/pages/{pid}")
    title = ""
    for prop in p.get("properties", {}).values():
        if prop.get("type") == "title":
            title = "".join(t.get("plain_text", "") for t in prop.get("title", []))
            break
    print(f"page_id    : {pid}")
    print(f"title      : {title or '(無標題屬性)'}")
    print(f"url        : {p.get('url')}")
    print(f"archived   : {p.get('archived')}")
    print(f"last_edited: {p.get('last_edited_time')}")
    kids = call("GET", f"/blocks/{pid}/children?page_size=100")
    print(f"top_blocks : {len(kids.get('results', []))}")


def cmd_render(md_file):
    blocks = md_to_blocks(open(md_file, encoding="utf-8").read())
    print(json.dumps(blocks, ensure_ascii=False, indent=2))
    print(f"\n-- {len(blocks)} blocks --", file=sys.stderr)


def cmd_append(url, md_file):
    pid = page_id(url)
    blocks = md_to_blocks(open(md_file, encoding="utf-8").read())
    total = 0
    for i in range(0, len(blocks), MAX_BLOCKS_PER_CALL):
        batch = blocks[i : i + MAX_BLOCKS_PER_CALL]
        call("PATCH", f"/blocks/{pid}/children", {"children": batch})
        total += len(batch)
    print(f"OK appended {total} blocks -> {pid}")


def cmd_comment(url, txt_file):
    pid = page_id(url)
    text = open(txt_file, encoding="utf-8").read().strip()
    r = call(
        "POST",
        "/comments",
        {"parent": {"page_id": pid}, "rich_text": rich(text)},
    )
    print(f"OK comment {r.get('id')} -> {pid}")


def cmd_readback(url, n="10"):
    pid = page_id(url)
    kids = call("GET", f"/blocks/{pid}/children?page_size=100")
    res = kids.get("results", [])
    print(f"-- 頁面共 {len(res)} 個 top-level block，顯示最後 {n} 個 --")
    for b in res[-int(n) :]:
        t = b.get("type")
        rt = b.get(t, {}).get("rich_text", [])
        txt = "".join(x.get("plain_text", "") for x in rt)
        print(f"  [{t}] {txt[:120]}")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]
    fn = {
        "resolve": cmd_resolve,
        "probe": cmd_probe,
        "render": cmd_render,
        "append": cmd_append,
        "comment": cmd_comment,
        "readback": cmd_readback,
    }.get(cmd)
    if not fn:
        sys.exit(f"unknown command: {cmd}\n{__doc__}")
    fn(*args)


if __name__ == "__main__":
    main()
