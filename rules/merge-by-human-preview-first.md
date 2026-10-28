# Merge By Human, Preview First

Scope: Apply when a plan, skill run, or agent session is about to merge a PR into a branch that deploys to production or a prod-connected cluster (e.g. web-app `hotfix`), or writes a plan step that does so.

## 規則

1. **agent 不可自己 merge 這類 PR**，即使使用者在 AskUserQuestion 選了「照清單執行」。agent 只把 PR 準備到「可 merge」並回報，merge 由人按。
2. **每個 PR 先在 preview 或 staging 做 live 驗證並留證據**，再交人 merge。CI 綠、本機 diff、fresh-context 驗收都不能取代。
3. **stacked PR 沒有 preview**，不能借用上一個 PR 的驗證；要驗就先 retarget 或併成單一 PR。
4. 對 prod 等價環境的查核**只讀**。
5. 證據不齊時，不要把 merge 列成選項。

## Why

2026-10-28 agent 在 AskUserQuestion 列清單後，自己執行了三個 squash merge 進 hotfix，其中一個 PR 沒有任何 preview 驗證，使用者喊停並要求還原。

## How to apply

- `/design`：merge step 一律 `Requires-Approval: true`，Agent 欄寫「使用者」，前置 step 含 preview／staging 驗證。
- `/plan-run`、`dispatch-loop`：推進到 merge step 只回報「已具備 merge 條件」與證據路徑，不跑 `gh pr merge`。
