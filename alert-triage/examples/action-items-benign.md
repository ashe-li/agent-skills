---
title: KubePodNotReady benign：Karpenter 新 node cold-start 窗內的暫態
date: 2026-10-14
type: incident-rca
tags: [alert-triage, karpenter, cold-start]
status: benign 記錄
action_items_open: 0
---

<!-- 範例（虛構案例，僅示範格式）：alert-triage 報告，benign，沒有 action item。 -->

## TL;DR
KubePodNotReady 在新 node 剛 provision 的 3 分鐘暫態窗內觸發，之後自行恢復，判定為 benign（HIGH）。不開 PR。

## Alert Payload（正規化）
| 欄位 | 值 |
|---|---|
| alertname | KubePodNotReady |
| namespace | arc-runners |
| startsAt | 2026-10-14T03:01:00Z |

## KB Priors
- 命中 Karpenter cold-start benign prior。

## Live 證據
- `kubectl get events -n arc-runners` 顯示 Pod 於 03:01 排程到新 node、03:04 Ready。信心 HIGH。

## 分類結論
benign false-positive，信心 HIGH。

## 處置
符合 cold-start 窗，不開 PR；記錄於本報告。

## Action Items
無
