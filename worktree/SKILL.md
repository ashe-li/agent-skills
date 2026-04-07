---
name: worktree
description: Git worktree 生命週期管理 — 建立、列出狀態（含 PR 進度）、清理已 merge 的 worktree
allowed-tools: Bash, AskUserQuestion
argument-hint: [status|create <name>|cleanup|prune]
---

# /worktree — Git Worktree 生命週期管理

管理 worktree 的建立、狀態查詢、清理。統一存放位置為 `~/Documents/<repo>-<name>`。

## 子指令解析

從 `$ARGUMENTS` 判斷子指令：

| 輸入 | 子指令 |
|------|--------|
| （空）或 `status` | status |
| `create <name>` | create |
| `cleanup` | cleanup |
| `prune` | prune |

---

## 共用：偵測 Repo 資訊

所有子指令開始前，先取得 repo 資訊：

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
REPO_NAME=$(basename "$REPO_ROOT")
```

若不在 git repo 中，提示使用者切換到 git repo 目錄。

---

## status（預設）

列出所有 worktree 及其狀態。

**步驟：**

1. `git worktree list --porcelain` 取得所有 worktree
2. 排除 main worktree（即 `REPO_ROOT` 本身）
3. 對每個 worktree：
   - 取得路徑和分支名稱
   - `gh pr list --head <branch> --state all --json number,state,mergedAt --limit 1` 查 PR 狀態
   - `du -sh <path>` 取得磁碟用量
   - 判斷路徑類型：
     - `~/Documents/<repo>-*` → 標準路徑（無標記）
     - `.claude/worktrees/` → `[legacy]`
     - `/tmp/` 或 `/private/tmp/` → `[non-standard]`
     - 其他 → `[non-standard]`

4. **一致性檢查**（依據：DAMA-DMBOK Completeness — manifest vs. actual set difference）：
   - 對每個 `git worktree list` 回傳的路徑，執行 `test -d <path>` 確認目錄實際存在
   - 若路徑在 git 清單中但目錄不存在 → 標記 `[orphan]`，並在輸出最後顯示警告：
     ```
     警告：偵測到 N 個孤立 worktree metadata（目錄已不存在但 git 仍有紀錄）
     執行 `/worktree prune` 清理孤立 metadata。
     ```

**輸出格式：**

```
Repo: <repo-name>  |  Base: <current-branch>  |  N worktrees (+ main)

Path                                    Branch                          PR      State    Disk
─────────────────────────────────────────────────────────────────────────────────────────────
~/Documents/<repo>-<name>               worktree-<name>                 —       no PR    196M
~/Documents/<repo>-<name2>              feat/<name2>                    #1234   MERGED   1.8G
.claude/worktrees/<name3>               worktree-<name3>                #5678   OPEN     800M  [legacy]

Tip: 有 MERGED/CLOSED 的 PR → `/worktree cleanup`
```

> Tip 行只在有可清理 worktree 時顯示。

---

## create <name>

建立新 worktree。

**步驟：**

1. 從 `$ARGUMENTS` 解析 name（`create` 後的部分）
2. 計算路徑：`~/Documents/<repo>-<name>`
3. 計算分支名：`worktree-<name>`
4. 檢查路徑是否已存在（`test -d`）→ 已存在則提示並中止
5. 檢查分支是否已存在（`git branch --list`）→ 已存在則提示並中止
6. 使用 AskUserQuestion 詢問 base branch：

   > 從哪個分支建立 worktree？
   >
   > 1. **<current-branch>**（目前分支）
   > 2. **master**
   > 3. **其他**（請指定）

7. 建立 worktree：
   ```bash
   git worktree add -b worktree-<name> ~/Documents/<repo>-<name> <base-branch>
   ```
8. 輸出結果：
   ```
   Worktree 已建立：
   - 路徑：~/Documents/<repo>-<name>
   - 分支：worktree-<name>
   - Base：<base-branch>

   開新 Claude Code session 在該目錄開發：
   cd ~/Documents/<repo>-<name> && claude
   ```

---

## cleanup

自動偵測並清理已 merge/closed PR 的 worktree。

**步驟：**

1. `git worktree list --porcelain` 列出所有非 main worktree
2. 對每個 worktree 判斷是否符合清理條件：
   - 先執行 `gh pr list --head <branch> --state all --json number,state,mergedAt --limit 1` 查詢 PR 狀態
   - 若 `gh` 指令失敗（非零 exit code）或回傳空結果 → PR 狀態標記為 `[unknown]`，**不靜默跳過**，仍列入清理候選並在表格中顯示 `[unknown]`（依據：ITIL CMDB Reconciliation — 查詢失敗需明確標記，不得隱式忽略）
   - PR 狀態為 MERGED 或 CLOSED → 符合清理條件
   - PR 狀態為 `[unknown]` → 列入清理候選，表格標記 `[unknown]`，讓使用者自行判斷
   - 或無 PR 且分支已 merged into base（`git branch --merged <base> | grep <branch>`）
3. 對符合條件的 worktree，檢查髒目錄：
   - `git -C <path> status --porcelain` 若有輸出 → 標記 `[dirty]` 並警告
4. 若無可清理的 worktree → 提示「沒有需要清理的 worktree」並結束
5. 顯示清理清單，使用 AskUserQuestion 確認：

   > 以下 worktree 符合清理條件：
   >
   > | Path | Branch | PR | State | Disk | Note |
   > |------|--------|----|-------|------|------|
   > | ~/Documents/<repo>-<name> | worktree-<name> | #1234 | MERGED | 800M | |
   > | .claude/worktrees/<name2> | worktree-<name2> | — | merged | 200M | [legacy] |
   > | ~/Documents/<repo>-<name3> | worktree-<name3> | #5678 | CLOSED | 1.2G | [dirty] ⚠️ |
   >
   > 確認清理？（dirty worktree 將被跳過）
   >
   > 1. **確認** — 清理上述 worktree（跳過 dirty）
   > 2. **全部清理** — 包含 dirty worktree（未 commit 變更將遺失）
   > 3. **取消**

6. 依使用者選擇執行：
   ```bash
   git worktree remove <path>
   git branch -d <branch>  # 若分支已 merged
   ```
7. **操作驗證**（依據：DAMA-DMBOK Completeness — expected vs. actual count）：
   - 刪除後重新執行 `git worktree list --porcelain`，確認已刪除的路徑不再出現於列表
   - 逐條對照清理清單（manifest）：

     | Path | 預期結果 | 實際結果 |
     |------|----------|----------|
     | ~/Documents/<repo>-<name> | 已移除 | PASS / FAIL |

   - 若任何路徑仍在列表中 → 輸出 `[FAIL]` 並提示手動執行 `git worktree prune`
8. 報告結果：
   ```
   已清理 N 個 worktree，回收 X 磁碟空間。
   驗證：N/N 個 worktree 已從 git 列表移除。
   ```

---

## prune

清理孤立 worktree metadata（worktree 目錄已被手動刪除但 git 紀錄仍在）。

**步驟：**

1. `git worktree list --porcelain` 檢查每個 worktree 路徑是否存在
2. 若有孤立 metadata，顯示清單
3. 執行 `git worktree prune`
4. 報告清理結果

---

## 設計原則

- **Repo-agnostic**：用 `git rev-parse` 偵測 repo，不 hardcode
- **命名慣例**：路徑 `~/Documents/<repo>-<name>`，分支 `worktree-<name>`
- **確認閘門**：cleanup/create 前都用 AskUserQuestion 等待確認
- **髒目錄保護**：cleanup 預設跳過有未 commit 變更的 worktree
- **Legacy 標記**：status 輸出標記非標準路徑，鼓勵遷移至標準路徑
- **Manifest-driven 驗證**：cleanup 後必須重新 list 比對（expected set ≡ actual set）；status 交叉驗證 git metadata 與實際目錄（依據：DAMA-DMBOK Completeness）
- **Fail-explicit**：`gh` 查詢失敗時標記 `[unknown]`，不靜默跳過（依據：ITIL CMDB Reconciliation）
