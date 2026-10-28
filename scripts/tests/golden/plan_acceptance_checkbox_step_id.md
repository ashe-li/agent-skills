# Implementation Plan: SW Fallback Marker And Contents Retry Loop (minimal golden)

> 縮自 2026-10-27 web-app `sw-fallback-marker-and-contents-retry-loop` plan（33 steps）。
> 只留重現所需的區段：真 step 用粗體 step id、有 Dependencies；
> Acceptance Criteria 裡有兩行以 step id 開頭的 checkbox（原文照搬），
> 舊版 parser 會把它們當成 step header 註冊並覆蓋真 step。

## Overview

跟 bug 無關的背景說明，不含 checkbox。

## Implementation Steps

### Phase 0: 準備與診斷

- [ ] **S0.1** — 建兩個 sibling worktree
  - Files: （無）
  - Agent: `general-purpose`（Sonnet）
  - Action: 建 PR-A 與 PR-B 兩個 worktree
  - Dependencies: 無

- [ ] **S0.5** — 本機 baseline production build 加 headed baseline
  - Files: （無）
  - Agent: `general-purpose`（Sonnet）
  - Action: 跑 `pnpm build` 後量三件事
  - Dependencies: S0.1

- [ ] **S0.6** — 診斷 gate 裁決
  - Files: s0.6-gate.md
  - Agent: 主模型（Opus）
  - Action: 寫 G-A 與 G-B 兩條結論
  - Dependencies: S0.5
  - Why: 使用者要求先確認機制才實作

### Phase 5: 上線後觀察

- [ ] **S5.1** — merge 前抓 baseline
  - Files: s5.1-baseline.md
  - Agent: `general-purpose`（Sonnet）
  - Action: 用 Sentry `events-stats` 抓 14 天日量
  - Dependencies: S0.6

- [ ] **S5.2** — release 後 D+1、D+3、D+7 量測
  - Files: 同目錄 s5.2-d{1,3,7}.md
  - Agent: `general-purpose`（Sonnet）
  - Action: 以 release 篩選為主，日量為輔
  - Dependencies: S5.1
  - Why: R9

## Risks & Mitigations

| 風險 | 對策 |
|---|---|
| F3 處理錯，所有 upstream 都被標成 network | S1.1 的 F3 測試 |

## Acceptance Criteria

- [ ] S0.6 寫完 G-A 和 G-B 的裁決，附證據路徑；G-B 沒過就不實作 PR-B
- [ ] PR-A：SW 合成回應帶 `X-SW-Fallback`，page 端讀得到
- [ ] S5.2 的 D+7 報告：4 個 503 issue 來自新 release 的 event 約為 0、金絲雀上升、Loki 對照組不變

## Review Notes

- 沒有 checkbox
