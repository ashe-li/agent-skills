---
fixture_id: "11"
kind: regression
pr: "web-app 近三次 release PR（2026-10-23、2026-10-27 KYC、2026-10-27 另一次）的 Step 4.5 退回紀錄；PR 號與逐條錯誤清單 KB 未記載，此處不編造"
scale:
  note: 規模未記載；本 fixture 測的是流程順序（起草前有無 claim ledger），不依賴特定 PR 的檔案數
source_kb: wiki/learned/release-notes-transcribed-from-commits-inherit-their-errors.md
expected_verdict: BLOCK
notes: 實驗性。起草前先產 claim ledger 是否能降低 Step 4.5 退回輪數，需接下來 3 次 release PR 驗證；退回輪數沒下降就 revert（連同本 fixture 與 coverage 列）。三次退回輪數（3 輪／2 輪／1 條）為使用者提供的觀察值，來源是當次 Step 4.5 驗收員輸出，非本 repo 可重算
---

## 情境（Step 2.5 ledger → Step 3 → Step 4.5 的銜接）

近三次 release PR 都走了 `/release-pr`，但 Step 2.5 只拿檔案清單做四個零成本檢查，**沒有逐條 claim 的證據表**；逐條對照 diff 要到 Step 4.5 才由 fresh-context 驗收員第一次做，於是錯誤在驗收員那裡才浮現，body 被退回修了 3 輪／2 輪／1 條。把對照表提前到起草前，目的是讓作者在動筆時就只用有證據的說法。

## 觀察到的失效

兩種形態，機制相同——body 的說法沒有可重跑的證據指令撐著：

1. **ledger 外的說法**：作者起草時順手補了一句「這個改動不影響 X」或「順帶重構了 Y」，ledger 裡沒有這一列，也就沒有人跑過證據指令。
2. **ledger 判「無法佐證」的說法仍進 body**：例如 commit 尾段的「本機 N suites 全綠」在 diff 查不到，判了無法佐證卻因為「看起來是好消息」被留下。

## 期望產出

起草前產出 ledger（`PR# | 說法 | 證據指令 | 關鍵輸出 | 判定`），Step 3 的每條 bullet 都對得回一列「正確」。Step 4.5 驗收員逐列重跑證據指令，並反向把 body 每條 bullet 對回 ledger：

```bash
gh api repos/<repo>/compare/<base>...<head> \
  --jq '.files[] | "\(.status)\t+\(.additions)/-\(.deletions)\t\(.filename)"'
gh pr view <PR> --repo <repo> --json files --jq '.files[].path'
```

對不上 ledger 的 bullet，或對到「無法佐證」列的 bullet，驗收員記 `通過?=否`，擋門規則不變（不自動寫回 PR）。

## 判定規則

| 條件 | 判定 |
|---|---|
| body 有 bullet 在 ledger 找不到對應列 | **FAIL**（ledger 外說法）|
| body 有 bullet 對到判定「無法佐證」的列 | **FAIL**（無法佐證的說法進了 body）|
| body 沒有 ledger 外說法，且 ledger 每列證據指令重跑結果與判定一致 | **PASS** |
| 起草前沒有 ledger（Step 4.5 才第一次逐條對照）| **FAIL**（流程順序錯誤，退回補 ledger）|

## 反例警告

ledger 是要讓 body 有據可查，**不是要讓 body 變短或變空**。「無法佐證」的說法正確處理是改寫成可查證版本（補 scope 限定詞、改指向 check runs）再重列，不是一律刪句；同理，不要為了讓 ledger 好看而只列最安全的說法，把重要但難查的影響範圍宣告（參見 fixture 04）排除在外。ledger 也不是人工關卡：產完直接進 Step 3，整條鏈跑完才回報使用者。
