# Security guidance（全域 reviewer 規則）

供 `security-guidance` plugin 的 model-backed review（end-of-turn / commit）載入，作為內建漏洞清單之外的補充。
非硬性 guardrail：違規會以 finding 提示 Claude 修正，不會 block 寫入。硬性阻擋請改用 hook 或 CI。

## Secrets（最高優先）

- 任何情況都不得把 secret 寫進 source、log、測試 fixture、commit message 或終端輸出：
  API key / token / password / JWT / private key / connection string。
- 憑證一律從環境變數或 secret manager 載入，不得硬編。
- 偵測到 secret 形狀（`sk_live_`、`sk-ant-`、`AKIA`、`ghp_`、`github_pat_`、`xoxb-`、`xoxp-`、`AIza`、PEM 私鑰區塊）即視為 finding。

## Web / TypeScript / React

- 禁止用未經 sanitize 的值餵 `dangerouslySetInnerHTML`、`.innerHTML`、`document.write`、`eval`、`new Function`。
- 所有外部輸入（query、body、header、URL param）在進 DB query / 檔案路徑 / shell 前必須驗證或參數化。
- fetch 外部 URL 前（`urllib.urlopen`、`requests.get`、`fetch`）若來源是 config 或使用者輸入，需防 SSRF：白名單 host、禁 internal IP。

## Python

- 不得 `pickle.loads` 不可信資料；不得 `os.system` / `subprocess(shell=True)` 帶未驗證輸入。
- ORM 一律參數化，不得字串拼接 SQL。

## Go / Swift

- Go：error 不得靜默吞掉；`exec.Command` 不帶 shell 拼接。
- Swift：不得把 token 存進 `UserDefaults`，敏感資料用 Keychain。

## 跨語言

- 比對 token / 簽章用 constant-time 比較（`crypto.timingSafeEqual` / `hmac.compare_digest`），不用 `==`。
- 弱演算法（MD5 / SHA1 用於安全用途、ECB 模式）視為 finding。
- 授權檢查不得只靠前端；後端每個受保護路由都要驗權限。
