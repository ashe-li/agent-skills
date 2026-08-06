---
name: ship-ticket
description: 自主 ticket-to-PR pipeline — 貼 Notion ticket URL（逗號分隔可多張），每張票在獨立 worktree + 背景 agent 跑完 repro-first gate → RCA/fix → PR + preview 驗收 → CI babysit → KB 沉澱，結尾輸出總表。觸發：「ship 這張票」「這幾張票修到 PR」或 /ship-ticket <url>。
allowed-tools: Bash, Read, Write, Edit, Agent, AskUserQuestion, Skill, TaskCreate, TaskUpdate, TaskOutput
argument-hint: <Notion URL>[, <Notion URL>...] [--repo <path>] [--base <branch>] [--max-parallel N]
---

# /ship-ticket — 自主 ticket-to-PR pipeline

每張 Notion ticket 一條獨立 lane（worktree + 背景 agent），端到端跑到 PR + CI 綠 + 證據落地，中途不 check-in。主對話只當 orchestrator：解析、派工、抽查驗收、彙總，不下場實作。

## 硬規則（整條 pipeline 適用）

1. **永不 merge**。AskUserQuestion 僅限四種不可逆決策：merge、改 PR base、force-push、刪 remote branch（前三者為使用者 2026-08-05 明列，第四者同屬不可逆範疇的延伸，正常流程不會觸發）。其餘一律自主推進。
2. **Repro-first gate**：修復前必須親眼看到 repro FAIL 並留標註證據。repro 是綠的 → 該 lane 標「前提未證實」終止，不做任何修復。接受或否決前提都要有標註證據，禁止空口裁定。
3. **`.evidence/` 永不 stage、任務結束不刪**（diagnostics 規則）。commit 只 stage 明確列出的路徑。
4. **PR title/body 一律繁體中文**；body 嚴格由 `git diff <base>...HEAD` 導出，不得寫 diff 裡沒有的東西。外部系統引用用全名格式（`Linear PDT-8949`），不用裸 `#數字`。
5. **Escalation 不阻塞**：任一 lane 停止（前提未證實 / 迭代上限 / self-heal ×2 失敗 / preview 起不來）→ 記入報表、其他 lane 繼續，結尾一次呈報。
6. **回收前 KB gate**：每個 agent 回收前，其教訓必須已寫入 `wiki/learned/`（掛 INDEX）或 lane manifest；未落地不回收。

## Repo profiles

| Repo | base | preview URL | dev API | 備註 |
|---|---|---|---|---|
| vocus-web-ui | `hotfix`（記憶規則，不可默改） | `https://<PR#>.preview.vocus.cc` | 一律指 staging（禁指 prod） | preview env 在 merge 時拆除 → S3 驗收必在 merge 前 |
| 其他 repo | AskUserQuestion 例外：非不可逆，但 base 不明時以 `--base` 為準，未給則用 repo 預設分支並在報表註記 | 無已知 pattern → S3 fallback：local dev server headed 驗收，報表註記「無 preview env，驗收於 local」 | 同左 | |

## Step 1：解析引數

1. 從 `$ARGUMENTS` 切出 Notion URL 清單（逗號分隔）與 flags：`--repo`（預設 `~/Documents/vocus-web-ui`）、`--base`（預設查上表）、`--max-parallel`（預設 3，**硬上限 3**：N>3 一律箝制回 3 並在開跑訊息註明原因——KB 前科「並行 build/headed browser 搶 CPU」；要放寬需使用者修改本檔，不提供 flag 繞過）。
2. URL 網域白名單與 pageId 抽取沿用 `/notion-plan` Step 1（dot-boundary 比對 `notion.com` / `notion.so` / `notion.site`）。
3. 每張票配 lane index（0-based）。ticket-id（如 `PDT-10618`）與 slug 在 S0 擷取後才定案；worktree 先用暫名 `lane<idx>-<pageId前8碼>`，S0 完成後 orchestrator 以 `git worktree move` + `git branch -m` 改為正式名。

## Step 2：開跑前置（orchestrator 依序執行）

1. **印出 token 預估**（不問）：`張數 × 350–550k`，附各 stage 分解。`/ship-ticket` 的呼叫本身即編隊授權——此條刻意覆寫全域「並行 ≥2 subagent 需 AskUserQuestion」規則（使用者 2026-08-05 核准本設計時已同意）。
2. `git -C <repo> fetch origin`。
3. **逐票建 worktree**（sibling 慣例，不用 `.claude/worktrees/`）：
   ```bash
   git -C <repo> worktree add -b fix/<ticket-id>-<slug> ~/Documents/<repo名>-<slug> origin/<base>
   ```
   路徑或分支已存在 → 該 lane 直接標 ESCALATED（collision）記入報表，不覆蓋、不改名硬上。
4. **`.evidence/` 排除**（每 repo 一次即可）：
   ```bash
   echo ".evidence/" >> "$(git -C <repo> rev-parse --git-common-dir)/info/exclude"
   ```
5. **依序（不並行）跑各 worktree 的 install**：KB 前科「並行 build/install 搶 CPU 害 pre-commit 卡死逾時」。install 失敗且錯誤含 husky/prepare → 先跑 `bm25_retrieve.py "worktree husky prepare git file"` 撈既有 workaround 再重試一次。
6. **配發 port**：lane idx → `PORT=3001+idx`，記入 lane manifest。
7. 建 run manifest：`<scratchpad>/ship-ticket-<YYYYMMDD-HHMM>/manifest.md`，每 lane 一節（狀態機欄位見 Step 3），orchestrator 全程維護。

## Step 3：Lane 狀態機與派工拓撲

```
SETUP → S0-S2+PR (Implementer, Opus, bg) → SPOTCHECK (主對話)
      → S3 (Fresh Verifier, Sonnet, bg) → S4 (CI Babysit, Sonnet, bg) → DONE
任一節點可轉出：PREMISE_UNVERIFIED | ESCALATED（皆為終態，附證據路徑）
```

- 同時活躍 lane ≤ `--max-parallel`；超過的票排隊，有 lane 到終態才補位。
- Implementer 與 Verifier **必須是不同 agent**（驗證不自驗）：Verifier 拿不到實作過程脈絡，只拿 worktree 路徑、PR、preview URL、spec 路徑、ticket brief、Figma ref（若有）。
- 三類 agent 都用 Agent tool 背景派出，`model` 明帶（Implementer=opus，Verifier/Babysit=sonnet）。
- 每個 agent 回傳結構化 manifest 區塊（狀態、產物路徑、證據路徑、教訓落點），orchestrator 據此更新 run manifest 再推進狀態機。

## Step 4：Worker prompt 模板

佔位符：`{{WORKTREE}}` `{{PORT}}` `{{TICKET_URL}}` `{{BASE}}` `{{PREVIEW_URL}}` `{{BRANCH}}` `{{PR}}` `{{BRIEF_PATH}}` `{{SPEC_PATH}}`。

### 4a. Implementer（Opus，S0–S2 + 開 PR）

```
你在 {{WORKTREE}}（獨立 git worktree，分支 {{BRANCH}}，base {{BASE}}）修一張 ticket，端到端到開出 PR。dev server 一律用 PORT={{PORT}}，API 指 staging（禁 prod）。開工先跑
python3 ~/Documents/knowledge-base/tools/skill-retriever/bm25_retrieve.py "<ticket 症狀關鍵字>" 撈既有經驗。

S0 擷取：照 ~/.claude/skills/notion-plan/SKILL.md Step 0–2 用 playwright-cli 抓 {{TICKET_URL}}（不用 WebFetch，Notion 在 blocklist）。產 ticket brief 寫入 {{WORKTREE}}/.evidence/S0-brief.md：
- repro 步驟、expected vs actual、影響環境 URL
- 症狀清單：把 ticket 描述的每一個 user-visible 症狀列成獨立 checklist 項
- ticket-id（PDT-xxxxx）與建議 slug
brief 缺 repro 步驟 → 回報 ESCALATED（brief-incomplete）附已抓到的內容，停止。

S1 前提驗證（硬 gate）：
1. 先對回報環境做 read-only probe（不登入、不寫入、≤10 頁）：console error、關鍵 DOM 狀態、network。probe 產物存 .evidence/S1-premise/。
2. 寫一個會重現該症狀的 failing test（優先 repo 既有測試框架的 spec；UI 行為用 Playwright headed assertion）。spec 路徑記入 manifest。
3. 實跑，必須親眼看到 FAIL。證據存 .evidence/S1-premise/：test output 全文 + 截圖 + EVIDENCE.md（標註每份證據對應哪個症狀、expected 與 observed 各是什麼）。
4. repro 是綠的 → 回報 PREMISE_UNVERIFIED，附上述標註證據，立即停止。不修任何東西、不開 PR。

S2 RCA + fix：
- 用 live 證據定根因（實跑、加探針、讀 network/console），不接受純 config/文件推論——推論性結論標 provisional 並用 live 證據補實。
- 實作修復。品質門檻：function <50 行、檔案 <800 行、無 emoji、偏好 immutability、新邏輯測試覆蓋 ≥80%。
- 迭代直到全部成立：repro test 綠 + 全套測試綠 + typecheck 綠 + lint 綠（指令從 package.json scripts 讀，不假設名稱）。
- 全綠後必須 headed 重跑「原始 user-visible repro」（照 S0 brief 的步驟操作真實頁面，不是只跑 test），逐條檢核症狀清單並在 .evidence/S2-fix/EVIDENCE.md 標註每條 PASS/FAIL + 截圖。任一症狀殘留 → 回頭繼續 RCA（可能有第二、第三根因），禁止標 out-of-scope 自行結案。
- 迭代上限 5 輪 → 回報 ESCALATED（rca-exhausted）附目前假設、已排除項、證據，停止。
- headed 跑之前驗 dev server 歸屬（KB 前科：Playwright reuseExistingServer 撿到別的 worktree 的 server）：
  lsof -iTCP:{{PORT}} -sTCP:LISTEN 確認 listener 的 cwd 是 {{WORKTREE}}；spec baseURL 綁死 localhost:{{PORT}} 並在跑前 curl 驗 host 有回應。

S3a 開 PR：
- commit：只 stage 明確列出的路徑（絕不含 .evidence/），依主題分組，繁中 commit message，不 --no-verify。
- push 自己的分支後開 PR：base={{BASE}}，title/body 繁中，body 嚴格由 git diff {{BASE}}...HEAD 導出（改了什麼、為什麼、如何驗證），引用 ticket 用全名（Linear/Notion PDT-xxxxx），不用裸 #數字。
- gh pr create 後必跑 gh pr view 驗 title/body 落地（write-then-verify）。
- 輪詢 preview URL（間隔 30s、上限 15 分鐘）直到 HTTP 200；逾時 → 回報 ESCALATED（preview-timeout），PR 保留。

收尾（回收 gate）：可復用教訓寫 ~/Documents/knowledge-base/wiki/learned/（掛 INDEX）；沒有普遍性教訓則明寫「無」。回傳 manifest 區塊：
STATUS: READY_FOR_VERIFY | PREMISE_UNVERIFIED | ESCALATED(原因)
TICKET_ID / BRANCH / PR: <number+url> / SPEC: <路徑> / EVIDENCE: <各 stage 路徑>
SYMPTOMS: <逐條 PASS/FAIL> / LEARNED: <檔名或「無」>
```

### 4b. Fresh Verifier（Sonnet，S3 驗收；不給實作脈絡）

```
你是 fresh-context 驗收者，對 PR {{PR}} 做部署環境驗證。你只有：worktree {{WORKTREE}}、preview {{PREVIEW_URL}}、repro spec {{SPEC_PATH}}、ticket brief {{BRIEF_PATH}}。不要讀實作過程的推理，證據自己採。

1. 照 verify-via-spec 原則：headed 實跑既有 spec 對 {{PREVIEW_URL}}（不重新推導散文步驟、不 mock、不用 prod 當 baseline）。
2. 截圖存 {{WORKTREE}}/.evidence/S3-preview/，附 EVIDENCE.md 標註每張圖對應的症狀與判定。
3. 視覺類 ticket：對 Figma reference 做 computed-style diff（照 ~/.claude/skills/figma-verify 流程），diff 結果一併落地。
4. 對照 brief 的症狀清單逐條裁定 PASS/FAIL。只跑這一輪，不疊加驗證。
回傳：VERDICT: PASS | FAIL(逐條原因) + 證據路徑。教訓有則寫 wiki/learned/，無則明寫「無」。
```

### 4c. CI Babysit（Sonnet，S4）

```
你負責看護 PR {{PR}} 的 CI 直到綠或 escalate。worktree {{WORKTREE}}、分支 {{BRANCH}}。

迴圈：gh pr checks {{PR}} --watch -i 60 --fail-fast
失敗時撈 failing run 的 log（gh run view --log-failed）分類：
- code bug（assertion / type error / lint）→ 在 worktree 修復（最小 diff）、跑該項本地驗證、commit + push → self-heal 計數 +1
- infra flake（timeout、runner lost、spurious cancel、429/network）→ gh run rerun --failed → self-heal 計數 +1
self-heal 累計 2 次後仍紅 → 停止，回報 ESCALATED(ci-selfheal-exhausted) 附兩次的分類、行動與 log 摘錄。
禁止：force-push、改 base、merge、--no-verify。
回傳：CI: GREEN | ESCALATED(...)，含每次 self-heal 的分類記錄。教訓有則寫 wiki/learned/，無則明寫「無」。
```

## Step 5：主對話抽查驗收（SPOTCHECK）

Implementer 回報 READY_FOR_VERIFY 後、派 Verifier 前，orchestrator 逐項核：

1. `git -C {{WORKTREE}} diff {{BASE}}...HEAD --stat` 與 manifest 宣稱的改動範圍一致（agent 輸出必用 git diff 驗 claim）。
2. `.evidence/S1-premise/` 確有 FAIL 證據、`.evidence/S2-fix/EVIDENCE.md` 症狀清單全 PASS——抽讀檔案本體，不只信 manifest。
3. `gh pr view {{PR}}` 確認 base 正確、body 繁中且對得上 diff。
4. 任一項不符 → 退回 Implementer 修正（同一 agent 用 SendMessage 續派，計入其迭代額度），不放行 Verifier。

## Step 6：結尾報表 + KB 沉澱

全部 lane 到終態後：

1. 總表：

   | Ticket | Branch | PR | CI | 驗收 | Evidence |
   |---|---|---|---|---|---|
   | PDT-xxxxx | fix/... | #NNNN | GREEN / ESCALATED | PASS / PREMISE_UNVERIFIED / ... | <worktree>/.evidence/ |

2. Escalation 清單：每條附原因分類與證據路徑，需要使用者裁決的（merge 與否、是否放棄票）列為待辦，不代決。
3. Run 摘要寫入 KB session 檔（`_pending/session-<date>-ship-ticket-<slug>.md`）：各 lane 結果、self-heal 記錄、新增的 learned 檔清單。
4. Worktree 一律保留（清理走 `/worktree cleanup`，那裡有 merge 狀態檢查與 HITL）。

## 已知地雷（派工 prompt 已內建防呆；細節用 BM25 撈）

- `playwright-reuse-existing-server-wrong-worktree` — 並行 worktree 下 dev server 誤撿
- `playwright-hardcoded-baseurl-env-var-ineffective-verify-host` — baseURL env 無效要驗 host
- `commit-hangs-from-precommit-build-cpu-contention-not-hook-failure` — 並行 install/build 搶 CPU
- `worktree-husky-prepare-script-git-file-not-dir` — worktree 的 `.git` 是檔案
- `ephemeral-preview-env-torn-down-at-merge-qa-report-timing` — preview 在 merge 時拆除
- `headed-parity-prod-not-baseline-use-staging` — prod 不可當視覺 baseline

## 使用方式

```
/ship-ticket https://www.notion.com/workspace/PDT-10618-xxx
/ship-ticket <url1>, <url2>, <url3> --max-parallel 2
/ship-ticket <url> --repo ~/Documents/other-repo --base develop
```
