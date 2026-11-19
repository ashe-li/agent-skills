# gh_samples

真實 `gh` 輸出的去識別樣本，用來確認 CI 證據（`verify-ci-evidence/1`）需要的每個欄位都能由 `gh` 取得。取樣用的 `gh` 版本為 2.86。

去識別方式：owner、repo、branch、URL、PR 編號換成虛構的 `example-org/example-web`、`feat/example-change`、`42`；run ID 與 job ID 換成虛構數字；SHA 換成虛構的 40 位十六進位字串。欄位名稱、巢狀形狀、列舉值（`completed`、`success`、`failure`、`MERGED`）都是原樣。

這些檔案只是格式參考，測試不會把它們當成可通過核對的證據。

## 樣本與產生指令

`repo-view.json`：目前 repo 的身分，用來與 `origin` remote 交叉確認。

```sh
gh repo view --json nameWithOwner,url
```

`pr-view.json`：PR 號、狀態、head branch、head SHA。

```sh
gh pr view <PR 號> --json number,state,headRefName,headRefOid,url
```

`run-list.json`：該 head SHA 上的所有 run。

```sh
gh run list --commit <head SHA> --json databaseId,attempt,headSha,headBranch,workflowName,status,conclusion,event
```

`run-view.json`、`run-view-failure.json`：單一 run 與它的 job。`gh run view --json jobs` 會連每個 step 一起輸出，樣本以 `--jq` 只留下需要的欄位。

```sh
gh run view <run ID> --json databaseId,attempt,headSha,headBranch,workflowName,status,conclusion,jobs \
  --jq '{databaseId,attempt,headSha,headBranch,workflowName,status,conclusion,jobs:[.jobs[]|{databaseId,name,status,conclusion}]}'
```

## 欄位對照

CI 證據欄位與提供它的 `gh` 欄位：

- `repo`：`gh repo view` 的 `url`（正規化成 `host/owner/repo`），PR 的 `url` 也帶同樣資訊
- `pr`：`gh pr view` 的 `number`
- `pr_state`：`gh pr view` 的 `state`（`OPEN`、`CLOSED`、`MERGED`，寫進證據時轉成小寫）
- `pr_head_sha`：`gh pr view` 的 `headRefOid`
- PR 所屬 branch：`gh pr view` 的 `headRefName`
- `runs[].run_id`：`gh run list` 或 `gh run view` 的 `databaseId`
- `runs[].attempt`：`gh run list` 或 `gh run view` 的 `attempt`
- `runs[].workflow`：`workflowName`
- `runs[].head_sha`：`headSha`
- `runs[].status`、`runs[].conclusion`：同名欄位
- `runs[].jobs[].name`、`status`、`conclusion`：`gh run view --json jobs` 內每個 job 的同名欄位

## 引擎該用的指令

`--from-gh` 依序呼叫以下指令，全部是讀取、全部以 argv 執行，PR 號與 run ID 只接受純數字：

1. `gh repo view --json nameWithOwner,url`
2. `gh pr view <PR 號> --json number,state,headRefName,headRefOid,url`
3. `gh run list --commit <headRefOid> --json databaseId,attempt,headSha,workflowName,status,conclusion`
4. 對上一步的每個 run：`gh run view <run ID> --json databaseId,attempt,headSha,workflowName,status,conclusion,jobs`

`gh run list` 與 `gh run view` 回傳的是每個 run 的最新一次 attempt。要取得較早的 attempt，`gh run view` 另有 `--attempt <n>`。

## 已知限制

- 取樣的 repo 沒有出現過重跑（attempt 大於 1）與尚未完成（`queued`、`in_progress`）的 run，這兩種情形的欄位值依 GitHub 文件推定，`attempt` 與 `status` 欄位本身已在樣本中確認存在。
- `gh pr view` 的 `headRepository` 在 fork 情境下才有意義，引擎不依賴它。
