# Alert-Triage 回放驗收摘要（S5.2）

驗收方法：對 `fixtures/01–05` 各跑一遍 SKILL.md 的 Step 1（Parse）→ Step 2（KB Priors）→ Step 3（Live-Verify）→ Step 4（Classify）決策樹，輸入用 fixture 記錄的**歷史真實證據**（非重跑 live query），比對決策樹輸出 vs `expected_*`。

## 結果總表

| Fixture | 期望分類 | 實際（決策樹）| 信心 | matched_priors 命中 | 判定 |
|---------|---------|--------------|------|--------------------|------|
| 01 imgproxy 422 | real（mixed sub-classes）| real — live 顯示兩類皆不自癒、doc119 歸因被推翻 | HIGH | ✓（marker flaw + attribution rot prior）| PASS |
| 02 CF Tunnel 404 | benign | benign — 精準層沒 fire + 404 全在 stale buildId prefetch + 自癒 | HIGH | ✓（cf-tunnel noise + attribution rot）| PASS |
| 03 ARC KubePodNotReady | benign | benign — 5min 自癒 + cap/taint 交集 + KubeDeploymentNotReady 未響 | HIGH | ✓（karpenter cold-start prior）| PASS |
| 04 cert-manager webhook | benign | benign — status.history 130 次成功自癒 + stg replica=1 by design | HIGH | ✓（flux-webhook-transient + replica-1 memory）| PASS |
| 05 Loki OOM | real | real — prod context 確認 OOMKilled + query 成本尖峰（非 ingestion）| HIGH | ✓（loki query-cost prior + 跨叢集 context 陷阱）| PASS |

**5/5 classification 相符、5/5 confidence 相符、5/5 matched_priors 命中。**

## 關鍵驗證點（決策樹通過的理由）

- **F1/F5（real）**：決策樹要求 real 必有 live 證據支撐且不接受 LOW；兩案都有量化 live 證據（distinct count / 記憶體時間軸），且都偵測到 `stated_cause` 與 live 衝突（doc119 / 「ingestion 才對」誤導），符合 `verify-runbook-attribution-before-citing`。
- **F2/F3/F4（benign）**：三案都命中既有 benign prior **且** live 證據吻合自癒/by-design（精準層未響 / 5min resolved / status.history 自癒），符合「benign 需 prior + live 雙重支撐」。
- **F4 防回歸**：決策樹明確阻止推 HA PR（stg replica=1 刻意）——避免重犯 2026-05-23 的初始誤建議。
- **F5 防誤判**：決策樹 Step 3 的「跨叢集共用 Prometheus 須用 job/uid label 區分 + 切對應 kubectl context」攔住了「預設 stg context 看到健康 pod → 誤判 benign」的陷阱。

## Mock Limitation（誠實揭露，Review Note 2）

- 本驗收的「live 證據」是 **fixture 記錄的歷史真實證據**（來自當時已 live-verify 的 KB report），**非本次重跑 live query**。它驗證的是**分類決策樹的邏輯正確性**，**不**驗證：
  1. grafana MCP 工具呼叫在當前 live infra 上是否成功（工具名已對齊但未在本次實跑）；
  2. LogQL / PromQL 模板對當前 label schema 是否仍有效（label 可能已變）；
  3. 歷史數據點是否仍代表當前狀態（數據會過期）。
- **因此本 skill 尚未達 ship-ready**（依 `no-ship-ready-without-acceptance`）。首次對**真實 firing alert** 跑時，須實跑 grafana MCP query 抽驗工具名/查詢模板有效，並以真實 Loki count / kubectl 輸出當證據（`real-flow-not-mock-for-verification`），通過後才算 feature-level 驗收。
- 回放驗收的定位：**logic-level gate**（決策樹不會對已知案例給錯答案），非 feature-level 驗收。
