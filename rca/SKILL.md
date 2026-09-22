---
name: rca
description: 平行 RCA — 接收一則告警（Grafana alert 連結、Sentry issue、CI 失敗 URL），同時派 OBSERVABILITY／HISTORY／KNOWLEDGE／INFRA 四個唯讀調查 agent，再由 SYNTHESIS 合併時間軸、用新證據裁決 agent 間矛盾、給根因＋信心＋falsifier、區分「自己造成該寫回 code 的變更」vs「真缺陷」，並草擬修復 PR 與 rollback checklist。觸發：/rca <url>、「幫這個告警做 RCA」「這個 Sentry issue 根因是什麼」「CI 為什麼掛」且需要跨 metrics／git／KB／infra 交叉比對時。單一 Grafana 告警只要判 benign vs real 用 alert-triage 即可。
allowed-tools: Bash, Read, Grep, Glob, Write, Agent, AskUserQuestion, mcp__sentry__get_sentry_issue, mcp__sentry__get_sentry_event_details, mcp__sentry__list_sentry_issues, mcp__grafana__grafana_api_request, mcp__grafana__alerting_manage_rules, mcp__grafana__query_loki_logs, mcp__grafana__query_prometheus, mcp__grafana__generate_deeplink
argument-hint: <Grafana alert URL | Sentry issue URL/shortId | CI run URL>
---

# /rca — 四路平行調查 → 對抗式彙整

跟 `alert-triage` 的分工：`alert-triage` 是單線流程，只吃 Grafana 告警，只判 benign 還是 real。`/rca` 是調度層：輸入多了 Sentry 和 CI，四路同時調查，彙整時強制做矛盾裁決與 drift 分類。`alert-triage` 的 **KB priors 對照表**和 **grafana MCP 工具 gotcha** 直接沿用（`~/.claude/skills/alert-triage/SKILL.md` Step 2、Tools 段），這裡不重抄。

## 硬規則（每一步都適用）

1. **四路全唯讀**。調查 agent 用 `readonly-verifier`（沒有 Write／Edit），`model: sonnet`。禁止 `kubectl apply/patch/delete/rollout`、`terraform apply/import/state rm`、`git push`、任何 POST／PUT 到 Sentry／Grafana。`terraform plan` 一律帶 `-lock=false -refresh=false`；需要 refresh 時改用 `-refresh-only -lock=false`，而且只讀輸出，不 apply。
2. **subagent 沒有 MCP**（learned `subagents-do-not-inherit-mcp-servers-hand-them-an-http-helper`）。派工 prompt 裡不能寫 `mcp__*`，資料管道一律用 `scripts/obs_http.py`：
   ```
   H=~/.claude/skills/rca/scripts/obs_http.py
   python3 $H sentry  get  'issues/<id>/'                    # issue 詳情
   python3 $H sentry  get  'issues/<id>/events/?full=true'   # 事件（含 release／tags／stack）
   python3 $H grafana get  /api/datasources                 # 找 datasource uid
   python3 $H grafana prom <uid> '<promql>' --start <ISO> --end <ISO> --step 60
   python3 $H grafana loki <uid> '<logql>'  --start <ISO> --end <ISO> --limit 200
   ```
   派工時一併交代：不要 cat 這支 helper、不要印 header、不要印 `~/.claude.json`。
3. **數字不用形容詞**。每一條發現都要附「指令＋關鍵輸出＋時間窗（UTC）」；寫「大量」「偶發」「最近」而沒附數字的，SYNTHESIS 一律當作沒有證據。
4. **反模式護欄**（兩條都是實際踩過的坑）：
   - **不用字面資源名 grep 判斷有沒有被 IaC 管理**。資源名稱常常是 `"${var.env}-${local.name}"`、`for_each`、module output 組出來的。判斷方式依序是：`terraform state list | grep`（權威來源）→ `terraform state show <addr>` → 反查 module／variables／`*.tfvars` 找出名稱是怎麼組的。只有「state 裡找不到，**而且**所有組名路徑都排除了」才能說 unmanaged。以前曾經用 grep 字面名稱，誤判某個 bucket 不歸 terraform 管。
   - **不先讀值就不報 secret**。看到像 key／token 的欄位，要先分辨是 placeholder（`changeme`、`<TOKEN>`、`${...}`、`xxx`、ESO／SOPS 引用、空字串）還是真值。讀值只為判斷類型，報告裡只寫「真值／placeholder／引用」與長度，**不輸出值本身**。沒讀到值就寫 `unverified`。
5. **ownership**：碰到 `smb-*`／`payment-*` 的 helm values、secrets、後端 EC2、他人的 PR 狀態，只調查、只提案，不代修（rules `repo-ownership.md`）。
6. **時區**：一律換算成 UTC 對齊時間軸；台灣時間（+08:00）放在括號裡。

## Step 0 — 正規化告警（主對話做，可以用 MCP）

判斷輸入類型，拉出 **envelope**，寫到 `.verification/<YYYY-MM-DD>/rca-<slug>/00-envelope.md`：

| 輸入 | 怎麼拉 | envelope 必填 |
|---|---|---|
| Sentry URL／shortId | `mcp__sentry__get_sentry_issue`＋`get_sentry_event_details`（最新一筆和最早一筆） | title、culprit、first/lastSeen、count、firstRelease／lastRelease、environment、stack 頂端 5 個 frame、**同 title 的其他 issue**（`list_sentry_issues` 查 `is:unresolved "<title>"`；Sentry 因 fingerprint 拆群時要全列出來） |
| Grafana alert URL | `grafana_api_request` `/api/prometheus/grafana/api/v1/alerts`＋`alerting_manage_rules` get | alertname、labels、rule query 原文、activeAt、datasource uid |
| CI run URL | `gh run view <id> --repo <r> --log-failed \| tail -200`、`gh run view --json headSha,headBranch,createdAt,event` | repo、workflow、branch、headSha、失敗 job／step、錯誤摘要、**前一次綠燈的 sha**（`gh run list --workflow <w> --branch <b> --status success -L 1`） |

envelope 另外要定出：**告警時間窗**（first seen −2h 到 last seen，最長 7d）、**涉及的程式碼路徑**（stack frame、culprit、失敗的測試檔）、**涉及的 repo 與 infra 元件**。這三項是四路共用的範圍，範圍定不出來就先 `AskUserQuestion`，不要讓四路各猜各的。

## Step 1 — 平行派工（同一則訊息送出四個 Agent call）

預計並行 ≥2 個 agent，照全域規則先用 AskUserQuestion 問要不要啟用編隊（附 token 預估：4 路 Sonnet 每路約 60–120k，SYNTHESIS 1 路 Opus 約 100k）。使用者已經在同一次請求裡答應的話就不重問。

每路 prompt 的共同骨架：envelope 全文、硬規則 1–6、`H=` helper 用法、輸出格式（見下）、**「結論先給，≤5 行」**。每路回報格式固定：

```
## <LANE> 結論（≤5 行）
## 發現
| # | 發現 | 時間 (UTC) | 證據指令 | 關鍵輸出（原文節錄） | 信心 H/M/L |
## 沒查到／查不了的
## 我認為其他路會看到什麼（供 SYNTHESIS 對賭）
```

最後一段是刻意設計的：要求每一路預測其他路的結果，SYNTHESIS 才有東西可以對照、找出矛盾。

| Lane | 範圍 | 必做 | 不做 |
|---|---|---|---|
| **OBSERVABILITY** | 告警時間窗前後的 metrics／logs／traces／Sentry events | 事件數的時間分佈（每小時 bucket）、受影響的 release／browser／URL 分佈（Sentry `tags`）、同時段的 error rate／latency／restart 數；每個數字都要附 query | 推論 code 根因 |
| **HISTORY** | 涉及的 repo，近 30 天內動過涉及路徑的 commit | `git fetch` 後 `git log --since=30.days --format='%h %ad %an %s' --date=iso -- <paths>`；把 Sentry release sha／CI 的綠紅 sha 對應到 commit 區間（`git log <good>..<bad>`）；依賴變更（`package.json`／lockfile 的 diff）；列候選 commit 並說明為什麼可疑 | 修 code、checkout 別的 branch（用 `git show`／`git log`，不切 worktree 狀態） |
| **KNOWLEDGE** | `~/Documents/knowledge-base` | 用 BM25 跑 `bm25_retrieve.py "<症狀>"`、`--index wiki/rules/INDEX.md`、`wiki/decisions/INDEX.md`；`grep -rn '\bAD-[0-9]\+'` 找出相關的 AD 編號決策，**原文逐字引用**（檔案:行號）；過去同類的 `reports/`；還要特別找「我們已經決定過了／by design／刻意」的筆記 | 改寫、摘要 AD 原文 |
| **INFRA** | cluster／CDN／IaC 的 live 狀態 | `kubectl config current-context` 先確認 context；nodes／events（spot interruption、Karpenter disruption）、PDB 與 selector 對不上的 orphan、ingress／CF cache rule；`terraform plan -lock=false -refresh=false` 或 `state list` 比對 drift；S3 lifecycle rules；**護欄 4 兩條必遵守** | 任何寫入動作；後端 own 的資源只看不評 |

INFRA 判斷「跟這次告警無關」時，要寫出排除依據（例如：時間窗內 node 事件數為 0，附指令），不能直接留空。

每一路的 prompt 裡都要交代：**回覆前先用 Bash heredoc 把完整報告逐字寫到** `.verification/<date>/rca-<slug>/{1-obs,2-history,3-knowledge,4-infra}.md`。不要靠主對話轉存，因為 teammate 的 idle notification 會截斷長報告（2026-09-22 首跑時就發生過）。主對話只核對檔案存在、行數合理。

## Step 2 — SYNTHESIS（派 1 個 Opus agent，fresh context，只給五份檔案路徑）

交給 SYNTHESIS 的是檔案路徑，不是主對話的摘要，避免主對話的先入為主傳下去。它要依序產出：

1. **合併時間軸**：一張表，欄位是 `時間(UTC) | 事件 | 來源 lane | 證據`。deploy、commit、merge、告警 first seen、數字轉折點、infra 事件都要放進去。
2. **矛盾清單與裁決**：列出每一組「A lane 說 X、B lane 說 Y」（包括某一路的預測和另一路的實測不符）。**裁決只能靠新證據**：SYNTHESIS 自己跑一條唯讀指令（helper／git／kubectl／grep）來解，把指令和輸出寫進去。**禁止**用「A 的信心比較高」「A 的描述比較具體」來裁決。新證據也解不了的，標 `UNRESOLVED`，寫出需要什麼證據才能解。
3. **根因**：一句話講根因，附信心（HIGH／MEDIUM／LOW＋理由），並寫出**唯一一條能推翻它的證據**（falsifier），格式是「如果 `<指令>` 的結果是 `<X>`，這個根因就不成立」。如果寫不出 falsifier，信心最高只能給 LOW。
4. **分類**（必選一項，可以拆 sub-signal）：
   - `SELF-INFLICTED → CODIFY`：有人手動改了 live 狀態（console、`kubectl edit`、CF dashboard），IaC／repo 還沒跟上。處置是**把 live 狀態寫回 IaC 或 repo**，不是把 IaC apply 回去蓋掉它。要先確認這個手動變更是刻意的（KNOWLEDGE 的 AD／決策筆記、commit message、使用者）。
   - `DRIFT → REVERT`：live 狀態偏離，而且這個偏離是錯的，要讓 live 回到 IaC 的定義。
   - `REAL DEFECT → FIX`：程式碼或設定本身有缺陷。
   - `EXTERNAL／BENIGN → RECORD`：第三方、使用者環境（瀏覽器擴充套件等）、或 by-design，只記錄，不開 PR。
   這一步要說明為什麼不是其他三類。**特別注意**：看到 drift 時，預設問題是「這個 drift 是不是該寫進 terraform 的正確狀態」，不是「要不要 apply 回去」。以前吃過虧：把本來該 commit 進 terraform 的 drift 直接當成正常狀態套用了。
5. **修復 PR 草稿＋rollback checklist**：寫成草稿檔，不開 PR（開 PR 要經過 HITL G1）。草稿包含 repo、base branch（`vocus-web-ui` 用 `hotfix`）、改動的 diff 或具體檔案:行號、繁體中文 PR body、驗收方式（要可觀察：哪一條 query 的數字降到多少）。rollback checklist 包含：觸發條件（哪個數字回升到多少）、每一步的指令、每一步的預期輸出、資料／狀態是否可逆、通知誰。

SYNTHESIS 的產出寫到 KB 的 `reports/<YYYY-MM-DD>-rca-<slug>.md`（frontmatter `type: incident-rca`，tags 帶 `rca`），並掛到 `reports/INDEX.md`。PR 草稿放在同目錄的 `.verification/<date>/rca-<slug>/pr-draft.md`。

## Step 3 — 驗收與 HITL

- 派 `readonly-verifier`（fresh context）抽驗：時間軸挑 3 條、矛盾裁決全部、falsifier 指令實際跑一次，逐條標 PASS／FAIL，證據存 `.verification/`。有 FAIL 就回 Step 2 修正。只有零 FAIL，報告的 status 才能寫成 `verified`。
- **G1 開 PR 前**、**G3 動到後端 own 的資源**、**G4 根因信心 LOW**：用 AskUserQuestion 停下來等使用者決定。分類是 `SELF-INFLICTED → CODIFY` 時，另外要問使用者「這個手動變更是不是你們刻意做的」，除非 KNOWLEDGE 已經找到白紙黑字的決策。
- KB gate：回收 agent 之前，要把本次教訓寫到 `_pending/session-<date>-rca-<slug>.md`；可以重複使用的做法寫到 `wiki/learned/`，並掛上 INDEX。

## 交付給使用者的格式

結論先講（≤5 行）：根因、信心、falsifier、分類、下一步要使用者決定的事。附上報告路徑和 PR 草稿路徑。細節等使用者說「展開」再給。
