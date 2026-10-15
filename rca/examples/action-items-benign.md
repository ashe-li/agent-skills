---
title: Sentry TypeError RCA：瀏覽器擴充套件注入造成的雜訊（EXTERNAL／BENIGN → RECORD）
date: 2026-10-14
type: incident-rca
tags: [rca, sentry, browser-extension]
status: verified
action_items_open: 0
---
<!-- 範例（虛構案例，僅示範格式）：rca SYNTHESIS 報告，無待辦。 -->

## 合併時間軸
| 時間(UTC) | 事件 | 來源 lane | 證據 |
|---|---|---|---|
| 2026-10-14 01:05 | Sentry issue first seen，總數 41 | OBSERVABILITY | `issues/<id>/` |

## 矛盾清單與裁決
無矛盾。

## 根因
stack 頂端 frame 位於 `chrome-extension://` 來源，41 筆事件全部來自同一類擴充套件，信心 HIGH。Falsifier：如果 `tags` 分佈中出現非擴充套件來源的 frame 且數量 > 0，這個根因就不成立。

## 分類
EXTERNAL／BENIGN → RECORD。不是 REAL DEFECT（repo 內無涉及路徑的近 30 天 commit）、不是 DRIFT、不是 SELF-INFLICTED。

## 修復 PR 草稿與 rollback checklist
不適用（只記錄，不開 PR）。

## Action Items
無
