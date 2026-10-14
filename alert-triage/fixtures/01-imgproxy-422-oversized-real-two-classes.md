---
fixture_id: "01"
alert: "imgproxy: High 422 Rate (Oversized Source Images)"
expected_classification: real
expected_confidence: HIGH
expected_matched_priors:
  - wiki/learned/dedup-marker-tried-vs-succeeded
  - memory/verify-runbook-attribution-before-citing
  - reports/2026-05-13-imgproxy-security-error-calc-audit
source_report: reports/2026-05-27-imgproxy-422-alert-rca-two-classes-marker-flaw-recurrence.md
notes: two-classes — 兩個子根因都是 real（非 benign），但修法不同；runbook doc 119 歸因錯誤
---

## Alert Payload（正規化 envelope）

| 欄位 | 值 |
|------|----|
| alertname | imgproxy: High 422 Rate (Oversized Source Images) |
| rule_uid | cfixsxflpdz40f（>5% 門檻）|
| severity | warning |
| state | firing（~14:25 UTC+8）|
| namespace / service | vocus / imgproxy（images.vocus.cc，bucket sosreader-images-production）|
| datasource | Loki Production |
| stated_cause（runbook doc 119）| oversized source → copy-to-self 壓縮自癒 |

## Recorded Live Evidence（回放用，當時真實證據）

- 422 拆兩類（1h 全量非採樣）：`type not supported` 272（distinct 166，spike 主體）/ `resolution is too big` 96（distinct 145）。
- S3 head-object 實測：壞 bytes = 0-byte 空檔 或 HTML 被存成 .jpg；oversized = 真 30–45 MP 原圖。
- backfill cronjob `*/10` 健康，但：oversized top 21/21 已在 marker 仍 422（tried-not-succeeded marker 缺陷）；type-not-supported 0/10 在 marker（擷取過濾沒涵蓋）。
- bucket 未開 versioning，壞 bytes LastModified 跨 2022→2026 = 長期累積、ingestion 沒驗 content。

## Expected RCA 結論

- **分類：real（mixed sub-classes，皆 real）**。兩類都無法靠現行 backfill+compressor loop 自癒。
- **stated_cause 對賭**：runbook doc 119 只對應 oversized 一類且當前修不動 → 標 attribution 錯誤（`verify-runbook-attribution-before-citing`）。
- **Guardrail（real 路徑）**：(1) marker 改記「成功壓縮」而非「已嘗試」；(2) ingestion 端驗 content-type；(3) alert description 分流。base 規則：source-image-compressor repo 本團隊 owns。
- 不接受 LOW 信心下 real 判定——本案 live 證據充分，HIGH。
