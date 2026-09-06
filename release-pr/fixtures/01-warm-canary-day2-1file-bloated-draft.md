---
fixture_id: "01"
kind: regression
pr: sosreader/vocus-web-ui#8124
title: "Release：啟用 prod SSR warm-up kill switch（WARM_ENABLED Day 2）"
scale:
  content_commits: 1
  merge_commits: 1
  files: 1
  additions: 23
  deletions: 7
observed_draft_chars: 3993       # GitHub userContentEdits 實測，非估算
expected_body_chars: [900, 1400]
expected_verdict: TRIM
source_incident: knowledge-base/reports/2026-09-07-alert-triage-batch-oom-loki-imgproxy-p95.md
notes: 實際發生過的失效——草稿已送出成 PR，經使用者指出後才修正
---

## 情境（Step 3 的輸入條件）

作者剛完成一輪四則告警的 live triage，手上有：

- 一份 75 分鐘 P95 劣化的完整證據表（P95／P50／P99／RPS／5xx／HPA replicas／37 天 alert state history）
- CodePipeline 的部署機制細節（helm values 走 GitHubSource、Deploy stage 手動核准）
- 一份既有 KB 文件的誤導性宣稱與其更正
- feature PR #8123 的完整 body（已含上述全部內容）
- KB 報告 §4（已含上述全部內容）

而 release 的實質內容是：**`WARM_ENABLED` 由 `"false"` 改成 `"true"`，一行**，其餘 22 行 diff 全是註解。

## 觀察到的失效

產出 3993 字元的 body，內含四個與「要不要核准這次部署」無關的區塊：

1. **七列的 P95 證據表**（P95 峰值／基線、RPS、5xx、P50/P99、ReplicaSet、HPA、37 天 flap 統計）
2. **因果推導段**（縮容早於尖峰 → 30 顆撐不住 → 三波擴容 → 冷啟疊加）
3. **CodePipeline 機制的完整說明**（GitHubSource／CodeStar／`DetectChanges=false` 的來龍去脈）
4. **對 `plans/completed/evidence-frontend-warm-endpoint.md` 的更正**（PR #7753 改的是 staging-v1 不是 prod）

四個區塊的**完整版本都已經存在於 #8123 與 KB 報告 §4**。

### 為什麼會發生

不是判斷力問題，是**位置壓力**：作者剛查完，脈絡在手上，而 release PR 是離手前最後一個可以傾倒的地方。傾倒的動機來自作者的狀態，與讀者的需求無關。

同構於 `2026-08-19-alert-description-bloat-audit`：那 15 條 Grafana rule 的作者也都剛查完 RCA。

## 期望產出

字元數落在 **900–1400**，且必須：

**保留**（reviewer 核准／部署時需要）：
- 這批的範圍（1 PR / 1 內容 commit / 1 檔 / +23−7）
- 唯一功能性變更是 `WARM_ENABLED` false → true
- **serving path 零行為變更**，因為 `startupProbe` 不在本次 diff、仍是 `/api/health_check`，故目前無物呼叫 `/api/warm`
- **生效時機**：helm values 不靠新 image，要等通過 `approve-prod-eks-deploy` 的 Deploy stage
- 回滾方式：revert 本 PR，不要 `kubectl set env`
- 部署後 24h 的觀察指標

**外連**（上列四個區塊）：一句話帶過 + 指向 #8123 與 KB 報告 §4。

## 判定規則

| 條件 | 判定 |
|---|---|
| body 仍含完整證據表或因果推導段 | **FAIL**（主指標未過）|
| 刪到連「serving path 零變更」或「生效時機」都不見 | **FAIL**（過度修剪，這兩條是核准所必需）|
| 900–1400 字元且六個保留項齊全、四個外連項不內嵌 | **PASS** |

## 反例警告

刪掉第 1、2 點（serving path 與生效時機）會讓 reviewer 誤以為「合併＝上線」，而實際上這個變更要等下一次 Deploy stage 核准才生效——**這正是本次最容易被誤解、也最該留在 body 裡的一條**。長度不是目標，正確的取捨才是。
