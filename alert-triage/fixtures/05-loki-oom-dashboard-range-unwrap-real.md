---
fixture_id: "05"
alert: "KubeContainerOOMKilled + Loki: Pod Crashing (monitoring/loki-0)"
expected_classification: real
expected_confidence: HIGH
expected_matched_priors:
  - reports/2026-05-25-prod-loki-oom-sw-cache-dashboard-range-unwrap
  - memory/grafana-mcp-cloudwatch-gap
source_report: reports/2026-05-25-prod-loki-oom-sw-cache-dashboard-range-unwrap.md
notes: 跨叢集共用 Prometheus 陷阱（預設 context 是 stg 健康 pod，差點誤判 benign）；real 但根因是 dashboard query 成本
---

## Alert Payload（正規化 envelope）

| 欄位 | 值 |
|------|----|
| alertname | KubeContainerOOMKilled + Loki: Pod Crashing |
| severity | critical |
| state | firing（14:46 CST，15 分鐘內重啟）|
| cluster / namespace | asheli-prod / monitoring（loki-0，container loki）|
| datasource | Prometheus LxIjrpMnk（同時 scrape prod + stg）|
| stated_cause | Loki OOM / crashing（未指根因）|

## Recorded Live Evidence（回放用，當時真實證據）

- **陷阱**：預設 context `asheli-stg-admin` 看到的是健康 stg pod（restarts=0）→ 差點誤判 benign。切 `--context asheli-prod` 才見 prod loki `restarts=1 / OOMKilled / exit=137 / finishedAt=06:45:09Z`。
- 記憶體時間軸（prod，30s）：06:33 385MB（基線）→ 06:42 833MB → 06:45 OOMKilled（cgroup 1Gi）→ 新 container 98MB。~90s 內 385→833MB = **讀取路徑（查詢）尖峰，非 ingestion/洩漏**；`increase(restarts[24h])=1`。
- querier log OOM 前被同一批查詢洗版 + 大量 `context canceled`。
- 兇手 panel：`SW Cache Metrics` dashboard「Cache Fallback Count」= `sum_over_time(... | unwrap cache_fallback [$__range])`，有人切「Last 7 days」→ `[1w]`，正是 OOM log 裡的 `unwrap cache_fallback[1w]`。

## Expected RCA 結論

- **分類：real regression**（root cause 鎖定，fix 已交付 PR infra repo PR 272）。
- **關鍵驗證**：必先用 job/uid label 區分 prod vs stg 再切對應 kubectl context（否則誤判 benign）。
- **Guardrail（real 路徑）**：限縮 dashboard query range / 對 unwrap range 設上限 / Loki `limits_config` query 成本上限。deployment-eks 屬後端 → HITL G3：產建議/協作 PR，不直接改。
- 不接受 LOW 信心下 real 判定——本案 live 證據充分，HIGH。
