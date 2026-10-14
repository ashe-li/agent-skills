---
fixture_id: "02"
kind: negative-control
alert: "CF Tunnel: Prod 404 Sustained High (Route Regression)"
expected_classification: benign
expected_verdict: benign
expected_confidence: HIGH
expected_matched_priors:
  - memory/verify-runbook-attribution-before-citing
  - reports/2026-05-15-cf-tunnel-alert-prefetch-404-regression
  - reports/2026-05-22-cf-tunnel-alert-multi-build-cdn-noise
source_report: reports/2026-05-27-cf-tunnel-404-fallback-stale-client-prefetch-not-cdn-cache.md
notes: 粗層 fallback alert；精準層沒 fire = 純 prefetch noise；runbook「CDN HTML cache」歸因錯誤
---

## Alert Payload（正規化 envelope）

| 欄位 | 值 |
|------|----|
| alertname | CF Tunnel: Prod 404 Sustained High (Route Regression) |
| rule_uid | efm4kkn10h6o0f（sustained 404 > 24/s for 90m，coarse fallback）|
| severity | warning |
| state | firing → 自行 RESOLVED（2026-05-26 ~18:00 TPE / UTC 10:00）|
| service | app-prod frontend（envoy_proxy）|
| datasource | Loki Production ffhwhnr95ygaof |
| stated_cause（runbook）| route regression / CDN HTML cache 吐 stale buildId |

## Recorded Live Evidence（回放用，當時真實證據）

- 精準層 alert `CF Tunnel: Prod Page-Level 404 Rate (Route Regression Precision)`（cfpage404prec，排除 `_next/data`）：`state: normal / health: ok` 全程沒 fire → 排除 route regression。
- LogQL by buildId：404 100% 集中在 `/_next/data/.+`，三版 stale buildId 各貢獻數千/10min（Wp0PF… ~9362、mlid3… ~7884、IIvzw… ~4918），當前 build J872… 僅 ~25–281。
- 事件隨舊 client prefetch 自然衰減消退，無人工介入，當前 build 全程健康。

## Expected RCA 結論

- **分類：benign false-positive**。多代過期 Next.js buildId 並存，舊分頁 client 持續 prefetch 已不存在的 `/_next/data/{舊buildId}` → 404 洪水；非 route regression。
- **stated_cause 對賭**：runbook 說「CDN HTML cache 吐 stale buildId」**錯誤** → 實測 HTML 為 dynamic/no-store；標 attribution rot。
- **處置**：不開 fix PR（self-heal）；記錄為何 benign + 可考慮優化 alert 粗層 threshold / 抑制 `_next/data` prefetch noise（屬 alert rule 調校，非服務 regression）。
- 走 HITL G2（benign 自動關閉前確認）。
