# NemoClaw 系統架構與擴充規範

> Version: v1.0.0 | 相容 OpenClaw: 2026.5.22

---

## 一、系統概覽

NemoClaw 是一套讓 AI 沙盒（OpenClaw）能夠透過 Discord 與使用者互動的橋接系統。它不是一個單純的 Discord Bot，而是一個**三層式的代理架構**：

```
[ 使用者 Discord ]
       ↕  輪詢 / 發送
[ proxy.py — Discord 代理層 (Port 8888) ]
       ↕  HTTP Webhook (127.0.0.1:8888/callback)
[ api_server.py — API 伺服器層 (Port 80) ]
       ↕  LocalTunnel HTTPS
[ OpenClaw 沙盒 — AI 推理層 ]
       ↕  執行 Python 腳本
[ tools/food_picker.py / tools/manage_memory.py — 工具腳本層 ]
```

---

## 二、各層責任分工

### Layer 1：`proxy.py` — Discord 代理層

**職責：**
- 每 2 秒輪詢 Discord，抓取新訊息
- 把使用者訊息（含系統 Prompt、memory、資料檔）組裝成指令，傳入沙盒
- 接收 `api_server.py` 推送的 Webhook 回呼，轉發給 AI 沙盒
- 把 AI 的回覆發送回 Discord

**核心設計決策：**
- 使用 `queue.Queue` + 獨立 Worker Thread，避免 AI 推理（最長 5 分鐘）阻塞輪詢
- 使用 `seen_message_ids`（LRU 上限 1000）避免重複處理

**資料注入方式（每次呼叫沙盒時注入）：**

| 注入內容 | 說明 |
|---|---|
| `prompts/catchup_prompt.txt` | 系統指示（角色設定 + 可用 API 指令）|
| `memory.json` | 使用者長期記憶 |
| `morning_data.json` | 最新晨報資料 |
| `mail_data.json` | 最新信件摘要 |
| `ntu_data.json` | 最新 NTU COOL 通知 |
| `tools/food_picker.py` | 午餐推薦腳本 |
| `tools/manage_memory.py` | 記憶管理腳本 |
| `.api_key` | API Key（Base64 編碼注入，不洩漏 .env）|

---

### Layer 2：`api_server.py` — API 伺服器層

**職責：**
- 對外（透過 LocalTunnel）提供 REST API 給沙盒呼叫
- 在背景非同步抓取晨報、信件、NTU COOL 資料
- 管理 LocalTunnel 連線的自動重試與 URL 注入
- 處理排程鬧鐘、天氣、食物查詢等即時請求

**API 端點一覽：**

| Method | Route | 需要 API Key | 說明 |
|---|---|---|---|
| GET | `/health` | ❌ | 系統狀態監控 |
| GET | `/morning-report` | ✅ | 觸發非同步晨報抓取 |
| GET | `/mail` | ✅ | 觸發非同步信件抓取 |
| GET | `/ntu-cool` | ✅ | 觸發非同步 NTU COOL 抓取 |
| GET | `/weather` | ✅ | 即時天氣查詢 |
| POST | `/schedule` | ✅ | 設定鬧鐘提醒 |
| GET | `/food-nearby` | ✅ | 查詢附近餐廳（代理 OSM）|

**非同步抓取流程：**
```
[沙盒呼叫 GET /morning-report]
       ↓
[api_server 立即回傳 "請稍等..."]
       ↓
[背景執行 async_fetch_*() 抓取真實資料]
       ↓
[完成後 POST 127.0.0.1:8888/callback]
       ↓
[proxy.py 收到 Webhook → 轉發給沙盒 AI 總結]
```

---

### Layer 3：工具腳本層

**`tools/food_picker.py`** — 午餐推薦引擎
- 呼叫 `api_server.py` 的 `/food-nearby` 取得附近餐廳
- 讀取 `memory.json` 排除近 3 天吃過的店
- 讀取 `food_list.json` 加入個人收藏（優先推薦）
- 根據 `--mood` 參數過濾標籤

**`tools/manage_memory.py`** — 記憶管理器
- `add` / `list` / `del` — 管理待辦事項和記憶
- `food` — 記錄今天吃的餐廳
- `fav-add` / `fav-list` — 管理長期餐廳收藏

---

## 三、LocalTunnel 網址注入機制

這是整個系統最關鍵的設計：

```
api_server.py 啟動
       ↓
start_localtunnel() 依序嘗試 {PREFIX}-v1 ～ {PREFIX}-v5
       ↓
某個子網域連線成功，得到 actual_url
       ↓
update_prompt_file(actual_url) 執行 Regex 替換：
  - prompts/catchup_prompt.txt 中所有 loca.lt 網址 → 替換
  - tools/food_picker.py 中 HOST_API_BASE → 替換
       ↓
沙盒下次被呼叫時，拿到的 catchup_prompt 裡面
已經含有最新的連線網址
```

> **注意：** `prompts/catchup_prompt.txt` 和 `tools/food_picker.py` 裡面的 `loca.lt` 網址是「暫存佔位符」，**會在每次 api_server 啟動時被自動覆蓋**。不要在這兩個檔案裡手動寫死網址。

---

## 四、擴充規範

### 4.1 新增 API 端點

在 `api_server.py` 中加入新路由，請遵守以下規範：

```python
@app.route("/your-feature", methods=["GET"])
def your_feature():
    # 1. 一定要驗證 API Key
    err = verify_api_key()
    if err:
        return err

    # 2. 如果是耗時操作，使用非同步模式
    threading.Thread(target=async_your_feature, daemon=True).start()
    return "好的，我已經開始處理...", 200, {'Content-Type': 'text/plain; charset=utf-8'}

    # 3. 如果是即時查詢，直接回傳結果（如 /weather 的模式）
```

**非同步抓取函式規範：**
```python
def async_your_feature():
    data = {}
    try:
        # 抓取資料...
        data["your_key"] = "your_value"

        # 存成 JSON 檔，讓 proxy.py 下次注入沙盒
        with open("your_data.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # 完成後推播回呼給沙盒
        send_callback(format_your_report(data))
    except Exception as e:
        send_callback(f"[你的功能] 抓取失敗：{e}")
```

---

### 4.2 新增資料抓取模組（Fetcher）

每個 Fetcher 是一個獨立的 `.py` 檔，例如 `mail_fetcher.py`。請遵守：

- **對外只暴露一個主函式**，例如 `fetch_mail_data() -> str`
- **回傳格式**：純文字字串（讓 AI 能直接閱讀理解）
- **在 `api_server.py` 最頂部 import**，避免循環相依
- **失敗時回傳錯誤字串，不要 raise**

```python
# fetcher 範本
def fetch_your_data() -> str:
    """
    抓取某某資料並回傳格式化文字摘要。
    失敗時回傳錯誤訊息字串，不要 raise。
    """
    try:
        # ... 抓取邏輯
        return "【你的資料摘要】\n..."
    except Exception as e:
        return f"[錯誤] 無法抓取資料：{e}"
```

---

### 4.3 在 `prompts/catchup_prompt.txt` 新增 AI 指令

每個新功能都需要在 `prompts/catchup_prompt.txt` 中讓 AI 知道怎麼呼叫。格式如下：

```
- 你的功能描述 (觸發關鍵字1、觸發關鍵字2)：
```javascript
let res = await openclaw.tools.call("openclaw:core:exec", {
  command: "curl -sS --max-time 60 -H 'X-API-Key: $(cat /sandbox/.openclaw/workspace/.api_key)' https://YOUR_TUNNEL_URL.loca.lt/your-feature"
});
console.log(res.stdout || res);
```
```

> **注意：** 網址一律使用 `https://YOUR_TUNNEL_URL.loca.lt` 作為佔位符。`api_server.py` 啟動時會自動替換成真實的連線網址。

---

### 4.4 在 `proxy.py` 新增注入資料

如果您的新功能產生了需要傳入沙盒的資料檔（例如 `your_data.json`），請在 `proxy.py` 的 `data_files` 清單中加入：

```python
# proxy.py, process_and_send_to_sandbox() 函式中
data_files = [
    "morning_data.json",
    "mail_data.json",
    "ntu_data.json",
    "urgent_data.json",
    "tools/food_picker.py",
    "tools/manage_memory.py",
    "your_data.json",   # ← 加在這裡
]
```

並在 `.gitignore` 中加入這個資料檔（因為它是執行期自動產生的個人資料）。

---

## 五、資料流完整時序圖

```
使用者在 Discord 輸入「幫我看晨報」
  │
  ▼
proxy.py (poll_discord, 每 2 秒)
  │ 讀取 catchup_prompt.txt (來自 prompts/), memory.json, *_data.json
  │ 組裝指令 → 傳入 OpenClaw 沙盒
  ▼
OpenClaw 沙盒 (NemoClaw AI)
  │ 理解指令 → 決定呼叫 /morning-report
  │ curl https://{PREFIX}-v1.loca.lt/morning-report
  ▼
LocalTunnel (HTTPS 通道)
  ▼
api_server.py GET /morning-report
  │ 立即回傳「好的，請稍等...」
  │ 啟動背景 Thread: async_fetch_morning_report()
  ▼
沙盒 AI 看到「請稍等」→ 回覆 Discord「我去拿資料了」→ 結束本次推理
  │
  ▼ (背景，約 30 秒後)
async_fetch_morning_report() 完成抓取
  │ POST 127.0.0.1:8888/callback (含資料)
  ▼
proxy.py /callback
  │ 組裝訊息「爬蟲資料已收集完畢...」
  │ 傳入 OpenClaw 沙盒（再次推理）
  ▼
OpenClaw 沙盒 (NemoClaw AI)
  │ 讀取回傳的晨報資料
  │ 整理成口語化晨報
  ▼
proxy.py 把 AI 的回覆 → 發送到 Discord ✅
```

---

## 六、設定檔說明

| 檔案 | 版控 | 說明 |
|---|---|---|
| `.env` | ❌ 排除 | 所有密鑰與帳號 |
| `.env.example` | ✅ | 設定範本（供新使用者參考）|
| `config.json` | ❌ 排除 | 個人課表、個人設定 |
| `config.json.example` | ✅ | 設定範本（說明格式）|
| `prompts/catchup_prompt.txt` | ✅ | AI 行為規則（無個人資訊）|
| `prompts/morning_prompt.txt` | ✅ | 晨報格式 Prompt |
| `prompts/ntu_prompt.txt` | ❌ 排除 | 含個人學期資料，執行期產生 |
| `prompts/mail_prompt.txt` | ❌ 排除 | 含真實信件內容，執行期產生 |
| `food_list.json` | ✅ | 空的餐廳清單範本 |
| `memory.json` | ❌ 排除 | 個人記憶，執行期產生 |
| `*_data.json` | ❌ 排除 | 所有執行期資料 |
