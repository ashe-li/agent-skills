# release-pr golden set 驗收結果

**日期**：2026-09-06
**驗收對象**：SKILL.md 的 Step 3（產生 description）＋ 新增的 Step 3.5（範圍相稱性擋門）
**方法**：對 fixtures 01–03 的 `input` 條件逐一判讀，比對 `expected_*`。fixture 01 有真實的失效與修正紀錄可回放，02／03 用已發版的實際 body 當標準答案。

## 結果總表

| Fixture | 規模 | 期望 | Step 3.5 前 | Step 3.5 後 | 判定 |
|---|---|---|---|---|---|
| 01 warm canary Day 2（#8124）| 1 檔 / +23−7 | TRIM 至 900–1400 | **3993 字元，FAIL** | 1053 字元，六個保留項齊全、四個區塊改外連 | **修正後 PASS** |
| 02 sitemap 每作者上限（#8121）| 3 檔 / +420−2 | KEEP 2000–3200 | 2658 字元，PASS | 2658 字元，PASS（未觸發修剪）| **PASS** |
| 03 大批次（#7969）| 64 檔 / +3353−697 | KEEP 6000–9000 ＋ 擋門須跑 | 7897 字元，PASS | 7897 字元，PASS（未觸發修剪）| **PASS** |

**3/3 通過。關鍵是 02／03 在加入 Step 3.5 後沒有被誤剪** —— 這道關卡若寫成「一律縮短」就會在這兩個 fixture 上失敗。

## fixture 01 的失效是真實發生的，不是模擬

這不是設計出來的測試案例。2026-09-06 實際產出 3993 字元的 body 並送出成 PR #8124，**經使用者指出後才修正**。原始長度來自 GitHub `userContentEdits` API 實測（`prev_chars: 3993` → `1053`），非事後估算。

被外連的四個區塊：

1. 七列 P95 證據表（P95／P50／P99／RPS／5xx／ReplicaSet／HPA／37 天 flap 統計）
2. 因果推導段（縮容早於尖峰 → 撐不住 → 三波擴容 → 冷啟疊加）
3. CodePipeline 機制完整說明（GitHubSource／CodeStar／`DetectChanges=false`）
4. 對 `plans/completed/evidence-frontend-warm-endpoint.md` 的更正

四者的完整版本**都已存在於 feature PR #8123 與 KB 報告 §4**。

## 保留項的驗證（防過度修剪）

修正後的 body 仍保有六項核准／部署所必需的資訊，逐項確認：

- [x] 批次範圍（1 PR / 1 內容 commit / 1 檔 / +23−7）
- [x] 唯一功能性變更是 `WARM_ENABLED` false → true
- [x] **serving path 零行為變更**（`startupProbe` 不在 diff、仍是 `/api/health_check`，故無物呼叫 `/api/warm`）
- [x] **生效時機**（helm values 不靠新 image，要等通過 `approve-prod-eks-deploy` 的 Deploy stage）
- [x] 回滾方式（revert，不要 `kubectl set env`）
- [x] 部署後 24h 觀察指標

第 3、4 項是本次**最容易被誤解**的兩條：沒有它們，reviewer 會以為「合併＝上線」。**它們也是最可能被一個只看長度的修剪規則砍掉的兩條**，所以 fixture 01 把它們明文列為 FAIL 條件。

## 誠實揭露：這組 golden set 的侷限

1. **樣本只有 4 個 PR、單一 repo**（vocus-web-ui）。次指標的「每檔字元」區間（123–1053）是從這 4 點歸納的，換 repo 或換團隊寫作習慣就不適用。**它是聞味道用的，不該被當門檻硬套。**
2. **02／03 是「已發版且未被抱怨」，不是「經過主指標逐段審查」**。它們作為負對照的效力來自「實際運作良好」，不是來自有人逐段驗證過每一句都必要。嚴格說它們是 weak positive。
3. **主指標仍需人判讀**。「reviewer 需不需要知道」沒有機械判準；本 golden set 提供的是判準的措辭與三個錨點，不是自動化檢查。
4. **fixture 01 的 PASS 是人工修正後的結果**，不是重跑 Step 3 自動產生的。要證明 Step 3.5 真的有效，需要下一次獨立的 release PR 在**未經提示**的情況下通過——**在那之前，這條的有效性標記為 provisional**。

## 下次驗收的觸發條件

下一個 release PR 產出時，記錄：初版 body 字元數、每檔字元、Step 3.5 是否觸發修剪、修剪掉什麼。若初版就落在合理區間且無需修剪，把本檔第 4 點的 provisional 拿掉。
