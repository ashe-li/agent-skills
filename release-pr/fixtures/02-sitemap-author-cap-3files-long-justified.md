---
fixture_id: "02"
kind: negative-control
pr: sosreader/vocus-web-ui#8121
title: "Release：article sitemap 每作者每日 20 篇上限，擋灌量帳號死連結"
scale:
  content_commits: 1
  files: 3
  additions: 420
  deletions: 2
observed_body_chars: 2658
expected_body_chars: [2000, 3200]
expected_verdict: KEEP
notes: 單一 PR、僅 3 檔，但 body 2658 字元完全正當——用來擋「一律縮短」的過度修剪
---

## 情境

規模與 fixture 01 同屬「小批次」（1 個內容 commit、3 個檔案），若只看次指標的字元數，2658 相對 1 檔 1053 的 fixture 01 看起來「偏長」。

**但主指標判定它應該留下。**

## 為什麼長是對的

body 裡的每一段都直接影響 reviewer 能否核准：

| 段落 | 為什麼是核准所必需 |
|---|---|
| 死連結率與當日發文數的分桶統計（1 篇 0.3% → 201+ 篇 100%）| 這是 **20 這個數字的唯一依據**。沒有它，reviewer 無法判斷上限該不該是 20 |
| **數字口徑警告**（統計量測的是 `/api/seo/articles`，但產生器已於 #8103 換源到 `/api/articles`）| 直接影響「這些數字能不能拿來背書這次改動」，是 reviewer 最該知道的限制 |
| 台北時區日界的選擇理由 | 影響正確性判斷，且理由不在 diff 裡（在檔案註解裡，但 reviewer 讀 body 先於讀 diff）|
| 「不擴大 sitemap」的三個常數未變更 | **否定式宣稱**，是最容易出錯也最需要在 body 裡被明講的一類 |
| 「這是緩解不是修復」＋殘餘死連結歸屬後端 | 設定 reviewer 對成效的預期，避免事後被當成沒解決 |

**這些內容沒有別的地方有完整版本。** 與 fixture 01 的關鍵差異就在這裡——fixture 01 被外連的四個區塊，完整版本都已存在於 feature PR 與 KB 報告；這裡的證據鏈是首次出現。

## 判定規則

| 條件 | 判定 |
|---|---|
| 產出 <2000 字元、砍掉分桶統計或數字口徑警告 | **FAIL**（過度修剪）|
| 保留全部五段、落在 2000–3200 | **PASS** |
| 膨脹到 >3200（例如把整份 SEO 調查報告搬進來）| **FAIL**（同 fixture 01 的失效）|

## 這個 fixture 要防的回歸

看過 fixture 01 之後，實作容易學到錯誤的規則：「小批次 = 短 body」。**規模不是判準，內容的可外連性才是。**

一個一行的 feature flag 變更可以只要 1000 字元；一個 420 行、帶著全新統計依據的變更需要 2600 字元。兩者的差別不在 diff 大小，在於「reviewer 要核准它，需要知道多少他在別處讀不到的東西」。
