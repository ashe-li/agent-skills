# Non-Neutral Verification（非中性驗證：綠得太乾淨是警訊）

Scope: Apply when claiming a fix works, a guard fires, or a test protects something — i.e. any moment the evidence is「跑了測試，綠了」。

核心：**驗證的價值不在「綠」，在你能不能讓它「紅」。** 拿不出紅的綠，等於沒驗。

## 三條規則

### 1. 還原步驟本身要驗

非中性驗證＝把受測改動還原成 before，確認測試會紅，再套回修正確認轉綠。
**但「還原」這個動作本身會靜默失敗**，一旦失敗，兩輪跑的都是修好的程式碼，你會看到「綠→綠」卻以為是「紅→綠」。

還原用**明確路徑的 checkout**，不要用 stash：

```bash
git checkout <ref> -- <明確路徑1> <明確路徑2>   # 取得修改前版本
（跑測試 → 必須紅）
git checkout HEAD -- <同樣路徑>                 # 還原
（跑測試 → 綠）
```

**禁用 `git stash push <paths>` 做這件事**，兩個疊加的坑：

- 路徑**已 commit、工作區無修改**時，`stash push <paths>` **靜默什麼都沒收**（不報錯、不回非零）
- 因為沒東西可 stash，緊接著的 `git stash pop` 會彈出 **stack 頂端別人的 entry**（stash stack 是**全 repo 共用的**，worktree 也共用），在無關檔案造成 `UU` 衝突

**判準：出現 0 紅時不要往下走。** 正確反應是「不對，這條應該要紅」，回頭查還原有沒有生效。
`grep -c <新符號>` 可以當輔助檢查，但**當改動是「移除某個符號」時它會失效**（修正後也是 0）——
最可靠的證明始終是那個「紅」本身。

### 2. 驗收環境要有鑑別力

假綠的第二種來源不是測試邏輯，是**驗收環境缺了讓 bug 得以成立的前提**。

典型：bug 只在某 feature flag 開啟時才會發生，而驗收用的環境（preview / staging）**沒帶那個 flag**。
修正前的程式碼在那個環境會產生一模一樣的輸出 → 綠由環境造成，不是由修正造成。

**做法：在驗收環境本身跑一次對照組。** 把改動還原成 before，在**同一個環境**重跑：

- 出現紅 → 環境有鑑別力，正式輪的綠才算數
- 仍然綠 → **這個環境驗不了這件事，綠要撤回，不是通過**

flag-gated 的改動，動手前先比對各環境的 build-args：

```bash
grep -nE "NEXT_PUBLIC_[A-Z_]*=" .github/workflows/{build-production,deploy-*,preview-*}.yaml
```

差異就是「哪個環境驗得了、哪個驗不了」的答案。

### 3. 用「指紋」提早識破環境問題

挑一個**同時受該前提影響、但與受測改動無關**的可觀測值，放進驗收清單當第一項。

例：flag 開 → `og:image` 是 `img.vocus.cc/<sig>/w:1200/f:jpg`；flag 關 → `resize-image.vocus.cc`。
一眼判斷環境的 flag 狀態，不必翻 CI 設定，而且它比被驗的東西**更早**暴露環境問題。

### 連帶：發現環境缺前提，那本身是一個獨立缺口

「驗收環境缺 prod 有的 flag」代表**該類改動在那個環境永遠驗不了**，不只這一次，要單獨記一筆。
但補 flag 常有連帶設定（各環境指向不同的 in-cluster service 之類），
**別順手補一半反而把環境弄壞**——列成另案。

## Why

2026-08-25 單一 session 內同一類失敗踩了三次，兩次差點讓沒驗過的東西冒充驗過：

1. **還原沒生效**：補測試時用 `git stash push <paths>`，但那些檔案已 commit、工作區乾淨 → stash 靜默收不到 →「修改前」那輪跑的是修好的程式碼，**11/11 全綠**。緊接的 `pop` 彈出 repo 既有的 `lint-staged automatic backup`，在三個無關檔案造成衝突。改用 `git checkout origin/<base> -- <明確路徑>` 重跑，真實結果是 **9 紅 → 全綠**。
2. **環境沒有鑑別力**：拿 preview env 驗一個 flag-gated 的修正，得到「全部正確、零筆壞值」。事後查 `preview-deploy.yaml` **沒有帶那個 flag**（`build-production.yaml` 與 `deploy-hotfix-k8s.yaml` 都有）→ 修正前的程式碼在該環境會產生相同輸出，PASS 全數撤回。改在本地帶 flag 跑 production build，並補對照組，才拿到有鑑別力的結果。
3. **指紋救回一次**：同一輪本地驗證中，`og:image` 的 host 被當作 flag 狀態指紋放進斷言，直接自證「flag 真的開了」，避免重蹈第 2 點。

三次的測試邏輯都是對的，壞的都在「還原」與「環境」。

相關：`rules/debug-triage-order.md`（驗收跑既有 spec、只跑一輪 fresh）
