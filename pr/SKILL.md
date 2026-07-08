---
name: pr
description: 總結當前工作、commit、推送並建立或更新 PR。自動將對話脈絡寫入 PR description，確保 reviewer 能快速理解背景。
allowed-tools: Bash, Read, Glob, Grep, Edit, Write, AskUserQuestion, Skill
argument-hint: [PR 號碼或留空建立新 PR]
---

# /pr — 總結工作並維護 PR

請依照以下步驟執行：

## Step 1: 分析當前工作

### 1a. Git 分析

1. 執行 `git status` 和 `git diff`（staged + unstaged）了解**未 commit 的變更**
2. 執行 `git log` 查看近期 commit 風格
3. **關鍵步驟 — 同步遠端並確認 PR 完整範圍：**
   - **先執行 `git fetch origin`**（⚠️ Stale Local Branch 陷阱：本地的 `master`/`main`/`hotfix` 可能落後遠端數十個 commits，忘記 fetch 會把已經 merge 的 commits 算進 PR 範圍，導致 description 嚴重失準）
   - 執行 `git log --oneline origin/<base-branch>..HEAD` 查看 PR 包含的**所有 commits**（永遠用 `origin/<base-branch>` 比較，不要用本地 `<base-branch>`）
   - 執行 `gh pr diff <PR-number> --name-only` 查看 PR 涉及的**所有檔案**
   - 如果已有 open PR，執行 `gh pr view <PR-number> --json body` 讀取現有 description
   - **交叉驗證：** 比對 `git log` 結果與 `gh pr diff` 結果，若差異過大（例如 git log 顯示 50+ commits 但 gh pr diff 僅 1-2 files），以 `gh pr diff` 為準並重新檢視

### 1b. 對話脈絡分析

**回顧本次對話的完整內容**，提取以下 context 項目（這份清單是本 skill 唯一權威清單，Step 5 撰寫 PR description 時逐條比對，不重新定義）：

1. **動機**：使用者最初提出的問題或需求是什麼？
2. **討論過程**：過程中探索了哪些方案？做了哪些比較或調查？
3. **決策點**：哪些地方有多個選擇？最終為什麼選擇這個方案？
4. **放棄的嘗試**：有沒有試過但放棄的做法？為什麼放棄？
5. **隱含知識**：對話中出現但不會反映在 diff 裡的重要 context（例如：調查數據、外部工具比較、效能考量）
6. **業界/學術依據**：技術決策是否引用業界標準（RFC、OWASP 等）或學術研究？
7. **社群共識與反面意見**：對話中是否討論過社群主流看法、已知的反面意見或陷阱？
8. **Ticket 參照**：掃描對話中是否出現 `[A-Z]+-\d+` 編號（如 JIRA-123）、Notion URL、或「Notion Ticket」字樣，記錄找到的編號與票名（供 Step 5 PR 標題使用）

> ⚠️ **常見錯誤**：只看 `git diff` 會遺漏 PR 中其他 commits 的內容；只看 diff 不看對話會遺漏「為什麼這樣做」的決策脈絡。PR description 必須同時反映 **what changed（diff）** 和 **why it changed（對話 context）**。

**Step 1b 結束時建立「Context Manifest」**，供 Step 5 逐條比對：

```markdown
## Context Manifest（Step 1b → Step 5 交接）

| # | Context 類型 | 摘要 | 已寫入 PR Description？ |
|---|------------|------|------------------------|
| 1 | 動機 | 使用者原始需求 xxx | 待比對 |
| 2 | 放棄的方案 | 考慮過 A 但因 B 放棄 | 待比對 |
| 3 | 決策依據 | 選 X 不選 Y，因為 Z | 待比對 |
| ... | ... | ... | 待比對 |

expected_count = N（對話中識別的 context 項目總數）
```

### 1c. 總結

綜合 git 分析 + 對話脈絡，總結本次工作的**目的、做了什麼、為什麼這樣做**。

## Step 2: Quick Review

對所有變更進行快速 code review，檢查以下項目：

### 檢查清單

- **安全性**：是否有敏感資訊外洩（API keys、secrets、.env 內容）
- **正確性**：邏輯是否正確、有無明顯 bug
- **遺漏**：是否有 debug code（console.log、debugger）未清除
- **型別**：TypeScript 型別是否正確、有無 `any` 濫用
- **樣式**：是否符合專案既有 coding style

### 主動安全審查（依 [`rules/security-guidance/skill-integration.md`](../rules/security-guidance/skill-integration.md) 的觸發閘）

判斷變更是否觸及安全敏感面（認證/輸入/endpoint/DB/反序列化/檔案/shell/SSRF/DOM/加密，定義見上述文件）：

- **觸及** → 委派內建 `/security-review` skill 審查當前 branch 的 pending changes，判準以 `~/.claude/claude-security-guidance.md` 為準；findings 併入下方輸出，CRITICAL/HIGH 在「互動確認」中提示先修正
- **未觸及** → 明示「無安全敏感面，跳過」
- **若從 `/update` 串接**：Step 2 已含安全審查，此處不重複委派（去重）

### 輸出格式

用簡潔的表格或清單呈現 review 結果：
- ✅ 沒問題的項目一句帶過
- ⚠️ 有疑慮但不阻擋的項目說明原因
- ❌ 必須修正的問題列出檔案和行號

### 互動確認

Review 完成後，使用 AskUserQuestion 詢問使用者：

- **繼續 PR 流程**：review 結果沒問題，繼續 commit → push → PR
- **先修正問題**：先處理 review 發現的問題，修完再重新執行 `/pr`

如果使用者選擇「先修正問題」，則根據 review 結果逐一修正，修正完後重新從 Step 1 開始。

## Step 2b: 自動修正

若 Step 2 發現可自動修正的問題（dead code、命名、nesting、重複程式碼），委派內建 `/simplify` skill 執行；未發現則跳過。

## Step 2c: Plan 歸檔檢查

**在 commit 之前**，若本次對話涉及的 plan 已完成（`plans/active/*.md` 的 Status 為 `COMPLETED`/`完成`，或所有 Phase/Step 皆標記 `[x]`/`✅`），呼叫 `/plan-archive` skill 歸檔至 `plans/completed/`；沒有已完成的 plan 則跳過。

## Step 3: Commit 當前變更

1. 如果有未 commit 的變更，根據變更內容撰寫 commit message
2. 使用 conventional commits 格式（feat / fix / chore / refactor / test / docs），英文撰寫
3. 確保不要 commit 敏感檔案（.env, credentials 等）

## Step 4: 推送到遠端

有 remote tracking branch 用 `git push`；沒有則 `git push -u origin <branch>` 建立。

## Step 5: 建立或更新 PR

### Base Branch 防護（強制執行）

**PR 的 base branch 禁止直接指向主要的 production branch（如 `master`、`main`）。**

執行以下檢查：

1. 讀取專案的 branch 策略（如果有 `skills/git-workflow/SKILL.md` 或類似文件）
2. 如果專案有定義預設的 base branch（如 `hotfix`、`develop`），自動使用該 branch
3. 如果使用者明確要求指向 `master` 或 `main` → **必須中斷並警告**：
   - 使用 AskUserQuestion 提醒：「依照專案規範，PR 不建議直接指向 master/main。確定要繼續嗎？」
   - 提供選項：「改為 [專案預設 base branch]（推薦）」/「我確定要指向 master/main」
   - 只有使用者明確確認後才能繼續
4. 如果專案沒有特別的 branch 策略，使用 repo 的 default branch

```bash
# 範例：專案規範 base branch 為 hotfix
gh pr create --base hotfix ...

# 禁止（除非使用者明確確認）
gh pr create --base master ...
```

### CHANGELOG 檢查（Release PR 專用）

當 base branch 為 `master` 或 `main` 時，**必須**檢查 CHANGELOG.md：

1. 確認專案根目錄是否有 `CHANGELOG.md`
2. 如果有，檢查最新條目是否涵蓋本次 PR 的變更：
   - 讀取 CHANGELOG.md 最新版本區塊
   - 比對 `git log origin/<base-branch>..HEAD --oneline` 的 commits
   - 如果有 commits 未被記錄在 CHANGELOG 中 → 使用 AskUserQuestion 提醒：
     > CHANGELOG.md 尚未包含以下變更：
     > - `<未記錄的 commit 摘要>`
     >
     > 1. **幫我更新 CHANGELOG** — 自動補上缺少的條目
     > 2. **跳過** — 不更新 CHANGELOG，繼續 PR 流程
3. 如果專案沒有 CHANGELOG.md，跳過此步驟

### PR Title 格式

**依據 base branch 和 ticket 參照決定 PR 標題：**

#### Release PR（base branch 為 master/main）

當 PR 的 base branch 為 `master` 或 `main` 時，標題**必須**使用 Release 格式：

- 格式：`Release vX.Y.Z: <摘要>`
- 範例：`Release v1.18.0: add skill defer + CHANGELOG updates`
- 版本號推斷優先順序：CHANGELOG.md 最新版本 → package.json version → git tag
- 摘要從 PR 包含的 commits 中提取主要變更，簡短描述即可

> 更新既有 PR 時，也必須檢查 title 是否符合此規則，不符合則一併更新。

#### 一般 PR（base branch 非 master/main）

**使用 Step 1b 提取的 ticket 參照決定 PR 標題：**

- **有找到 ticket** → 標題必須包含 ticket 資訊，二擇一：
  1. 標題末尾附上 ticket 編號：`fix(seo): add noindex for empty about page (TICKET-1234)`
  2. 標題包含票名：`fix(seo): [Bug] empty about page should be no-indexed`
- **沒找到 ticket** → 正常標題，不需額外處理

### 判斷邏輯

- 如果提供了 PR 號碼（`$ARGUMENTS`），更新該 PR
- 如果沒有提供號碼，檢查當前 branch 是否已有 open PR
  - 有 → 更新該 PR description
  - 沒有 → 建立新 PR（使用專案規範的 base branch）

### PR Description 格式

PR description 必須包含以下區塊，使用繁體中文撰寫：

```markdown
## Summary

<!-- 1-3 句話說明這個 PR 的目的和背景脈絡，讓 reviewer 30 秒內理解「為什麼要做這件事」 -->

## Context（對話脈絡）

<!-- 最重要的區塊。內容依 Step 1b 的 Context Manifest 逐條寫入，來源優先序：對話脈絡 > git diff > commit messages -->
<!-- 目標：reviewer 不需要問「為什麼這樣做？」就能從這裡找到答案 -->

## Changes

<!-- 按主題分類列出變更，涵蓋 PR 的所有 commits（不只是當次對話的工作） -->
<!-- 用 git log origin/<base-branch>..HEAD 確認完整範圍（必須先 git fetch origin） -->
<!-- 用 git diff origin/<base-branch>..HEAD --diff-filter=D --name-only 確認刪除的檔案 -->
<!-- 每個主題明確標示：新增了什麼、刪除了什麼、修改了什麼 -->

## Test plan

<!-- 測試計畫，checkbox 格式 -->

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

**Manifest 逐條比對：** 撰寫完 Context 區塊後，逐條比對 Step 1b 的 Context Manifest，確保每個項目都已反映；差集（expected_count − actual_written）> 0 的項目必須補齊，不可遺漏。

## Step 6: 確認結果

1. 輸出 PR URL
2. 確認 PR description 已更新
3. 如果有相關的其他 PR（如 feature → develop、develop → master），檢查是否需要同步更新

### 使用方式

/pr # 自動偵測是否有 open PR，沒有就建新的
/pr 7195 # 更新指定 PR 的 description
