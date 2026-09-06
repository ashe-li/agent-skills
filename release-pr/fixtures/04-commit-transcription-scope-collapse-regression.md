---
fixture_id: "04"
kind: regression
pr: sosreader/vocus-web-ui#7969（另附 #8092 補充案例，該案例 scale 未記載於 KB）
scale:
  content_commits: 28
  files: 64
  additions: 3353
  deletions: 697
source_kb: wiki/learned/release-notes-transcribed-from-commits-inherit-their-errors.md
expected_verdict: QUALIFY
notes: 與 fixture 03 同一批次（#7969），但測的是 Step 2.5 的事實查核而非 Step 3.5 的範圍相稱性；#8092 案例的檔案規模 KB 未記載，標 provisional
---

## 情境（Step 2.5 的輸入條件）

Step 3 的材料是 commit message 轉寫，其中含兩類看起來人畜無害、實則會錯的句子：**否定式敘述**（「不動 X」「不新增 X」）與**絕對數字**（行數、物件數）。commit message 寫的時候都是真的，錯在轉寫時把它的真值範圍從「那個 commit／PR 當下」放大成「整個 release 批次」。

## 觀察到的失效

**否定式敘述塌陷**（#7969 批次內）：commit 訊息寫「不新增 JWT 解碼」（#7942，該 PR 確實沒有），三天後 #7945 為了在多顆同名 cookie 間 tie-break，在同一個 `utils/token.ts` 新增了 `decodeBase64Url()` 與 `getJwtExpiresAtMs()`。若原樣轉寫兩句相鄰 bullet，會在同一份 body 裡自己打自己。另一例：commit 寫「不動 `swrFetcher` / `httpClient` / `utils/token.ts` 的讀取順序」，但整批下來 `utils/token.ts` 是改動量第二大的原始碼檔（+150/−13），`getCtxTokenHeader` 的 SSR 分支整段改寫。

**量化數字塌陷**（#8092，2026-08-27）：redesign commit 自述「659 行 → 94 行」，實測 `git show origin/hotfix:<path> | wc -l` 是 **635**（差額 24 行是同批次前一個 PR 併入的事件面板）；inline style 物件實測 **38** 不是commit 寫的 41。兩個數字本身都沒錯，錯在把 commit scope 的數字掛在 release scope 的敘述下。#8092 的檔案／commit 規模數字 KB 未記載，此 fixture 該欄位標 provisional。

**整體查核結果**（#7969）：~62 條 claim，錯 2 條、誤導 5 條；**檔案路徑與符號名稱 0/30 錯**——查核預算不該平均分配。

## 期望產出

Step 2.5 對每一句「不動／未更動／不新增 X」跑：

```bash
gh api repos/<repo>/compare/<base>...<head> --jq '.files[].filename'
```

X 出現在清單裡就直接推翻。對每一個絕對數字跑：

```bash
git show <base>:<path> | wc -l
git show <commit>^:<path> | wc -l
```

兩者不同就代表 commit scope ≠ release scope。**修法是補回 scope 限定詞，不是刪句**：「本 PR 不新增 JWT 解碼；後續 #7945 為了 tie-break 才引入不驗簽的 `exp` 解析」、「頁面 635 → 94 行（相對 hotfix；redesign commit 當下的起點是 659 行，含本批次先前併入的事件面板）」。

## 判定規則

| 條件 | 判定 |
|---|---|
| body 原樣轉寫「不動 X」但 X 出現在 compare 檔案清單裡 | **FAIL**（否定式敘述未查核）|
| body 轉載 commit 自述的絕對數字，未對 base 重算 | **FAIL**（量化數字未查核）|
| body 把否定式敘述整句刪除、不補 scope 限定詞 | **FAIL**（過度修剪，見下）|
| 每句否定式敘述與絕對數字都補上 scope 限定詞、經 compare/`git show` 驗證 | **PASS** |

## 反例警告

修法不是「看到『不動 X』就整句刪掉」。這類句子常是重要的影響範圍宣告——真正該做的是**補回限定詞讓它在整批 scope 下仍為真**，而不是因為怕出錯就完全不寫否定式敘述。同理，絕對數字也不必刪，重算後照樣有價值（reviewer 需要知道實際改動幅度），只是要標明起點。
