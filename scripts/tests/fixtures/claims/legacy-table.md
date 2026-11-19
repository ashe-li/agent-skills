# 舊格式宣稱表（虛構內容）

| Claim | 證據指令 | 關鍵輸出 | 判定 |
|---|---|---|---|
| 「新增了 foo 模組」 | `git diff --name-only origin/main..HEAD` | `src/foo.py` | PASS |
| 「不影響 bar 模組」 | `git diff --stat origin/main..HEAD -- bar/` | bar/ 完全無 diff | PASS |
| 「本機測試全綠」 | 無法對當前 diff 重跑 | — | UNVERIFIABLE |
