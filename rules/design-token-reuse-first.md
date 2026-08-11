# Design Token Reuse First

Scope: Apply when writing or modifying UI styles that correspond to a design-spec token (typography、color、spacing、radius 等 Figma token/variable)，especially in repos where 多套樣式系統並存（Tailwind utility + styled-components / CSS-in-JS）。

## 規則

1. **手寫任何樣式值之前，先 grep 現成 token/utility**。至少查：
   - Tailwind `@utility` 定義檔（如 `styles/vocus-typography.css`、`styles/global.css`）
   - theme / design-token config（CSS variables、`tailwind.config.*`、`@theme` 區塊）
   - 查法：拿 Figma token 名（如 `Label3-Medium`）的關鍵字全 repo `grep -ri "label3"`，不要只看正在編輯的檔案

2. **兩套樣式系統並存 ≠ 只能用舊的**。styled-components 元件的 markup 一樣可以掛 Tailwind utility class（`className="label3-medium"`），utility 管 token 值、CSS-in-JS 管 utility 蓋不到的部分（狀態、巢狀 selector、動畫）。**預設用新系統（Tailwind utility）承載 token 值**，不要因為該元件的其他樣式住在 CSS-in-JS 就把 token 值手抄進去。

3. **多個 DOM 生產端共用同一組 class 時，逐一掛 class**：CSR 元件、SSR 字串產生器（如 `formatDom.js`）都要同步加，並確認 Tailwind content 掃描涵蓋字串產生器的檔案（掃不到的 class 不會產出 CSS）。

4. **AMP 副本可以例外**（使用者裁決 2026-08-11）：AMP 頁面只吃自己的 inline CSS，維持獨立副本即可，不強制納入 utility 遷移。

5. **真的掛不了 class 才允許手抄值**（例如第 3 點的掃描限制實測過不去），且必須：
   - 註解標明對應的 Figma token 名與「為什麼不能用 utility」
   - 在 PR description 標為 tech debt，指向未來收斂路徑（如 styled-migration）

## 具體 case（2026-08-11，vocus-web-ui PDT-10625）

投票元件照 Figma 稿改樣式時，`contexts/lexical/styles/PollNode.style.js`（styled-components template literal）把 `Label3-Medium` 的四個值（14px / 500 / 16px / letter-spacing 1px）手抄進 `.Poll__optionTextVotesCount`，附註解引 Figma token 名。事後發現 repo 早有一模一樣的 `@utility label3-medium`（`styles/vocus-typography.css`）。

實作者的推理是「styled-components 不走 Tailwind 處理器，`@apply` 展不開」——這只對了一半：**template literal 裡面確實用不了，但 markup 上掛 class 完全可行**，兩套系統本來就並存。正確做法是在 `PollComponent.tsx`（CSR）與 `formatDom.js`（SSR 靜態副本）的對應元素掛 `label3-medium`，CSS-in-JS 只留 utility 蓋不到的部分。

教訓：「正在編輯的檔案吃不到 token」不等於「這個 repo 用不了 token」。判斷層級要放在 **markup 能不能掛 class**，不是**當前樣式檔能不能 @apply**。

## Why

手抄值會讓 design token 改版時漏改（token 改了、抄的值不會跟著動），且同一個 token 在 repo 裡出現多份定義後，後續維護者無從判斷哪份是權威。此 rule 把「查 token → 掛 utility → 例外才抄值＋標記」變成預設順序，避免每次都靠 review 抓。
