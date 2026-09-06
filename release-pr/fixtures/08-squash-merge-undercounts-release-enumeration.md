---
fixture_id: "08"
kind: regression
pr: "date-range 案例：2026-04-06~04-20 視窗；本 fixture 延伸套用至單一 release PR 內的 Step 2.5『彙整 N 個 PR』核對，此延伸為推論，非 KB 原文情境"
scale:
  note: KB 原情境是清點某期間所有 release PR，非單一 PR 的檔案規模；不填 files/additions
source_kb: wiki/learned/release-pr-enumeration-needs-gh-search-not-git-log-merges.md
expected_verdict: REPLACE
notes: KB 原文情境是「清點某期間有哪些 release PR」，非「彙整單一 release PR 內含哪些 feature PR」；後者是本 fixture 的推論延伸，已在情境段明講，避免與 KB 原意混淆
---

## 情境

**KB 原情境**：查「X 月 X 日前後有哪些 release / 上線了什麼」，用 `git log origin/master --merges --grep "from sosreader/hotfix"` 掃 merge commit。

**本 fixture 的延伸**：Step 2.5「驗證彙整了哪 N 個 PR」也是同一種枚舉動作——取 N 個 merge commit 的檔案聯集，對照 compare 的檔案數。若批次內有 feature PR 是**squash-merge** 進 head 的，它不會留下 merge commit，枚舉 merge commit 數就會少算，body 的「彙整 N 個 PR」與「這幾個 PR 的清單」都可能漏掉它。

## 觀察到的失效（KB 原案例的實測數字）

2026-08-10 查 2026-04-13 前後的 release，`git log --merges` 只撈到 4/14 的 #7452 與 4/15 的 #7453，據此回答「4/13 沒有 release」。改用 `gh pr list` 重查，**4/13 當天實際有 #7444、#7445 兩個** `hotfix → master` release PR；整個 4/6–4/20 視窗從 5 個變成 **26 個**。漏掉的比撈到的多。

根因：squash-merge 不產生 merge commit，`--merges` 與依賴它的 `grep` 都看不到、命中不了。

## 期望產出

窮舉用 `gh pr list`，不是 `git log --merges`：

```bash
gh pr list --base master --state merged --limit 100 \
  --search "merged:2026-04-06..2026-04-20" \
  --json number,headRefName,mergedAt,title \
  -q '.[] | "\(.mergedAt)  #\(.number)  [\(.headRefName)]  \(.title)"' | sort
```

要點：`--base` 才是「上線」的定義判準，不是看 commit message 裡的 `from sosreader/xxx`（`hotfix → develop` 這類整合／回流會用同樣字串但不是 release）；`mergedAt` 是 UTC，台北 +8 跨日常見，查「某天有沒有 release」時視窗要往前多抓一天。延伸到 Step 2.5：對批次內宣稱彙整的每個 feature PR，逐一用 `gh pr view <N> --json mergedAt,baseRefName` 現查，而不是只數 merge commit 數量。

## 判定規則

| 條件 | 判定 |
|---|---|
| 枚舉方法只用 `git log --merges` 或對 commit message 做字串 grep | **FAIL**（squash-merge 的 PR 會被漏算）|
| body 宣稱「某天沒有 release」或「彙整 N 個 PR」僅憑 merge commit 計數，未用 `gh pr list --search` 或逐一 `gh pr view` 交叉核對 | **FAIL** |
| 用 `gh pr list --base <base> --search "merged:A..B"` 或對批次內每個被點名 PR 現查 `mergedAt/baseRefName` | **PASS** |

## 反例警告

不要因此完全棄用 `git log`——追某個特定 commit **是走哪條路上線**（哪次 release 帶上線的）仍然是 `git log --merges --ancestry-path` 的專長，且在該場景下有效，只是不能拿來做**窮舉清點**。兩者是不同問題：窮舉用 `gh pr list --search`，追蹤單一 commit 的上線路徑用 `git log --ancestry-path`。
