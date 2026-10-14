# alert-triage golden set — failure mode ↔ fixture coverage

這張表把 `/alert-triage` skill 已知的判讀情境對到對應的 fixture、SKILL.md 步驟與 KB 出處，供 CI script 機械檢查每一列的 fixture 檔案確實存在。`fixture` 欄只填檔名的編號前綴（`01`、`04`、`01, 02` 這種形式），不填完整檔名。

`kind` 的用法：`regression` = 期望 skill 判 `real` 並走 guardrail 路徑；`negative-control` = 期望判 `benign`、不開 fix PR（防止擋門退化成「見 alert 就修」）。各 fixture 的 `expected_verdict` 與 `expected_classification` 同值。

| 情境 / 分類 | fixture | 預期判定 | 對應 SKILL.md 步驟 | KB 出處 |
|---|---|---|---|---|
| imgproxy 422 兩個子根因皆 real，runbook 歸因（doc 119）被推翻；marker 只記「已嘗試」 | 01 | real（HIGH） | Step 3 stated_cause 對賭、Step 4 規則 2、Step 6 guardrail | reports/2026-05-27-imgproxy-422-alert-rca-two-classes-marker-flaw-recurrence.md |
| CF Tunnel 粗層 404 持續，實為舊 buildId 的 `/_next/data` prefetch noise，精準層未 fire | 02 | benign（HIGH） | Step 3 stated_cause 對賭、Step 4 規則 1、G2 | reports/2026-05-27-cf-tunnel-404-fallback-stale-client-prefetch-not-cdn-cache.md |
| ARC arm64 runner KubePodNotReady，Karpenter 冷啟加 dedicated taint 疊加，5 分鐘自癒 | 03 | benign（HIGH） | Step 3 對照組（KubeDeploymentNotReady 未響）、Step 4 規則 1、G2 | reports/2026-05-26-arc-arm64-runner-kubepodnotready-info-triage.md |
| cert-manager webhook "no endpoints" 單次 transient，`status.history` 已自癒，stg replica=1 by design（禁推 HA PR） | 04 | benign（HIGH） | Step 3 以 status.history 判斷、Step 4 規則 1、G2、G3（deployment-eks 屬後端） | reports/2026-05-23-cert-manager-webhook-transient-fail-triage.md |
| Loki OOM 來自 dashboard `unwrap [$__range]` 查詢成本；共用 Prometheus 與預設 stg context 會誤判 benign | 05 | real（HIGH） | Step 3 多 cluster 陷阱、Step 4 規則 2、Step 6 guardrail、G3 | reports/2026-05-25-prod-loki-oom-sw-cache-dashboard-range-unwrap.md |

## 未涵蓋 / 待補

以下情境在 SKILL.md 有明文規則，但目前沒有任何 fixture 驗證，誠實列出：

- **mixed 分類**（Step 4 規則 3：同時命中 benign 與 real 訊號，拆 sub-signal 分別處理）——fixture 01 雖是 two-classes，但兩類皆 real，並非 benign 與 real 並存。
- **LOW 信心路徑**——`real` 不接受 LOW 並走 G4、`benign` 為 LOW 時走 G2 人工確認，5 份 fixture 的 `expected_confidence` 全為 HIGH。
- **單 replica per-series flap**（alert rule 漏 `sum()`，Step 4 規則 1 第三條）與 Step 6 對 alert rule 的 update 草案（須帶完整 payload 含 `notification_settings` + `org_id`）。
- **imgproxy bypass / allowed-sources 漏設**（Step 4 規則 2 的 config gap 分支）。
- **Step 0 / Step 1 輸入形態**——只給 alertname 或 Grafana alert URL 時走 `get_alert_group`、必要欄位缺失以 `unknown` 填充並標 `provisional`；5 份 fixture 都是完整 envelope。
- **Step 5 報告的 Action Items 固定 schema**——fixture 只驗分類與處置，不驗報告格式（範例見 `examples/action-items-*.md`）。
- **headless / cron 觸發的 HITL 放寬規則**。
- **feature-level 驗收**——`acceptance_results.md` 是以歷史證據回放的 logic-level gate，未實跑 grafana MCP 查詢，skill 尚未達 ship-ready。
