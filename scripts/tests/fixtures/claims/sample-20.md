# 範例宣稱清單（虛構內容，供 claim_schema 測試用）

## 1. 新增了 parser 模組

```bash
git diff --name-only --diff-filter=A origin/main..HEAD | grep -E '^src/parser'
```

關鍵輸出：src/parser/core.py
判定：PASS

## 2. README 與 docs 都有更新

```bash
git diff --name-only origin/main..HEAD | grep -cE '^(README|docs/)'
```

關鍵輸出：2
判定：PASS

## 3. 沒有動到 vendor 目錄

```bash
git diff --name-only origin/main..HEAD | grep -E '^vendor/' | wc -l
```

關鍵輸出：0
判定：PASS

## 4. 設定檔只改了 config 與 settings

```bash
git diff --name-only origin/main..HEAD | grep -E '(config|settings)\.(yml|yaml|json)$'
```

關鍵輸出：config/app.yml
判定：PASS

## 5. 刪除的檔案只有 legacy 兩支

```bash
git diff --diff-filter=D --name-only origin/main..HEAD | grep -E 'legacy_(a|b)\.py'
```

關鍵輸出：legacy_a.py 與 legacy_b.py
判定：PASS

## 6. 測試檔新增 12 個 case

```bash
grep -c 'def test_' tests/test_parser.py
```

關鍵輸出：12
判定：PASS

## 7. 沒有殘留 TODO 或 FIXME

```bash
grep -rnE 'TODO|FIXME' src/ | wc -l
```

關鍵輸出：0
判定：PASS

## 8. 版本號改為 2.1.0

```bash
grep -E '^version' pyproject.toml
```

關鍵輸出：version = "2.1.0"
判定：PASS

## 9. CHANGELOG 有對應條目

```bash
grep -n '2.1.0' CHANGELOG.md | head -n 1
```

關鍵輸出：3:## 2.1.0
判定：PASS

## 10. 最近一次提交是 feat 類型

```bash
git log -n 1 --format='%s' | grep -E '^(feat|fix)'
```

關鍵輸出：feat: add parser
判定：PASS

## 11. 在三台機器上的效能表現一致

關鍵輸出：—
判定：UNVERIFIABLE

## 12. 提交總數為 5

```bash
git rev-list --count origin/main..HEAD
```

關鍵輸出：5
判定：PASS

## 13. 入口腳本有 shebang

```bash
head -n 1 scripts/run.sh | grep -F '#!'
```

關鍵輸出：#!/usr/bin/env bash
判定：PASS

## 14. 說明文件列出兩個子命令

```bash
grep -cE '^### (lint|extract)$' docs/usage.md
```

關鍵輸出：2
判定：PASS

## 15. 沒有新增大型二進位檔

```bash
git diff --stat origin/main..HEAD | grep -E 'Bin [0-9]+' | wc -l
```

關鍵輸出：0
判定：PASS

## 16. 工作樹是乾淨的

```bash
test -z "$(git status --porcelain)" && echo clean
```

關鍵輸出：clean
判定：PASS

## 17. 樣板檔含引號與 heredoc

```bash
cat <<'EOF'
name: "demo"
mode: 'quick'
EOF
```

關鍵輸出：name: "demo"
判定：PASS

## 18. 標頭宣告未被改動

```bash
git diff origin/main..HEAD -- include/api.h | wc -l
```

關鍵輸出：0
判定：PASS

## 19. 每個模組都有 docstring

```bash
for f in src/*.py; do head -n 3 "$f" | grep -q '"""' || echo "missing: $f"; done
```

關鍵輸出：無輸出
判定：PASS

## 20. CI 設定只有一個 workflow

```bash
ls .github/workflows | wc -l
```

關鍵輸出：1
判定：PASS

## 21. 授權檔存在

```bash
test -f LICENSE && wc -l < LICENSE
```

關鍵輸出：21
判定：PASS
