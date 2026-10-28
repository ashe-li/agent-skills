---
name: alert-triage
description: firing Grafana/Loki alert 進來 → live-data RCA → 分類 benign false-positive vs real regression → real 開 guardrail PR、benign 記錄不開 PR，產繁體中文 RCA report 沉澱到 KB。分類先驗來自內部 incident KB（非通用 SRE 知識）。觸發：使用者貼 firing alert payload、「triage 這個 alert」「這個告警是真的嗎」「查一下這個 firing alert」，典型案例 imgproxy 422/404、Loki OOM、CF Tunnel 404 noise、Karpenter cold-start、cert-manager webhook transient。
allowed-tools: Bash, Read, Grep, Glob, Write, Edit, AskUserQuestion, Agent, mcp__grafana__query_loki_logs, mcp__grafana__query_loki_stats, mcp__grafana__query_loki_patterns, mcp__grafana__query_prometheus, mcp__grafana__query_prometheus_histogram, mcp__grafana__list_alert_groups, mcp__grafana__get_alert_group, mcp__grafana__generate_deeplink, mcp__grafana__get_panel_image
argument-hint: <firing alert payload / alertname / Grafana alert URL>
---

# /alert-triage — Firing Alert → Live-Data RCA → 分類 → Guardrail

接收一則 firing Grafana/Loki alert，對 **live 資料**（grafana MCP + kubectl）做 root-cause analysis，分類為 **benign false-positive** 還是 **real regression**，real 提 guardrail fix 並開 PR、benign 記錄理由不開 PR，最後產一份繁體中文 RCA report 沉澱到知識庫。

## 設計原則（先讀）

1. **分類先驗來自內部 KB，不是通用 SRE 知識**（pattern: `incident-kb-as-llm-prompt-corpus`）。每次 triage 先 grep `wiki/learned/INDEX.md` + `reports/` 撈既有 RCA 當 priors，再對照當前 alert。
2. **下任何結論前先 live verify**（嚴格遵守 `~/.claude/rules/common/diagnostics.md`）。只靠 alert 描述 / runbook / config / 記憶推論的結論，一律標 `provisional`，等 live 證據確認。
3. **alert 描述與 runbook 歸因會 rot**（memory `verify-runbook-attribution-before-citing`）。annotation 寫的「根因」視為**待驗證假設**，不是事實；實測 header/metric/log 與它衝突時，以 live 為準並明確標示「runbook 說 X，實測 Y」。
4. **benign vs real 不是純二元**。同一個 alert 可能部分 benign、部分 real（典型：imgproxy 422 marker-flaw 的 two-classes），決策樹要能拆分 sub-signal 分別處理。
5. **編排既有資源，不重造輪子**。本 skill 整合既有 grafana MCP + kubectl + KB 慣例；report 沉澱、topic-grouped commit、`/kb-query`、`/verify-live-state` 都沿用，不重寫。

---

## Step 0 — 取得 alert payload

輸入可能是三種形態之一：

- **alertmanager webhook JSON**（headless/cron 觸發時）：直接拿 `alerts[]` 陣列。
- **使用者貼的 firing alert 文字**（手動觸發，現況主路徑）：可能只是 Grafana UI 複製的片段。
- **只給 alertname 或 Grafana alert URL**：用 `mcp__grafana__list_alert_groups` 撈當前 firing groups，`mcp__grafana__get_alert_group` 取該 alert 的 firing instances + labels 補齊。

若資訊不足以進 Step 1（缺 namespace / service / 時間範圍），先補齊再往下。

## Step 1 — Parse（payload 正規化）

把任意來源正規化成統一 envelope（後續步驟只認這個結構）：

| 欄位 | 來源 | 用途 |
|------|------|------|
| `alertname` | labels.alertname | grep KB priors 的關鍵字 |
| `severity` | labels.severity | 排序處理優先級 |
| `state` | status（firing/resolved）| resolved 多半已自癒，傾向 benign |
| `namespace` / `service` / `pod` | labels | kubectl / LogQL 篩選範圍 |
| `cluster` / `datasource` | labels | 選對 Loki/Prometheus datasource |
| `startsAt` / firing 持續時間 | status | 短時間自癒 → transient 訊號 |
| `summary` / `description` | annotations | 人類可讀說明（**非根因事實**）|
| `runbook_url` | annotations | runbook 連結（歸因可能 rot，須驗）|
| **`stated_cause`** | annotation/runbook 推導 | 標為「待驗證假設」，進 Step 3 對賭 |

三來源欄位差異（避免抓錯欄位）：
- **alertmanager webhook**：`alerts[].labels` / `alerts[].annotations` / `status` / `startsAt`。
- **Grafana unified alerting（ruler）**：label 在 rule 的 `labels`，instance 在 `get_alert_group` 回傳。
- **Loki ruler**：alert 由 LogQL 觸發，`description` 常含 query 片段，可直接拿來當 Step 3 的 LogQL 起點。

**缺失欄位 fallback（不可卡死、不可編造）**：必要欄位是 `alertname` + (`namespace` 或 `service` 二擇一)，缺這些才回 Step 0 補齊（或 `AskUserQuestion` 問人）。其餘欄位缺失一律以 `unknown` 填充並在後續結論標 `provisional`，繼續執行，**不可因欄位缺失停下或臆造數值**。

## Step 2 — KB Priors 檢索（分類語料）

下任何分類前，先用 parsed 訊號撈內部既有 RCA 當先驗：

```bash
cd ~/Documents/knowledge-base
# 1. learned skill 先驗（SOP / 已知陷阱）
grep -iE "<alertname/service/failure-class keywords>" wiki/learned/INDEX.md
# 2. 既往 RCA report（同類事件怎麼判的）
ls reports/ | grep -iE "<service>|<failure-class>"
# 3. pattern / memory 先驗
grep -riE "<keywords>" wiki/patterns/
```

先把 grep 裡的 `<...>` 佔位符換成 Step 1 解析出的實際 alertname / service / failure-class 再執行。撈到後**點開最相關 2–3 篇全文讀**（INDEX 只有摘要，body 才有判斷依據）。

### 已知 priors 對照表（grounding，非窮舉 — 每次仍要 grep 最新）

| Alert 類別 | 典型 KB 先驗 | 預設傾向 | 關鍵驗證點 |
|-----------|-------------|---------|-----------|
| cert-manager / webhook "no endpoints available" | `flux-kustomization-webhook-transient-fail-triage`(learned)、`2026-05-23-cert-manager-webhook-transient-fail-triage` | **benign** (by design) | Kustomization `status.history` 是否已自癒；webhook Pod AGE ≈ fail 時間；stg `cert-manager` replica=1 是刻意的（memory `cert-manager-stg-replica-1-by-design`）——**不要推 HA PR** |
| Karpenter cold-start / replicas-mismatch noise | `2026-05-08-frontend-prod-replicas-mismatch-karpenter-noise`、`2026-04-28-frontend-cold-start-3-change-cascade`、`hpa-cold-start-scaleup-policy-sizing`(learned) | **benign** | 新 node 是否剛 provision；Pod 是否在 warm-up 窗；是否單純 scale 事件 |
| Alert flapping on single replica | `grafana-alert-query-aggregation-per-series-trap`(learned) | **benign**(alert rule 設計缺陷) | Query A 是否漏 `sum()/avg()`，per-series 在單 replica transient 上抖動 → 修 rule 而非修服務 |
| CF Tunnel 404 / prefetch noise | `2026-05-27-cf-tunnel-404-fallback-stale-client-prefetch-not-cdn-cache`、`2026-05-15-cf-tunnel-alert-prefetch-404-regression`、`2026-05-22-cf-tunnel-alert-multi-build-cdn-noise` | **benign**(多數) | **實測 HTML response header**：若 `no-store`/`DYNAMIC` 則非 CDN cache，是 client stale prefetch 自癒；runbook 若寫「CDN HTML cache」是 **attribution 錯誤**，要標示 |
| CF Tunnel `TUNNEL_STATUS_TYPE_DOWN`（observability / remote_write tunnel，如 `prometheus-remote-write` c68076d9）| `2026-06-08-prometheus-remote-write-tunnel-down-alloy-530-1033-rca` | **real-but-often-transient**（非 benign noise，但常數分鐘自癒）| **權威信號是 producer 自己的 remote_write 錯誤**（alloy log `HTTP 530 / error 1033` = Argo Tunnel down），**不是自端 `curl`**——curl Access-protected host 只撞 CF Access gate(403/302)、對 tunnel 健康無定論。先看是否自癒（CF DOWN→HEALTHY webhook + producer 錯誤停止 + `resharding` flush WAL）。EC2 box 多半 `running`、掛的是其上 cloudflared；該 legacy box 常**無 SSM**→補救只能上機（跨界 HITL G3）。結構：單 cloudflared/EC2/tunnel = metrics pipeline SPOF |
| imgproxy OOM | `2026-05-11-imgproxy-pod-oom-rca`、`2026-05-18-imgproxy-prod-oom-rca`、`2026-05-21-app-imgproxy-hpa-smoothing` | **real** | `OOMKilled` events、memory working set 趨勢 → 需 resource/HPA guardrail |
| Loki OOM（dashboard query 成本）| `2026-05-25-prod-loki-oom-sw-cache-dashboard-range-unwrap` | **real** | unwrap/range query 成本爆量 → 需 query 成本 guardrail（限 range / 降頻）|
| imgproxy 422 | `2026-05-27-imgproxy-422-alert-rca-two-classes-marker-flaw-recurrence`、`2026-05-13-imgproxy-security-error-calc-audit` | **mixed**（two-classes）| 區分 marker-flaw recurrence(real) vs 已知良性來源；**不可純二元**，拆 sub-signal |
| Loki Discarded Samples / Replay too_far_behind（prod）| `2026-09-10-loki-discarded-samples-prod-helm-rollout-replay-4th-recurrence`、`2026-09-29-alert-triage-batch-0917-0929-loki-rate-limited-fp-imgproxy-svgload-recur`、`loki-side-rate-limited-discard-is-not-loss-check-producer-dropped-reason`(learned) | **benign**（alloy 重播）| alloy pod startTime 是否對上拒收尖峰；**Loki 端 `rate_limited` 不等於遺失**（alloy 會重試 429），判遺失看 producer 端 `loki_write_dropped_entries_total{reason="rate_limited"}` |
| imgproxy Pod Crashing / Native Decoder Crash（exit 2）| `imgproxy-exit-code-2-sigabrt-in-cgo-is-decoder-crash-not-oom`(learned)、`2026-09-29-alert-triage-batch-0917-0929-loki-rate-limited-fp-imgproxy-svgload-recur` | **real**（低頻，已知成因）| 先看 `lastState.exitCode`：2 = decoder crash、137 = OOM；**立刻存 `logs --previous`**；svgload 則是 HTML 偽圖（內嵌 `<svg>`），imgproxy 設定擋不住，資料端清理 |
| imgproxy bypass / allowed-sources | `2026-05-25-ticket9895-imgproxy-unsplash-allowed-sources-vs-bypass`、`2026-05-22-imgproxy-bypass-salon-products-fix` | **real**(config gap) | 來源是否該進 allowed-sources vs 該走 bypass；privacy 影響 |
| CF 日報 managed_challenge 攀升 /「100% 集中單一路徑」/「skip 規則從無變有」 | `2026-09-30-cf-managed-challenge-spike-single-ip-curl-burst-digest-limit100-truncation`、`top-n-grouped-query-summed-as-total-makes-dod-and-concentration-lie`(learned) | **mixed**（日報數字先當截斷假象驗，流量面另判）| 先用不帶 path / country 維度的查詢重跑真實總數，再談 DoD；「規則新增」看 ruleset `last_updated` / version 而非事件有無；分項加總大於總數代表母體不同；流量面看 IP / ASN 集中度、逐分鐘曲線、solved 數、origin 5xx；Grafana MCP 查不到 CF 資料，走 CF GraphQL API 唯讀 token |

> 對照表是 grounding 範例，**不是查表照抄**——每次仍 grep 最新 KB，且 live 證據優先於先驗。

## Step 3 — Live-Verify（證據蒐集，下結論前必做）

對每個假設（含 Step 1 的 `stated_cause`）列出支持它的 **live 證據**。嚴格遵守 `diagnostics.md`。

**Loki（log 實證）**
- `mcp__grafana__query_loki_logs`：跑 LogQL 撈實際 error log、算 count（如 404/422 真實發生率）。
- `mcp__grafana__query_loki_stats` / `query_loki_patterns`：估量級、抓 log pattern，判斷是否 spike。

**Prometheus（metric 實證）**
- `mcp__grafana__query_prometheus`：PromQL 查 `kube_pod_container_status_restarts_total`、OOMKilled、RPS、memory working set、HPA desired/current。
- `mcp__grafana__query_prometheus_histogram`：latency 分佈。

**kubectl（cluster live state，透過 Bash）**
```bash
kubectl get pod -n <ns> <pod> -o wide
kubectl describe pod -n <ns> <pod>        # OOMKilled / restart reason / events
kubectl logs -n <ns> <pod> --previous     # crash 前 log
kubectl top pod -n <ns>                    # 即時資源
kubectl get events -n <ns> --sort-by=.lastTimestamp | tail -30
```

> **多 cluster 陷阱（必讀）**：prod/stg 常共用同一 Prometheus datasource，且預設 kubectl context 可能是 stg。查 metric 前先用 `job` / `cluster` label 鎖定是哪個 cluster 的 series；跑 kubectl 前先 `kubectl config current-context` 確認、必要時 `kubectl config use-context <prod-context>`。否則會看到健康的 stg pod、把 prod 的 real regression 誤判成 benign（fixture 05 Loki OOM 即此陷阱：預設 stg context 看到 restarts=0，切 prod 才見 OOMKilled）。

**證據與信心輸出格式**（每個假設一行）：
```
假設 H1：<...>  | 證據：<Loki count / kubectl events / PromQL 值>  | 信心：HIGH/MEDIUM/LOW
```
- inference-only（無 live 證據）→ 明確標 `provisional`。
- **stated_cause 對賭**：實測與 runbook/annotation 宣稱衝突時，寫「runbook 說 X，實測 Y（證據）」，以 live 為準。CF 404 的「CDN cache」歸因就是這樣被推翻成 client stale prefetch 的。

> 時間戳：grafana MCP 無 offset 的 timestamp 視為 **UTC**；kubectl 與 Loki 對齊時先統一時區再比 firing 窗。

## Step 4 — Classify（決策樹 + 信心）

依 Step 2 先驗 + Step 3 live 證據走決策樹（**允許 mixed**）：

1. **命中 benign prior 且 live 證據吻合** → `benign false-positive`
   - webhook "no endpoints" + `status.history` 已自癒 + webhook Pod 剛 rolling → benign（by design）。
   - Karpenter/cold-start + 新 node 剛 provision / Pod warm-up 窗 → benign noise。
   - 單 replica per-series flap（alert rule 漏 `sum()`）→ benign（要修的是 rule，不是服務）。
   - CF tunnel 404 + 實測 HTML `no-store`/`DYNAMIC` + client stale prefetch → benign（並標 runbook 歸因錯誤）。
2. **live 證據顯示真實 regression** → `real regression`
   - imgproxy / pod `OOMKilled` events + memory 爬升 → real。
   - Loki OOM 來自 dashboard unwrap/range query 成本 → real。
   - imgproxy 422 marker-flaw recurrence（與既往 RCA 同 signature）→ real。
   - imgproxy bypass / allowed-sources 漏設 → real config gap。
3. **同時命中 benign 與 real 訊號** → `mixed`：拆成各 sub-signal，分別走 benign 記錄 / real 修復。

**信心標示**：每個分類結論標 `HIGH / MEDIUM / LOW`。
- **`real` 判定不接受 `LOW`**：證據不足以支撐 real 時，不直接開 PR → 回 Step 3 補證據，或標 `provisional` 走 HITL G4。
- `benign` 判定為 `LOW` 時也不可自動關閉 → 走 HITL G2 由人確認。

## Step 5 — RCA Report（繁體中文 → KB）

寫 `~/Documents/knowledge-base/reports/<YYYY-MM-DD>-<slug>.md`，**沿用既有 report 格式**（看同目錄近期 report 對齊）：

```markdown
---
title: <alertname> <一句結論：benign/real + 根因>
date: <YYYY-MM-DD>
type: incident-rca
tags: [alert-triage, <service>, <failure-class>]
status: <benign 記錄 / real 已開 PR / provisional 待補證據>
action_items_open: <n>   # Action Items 表中狀態為 open 的筆數；無 action item 寫 0
---

## TL;DR
<2–3 句：alert 是什麼、判 benign 還是 real、根因、處置>

## Alert Payload（正規化）
<Step 1 envelope 表>

## KB Priors
<Step 2 撈到的相關 RCA / learned skill 連結 + 預設傾向>

## Live 證據
<Step 3 每個假設 + 證據 + 信心；含 stated_cause 對賭結果>

## 分類結論
<benign / real / mixed + 信心 + 理由>

## 處置
<benign：為何 benign、不開 PR 的理由；real：guardrail 方案 + PR 連結>

## Action Items
<有項目：下方 schema 的表格；無項目（典型是 benign）：只寫「無」一個字>
```

- **benign**：報告寫清楚「為何 benign」（引 prior + live 證據），**不開 PR**。
- **real**：附證據連結進 Step 6（Loki query 字串 / `mcp__grafana__generate_deeplink` 生的 Grafana deeplink / kubectl 輸出片段）。
- 文件原則：Markdown report，**不寫一次性 `.sh` 驗證腳本**（rule `documentation-style`）；commit 依主題分組。

### Action Items 固定 schema（S3.6 digest 會解析，不可更動）

表頭、欄位順序、分隔符一字不改：

```markdown
| AI-ID | 項目 | owner | 期限(YYYY-MM-DD) | 狀態(open/done/wontfix) | 連結 |
|---|---|---|---|---|---|
| AI-20261014-01 | <一句話、動詞開頭的待辦> | 使用者 | <使用者給的 YYYY-MM-DD；未給寫 TBD> | open | <PR／issue／report 連結，沒有寫 `-`> |
```

- **標題**：固定 `## Action Items`（二級標題，英文，大小寫照寫）；表格緊接在標題下方，標題與表格之間不放其他段落。
- **AI-ID**：`AI-<報告日期 YYYYMMDD>-<序號>`，序號兩位數、從 `01` 起、同一份報告內不重複（例：`AI-20261014-01`）。報告日期就是 frontmatter 的 `date`。
- **owner**：只能填 `使用者` 或使用者指定的人名，不得填團隊名、`TBD`、空白。
- **期限**：由使用者給，格式 `YYYY-MM-DD`。**agent 不得自行填日期**；使用者還沒給就寫 `TBD`。
- **狀態**：只能是 `open`、`done`、`wontfix` 三者之一（小寫）。
- **連結**：沒有就寫 `-`，欄位不可留空。
- **後端 own 的項目一律不列入表格**（`smb-*`／`payment-*` 的 helm values／secrets、後端 EC2、prod 的 cloudflared／alloy deploy trigger 等，見 `~/.claude/rules/common/repo-ownership.md`）。殘餘問題在後端層時，只在正文（處置或分類結論）用一句話標「屬後端」，不寫通知草稿、不寫提案、不列 row。
- **無項目**：結論是 benign 且沒有任何 action item 時，`## Action Items` 底下只寫「無」，不放表頭，frontmatter 寫 `action_items_open: 0`。
- **`action_items_open`**：必須等於表中狀態為 `open` 的筆數；改了 row 狀態要同步改這個數字。
- **格式約束**：每筆一行；任一欄位內不得出現 `|` 或換行（需要時用全形 `｜` 或改寫）；不得合併儲存格、不得在表格中間插入空行或註解。

> **解析契約**：S3.6 的 digest 會用 shell 解析這張表，欄位順序和分隔符（`|`）不可更動；格式錯誤（表頭不符、欄數不是 6、AI-ID／狀態不合規、期限不是 `YYYY-MM-DD` 也不是 `TBD`、`action_items_open` 與 open 筆數不一致）會被列為「解析失敗」。

完整範例：[`examples/action-items-open.md`](examples/action-items-open.md)（2 筆 open）、[`examples/action-items-benign.md`](examples/action-items-benign.md)（benign 寫「無」）。

## Step 6 — Guardrail Fix 草案（僅 real）

1. **定位該修的 repo + base 規則**：
   - `web-app`：PR base **一律 hotfix**（memory `web-app-pr-base-hotfix`）。
   - `deployment-eks`：後端團隊負責，**不直接改**（memory `repo-ownership`）→ 只產建議 / 開 issue（HITL G3）。
   - infra Helm/Flux 託管資源：改 chart values 走 PR，**不裸 `kubectl patch`**（memory `helm-persist-before-apply`）。
2. **產 guardrail**（對症）：
   - OOM → resource limit / HPA scaleUp 平滑 / memory guardrail。
   - Loki query 成本 → 限 range / 降頻 / unwrap 上限。
   - alert flap → Query A 補 `sum()/avg()`（修 alert rule 經 `mcp__grafana__alerting_manage_rules`，或走 Grafana-as-code PR）。**alert rule 一律不自動 apply**：先附 backfit query 建議交 G1 確認；update 草案須帶**完整既有 payload（含 `notification_settings` + `org_id`）**，漏帶會清空 routing（memory `grafana-alert-update-notification-settings`）。
   - config gap（allowed-sources）→ 補設定 + regression test。
3. **開 PR**：繁體中文 body（memory `pr-traditional-chinese`），附 RCA report 連結 + 證據；external system 用全名引用（`Linear ABC-1234`、`Sentry Issue N`），不用裸 `#數字`。

## HITL Gates（G1–G4）

本 skill 在這四個點**必停下用 `AskUserQuestion` 等人確認**（呼應 `no-ship-ready-without-acceptance`、`real-flow-not-mock-for-verification`）：

- **G1 — 開 PR 前**：呈現分類結論 + live 證據 + 擬修方案，確認後才開 PR。
- **G2 — benign 自動關閉 / 標記前**：列出為何 benign（prior + 證據），確認後才記錄關閉。
- **G3 — 跨 repo / 後端 repo**：涉及 `deployment-eks` 等非自有 repo 時，只產建議不直接改，由人決定。
- **G4 — low-confidence real**：信心不足以支撐 real 開 PR 時，不開 PR，回 Step 3 補證據或交人判斷。

> headless/cron 觸發時，G1/G3 不可略過（real 開 PR、跨 repo 改動須留 HITL）；唯一可放寬的是 G2 benign 自動關閉，且前提是**信心 HIGH 且命中已知 benign prior**——LOW/MEDIUM 信心的 benign 即使 headless 也須人工確認。所有自動關閉一律留 report audit。

## Tools（MCP / kubectl gotcha）

**已驗證的 grafana MCP 工具名**（用錯名會 silent fail / InputValidationError）：

| 用途 | 正確工具 | 不要用（不存在）|
|------|---------|----------------|
| Loki log 查詢 | `mcp__grafana__query_loki_logs` | — |
| Loki 量級 / pattern | `mcp__grafana__query_loki_stats` / `query_loki_patterns` | — |
| Prometheus 查詢 | `mcp__grafana__query_prometheus` / `query_prometheus_histogram` | — |
| firing alert 實例 / 群組 | `mcp__grafana__list_alert_groups` / `get_alert_group` | ~~`list_alert_rules`~~ |
| alert rule 存取（list/get/create/update）| `mcp__grafana__alerting_manage_rules`（統一 `action` 入口）| ~~`get_alert_rule`~~ / ~~`alerting_list_alert_rules`~~ |
| 產 Grafana deeplink | `mcp__grafana__generate_deeplink` | ~~`grafana_navigation_generate_url`~~ |
| panel PNG（貼報告/PR）| `mcp__grafana__get_panel_image` | — |
| 任意 Grafana API | `mcp__grafana__grafana_api_request` | — |

- **寫入類工具不預先核准**：`alerting_manage_rules`、`grafana_api_request` 可以建立、修改或刪除 prod 規則與 Grafana 資源。本 skill 會讀 Loki log，log 內容可受外部影響，所以這兩個工具刻意不放進 `allowed-tools`，每次呼叫都要經過權限確認，list／get 也一樣。唯讀查詢優先用上表其他工具（2026-10-21 S6.3 security review）。
- **kubectl** 一律透過 Bash（`get` / `describe` / `logs --previous` / `top` / `get events`）。
- grafana MCP 無 offset 的 timestamp 視為 **UTC**；跨 Loki/Prometheus/kubectl 比對時先對齊時區。
- ALB/AWS 原生 metric grafana MCP 查不到（memory `grafana-mcp-cloudwatch-gap`）→ 走 Athena / AWS console。
- **`alerting_manage_rules` update footgun**：要 update/edit alert rule 前先 get 完整 rule，update 時帶回**全部欄位**（含 `notification_settings` + `org_id`），漏帶會被靜默清空（memory `grafana-alert-update-notification-settings`）；且預設**不自動 apply**，產草案交 HITL G1。

## 不重造既有流程（編排，非取代）

- **report 沉澱 / commit**：沿用 KB 既有慣例（topic-grouped commit、Markdown runbook、不裸 `.sh`）。
- **learned skill 先驗**：每次 grep `wiki/learned/INDEX.md`（如 `flux-kustomization-webhook-transient-fail-triage`、`grafana-alert-query-aggregation-per-series-trap`、`karpenter-*`、`hpa-cold-start-scaleup-policy-sizing`）。
- **不重寫** `/kb-query`、`/verify-live-state`、`/release-pr`、`/pr`——本 skill 在對應步驟**引用**它們。

## Trigger Options（觸發層 — 僅選項分析，本次不實作）

| 選項 | 機制 | 優點 | 缺點 | 適用階段 |
|------|------|------|------|---------|
| **A 手動貼** | 使用者貼 payload 跑 `/alert-triage` | 零基建、HITL 天然內建、可控 | 需人起手、非即時 | 現況 / MVP |
| **C cron 輪詢** | cron 定期撈 firing alerts 批次 triage | 實作簡單、頻率可控、無常駐 webhook | 非即時、需去重避免重跑同 alert | 過渡 |
| **B alertmanager webhook** | AM webhook → headless Claude SDK agent | 即時、無人值守、可自動關 benign | 需 webhook 接收端 + 認證 + 去重 + rate limit + 防 alert storm 自我 DoS | 目標（成熟後）|

**推薦演進路徑**：A（現在，驗證分類品質）→ C（過渡，低風險自動化）→ B（目標，需 SDK headless + 去重 + rate limit + G1/G3 HITL 保留）。觸發層實作不在本 skill 範圍，本節僅供選型。
