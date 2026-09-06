---
fixture_id: "10"
kind: regression
pr: "延伸案例：2026-05-06 kb-review batch 2（vocus-web-ui，非 release PR 情境，是 kb-compile 產出的 wiki 頁）"
scale:
  note: KB 原情境非單一 release PR，不填 files/additions；本 fixture 將偵測技術套用到 Step 4.5 的 fact-check 擋門
source_kb: wiki/learned/commit-hash-hex-validity-fabrication-detector.md
expected_verdict: BLOCK
notes: KB 原案例來自 kb-compile 而非 /release-pr，套用到 Step 4.5 是本 fixture 的推論延伸，已在情境段明講
---

## 情境

**KB 原案例**：`/kb-review` 批次 2，7 份 wiki 頁由平行的 haiku subagent 撰寫，其中引用了具體 git commit hash 當佐證。**本 fixture 的延伸**：release PR body 若在 Step 3／Step 4.5 引用具體 commit hash（例如「詳見 commit abc1234」）當佐證，同樣的機制風險成立——fresh-context 的 fact-check agent 必須驗證每個被引用的 hash，而不是信任「看起來有引用來源」這件事本身。

## 觀察到的失效

git short SHA 是 hex（`[0-9a-f]{7,40}`），`g`–`z` 絕不合法。KB 原案例的實測：7 份 wiki 頁裡 **4 份**含完全捏造的 commit 引用，hash 形如 `da2e3f4g5`、`fc4g5h6i7`——`g`／`h`／`i` 都是非法字元，**這是零 false positive 的機械判準**。全 hex 但過度規律（如 `a1b2c3d4e`）則是次一級的可疑訊號。

危險之處在於：**「Sources Cited」這類結構化區塊會製造虛假信任**——讀者看到「有引用來源、有 hash」就傾向直接相信，而 LLM 產出這類區塊時恰恰是為了讓稀薄的材料看起來完整，捏造正好藏在這裡。本案例的捏造內容不只是 hash，整頁內容本身就是虛構的，不是「引用對了但描述錯了」的輕微失準。

## 期望產出

Step 4.5 的 fact-check agent 對 body 內每一個 commit hash 引用跑三層查核：

```bash
# Layer 1：hex 合法性（零 false positive）
grep -rE 'commit [a-f0-9]*[g-z][a-f0-9]*\b' <body 檔案>

# Layer 2：在來源中確實存在
gh api repos/<repo>/compare/<base>...<head> --jq '.commits[].sha' | grep -c '<hash>'

# Layer 3：主題一致（hash 真實但描述被誤植）
git show <hash> --stat
```

Layer 1 命中 ⇒ 直接判定捏造，不必等 Layer 2。Layer 1 通過但 Layer 2 查無 ⇒ 同樣判定捏造（hash 合法不代表存在於這次 compare 範圍）。兩者皆過才進 Layer 3 核對描述是否對得上。

## 判定規則

| 條件 | 判定 |
|---|---|
| body 引用的 commit hash 含 `g`–`z` 字元 | **FAIL**（Layer 1 判定捏造，無條件）|
| hash 合法但未出現在 `compare` 的 commits 清單裡 | **FAIL**（Layer 2 判定捏造）|
| hash 存在但描述的內容與 `git show` 的實際 diff 不符 | **FAIL**（Layer 3，誤植）|
| 每個引用的 hash 都通過三層查核，且描述與實際 diff 一致 | **PASS** |

## 反例警告

不要因此在 body 裡完全避免引用具體 commit hash——真實、可查核的 hash 引用比只寫「已修正」更利於 reviewer 溯源，是有價值的寫法。要擋的是**未經三層驗證就信任任何看起來像引用的內容**，尤其是結構工整的「Sources Cited」區塊；不是引用行為本身有問題。同理，也不該把「有 Sources Cited 區塊」本身當成扣分項——它只是提高了查核的優先序，不是判定捏造的依據。
