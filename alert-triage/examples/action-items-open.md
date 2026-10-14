---
title: ImgproxyOOMKilled real：dashboard 大範圍 unwrap 查詢造成 memory 爬升
date: 2026-10-14
type: incident-rca
tags: [alert-triage, imgproxy, oom]
status: real 已開 PR
action_items_open: 2
---

<!-- 範例（虛構案例，僅示範格式）：alert-triage 報告，real，2 筆 open。人名與日期皆為示範值。 -->

## TL;DR
imgproxy pod 在 02:10–02:40 UTC 連續 OOMKilled 3 次，判定為 real regression（HIGH）。根因是新上的 dashboard 以 7d range 跑 unwrap 查詢。已開 guardrail PR；alert rule 補 `sum()` 需使用者確認。

## Alert Payload（正規化）
| 欄位 | 值 |
|---|---|
| alertname | ImgproxyOOMKilled |
| namespace | imgproxy |
| startsAt | 2026-10-14T02:12:00Z |

## KB Priors
- 命中 imgproxy OOM 既往 RCA，預設傾向 real。

## Live 證據
- `kubectl get pod -n imgproxy -o json` 的 `lastState.terminated.reason` = `OOMKilled`，3 筆（02:10／02:24／02:38 UTC）。信心 HIGH。

## 分類結論
real regression，信心 HIGH。

## 處置
guardrail：memory limit 由 512Mi 調為 768Mi，並限制 range。PR 已開（見下表）。另 Loki 端的查詢逾時設定屬後端，不在本報告處理範圍。

## Action Items
| AI-ID | 項目 | owner | 期限(YYYY-MM-DD) | 狀態(open/done/wontfix) | 連結 |
|---|---|---|---|---|---|
| AI-20261014-01 | 審核並合併 imgproxy memory limit guardrail PR | 使用者 | 2026-10-21 | open | https://github.com/example-org/example-repo/pull/123 |
| AI-20261014-02 | 確認 ImgproxyOOMKilled rule 的 Query A 是否補 `sum()` | 王小明 | TBD | open | - |
