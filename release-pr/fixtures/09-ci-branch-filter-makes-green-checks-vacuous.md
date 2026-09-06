---
fixture_id: "09"
kind: regression
pr: "sosreader/vocus-web-ui（案例一：base=develop 的重構 PR；案例二：PDT-10581，base=hotfix）"
scale:
  note: KB 未記載這兩個案例的檔案數與 +/− 規模，此欄位不填數字
source_kb: wiki/learned/ci-workflow-branch-filter-makes-checks-vacuous.md
expected_verdict: BLOCK
notes: 測的是 Step 2.5 規則 2（本機執行結果數字不轉載，改指向本 PR 的 check runs）與 Step 5 之間的銜接——「指向 check runs」這個補償做法本身也可能是空的
---

## 情境

Step 2.5 規則 2 對「本機測試通過」這類數字的處置是「不轉載，改指向本 PR 的 check runs」。這個補償做法隱含一個假設：check runs 真的涵蓋了這次變更。**這個假設不一定成立。**

## 觀察到的失效

**案例一**：一條 base 為 `develop` 的重構 PR，`gh pr checks` 回 exit 0、唯一一列 `CodeRabbit pass`，看起來像通過。實際上 `.github/workflows/ci.yaml` 的 trigger 是 `branches: [master, hotfix]`——develop 不在裡面，lint／unit-test／trivy 一個都沒跑。`gh pr checks` 的 exit code 只回報「現有 check 的狀態」，沒有 check 就沒有 failing check，exit 0，這與「全部通過」在輸出上幾乎無法區分。

**案例二**：PDT-10581（base=`hotfix`）看到 **9 個 check 全綠**，逐支反查發現 `ci.yaml`／`styled-migration-progress.yaml`／`lint-workflows.yaml` 確實涵蓋 `hotfix`，但 `playwright.yml`（E2E）的 filter 只有 `[master]`——**主 CI 綠是真的跑過，但「E2E 有跑」是假的**，同一個 repo 內不同 workflow 的 filter 各自演化，不能查一支就下結論。

**延伸案例**：`preview-deploy.yaml` 用 `types: [labeled, synchronize, reopened]`，沒有 `opened`——剛開好的 PR 完全不會出現 preview 這一列 check，比「缺 synchronize」更難察覺，因為連 workflow 存在的痕跡都看不到。

## 期望產出

release PR body 若要指向 check runs 當「已驗證」的依據，Step 5 寫回前先反查：

```bash
gh pr view <n> --json baseRefName -q .baseRefName
rg -n -A10 "^on:" .github/workflows/*.y*ml | rg -A6 "pull_request"
```

逐支比對 base branch 是否在每支 workflow 的 `branches`／`branches-ignore`／`paths` 過濾條件內，`types` 也要一併看。若某支未涵蓋，body 不能籠統寫「CI 綠」，要**具體點名哪幾支跑了、哪幾支沒跑**，未涵蓋的部分若有本機證據要寫進去（指令＋實際輸出），沒有就明說「此範圍未經 CI 或本機驗證」。

## 判定規則

| 條件 | 判定 |
|---|---|
| body 寫「CI 綠」但未反查各 workflow 的 branch filter 是否涵蓋這條 PR 的 base | **FAIL** |
| 某支 workflow（如 E2E）filter 不涵蓋，body 卻籠統宣稱「測試皆通過」 | **FAIL** |
| body 具體列出哪幾支 workflow 跑了／沒跑，未涵蓋範圍註明本機證據或誠實揭露 | **PASS** |

## 反例警告

不要因為發現一支 workflow 沒涵蓋，就整批宣稱「這條 PR 沒有 CI」——案例二裡主 CI 明明真的跑過。要精確到「哪幾支跑了、哪幾支沒跑」，籠統的全有或全無宣稱兩個方向都是錯的：全綠不代表全跑，某支沒跑也不代表全部沒跑。
