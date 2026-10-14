---
fixture_id: "03"
alert: "KubePodNotReady (INFO — diagnostic)"
expected_classification: benign
expected_confidence: HIGH
expected_matched_priors:
  - wiki/learned/hpa-scaleup-containercreating-not-failure
  - wiki/learned/diagnose-cap-vs-count-before-optimizing
  - wiki/learned/karpenter-consolidation-blockers
source_report: reports/2026-05-26-arc-arm64-runner-kubepodnotready-info-triage.md
notes: INFO severity diagnostic；5 分鐘自行 RESOLVED；對照 KubeDeploymentNotReady 未響確認非真故障
---

## Alert Payload（正規化 envelope）

| 欄位 | 值 |
|------|----|
| alertname | KubePodNotReady（INFO / diagnostic）|
| severity | info |
| state | 12:30 FIRING:3 → 12:35 RESOLVED:3（5 分鐘）|
| cluster / namespace | app-stg / arc-system |
| pods | arc-arm64-2sdjn-runner-*（ephemeral CI runner）|
| stated_cause | pod not ready > 15m |

## Recorded Live Evidence（回放用，當時真實證據）

- ARC 健康，runner set 已回 idle（`arc-arm64` 0/8）。
- scheduler 事件：`FailedScheduling: 0/10 nodes ... Insufficient cpu/memory + untolerated taint + node affinity/selector` → capacity 與 dedicated-pool taint **兩者交集**無解。
- runner pod 帶 arm64 dedicated nodepool 的 nodeSelector + toleration（刻意隔離 CI 與 app）；現有 node 無一同時滿足 affinity/taint + 剩餘資源。
- Karpenter provision 新 arm64 node（i-0412ef802c421fab5）Ready 後，整批 pod 秒排上去、跑完、GC。
- 對照組：`KubeDeploymentNotReady` 未 fire → 非真實 Deployment 故障。

## Expected RCA 結論

- **分類：benign false-positive（resolved-no-action）**。擴容期 Karpenter 冷啟延遲 + dedicated taint 疊加造成 ephemeral runner 短暫 NotReady，設計使然。
- **處置**：不開 fix PR；記錄為 benign。可選優化：dedicated arm64 pool 預留 warm node / 提高 INFO alert 的 `for` 窗（屬 alert/容量調校，非故障）。
- 走 HITL G2。
