# 🦞 NemoClaw — 大學生活 AI 助理

[![Version](https://img.shields.io/badge/version-1.0.0-orange.svg)](VERSION)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-green.svg)](https://python.org)
[![OpenClaw](https://img.shields.io/badge/OpenClaw-2026.5.22-purple.svg)](https://openclaw.ai)

一個住在 OpenClaw 沙盒裡的 AI 助理系統，透過 Discord 與使用者互動，能夠自動整理晨報、查詢信件、瀏覽 NTU COOL 通知，並幫使用者決定今天要吃什麼午餐。

> 專為大學生設計：幫你追蹤課堂公告、整理每日資訊、還有最重要的——幫你決定今天吃什麼！

📐 **想了解系統架構或如何擴充新功能？** → 請閱讀 [ARCHITECTURE.md](ARCHITECTURE.md)

---

## 📖 功能列表

| 功能 | Discord 指令 / 描述 |
|---|---|
| 🌅 **每日晨報** | 每天早上 8 點自動推播晨報（天氣 / 信件 / NTU COOL）|
| 📬 **信件查詢** | 查詢最新 Gmail 與 NTU Webmail 信件 |
| 📚 **NTU COOL** | 抓取最新課程公告與作業截止日 |
| 🍱 **午餐決策** | 根據心情、地點、喜好推薦附近餐廳 |
| 🧠 **記憶管理** | 記住使用者的喜好、習慣、常吃的食物 |
| ⏰ **提醒服務** | 設定自訂時間提醒 |
| 📮 **緊急通知** | 偵測重要信件並即時推播到 Discord |

---

## 🏗️ 系統架構

```
Discord ←→ proxy.py (Discord Bot)
               ↕  (HTTP / Webhook)
         api_server.py (Flask API, Port 80)
               ↕  (LocalTunnel)
      OpenClaw Sandbox (NemoClaw AI 沙盒)
```

- **`api_server.py`**：主要 API 伺服器，處理晨報、信件、NTU COOL 抓取，並透過 [localtunnel](https://theboroer.github.io/localtunnel-www/) 對沙盒開放存取。
- **`proxy.py`**：Discord Bot，接收使用者訊息並橋接 AI 沙盒。
- **`food_picker.py`**：午餐推薦引擎，整合 Foursquare API 查詢附近餐廳。
- **`manage_memory.py`**：記憶體管理，讀寫 `memory.json` 與 `food_list.json`。

---

## 🚀 快速開始

### 1. 安裝需求

```bash
git clone https://github.com/Dogyou0527/nemoclaw--.git
cd nemoclaw--
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

需要另外安裝：
- **[NemoClaw](https://openclaw.ai)** — 本專案的核心 AI 沙盒，請確保終端機可執行 `nemoclaw` 指令。
- [Node.js](https://nodejs.org/) — 用於執行 `localtunnel`
- `npm install -g localtunnel`
- [Playwright](https://playwright.dev/python/) — 用於 NTU COOL 自動化登入

```bash
playwright install chromium
```

### 2. 設定環境變數

```bash
cp .env.example .env
```

編輯 `.env`，填入以下資訊：

```env
DISCORD_BOT_TOKEN=your_discord_bot_token
DISCORD_CHANNEL_ID=your_channel_id
NTU_USERNAME=your_student_id
NTU_PASSWORD=your_password
WEBMAIL_USERNAME=your_student_id
WEBMAIL_PASSWORD=your_password
GMAIL_EMAIL=your_email@gmail.com
API_SECRET_KEY=your_random_secret   # 用 python3 -c "import secrets; print(secrets.token_hex(32))" 產生
FOURSQUARE_API_KEY=your_foursquare_key
TUNNEL_SUBDOMAIN_PREFIX=your-unique-prefix   # 自訂您的專屬連線前綴
```

### 3. 設定 Google Gmail API（選用）

1. 至 [Google Cloud Console](https://console.cloud.google.com/) 建立專案
2. 啟用 Gmail API，下載 `credentials.json` 放至專案根目錄
3. 第一次執行時會自動引導您完成 OAuth 登入

### 4. 沙盒初始化 (首次執行必做)

為了解決沙盒安全性限制，請在第一次使用前執行以下腳本將防火牆白名單安裝進沙盒：

```bash
./scripts/setup_sandbox.sh
```

### 5. 啟動系統

```bash
./scripts/start_proxy.sh
```

啟動後會自動：
1. 在 Port 80 啟動 API 伺服器
2. 嘗試建立 LocalTunnel 通道（讓沙盒可以連回主機）
3. 啟動 Discord Bot

> ⚠️ **【極度重要】沙盒連線核准 (openshell term)**：
> 啟動伺服器後，沙盒對外連線會處於 `[pending]` 狀態而被阻擋。請務必開啟 **另一個終端機視窗**，執行 `openshell term` 進入防火牆總管介面，並按下 `[A]` (Approve All) 來核准 `<你的前綴>-*.loca.lt` 以及 `host.docker.internal` 的網路規則。若未核准，AI 助理將完全無法取得外界資料！

---

## 📁 Prompt 設定

系統使用以下 prompt 文字檔來引導 AI 的行為，可以自行調整：

| 檔案 | 說明 |
|---|---|
| `catchup_prompt.txt` | 沙盒啟動時的系統指示，包含可用 API 指令 |
| `morning_prompt.txt` | 晨報格式與內容指示 |
| `mail_prompt.txt` | 信件摘要格式（*每次執行後自動覆寫，不納入版控*）|
| `reminder_prompt.txt` | 提醒服務的格式指示 |
| `ai_judge_prompt.txt` | AI 判斷信件重要程度的規則 |

---

## 🔒 資安注意事項

- `.env` 已納入 `.gitignore`，絕對不會被 commit
- `ntu_prompt.txt` / `mail_prompt.txt` 等包含個人資訊的 prompt 也已排除版控
- 請妥善保管您的 `API_SECRET_KEY`，沙盒每次呼叫 API 都需要附帶此 Key

---

## 🛠️ 開發相關

### 手動查詢午餐

```bash
python3 food_picker.py --location 博雅館 --meal lunch
python3 food_picker.py --location 公館 --mood 想吃麵
```

### 查看 API Server 日誌

```bash
tail -f /tmp/api_server.log
```

### 管理記憶

```bash
# 新增最愛餐廳
python3 manage_memory.py fav-add "鬍鬚張" --tags 台式 滷肉飯 --meal lunch

# 查看記憶內容
python3 manage_memory.py show
```

---

## 🔖 版本資訊

| 項目 | 版本 | 說明 |
|---|---|---|
| 本專案 (Proxy) | `v1.0.0` | 代理伺服器層 |
| NemoClaw (CLI) | `v0.0.55` | 終端機指令介面 |
| OpenClaw (Sandbox)| `2026.5.22` | AI 執行沙盒環境 |
| 語言模型 (LLM) | `Gemini 2.5 Flash` | 建議使用 Gemini 2.5 Flash 以獲得最佳理解力與 Token 處理速度 |
| Python | `3.9+` | |

---

## 🖥️ 測試與運作環境 (Tested Environment)

本專案在以下環境中開發與測試通過：

- **作業系統**: macOS 26.0 (Apple Silicon / Intel)
- **容器環境**: Docker Desktop for Mac
- **AI 沙盒**: NemoClaw v1.0 (基於 OpenClaw v2026.5.22) (`/opt/homebrew/bin/nemoclaw`)
- **網路工具**: Node.js v18+ & LocalTunnel

> **注意**：啟動腳本中的 `caffeinate` 是 macOS 專屬指令（用來防止休眠）。如果您在 Linux/Windows 上執行，請將 `scripts/start_proxy.sh` 裡的 `caffeinate -i python ...` 改為一般的 `python ...` 即可。

---

## 📋 需求清單


```
flask
requests
python-dotenv
playwright
google-auth-oauthlib
google-api-python-client
```

---

## 📄 License

MIT License — 詳見 [LICENSE](LICENSE)
