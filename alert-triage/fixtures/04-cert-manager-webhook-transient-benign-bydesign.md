---
fixture_id: "04"
kind: negative-control
alert: "kustomize-controller: Certificate dry-run failed (no endpoints available for service cert-manager-webhook)"
expected_classification: benign
expected_verdict: benign
expected_confidence: HIGH
expected_matched_priors:
  - wiki/learned/flux-kustomization-webhook-transient-fail-triage
  - memory/cert-manager-stg-replica-1-by-design
  - reports/2026-04-25-stg-flux-prereq-webhook-eviction
source_report: reports/2026-05-23-cert-manager-webhook-transient-fail-triage.md
notes: transient by-design；status.history 已自癒；stg 1 replica 刻意 → 禁推 HA PR；deployment-eks 屬後端
---

## Alert Payload（正規化 envelope）

| 欄位 | 值 |
|------|----|
| alertname | kustomize-controller（Slack）— Certificate/arc-system/buildkitd-amd64-tls dry-run failed |
| reason | no endpoints available for service "cert-manager-webhook" |
| state | firing（顯示 03:56 TPE = UTC 2026-05-22 19:56）|
| cluster / namespace | vocus-stg / arc-system（webhook 在 cert-manager）|
| stated_cause | webhook 無 endpoint（看似 webhook 壞）|

## Recorded Live Evidence（回放用，當時真實證據）

- `kubectl -n cert-manager get pods`：webhook Pod Running 8h，endpoint 10.0.158.232:10250 正常。
- `kubectl -n flux-system get kustomization arc-system -o yaml`：`status.history` = 19:56 failed ×1，後續 **130 次 ReconciliationSucceeded**（已自癒）。
- webhook Pod AGE 8h 反推 ~20:00 重啟 → rolling 撞 reconcile 空窗。
- `deployment-eks/apps/stage/cert-manager.yaml` line 35 後端註解：「stg 1 replica 已足；prod 評估遷時再上 HA」+ line 38 重啟風險已知接受。

## Expected RCA 結論

- **分類：benign（transient, by-design）**。單次 Kustomization fail + 自動 retry 成功，判斷依據是 `status.history` 後續結果而非當下 alert。
- **stated_cause 對賭**：「webhook 壞」誤導 → 實為 rolling restart transient。
- **處置**：**不開 PR**，尤其**不推 HA（replica=2 + PDB）PR**（stg 1 replica 刻意；`cert-manager-stg-replica-1-by-design`）。deployment-eks 屬後端團隊（HITL G3，只記錄不改）。
- 走 HITL G2/G3。
