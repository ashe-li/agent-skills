"""notion-report/scripts/notion_browser.py 的圖片與讀回子指令。

這些函式只產生要交給瀏覽器執行的 JS 字串，所以這裡驗的是：輸入有被正確
編進字串、守衛條件會擋、指令表有掛上。JS 在真實瀏覽器裡的行為另以假頁面實測。
"""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO / "notion-report" / "scripts" / "notion_browser.py"


def _load():
    spec = importlib.util.spec_from_file_location("notion_browser", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


nb = _load()


class StashImageJs(unittest.TestCase):
    def _write(self, directory: str, name: str, payload: bytes) -> str:
        path = Path(directory) / name
        path.write_bytes(payload)
        return str(path)

    def test_embeds_base64_name_type_and_index(self):
        payload = bytes(range(256)) * 3
        with tempfile.TemporaryDirectory() as d:
            js = nb.stash_image_js(self._write(d, "01-dark.png", payload), "2")
        self.assertIn("window.__nrImgs[2] =", js)
        self.assertIn(json.dumps(base64.b64encode(payload).decode("ascii")), js)
        self.assertIn('name: "01-dark.png"', js)
        self.assertIn('type: "image/png"', js)
        self.assertIn("bytes: %d" % len(payload), js)

    def test_filename_with_quotes_is_json_escaped(self):
        with tempfile.TemporaryDirectory() as d:
            js = nb.stash_image_js(self._write(d, 'a "b".png', b"x"), 0)
        self.assertIn(json.dumps('a "b".png'), js)

    def test_unknown_extension_falls_back_to_png(self):
        with tempfile.TemporaryDirectory() as d:
            js = nb.stash_image_js(self._write(d, "shot.unknownext", b"x"), 0)
        self.assertIn('type: "image/png"', js)

    def test_jpeg_mime_is_detected(self):
        with tempfile.TemporaryDirectory() as d:
            js = nb.stash_image_js(self._write(d, "shot.jpg", b"x"), 0)
        self.assertIn('type: "image/jpeg"', js)

    def test_oversized_image_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._write(d, "big.png", b"\0" * (nb.MAX_IMAGE_BYTES + 1))
            with self.assertRaises(SystemExit) as ctx:
                nb.stash_image_js(path, 0)
        self.assertIn("超過上限", str(ctx.exception))

    def test_image_at_limit_is_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._write(d, "edge.png", b"\0" * nb.MAX_IMAGE_BYTES)
            js = nb.stash_image_js(path, 0)
        # base64 後仍要小於 1 MiB 的指令參數上限
        self.assertLess(len(js.encode("utf-8")), 1_048_576)

    def test_negative_index_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = self._write(d, "a.png", b"x")
            with self.assertRaises(SystemExit):
                nb.stash_image_js(path, -1)


class PasteImagesJs(unittest.TestCase):
    def test_expected_count_and_null_anchor(self):
        js = nb.paste_images_js("3")
        self.assertIn("const EXPECTED = 3;", js)
        self.assertIn("const ANCHOR = null;", js)

    def test_anchor_is_json_escaped(self):
        js = nb.paste_images_js(1, '最後 "一" 行')
        self.assertIn("const ANCHOR = %s;" % json.dumps('最後 "一" 行'), js)

    def test_single_paste_event_and_guards(self):
        js = nb.paste_images_js(2)
        self.assertEqual(js.count('new ClipboardEvent("paste"'), 1)
        self.assertIn("imgs.length !== EXPECTED", js)
        self.assertIn("window.__nrImgs = [];", js)
        self.assertTrue(js.startswith("() => {"))
        self.assertTrue(js.rstrip().endswith("}"))

    def test_non_integer_count_is_rejected(self):
        with self.assertRaises(ValueError):
            nb.paste_images_js("two")


class ScanJs(unittest.TestCase):
    def test_needle_optional(self):
        self.assertIn("const NEEDLE = null;", nb.scan_js())
        self.assertIn("const NEEDLE = %s;" % json.dumps("標題"), nb.scan_js("標題"))

    def test_scroll_loop_is_bounded(self):
        js = nb.scan_js()
        match = re.search(r"step < (\d+)", js)
        self.assertIsNotNone(match)
        self.assertLessEqual(int(match.group(1)), 500)
        self.assertIn("resolve(JSON.stringify(", js)


class Dispatch(unittest.TestCase):
    def _run(self, *argv: str) -> str:
        out = io.StringIO()
        with mock.patch.object(nb.sys, "argv", ["notion_browser.py", *argv]), redirect_stdout(out):
            nb.main()
        return out.getvalue()

    def test_new_commands_are_registered(self):
        self.assertIn("const EXPECTED = 4;", self._run("paste-images-js", "4"))
        self.assertIn("const NEEDLE = null;", self._run("scan-js"))
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "a.png"
            path.write_bytes(b"x")
            self.assertIn("window.__nrImgs[1] =", self._run("stash-image-js", str(path), "1"))

    def test_existing_commands_still_work(self):
        self.assertIn("findContent", self._run("probe-js"))
        self.assertIn(json.dumps("abc"), self._run("verify-js", "abc"))

    def test_unknown_command_exits(self):
        with self.assertRaises(SystemExit):
            self._run("nope")


if __name__ == "__main__":
    unittest.main()
