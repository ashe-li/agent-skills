---
fixture_id: "03"
kind: negative-control
pr: sosreader/vocus-web-ui#7969
scale:
  content_commits: 28
  files: 64
  additions: 3353
  deletions: 697
observed_body_chars: 7897
expected_body_chars: [6000, 9000]
expected_verdict: KEEP
expected_gate: fact-check-agent-required
notes: 最大的既有批次；每檔字元 123 是四個樣本裡最低的，示範次線性關係
---

## 情境

28 個內容 commit、64 個檔案、跨多個獨立 feature 的發版批次。body 7897 字元。

## 為什麼長是對的

**每檔字元 = 123**，是四個樣本裡最低的（#8121 是 886、修正後的 #8124 是 1053）。也就是說這份 body 相對於它要交代的內容，密度反而是最高的——長是因為要交代的東西真的多，不是因為傾倒。

reviewer 要核准 64 個檔案的變更，需要知道每個 feature 各自改了什麼、彼此有無交互作用。這些內容沒有單一的外連目標可以取代（分散在 28 個 commit 與各自的 feature PR 裡，而 release PR 的職責正是把它們收攏成一份可讀的清單）。

## 這個 fixture 額外測的東西：擋門必須觸發

SKILL.md 對 >20 commits 或 >40 檔的批次要求派 fact-check agent。本案的實測結果（記錄於 `knowledge-base/wiki/learned/release-notes-transcribed-from-commits-inherit-their-errors.md`）：

- 約 **62 條 claim**，其中 **錯 2 條、誤導 5 條**
- **檔案路徑與符號名稱 0/30 錯**

推論：**查核預算要放在否定式敘述與跨 PR 一致性，不是路徑**。路徑類的 claim 幾乎不會錯（照抄自 diff），會錯的是作者自己歸納出來的斷言，尤其是「不動 X」「不新增 X」這種在單一 PR 當下為真、但在整個批次 scope 下為假的句子。

## 判定規則

| 條件 | 判定 |
|---|---|
| 產出 <6000 字元 | **FAIL**（過度修剪，64 檔交代不完）|
| 未派 fact-check agent 就寫回 PR | **FAIL**（Step 2.5／4.5 擋門未執行）|
| 6000–9000 字元、擋門有跑、否定式敘述都帶 scope 限定詞 | **PASS** |

## 三個 fixture 合起來要建立的判準

| | 規模 | body | 每檔字元 | 為什麼 |
|---|---:|---:|---:|---|
| 01 修正後 | 1 檔 | 1053 | 1053 | 內容少，且多數脈絡已在別處 → 外連 |
| 02 | 3 檔 | 2658 | 886 | 內容少但**證據鏈首次出現** → 留下 |
| 03 | 64 檔 | 7897 | 123 | 內容多且**只有這裡收攏得起來** → 留下 |

**「每檔字元」單調遞減不是巧合，是次線性關係的表現。** 01 的原始草稿是 3993 字元 / 1 檔，會讓這條曲線在最左端翹上去——那個翹起就是傾倒的訊號。
