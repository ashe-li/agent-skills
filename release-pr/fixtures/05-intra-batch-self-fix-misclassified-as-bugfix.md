---
fixture_id: "05"
kind: regression
pr: sosreader/vocus-web-ui#7969
scale:
  content_commits: 28
  files: 64
  additions: 3353
  deletions: 697
source_kb: wiki/learned/intra-batch-self-correction-is-not-a-bug-fix.md
expected_verdict: RECLASSIFY
notes: 與 fixture 03/04 同一批次（#7969）；本 fixture 測的是 Step 2 分類與 Step 2.5 檢查 3，不是事實正確性或範圍相稱性
---

## 情境（Step 2/2.5 的輸入條件）

批次裡有一個 `fix(...)` commit，措辭是標準 bug 修復語氣（「訂正為 scoped」「修復 a11y 回歸」）。Step 2 依 Conventional Commit prefix 直覺歸類到 Bug Fixes。

## 觀察到的失效

`styles/lexical-web-theme/base.css` 的 `* { outline: 0 }` 在 styled → Tailwind 遷移中一度被搬成 partial 頂層的全域規則——作用域從「閱讀器容器內後代」擴張到全站每個元素，清掉所有頁面的 focus outline，是貨真價實的 a11y 回歸。同批次下一個 commit 已訂正回 `.lexical-web-theme *`。

**這個錯誤版本從未進入 master**——`base.css` 本身是本批次新增檔（`gh api .../compare/<base>...<head> --jq '.files[] | select(.filename=="styles/lexical-web-theme/base.css") | .status'` 回傳 `added`）。若放進 Bug Fixes，reviewer 會合理推斷「線上曾經全站 focus outline 消失」，觸發不必要的影響評估、客訴回溯、通知 QA 回歸測試——對一個從未存在於 production 的問題。

## 期望產出

Step 2.5 檢查 3 對每個 `fix(` commit 觸及的檔案跑 file status 查核。`status == "added"` ⇒ 該檔在 base 根本不存在 ⇒ 檔內所有「修復」必然都是批次內自修，不可能是線上 bug。檔案本來就存在（`modified`）時，退一步核對被修那幾行在 base 上長什麼樣，或用 `git log <base>..<head> --oneline -- <path>` 確認引入與修正是否都落在同一批次範圍內。

判定為批次內自修後，**不是刪掉**，而是把它降級成對應 Improvements 條目的括號註記，明寫「批次內自我修正，未上線」：

> …（批次內自我修正，未上線：`* { outline: 0 }` 一度被搬為 partial 頂層的全域規則，會把作用域擴張到全站每個元素、清掉所有頁面的 focus outline；同批次下一 commit 已訂正回 `.lexical-web-theme *`，與遷移前行為等值。`base.css` 是本批新增檔，錯誤版本從未進入 master。）

## 判定規則

| 條件 | 判定 |
|---|---|
| body 把該項目放進 Bug Fixes、未標「未上線」 | **FAIL**（會讓 reviewer 誤判線上事故）|
| body 完全刪掉這個 commit、不留任何記錄 | **FAIL**（過度修正，見下）|
| body 把該項目移到 Improvements 括號註記，且明寫「批次內自我修正，未上線」+ file status 依據 | **PASS** |

## 反例警告

不要因為「怕誤導」就整條刪掉。這類 commit 常帶著最有價值的教訓（本例：stylis 會把 styled 模板內的巢狀規則編成 wrapper 後代選擇器，照原始碼縮排判斷 scope 會錯，要看 SSR 實際產物）。教訓的能見度要保住，只是不能放進暗示「線上曾經壞過」的區塊。`fix(` prefix 不蘊含「線上有 bug」，但這不代表 `fix(` commit 沒有價值可寫。
