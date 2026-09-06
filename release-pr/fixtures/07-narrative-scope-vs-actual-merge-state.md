---
fixture_id: "07"
kind: regression
pr: "抽象案例（KB 原始記錄未附真實 PR 編號，僅以 Layer 1/2/3 命名示意；confidence: agent-generated）"
scale:
  note: KB 未記載具體檔案/commit 數字，本 fixture 沿用 KB 原始的抽象層級，不得杜撰數字
source_kb: wiki/learned/release-pr-scope-audit-narrative-vs-merge-state.md
expected_verdict: BLOCK
notes: KB 條目本身即為示意性描述（無真實 PR 編號可引），此 fixture 標明此點並沿用相同抽象層級
---

## 情境

一次 session 產出多個協同的 feature PR，分波上線：PR A（read-side guard）、PR B（write-side prevention）、PR C（caller fallback cleanup）。session 內的口頭敘事是「Layer 1+3 一起 ship」。開 release PR 時，作者把這句 session 敘事直接寫進 body：「三層防護已全數上線」。

## 觀察到的失效

session 敘事**沒有強制同步機制**——它是人寫的故事，不會自動對齊 git 實際狀態。開 release PR 的當下實測：PR A MERGED、**PR B 仍為 OPEN**、PR C MERGED。release diff 只包含 A + C，敘事卻宣稱三者皆已上線。

下游後果：reviewer 信任 body 直接核准；deploy 看起來健康；使用者事後回報「我以為 B 已經修好了」，debug 從錯誤的前提開始。根因不是判斷力，是**作者把 session storyline 直接複製進 release body，沒有逐一核對每個被點名的 PR 相對 release base 的 merge state**。

## 期望產出

開 release PR 前的稽核清單：

```bash
# 1. 列出 release scope 內實際的 commit
git log --oneline origin/<base>..origin/<head>

# 2. body 裡點名的每個 PR 逐一現查狀態
gh pr view <PR#> --json state,baseRefName,mergedAt
# 預期：state=MERGED，baseRefName=<release base>，mergedAt!=null

# 3. 對「成對敘事」（Layer 1+3、A+B 綁定上線）——任一方 OPEN 時明確處理三選一：
#    (a) 等它 merge 再開 release PR
#    (b) body 明寫「Layer X 不在此批，下一輪」
#    (c) 整段成對敘事從 body 移除
```

reviewer 側的對應稽核：body 點名的每個 PR # 是否都出現在 `git log <base>..<head>`？「X+Y 綁定」的兩個 PR 是否都已 merge？log 裡有但 body 沒提到的 commit，是否該補上測試計畫？

## 判定規則

| 條件 | 判定 |
|---|---|
| body 沿用 session 敘事，未逐一核對每個被點名 PR 的 merge state | **FAIL** |
| 敘事宣稱「X+Y 一起上線」，但其中之一 `state=OPEN` 或 `baseRefName` 不是 release base | **FAIL** |
| body 只用 `gh pr list --author '@me'` 篩選（漏掉同批次其他作者的 PR） | **FAIL**（篩選範圍不足）|
| 每個被點名的 PR 都現查過 state/baseRefName/mergedAt，未合併者明寫排除理由 | **PASS** |

## 反例警告

不要因為「敘事可能不準」就完全不寫「這幾個 PR 綁定上線」這類統整性描述——這種描述對 reviewer 理解變更全貌很有價值。要擋的是**未經驗證就照抄**，不是統整本身；驗證通過後，敘事照樣可以寫，且比逐條列 PR 編號更好讀。也不該無限期卡住 release：只要一方 OPEN 就有處置方式（等待／明寫排除／移除敘事），三選一即可繼續，不必阻斷整個 release。
