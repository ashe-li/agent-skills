# release-pr golden set 驗收結果

**日期**：2026-09-06
**驗收對象**：SKILL.md Step 3.5（範圍相稱性擋門）＋ `scripts/check_release_fixtures.py`（機械完整性擋門）
**方法**：判讀層對 fixtures 逐條比對 `expected_*`；機械層實跑 script（含 `--self-test` 突變測試）。

## 兩層，強度不同，不要混為一談

| 層 | 驗什麼 | 能否機械化 | 落點 |
|---|---|---|---|
| **判讀層** | 這次產出的 body 取捨對不對 | ❌ 需人或 agent | SKILL.md Step 3.5 的強制步驟 |
| **機械層** | golden set 自身的完整性 | ✅ | `scripts/check_release_fixtures.py`（CI） |

**機械層驗不了「這次的取捨對不對」。** 把 CI 綠當成 Step 3.5 做過了，就是 KB `ci-green-doesnt-mean-your-new-test-ran-check-collected-count` 的重演。

## 機械層：實跑結果

```
$ python scripts/check_release_fixtures.py --repo-dir . --base-ref origin/main
::notice::release-pr/fixtures: 10 個 fixture，manifest 對得上
golden set 擋門：1 個 fixtures 目錄，0 個問題        exit=0
```

### 守衛自己的突變測試（`--self-test`，不依賴 pytest）

**先證明它會擋，再讓它上崗。** baseline 必須乾淨，五個突變必須全被擋下：

```
PASS  baseline（合法 golden set）              exit 0
PASS  added_step_without_fixture               → 擋下
PASS  orphan_fixture                           → 擋下
PASS  no_negative_control                      → 擋下
PASS  manifest_points_at_missing_file          → 擋下
PASS  id_prefix_mismatch                       → 擋下

self-test：6 個情境，0 個失敗
```

> **過程中抓到一次假綠**：第一輪把 script 複製進臨時 repo 的工作目錄，`git checkout main` 把未追蹤的它一起清掉，兩個情境回 `exit=2`（檔案不存在）而差點被記成「正確擋下」。**exit 非零不等於守衛生效——要看錯誤訊息內容。** 已重做，script 置於 repo 外。

## 判讀層：fixture 逐條結果

| Fixture | kind | 規模 | 期望 | 結果 |
|---|---|---|---|---|
| 01 warm canary Day 2（#8124）| regression | 1 檔 / +23−7 | TRIM 至 900–1400 | 3993 → 1053，**修正後 PASS** |
| 02 sitemap 每作者上限（#8121）| negative-control | 3 檔 / +420−2 | KEEP 2000–3200 | 2658，未觸發修剪，**PASS** |
| 03 大批次（#7969）| negative-control | 64 檔 / +3353−697 | KEEP 6000–9000 | 7897，未觸發修剪，**PASS** |
| 04 否定式敘述 scope 塌陷 | regression | — | QUALIFY | 判讀規則已定義 |
| 05 批次內自我修正誤列 Bug Fix | regression | — | RECLASSIFY | 判讀規則已定義 |
| 06 body 在 PR 開啟期間過期 | regression | — | RERUN | 判讀規則已定義 |
| 07 口頭敘事 vs 實際 merge state | regression | — | BLOCK | 判讀規則已定義 |
| 08 squash-merge 枚舉漏算 | regression | — | REPLACE | 判讀規則已定義 |
| 09 branch filter 造成空綠 check | regression | — | BLOCK | 判讀規則已定義 |
| 10 body 引用的 commit hash 未驗證 | regression | — | BLOCK | 判讀規則已定義 |

**02／03 在加入 Step 3.5 後沒有被誤剪**，這是本組最重要的一項——這道關卡若寫成「一律縮短」就會在這兩個 fixture 上失敗。

## 涵蓋率

初版只涵蓋 **1 種**失效（scope bloat）。現為 **8 種**，KB 出處已逐一核對存在（`coverage.md`）。

**已知未涵蓋 2 種**（誠實列出，見 `coverage.md` 底部）：

1. `merge-commit-detection-by-parent-count-not-message-prefix` — 與 fixture 08 主題相鄰但判準不同（parents count vs message prefix），值得獨立成 fixture
2. `release-notes-acceptance-list-is-executable-not-prose` — **SKILL.md 的 Step 2.5／3.5／4.5 目前都沒有對應檢查點**，應先評估要不要加 step 再補 fixture，否則會測到 skill 尚未涵蓋的行為

## 侷限（誠實揭露）

1. **fixture 01 的 PASS 是人工修正後的結果**，不是重跑 Step 3 自動產生的。**Step 3.5 判讀層的實際有效性仍標 `provisional`** —— 要等下一個 release PR 在未經提示的情況下初版就落在合理區間才能解除。機械層不受此限（已有突變測試）。
2. **04–10 只定義了判讀規則，沒有真實回放**。它們的 `expected_verdict` 來自 KB 記載的失效，但沒有像 01 那樣「實際跑一次 → 失敗 → 修正」的閉環。**嚴格說是規格，不是通過的測試。**
3. **次指標的樣本只有單一 repo、4 個 PR**（每檔字元 123／389／886／1053）。換 repo 或團隊寫作習慣就不適用，是聞味道用的不是門檻。
4. **07 與 10 是推論延伸**：07 的 KB 原文本身是抽象示例（PR A/B/C 非真實編號）；10 的 KB 原案例來自 `/kb-review` 的 kb-compile 產出而非 release PR。兩者都已在各自 fixture 內文標明，不要當成 KB 直接記載的 release-pr 情境。
5. **08 同屬延伸**：KB 原情境是「清點某期間有哪些 release PR」，fixture 把它延伸到 Step 2.5 的「彙整 N 個 PR」場景，已在情境段明講。
6. **04／06／09 的部分規模數字 KB 未記載**，fixture 以 `note` 說明而非填假數字。

## 下次驗收的觸發條件

下一個 release PR 產出時記錄：初版 body 字元數、每檔字元、Step 3.5 是否觸發修剪、修剪掉什麼、以及 04–10 是否有任一條命中。若初版就落在合理區間且無需修剪，把侷限第 1 點的 `provisional` 拿掉。
