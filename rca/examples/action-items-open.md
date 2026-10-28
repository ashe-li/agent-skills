---
title: web-ui 前端 5xx 升高 RCA：dependency 升版造成 SSR 例外（REAL DEFECT → FIX）
date: 2026-10-14
type: incident-rca
tags: [rca, web-app, ssr]
status: verified
action_items_open: 2
---
<!-- 範例（虛構案例，僅示範格式）：rca SYNTHESIS 報告，2 筆 open。人名、日期、連結皆為示範值。 -->

## 合併時間軸
| 時間(UTC) | 事件 | 來源 lane | 證據 |
|---|---|---|---|
| 2026-10-13 09:12 | merge 依賴升版 commit `abc1234` | HISTORY | `git log --since=30.days -- package.json` |
| 2026-10-13 09:40 | release 上線 | OBSERVABILITY | Sentry `firstRelease` |
| 2026-10-13 09:43 | 告警 first seen，每小時事件數由 2 升至 310 | OBSERVABILITY | Sentry events 每小時 bucket |

## 矛盾清單與裁決
| # | A lane 說 | B lane 說 | 新證據 | 裁決 |
|---|---|---|---|---|
| 1 | INFRA：node 無異常 | OBSERVABILITY：同時段 restart 升高 | `kubectl get events` 時間窗內 node 事件數 0 | 與 node 無關 |

## 根因
依賴升版 `abc1234` 改變 SSR 序列化行為，信心 HIGH。Falsifier：如果 `git revert abc1234` 的 preview 部署後 5xx 仍高於 50/h，這個根因就不成立。

## 分類
REAL DEFECT → FIX。不是 SELF-INFLICTED（無手動改 live 狀態）、不是 DRIFT（IaC 與 live 一致）、不是 EXTERNAL（瀏覽器分佈無集中）。Loki 端的取樣設定屬後端，本報告不處理。

## 修復 PR 草稿與 rollback checklist
見 `.verification/2026-10-14/rca-web-ui-ssr/pr-draft.md`。

## Action Items
| AI-ID | 項目 | owner | 期限(YYYY-MM-DD) | 狀態(open/done/wontfix) | 連結 |
|---|---|---|---|---|---|
| AI-20261014-01 | 審核 PR 草稿並決定是否開 PR（HITL G1） | 使用者 | 2026-10-16 | open | .verification/2026-10-14/rca-web-ui-ssr/pr-draft.md |
| AI-20261014-02 | 為 SSR 5xx 補一條 error-rate alert rule | 李小華 | TBD | open | - |
