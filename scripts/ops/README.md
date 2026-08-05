# scripts/ops — 沉澱下來的維運指令鏈

這三支腳本取代的是每個 session 都在重建的 bash 指令鏈。重建本身就是錯誤潛入的地方：
rev-list pipeline 寫壞過一次導致整批判斷失效、刪除條件漏過「有未推送 commit」的 worktree、
6 個殭屍 dev server 跑了 6 天佔住 worktree 刪不掉。腳本的意義是把已經修正過的邊界條件
寫死一次，不再退化。

每個判斷條件的來源都標在腳本裡對應那段的註解（指向 `~/Documents/knowledge-base/wiki/learned/`）。
**要改判斷條件前先讀那段註解** —— 大部分看起來多餘的檢查都對應一次實際事故。

## 共同約定

| 約定 | 說明 |
|---|---|
| dry-run 是預設 | 要實際動手必須明確加 `--apply`。`pr-deploy-trace.sh` 與 `ci-watch.sh` 全程唯讀，沒有 `--apply` |
| 刪除前逐一列路徑 | 不用萬用字元，先印完整清單與「為什麼這個可刪」再動手 |
| 冪等 | 重跑不造成額外損害（目錄已不存在只補 `git worktree prune`） |
| `set -euo pipefail` | pipeline 任一段失敗都會被偵測，不讓失敗偽裝成空結果 |
| 註解寫「為什麼」 | 不只寫做什麼 |

相容性：macOS 內建 **bash 3.2**（無 associative array、無 `mapfile`，BSD sed/awk 語法差異）。
改腳本時不要用 bash 4+ 語法。另注意 bash 3.2 是 byte-oriented，中文字串裡的變數一律寫
`${var}`，否則 `$var、` 會把「、」的位元組吃進變數名（踩過，會噴 unbound variable）。

---

## worktree-cleanup.sh

跨 repo 盤點 / 清理 git worktree。

```bash
worktree-cleanup.sh                                  # 唯讀盤點（預設）
worktree-cleanup.sh --du                             # 加算磁碟用量（慢，node_modules 很大）
worktree-cleanup.sh --fetch --apply                  # 實際移除判定為 remove 的
worktree-cleanup.sh --fetch --apply --kill-blockers  # 連佔用行程一起 kill
worktree-cleanup.sh --root <dir>                     # 覆寫掃描根目錄（預設 ~/Documents）
```

**`--apply` 強制要求 `--fetch`**：沒 fetch 就是拿 stale 的 origin ref 判斷「已合併 / 無未推送」，
等於在錯的資料上做不可逆動作。

判定為 `remove` 需要四關全過：

1. PR 是 MERGED / CLOSED
2. 工作區乾淨（`status --porcelain` 為空）
3. **無未併入 commit** —— `rev-list --count HEAD --not origin/<所有 base>` 為 0。
   這關是關鍵：「已 commit 但未 push」的變更在第 1、2 關都長得像乾淨。
   若 rev-list > 0，再比 PR 的 `headRefOid` 與 worktree HEAD —— 相同代表是 squash/rebase
   造成的假陽性（標為 `0*` + `squash-fp`），不同才是真孤兒。
4. **沒有行程以該 worktree 為 cwd**（dev server 等），除非加 `--kill-blockers`

**永遠不刪 branch。** worktree 目錄刪掉之後 branch ref 仍在，那是誤刪時唯一的救命索；
順手 `git branch -D` 會把它砍斷。這條同時涵蓋 long-lived branch（develop / dev-vN / release-*）
本來就不該刪的情況。

**管線故障偵測**：若 2/3 以上的 worktree PR 狀態查不到，腳本會以退出碼 3 中止而不是把清單交出去。
整排同一個 fallback 值代表 gh 查詢壞掉，不是真的沒有 PR。

退出碼：`0` 正常 / `1` 參數錯 / `2` 前置條件不足 / `3` 偵測到管線故障

## pr-deploy-trace.sh

輸入 PR 編號，回答「這個 PR 現在跑在哪些環境」。全程唯讀。

```bash
pr-deploy-trace.sh 7970
pr-deploy-trace.sh 7970 --repo sosreader/vocus-web-ui
pr-deploy-trace.sh 7970 --no-k8s        # 只查 git / GitHub，不打叢集
pr-deploy-trace.sh --sha 5170cbf1       # 直接用 commit sha 反查
```

四層，每層都可能跟上一層矛盾，不可跳層：

1. PR → **merge commit**（不是 head sha —— image tag 裡的 sha 來自 `GITHUB_SHA`，
   push 到部署分支時那是 merge commit）
2. 那顆 sha 進了哪些 remote branch → 理論上該在哪些環境
3. build / deploy workflow 跑完沒 → image 有沒有推上 registry
4. **叢集實際跑的 image tag 尾巴的 sha** → 唯一能定案的證據

第 3 層不能當結論：`deploy-staging.yaml` 自 2026-04-25 起已無 helm upgrade step，換版由 Flux
掃 ECR 觸發，workflow 綠到 pod 起來實測差 18 分鐘。

**kubectl context**：本機預設 current-context 是 `vocus-prod-3az`（正式站）。腳本所有 kubectl
呼叫一律明帶 `--context`，絕不依賴預設值。改腳本時請維持這條。

環境對照（live 驗於 2026-08-05，來源 `vocus-web-ui/.github/workflows/` 與實跑 kubectl）：

| 分支 | workflow | ECR repo | context | deployment |
|---|---|---|---|---|
| `develop` | `deploy-staging.yaml` | `vocus-web-ui-staging` | `vocus-stg` | `frontend-v1` |
| `develop-v2~v4` | `deploy-staging.yaml` | `vocus-web-ui-staging-v2~v4` | `vocus-stg` | `frontend-v2~v4` |
| `hotfix` | `deploy-hotfix-k8s.yaml` | `vocus-web-ui-hotfix` | `vocus-prod-3az` | `frontend-hotfix` |
| `master` | `build-production.yaml` | `vocus-web-ui-production` | `vocus-prod-3az` | `frontend-prod` / `frontend-prod-base` |

image tag 格式：`<yyyymmddHHMM>-<runNumber>-<sha 前 8 碼>`。

## ci-watch.sh

輪詢 PR / branch 的 CI 直到收斂，紅的時候只摘關鍵指紋。全程唯讀 —— **不會自動 rerun**，
因為「這是假紅」的判定需要人看過再決定。

```bash
ci-watch.sh 7970
ci-watch.sh --branch hotfix
ci-watch.sh 7970 --timeout 2400 --interval 30    # 預設值
```

- **還有 pending 就不下任何結論**。混合畫面裡的 fail 可能屬於前一顆 commit 的 run（歷史殘影），
  腳本會比對 run 的 `headSha` 與本地 HEAD 並標出來。
- 紅了輸出三件事：失敗的 step 名稱（結構化查詢，不倒幾百行 checkout debug log）、
  run 的 headSha / event、以及 log 裡的指紋行（最多 12 行）。
- 附一張「怎麼判」對照表：真失敗 / 併發取消 / runner 被回收 / merge ref 過期。

**預設 timeout 2400 秒（40 分鐘）**：vocus-web-ui 用 ARC 自架 runner，尖峰光排隊就可能吃掉
20 分鐘。用 GH-hosted 的 20 分鐘上限會拿一個「timeout」去當結論回報。

退出碼：`0` 全綠 / `1` 參數錯 / `2` 前置條件不足 / `3` 有 check 紅 / `4` 逾時未收斂。
（`gh pr checks` 自己在「尚未收斂」時 exit 8，那不是失敗；本腳本已把這個語意吃掉。）

---

## 已知限制與 TODO

| 項目 | 狀態 |
|---|---|
| `pr-deploy-trace.sh` 不查 CodePipeline 執行狀態 | **TODO**。`master` 那條是 build 推 `:latest` → EventBridge → CodePipeline `vocus-web-ui-production-ecs-cluster`(V2)，Deploy stage 卡手動核准。要接需要 `AWS_PROFILE`，且 rollback 執行的 source revision 有陷阱（會用「上次成功」的 source 而非當前 HEAD）。判讀方式見 KB `aws-codepipeline-v2-stage-rollback-source-revision.md` |
| `pr-deploy-trace.sh` 的環境對照表寫死 vocus-web-ui | 其他 repo 只會跑到第 1-3 層，第 4 層印「找不到 frontend deployment」。要擴充時改 `K8S_TARGETS` 與 grep 條件 |
| `pr-deploy-trace.sh` 不檢查 pod `startTime` | 只比 deployment 的 image tag。「image 對但 pod 還沒重啟」的窗口需另外看 `get pods -o wide`。未做是因為要正確關聯 label selector，跨環境不一致 |
| `worktree-cleanup.sh` 對 local-only repo（無 remote）的未推送判定 | 顯示 `?` 而非數字。這類 repo 沒有 `origin/<base>` 可比，KB 建議改用 `rev-list --count <base>..<branch>` 對本地 base。**未實作**，目前一律判 `keep`（保守，不會誤刪） |
| `worktree-cleanup.sh` 的 `--du` 預設關閉 | 對含 `node_modules` 的 worktree 跑 `du` 很慢。要磁碟統計才加 |
| `ci-watch.sh` 的 log 指紋 grep 是固定 pattern | 只涵蓋已知的四種形態。新形態的失敗會落到「查不到指紋」，此時退回人工看完整 log |
| 三支腳本都未加自動化測試 | 驗證方式是實跑 dry-run + `bash -n` |
