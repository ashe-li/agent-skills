# hooks/ — 把 rules 從「常駐」改成「觸發式」

## 這個目錄解決什麼

`rules/` 底下的規則，一般用法是 symlink 進 `~/.claude/rules/common/`：

```bash
ln -s ~/Documents/agent-skills/rules/debug-triage-order.md ~/.claude/rules/common/
```

這樣做**每個 session 都會全文載入**。對「每次都要守」的紀律（coding style、
輸出格式）是合理的；但對**情境型**規則就是純浪費——`debug-triage-order` 只在
「debug 一個線上回報的 bug」時適用，`worktree-prompt` 只在「開工實作」那一刻適用，
其餘 session 付了 token 卻用不到。

實測：兩份合計約 1,120 tokens，佔某台機器常駐預算的 15%。

## 兩種安裝模式，二選一

| | 常駐 rule | 觸發式 hook |
|---|---|---|
| 安裝 | symlink 進 `~/.claude/rules/common/` | 註冊進 `settings.json` 的 `UserPromptSubmit` |
| 每 session 成本 | 全文（數百 tokens） | **0** |
| 何時生效 | 一直在 context 裡 | 使用者的訊息命中偵測條件時注入 |
| 適合 | 每次都要守的紀律 | 情境型、只在特定任務適用 |
| 風險 | 長 context 稀釋注意力 | 偵測條件漏接就不會提醒 |

**不要兩個都裝**——會在同一個 session 裡看到規則兩次。

## 安裝（觸發式）

```jsonc
// ~/.claude/settings.json
{
  "hooks": {
    "UserPromptSubmit": [{
      "hooks": [
        { "type": "command", "command": "bash ~/Documents/agent-skills/hooks/debug-triage-order-hint.sh" },
        { "type": "command", "command": "bash ~/Documents/agent-skills/hooks/worktree-prompt-hint.sh" }
      ]
    }]
  }
}
```

repo 不在預設位置時設 `AGENT_SKILLS_DIR`，hint 訊息裡的規則全文路徑會跟著調整。

## 設計原則

1. **只注入 hint，不阻擋。** `UserPromptSubmit` 走 `additionalContext`，
   偵測錯了最多是多一段文字，不會擋住任何操作。
2. **slash command 開頭一律跳過。** 使用者已明確指定 skill 時不要插話。
3. **高精度優先於高召回。** `debug-triage-order-hint` 要同時命中「debug 訊號」
   與「可觀測環境訊號」才觸發——只講「這段程式有 bug」不算，那是本地邏輯題，
   prod-first probe 不適用。誤報的成本是雜訊，會讓人把整個 hook 關掉。
4. **hint 要自包含。** 訊息本身就帶可執行的重點（三條順序、worktree 指令），
   不能只寫「請參閱某某規則」——那等於沒提醒。

## 驗證

每個 hook 都可以直接餵 JSON 測：

```bash
echo '{"prompt":"線上文章頁圖片壞掉，dev 正常，幫我查"}' | bash hooks/debug-triage-order-hint.sh
# 命中 → 輸出 hookSpecificOutput JSON；不命中 → 無輸出
```

改偵測條件後，正反案例都要跑：漏接（該提醒沒提醒）與誤報（不相干的話也提醒）
是兩種不同的失敗，只測其中一邊會過度放寬或過度收緊。
