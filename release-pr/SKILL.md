---
name: release-pr
description: 為 release PR（如 hotfix → master / develop → master）自動產生標題 + description。掃描 base...head 的 commit 範圍，依 Conventional Commit prefix 分類為 Features / Bug Fixes / Improvements，輸出繁體中文 release notes，並寫回 PR 標題與 body。觸發：使用者說「release pr」「寫 release pr」；**或**要為 base 是 master/main、head 是 hotfix/develop/release-* 的 PR 寫標題或說明——即使沒講「release pr」三個字也適用，例如「這批要上線的 PR 幫我寫說明」「發版說明」「hotfix 合回 master 那個 PR 的 description」。不適用 feature PR（那走 /pr）。
allowed-tools: Bash, Read, AskUserQuestion, Agent, Skill
argument-hint: [PR 號碼或 PR URL]
---

# /release-pr — Release PR 標題 + Description 產生器

為 release PR（彙整多個已 merge feature 的「往 master 發版」PR）自動產生**標題**與**description**並寫回。

適用情境：`hotfix → master`、`develop → master`、`release/* → master` 這類把一段期間累積的變更發版的 PR。**不是** feature PR（feature PR 用 `/pr`）。

## Step 0：取得目標 PR

- 引數有給 PR 號碼 / URL → 用它。
- 沒給 → `gh pr list --repo <repo> --state open` 找出 head 為 hotfix/develop/release 的 release PR；若多筆，用 AskUserQuestion 讓使用者選。
- repo 從引數 URL 推斷，或當前目錄 `gh repo view --json nameWithOwner`。

```bash
gh pr view <PR> --repo <repo> --json number,title,body,baseRefName,headRefName,state,url
```

## Step 1：取得 commit 範圍（關鍵 — 用遠端比較，不用 local）

⚠️ Local base branch 可能落後遠端數十個 commit，務必用 GitHub compare API 取「真正只屬於這個 PR」的範圍：

```bash
gh api repos/<repo>/compare/<base>...<head> \
  --jq '.commits[] | .commit.message | split("\n")[0]'
```

需要完整 message（取 feature 細節寫進 notes）時，對重要 commit 再撈 full body：

```bash
gh api repos/<repo>/compare/<base>...<head> --jq '.commits[].commit.message'
```

## Step 2：分類

**忽略 merge commit**（`Merge pull request ...` / `Merge branch ...` / `Merge remote-tracking ...`）— 它們不是內容，只是合併動作。從非 merge commit 的 Conventional Commit prefix 分類：

| Prefix | 歸類 |
|---|---|
| `feat` | **Features** |
| `fix` | **Bug Fixes** |
| `perf` / `refactor` / `chore` / `docs` / `ci` / `build` / `style` | **Improvements** |

判斷原則：
- `refactor` 若實質是新行為（如串接新 vendor API）可放 Features，由語意判斷而非死守 prefix。
- 同一 feature 的多個 fixup commit 合併成一條，不要逐 commit 列。
- 盡量帶上對應的 feature PR 編號（從 merge commit 的 `#NNNN` 對應回去），格式用 `#NNNN`（同 repo auto-link 正確）。
- 每條一句話講清楚「改了什麼 + 為什麼 / 效益」，不要只貼 commit headline。
- **`fix(` 不蘊含「線上有 bug」**：若該 commit 修的是同批次前面 commit 剛引入的問題，那個問題從未上線，放進 Bug Fixes 會讓 reviewer 誤判線上事故、觸發不必要的影響評估與客訴回溯。判別器見 Step 2.5。

## Step 2.5：對 diff 查核（強制，不可略過）

Step 1 拿到的是 **commit message**，不是事實。commit 自述會錯，而且**寫得越詳實越容易被照抄**。先拉一次檔案清單當共同底稿：

```bash
gh api repos/<repo>/compare/<base>...<head> \
  --jq '.files[] | "\(.status)\t+\(.additions)/-\(.deletions)\t\(.filename)"'
```

拿這份清單跑三個檢查（成本都是零，不必讀任何 patch）：

1. **否定式敘述**——commit 說「不動 X」「不新增 X」時，其真值範圍是**寫下它的那個 PR 當下**，release notes 的 scope 是整個批次。X 只要出現在檔案清單就直接推翻；正確修法是補回 scope 限定詞（「本 PR 不…；後續 #NNNN 才…」）而非刪句。**跨 PR 並排後才浮現的矛盾同理**：整份 body 寫完通讀一次，專找互斥的兩句。
2. **本機執行結果數字**——commit 尾段的「N unit suites / M tests 綠、build 成功」在 diff 裡永遠查不到，且後續 PR 進版後必然失效。**不轉載**，改指向本 PR 的 check runs；要保留品質訊號（如 mutation testing 結果）就標明「PR #NNNN 當時的本機驗證」。
3. **批次內自我修正**——`.files[] | select(.filename=="X") | .status` 為 `added` ⇒ 該檔在 base 根本不存在 ⇒ 檔內所有「修復」必然都是批次內自修，移出 Bug Fixes、降級成對應 Improvements 條目的括號註記並明寫「批次內自我修正，未上線」。
4. **分類與檔案範圍矛盾**——`fix:`／`feat:`／`refactor:` 等 prefix 宣稱的分類，與該 commit 實際觸及的檔案清單矛盾時（例：訊息說「修正 X」但檔案清單全是新檔、或改動範圍遠超訊息描述），以 diff 為準重新分類，不採信 prefix 字面；判準對齊 `evidence-gate` skill 第 2 節 Diff-Grounding 規則。

**驗證「彙整了哪 N 個 PR」**：取那 N 個 merge commit 的檔案聯集，與 compare 的檔案數比對，差額必須逐項解釋得通（base 本就沒有的檔，其 delete 不會出現在 compare 裡）。

大型批次（>20 commits 或 >40 檔）派一個 agent 全包查核，prompt 必含兩句：**「commit message 的自述不能當證據，commit 與 diff 衝突時 diff 是真相」**、**「逐條給 正確／錯誤／無法佐證 三分類，diff 沒有直接證據一律歸『無法佐證』，不要為了讓 body 看起來對而放寬」**。

（依據：vocus-web-ui #7969 實測，28 commits / 64 檔，~62 條 claim 錯 2 條、誤導 5 條，且**檔案路徑與符號名稱 0/30 錯**——查核預算要放在否定式敘述與跨 PR 一致性，不是路徑。詳見 KB `wiki/learned/release-notes-transcribed-from-commits-inherit-their-errors.md`、`intra-batch-self-correction-is-not-a-bug-fix.md`）

## Step 3：產生 description

沿用該 PR 既有 template 結構（通常是 `# Features` / `# Bug Fixes` / `# Improvements`，以 `---` 分隔）。若原 body 是空 template，填入分類結果即可。**保留原有的 section 標題語言與分隔線格式。**

每條 bullet 用 `- **重點**（#PR）：說明`。

## Step 3.5：範圍相稱性擋門（scope proportionality）

Step 3 寫完，送進 Step 4 之前，對 body 的**每一個段落**問一句：

> **reviewer 為了決定「要不要核准並部署這次變更」，需要知道這件事嗎？**

- 需要 → 留下（改了什麼、會不會改變 serving 行為、何時生效、怎麼回滾、部署後看什麼）
- **「出事時／查證時才需要」→ 外連，不內嵌**（完整證據表、根因推導、機制教學、對其他文件的更正）

判準的鋒利處：被外連的內容**不是不重要，而是它的完整版本已經存在別處**（feature PR、KB 報告、runbook）。在 release PR 再寫一次只會製造第二個維護點與第二個會過期的副本。

**這不是「越短越好」。** 修剪的對象是與核准決策無關的內容，不是長度本身——64 檔的批次寫 7897 字元完全正當。

### 次指標：聞味道用，不是硬上限

body 字元數應隨改動規模**次線性**成長。既有房規（2026-09-06 實測）：

| PR | 檔案 | body 字元 | 每檔字元 |
|---|---:|---:|---:|
| #7969 | 64 | 7897 | 123 |
| #8117 | 8 | 3108 | 389 |
| #8121 | 3 | 2658 | 886 |
| #8124 | 1 | 1053 | 1053 |

**「每檔字元」顯著高於同規模鄰居就停下來重跑主指標。** 單檔但語義極重的變更（feature flag、schema migration、安全修補）可以合理偏高，但要說得出理由。

### 為什麼需要這道關卡

失效模式不是判斷力問題，是**位置壓力**：作者剛做完調查、脈絡都在手上，而 PR body 是離手前最後一個可以傾倒的地方。**傾倒的動機來自作者的狀態，與讀者的需求無關。**

同構前例：`knowledge-base/reports/2026-08-19-alert-description-bloat-audit-and-rewrite-proposal.md` —— 106 條 Grafana rule 裡 15 條把 RCA 全文塞進每次 firing 都會整份推進 Slack 的 `description`。該報告的結論可直接移植：「根本問題不是寫太多，是寫錯地方。」

實測回歸：#8124（1 檔／+23−7）初版 body **3993 字元**，含四個完整版本已存在於 #8123 與 KB 報告的區塊（P95 證據表、因果推導、CodePipeline 機制、對某份 KB 文件的更正），修正後 1053 字元。

### 執行方式（不是「參考」，是要跑的）

**送進 Step 4 之前，讀 `fixtures/coverage.md`，對每一條列出的 failure mode 逐條自檢，並把結果寫成表輸出：**

```
| fixture | failure mode | 本次是否命中 | 依據 |
|---|---|---|---|
| 01 | scope bloat | 否 | 每檔字元 1053，落在區間；四個可外連區塊均已外連 |
| 04 | commit message 照抄 | 否 | 每條 claim 都溯源 diff，非 commit 自述 |
| …  | … | … | … |
```

**任一條命中就回 Step 3 改寫，不得帶著命中項進 Step 4。**

`fixtures/` 若不在本機（`npx skills` 的快照可能只帶 `SKILL.md`），從 repo 取：
`https://github.com/ashe-li/agent-skills/tree/main/release-pr/fixtures`。**取不到就明講「golden set 未取得，本次未做涵蓋率自檢」，不要跳過還宣稱做了。**

> **這一層是判讀，不是機械檢查。** CI 的 `scripts/check_release_fixtures.py` 只驗 golden set 自身的完整性（manifest 對得上檔案、有 negative-control、新增 Step 必須同步動 fixtures），**它驗不了「這次的取捨對不對」**。兩層的強度不同，不要把 CI 綠當成本步驟做過了——同構失效見 KB `ci-green-doesnt-mean-your-new-test-ran-check-collected-count`。

## Step 4：產生標題

格式：`Release：<主軸1>、<主軸2>、…`

- 從 Features + 重大 Improvements 抽 3-5 個主軸，用頓號連接。
- 若使用者偏好版本號 / 日期，可改 `Release YYYY-MM-DD：…`（不確定時用 AskUserQuestion 問一次，之後沿用偏好）。

## Step 4.5：寫回前的擋門（強制）

Step 3、4 的 title + body 進 Step 5 之前，派一個 **fresh-context** `Agent`（`subagent_type: general-purpose`，需 Bash 跑 `gh`/`git`）重跑一次 Step 2.5 的四個查核——不是新規則，是換一個沒看過草稿產生過程的人重驗一次（核心紀律 #2「驗證不自驗」；Step 2.5 只在 >20 commits 時才派 agent，且屬產出前自查）。

本步驟即 `evidence-gate` skill 的 caller 落地（該 skill 第 6 節 Caller 呼叫契約已列本步驟為對應實作）；可直接 `Skill({ skill: "evidence-gate" })` 取用其 Claim Schema（第 1 節）與 Fact-Checker 派遣模板（第 4 節），不必重新定義格式。

**輸入**：draft title/body 全文、PR 號、repo。

**輸出**：每條 claim 一列 `CLAIM | 驗證指令 | 實際結果 | 通過?`。base/head/state 一律現查 `gh pr view <PR> --repo <repo> --json baseRefName,headRefName,state`，不沿用記憶或先前對話推斷的值。

**擋門**：任一列「通過?=否」就停手，把未過的列回報，**不自動寫回 PR**；修正後重跑本關卡，全過才進 Step 5。

## Step 5：寫回並驗證（write-then-verify）

```bash
gh pr edit <PR> --repo <repo> --title "<title>" --body-file /tmp/release-body.md
gh pr view <PR> --repo <repo> --json title,body --jq '.title, .body'
```

務必 `gh pr view` 比對標題與 body 實際生效（tool success ≠ 已生效）。

## 規則對齊

- 標題 + body 一律**繁體中文**；Conventional Commit prefix 與技術 keyword 保留英文。
- 外部 / 跨團隊 ref 用全名格式；同 repo PR 編號用 `#NNNN`。
- 不臆造未在 commit 範圍內的變更；分類有疑義時據 commit 語意判斷，必要時問使用者。
