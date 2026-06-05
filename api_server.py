import os
import sys
import re
import subprocess
import threading
import time as pytime
import json
import requests
from datetime import datetime, timedelta, timezone
from flask import Flask, request
from dotenv import load_dotenv

load_dotenv()

import ntu_fetcher
import mail_fetcher
import gmail_fetcher

app = Flask(__name__)

# ──────────────────────────────────────────────
# 設定
# ──────────────────────────────────────────────
API_SECRET_KEY = os.getenv("API_SECRET_KEY", "")
TW_TZ = timezone(timedelta(hours=8))
_server_start_time = datetime.now(TW_TZ)
_last_fetch_times: dict = {}  # 記錄各功能最後一次成功抓取時間

def verify_api_key():
    """驗證請求是否帶有合法的 API Key，回傳 None 代表驗證通過，否則回傳 error response"""
    if not API_SECRET_KEY:
        return None  # 如果沒設定 Key，跳過驗證（開發模式）
    provided = request.headers.get("X-API-Key", "")
    if provided != API_SECRET_KEY:
        return {"error": "Unauthorized"}, 401
    return None


# ──────────────────────────────────────────────
# 工具函式
# ──────────────────────────────────────────────
def send_callback(report):
    try:
        requests.post("http://127.0.0.1:8888/callback", json={"content": report}, timeout=10)
    except Exception as e:
        print(f"發送回呼失敗: {e}")

def send_schedule_callback(message):
    try:
        content = f"【主動鬧鐘提醒】時間到了！請把以下訊息傳達給主人：\n{message}"
        requests.post("http://127.0.0.1:8888/callback", json={"content": content}, timeout=10)
        print(f"【鬧鐘系統】成功推播提醒: {message}")
    except Exception as e:
        print(f"【鬧鐘系統】發送提醒失敗: {e}")


# ──────────────────────────────────────────────
# 背景抓取任務
# ──────────────────────────────────────────────
def async_fetch_morning_report():
    print("【背景任務】開始抓取晨報資料...")
    data = {}

    # 注入今日課表（問題 2B 修正）
    try:
        day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        today_en = day_names[datetime.now(TW_TZ).weekday()]
        with open("config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
        schedule_today = cfg.get("schedule", {}).get(today_en, [])
        data["today_schedule"] = {
            "day": today_en,
            "courses": schedule_today
        }
        print(f"【晨報】今天是 {today_en}，注入 {len(schedule_today)} 門課")
    except Exception as e:
        print(f"【晨報】讀取課表失敗: {e}")
        data["today_schedule"] = {"day": "", "courses": []}

    try:
        weather_resp = requests.get("https://wttr.in/Taipei?format=3", timeout=5)
        if weather_resp.status_code == 200:
            data["weather"] = weather_resp.text.strip()
    except Exception as e:
        data["weather"] = f"無法抓取 ({e})"

    try:
        important, digest, skipped = gmail_fetcher.fetch_gmail_data(max_results=10)
        # 只在有重要信件時才寫入晨報資料，AI 連 key 都看不到 → 不會再提及 Gmail
        if important:
            data["gmail"] = {"important": important}
        # 不管有沒有重要信件，都同步到 seen_cache（避免巡邏員重複推播）
        _sync_gmail_to_seen_cache(important)
    except Exception as e:
        print(f"【晨報】Gmail 抓取失敗: {e}")

    try:
        data["ntu_cool"] = ntu_fetcher.fetch_ntu_data()
    except Exception as e:
        data["ntu_cool"] = f"抓取失敗: {str(e)}"

    try:
        with open("morning_data.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        _last_fetch_times["morning_report"] = datetime.now(TW_TZ).isoformat()

        # 讀取 morning_prompt.txt 當作回呼訊息
        prompt_path = "morning_prompt.txt"
        if os.path.exists(prompt_path):
            with open(prompt_path, "r", encoding="utf-8") as f:
                morning_msg = f.read().strip()
        else:
            morning_msg = (
                "【系統自動回傳】晨報資料已收集完畢！"
                "請立刻執行 openclaw:core:exec，輸入指令 `cat /sandbox/.openclaw/workspace/morning_data.json` 來讀取檔案內容！"
            )
        send_callback(morning_msg)
    except Exception as e:
        send_callback(f"寫入 morning_data.json 失敗: {e}")


def _sync_gmail_to_seen_cache(important_list: list):
    """戴報抓到的重要 Gmail 同步寫入 seen_cache，避免巡邏員重複推播"""
    cache_file = "seen_cache.json"
    try:
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                seen_cache = json.load(f)
        except Exception:
            seen_cache = {}
        gmail_seen = seen_cache.get("gmail", [])
        for msg in important_list:
            msg_id = msg.get("id")
            if msg_id and msg_id not in gmail_seen:
                gmail_seen.append(msg_id)
        seen_cache["gmail"] = gmail_seen[-50:]
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(seen_cache, f, ensure_ascii=False, indent=2)
        print(f"【晨報】已同步 {len(important_list)} 封 Gmail 到 seen_cache")
    except Exception as e:
        print(f"【晨報】同步 seen_cache 失敗: {e}")


def async_fetch_mail():
    print("【背景任務】開始抓取信箱資料...")
    data = {}

    try:
        important, digest, skipped = gmail_fetcher.fetch_gmail_data()
        data["gmail"] = {"important": important, "digest": digest, "skipped": skipped}
    except Exception as e:
        data["gmail"] = f"Gmail 抓取失敗: {e}"

    try:
        data["webmail"] = mail_fetcher.fetch_mail_data()
    except Exception as e:
        data["webmail"] = f"NTU Webmail 抓取失敗: {e}"

    try:
        with open("mail_data.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        _last_fetch_times["mail"] = datetime.now(TW_TZ).isoformat()
        msg = (
            "【系統自動回傳】信箱資料已收集完畢！詳細資料已寫入沙盒中。"
            "請立刻執行 openclaw:core:exec，輸入指令 `cat /sandbox/.openclaw/workspace/mail_data.json` 來讀取檔案內容！\\n\\n"
            "【強制規範】請嚴格依照以下原則回報：1. 詳細列出所有標有 🚨 的重要信件。 2. 對於一般信件，請勿逐條列出！請用一句話概括總結（例如：另有 15 封校園演講與一般通知），保持版面精簡！"
        )
        send_callback(msg)
    except Exception as e:
        send_callback(f"寫入 mail_data.json 失敗: {e}")


def async_fetch_ntu_cool():
    print("【背景任務】開始抓取 NTU COOL 資料...")
    data = {}
    try:
        data["ntu_cool"] = ntu_fetcher.fetch_ntu_data()
    except Exception as e:
        data["ntu_cool"] = f"NTU COOL 抓取失敗: {e}"

    try:
        with open("ntu_data.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        _last_fetch_times["ntu_cool"] = datetime.now(TW_TZ).isoformat()
        msg = "【系統自動回傳】NTU COOL 資料已收集完畢！詳細資料已寫入沙盒中。請立刻執行 openclaw:core:exec，輸入指令 `cat /sandbox/.openclaw/workspace/ntu_data.json` 來讀取檔案內容，並總結公告與作業！"
        send_callback(msg)
    except Exception as e:
        send_callback(f"寫入 ntu_data.json 失敗: {e}")


# ──────────────────────────────────────────────
# API 路由
# ──────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health_check():
    """系統狀態監控端點（不需要 API Key）"""
    return {
        "status": "ok",
        "uptime_since": _server_start_time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "last_fetch": _last_fetch_times,
        "api_key_enabled": bool(API_SECRET_KEY),
    }, 200


@app.route("/morning-report", methods=["GET"])
def get_morning_report():
    err = verify_api_key()
    if err:
        return err
    print("【收到請求】觸發非同步晨報...")
    threading.Thread(target=async_fetch_morning_report, daemon=True).start()
    return "好的，我已經開始在背景幫您抓取晨報資料，請稍等約 30 秒...", 200, {'Content-Type': 'text/plain; charset=utf-8'}


@app.route("/mail", methods=["GET"])
def get_mail():
    err = verify_api_key()
    if err:
        return err
    print("【收到請求】觸發非同步信箱...")
    threading.Thread(target=async_fetch_mail, daemon=True).start()
    return "好的，我已經開始在背景幫您抓取信箱資料，請稍等...", 200, {'Content-Type': 'text/plain; charset=utf-8'}


@app.route("/ntu-cool", methods=["GET"])
def get_ntu_cool():
    err = verify_api_key()
    if err:
        return err
    print("【收到請求】觸發非同步 NTU COOL...")
    threading.Thread(target=async_fetch_ntu_cool, daemon=True).start()
    return "好的，我已經開始在背景幫您抓取 NTU COOL 資料，請稍等...", 200, {'Content-Type': 'text/plain; charset=utf-8'}


@app.route("/weather", methods=["GET"])
def get_weather():
    err = verify_api_key()
    if err:
        return err
    try:
        weather_resp = requests.get("https://wttr.in/Taipei?format=3", timeout=5)
        return weather_resp.text, 200, {'Content-Type': 'text/plain; charset=utf-8'}
    except Exception as e:
        return f"天氣抓取失敗: {e}", 500, {'Content-Type': 'text/plain; charset=utf-8'}


@app.route("/schedule", methods=["POST"])
def schedule_alarm():
    err = verify_api_key()
    if err:
        return err

    data = request.json
    if not data or 'time' not in data or 'message' not in data:
        return "Missing time or message", 400

    # ── 修正：全程使用帶時區的 datetime 避免混用問題 ──
    now_tw = datetime.now(TW_TZ)

    try:
        target_time = datetime.fromisoformat(data['time'])
        # 如果傳入的時間沒有時區資訊，假設是台灣時間
        if target_time.tzinfo is None:
            target_time = target_time.replace(tzinfo=TW_TZ)
    except ValueError:
        try:
            # 支援 HH:MM 格式
            parts = data['time'].split(':')
            hours, minutes = int(parts[0]), int(parts[1])
            target_time = now_tw.replace(hour=hours, minute=minutes, second=0, microsecond=0)
            if target_time <= now_tw:
                target_time += timedelta(days=1)
        except Exception:
            return "Invalid time format (use ISO 8601 or HH:MM)", 400

    delay = (target_time - now_tw).total_seconds()
    if delay < 0:
        delay = 0

    threading.Timer(delay, send_schedule_callback, args=(data['message'],)).start()
    return f"好的，我已經幫您設定好鬧鐘，將在 {target_time.strftime('%Y-%m-%d %H:%M:%S')} (台灣時間) 準時提醒您！", 200, {'Content-Type': 'text/plain; charset=utf-8'}


@app.route("/memory", methods=["GET", "POST"])
def manage_memory():
    return "這是記憶 (已被沙盒內本地讀寫取代)", 200, {'Content-Type': 'text/plain; charset=utf-8'}


@app.route("/food-nearby", methods=["GET"])
def food_nearby():
    """代理 OSM Overpass API，讓沙箱內的 food_picker.py 可以查詢附近餐廳"""
    err = verify_api_key()
    if err:
        return err
    try:
        lat    = float(request.args.get("lat", 25.0170))
        lon    = float(request.args.get("lon", 121.5400))
        radius = int(request.args.get("radius", 600))
    except (ValueError, TypeError):
        return {"error": "Invalid parameters"}, 400

    overpass_url = "https://overpass-api.de/api/interpreter"
    query = f"""
    [out:json];
    node(around:{radius},{lat},{lon})["amenity"~"restaurant|fast_food|cafe|food_court"];
    out center;
    """
    try:
        endpoints = [
            "https://overpass-api.de/api/interpreter",
            "https://lz4.overpass-api.de/api/interpreter",
            "https://z.overpass-api.de/api/interpreter"
        ]
        
        result = None
        for ep in endpoints:
            try:
                resp = requests.post(
                    ep,
                    data={"data": query},
                    headers={"User-Agent": "NemoClawBot/1.0"},
                    timeout=(3, 10)  # 3秒連線，10秒讀取
                )
                if resp.status_code == 200:
                    try:
                        result = resp.json()
                        break
                    except ValueError:
                        pass
            except Exception:
                continue
                
        if not result:
            return {"error": "All Overpass API endpoints failed or timed out", "elements": []}

        elements = []
        for el in result.get("elements", []):
            tags = el.get("tags", {})
            name = tags.get("name") or tags.get("name:en")
            if not name:
                continue
                
            cuisine = tags.get("cuisine", "")
            amenity = tags.get("amenity", "")
            el_tags = [cuisine, amenity] if cuisine else [amenity]
            
            elements.append({
                "name":      name,
                "rating":    None,
                "ratings_count": 0,
                "tags":      el_tags,
                "distance_m": 0,
                "lat":       el.get("lat"),
                "lon":       el.get("lon"),
                "address":   tags.get("addr:full", "") or tags.get("addr:street", ""),
            })
        
        return {"elements": elements}
    except Exception as e:
        print(f"[/food-nearby] OSM 查詢失敗: {e}")
        return {"error": str(e), "elements": []}
        return {"error": str(e), "elements": []}, 200


# ──────────────────────────────────────────────
# LocalTunnel 自動維護
# ──────────────────────────────────────────────
def update_prompt_file(new_url):
    files_to_update = ["catchup_prompt.txt", "food_picker.py"]
    for file_path in files_to_update:
        if not os.path.exists(file_path):
            continue
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            updated_content = re.sub(r'https://[a-zA-Z0-9-]+\.loca\.lt', new_url, content)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(updated_content)
            print(f"【系統】已自動更新 {file_path} 中的網址為 {new_url}！")
        except Exception as e:
            print(f"更新 {file_path} 失敗: {e}", file=sys.stderr)


def start_localtunnel(port):
    prefix = os.getenv("TUNNEL_SUBDOMAIN_PREFIX", "my-nemoclaw-api")
    subdomains = [f"{prefix}-v{i}" for i in range(1, 6)]
    current_index = 0

    while True:
        subdomain = subdomains[current_index % len(subdomains)]
        expected_url = f"https://{subdomain}.loca.lt"
        process = None
        try:
            print(f"【系統】嘗試啟動 LocalTunnel (子網域: {subdomain})...")
            process = subprocess.Popen(
                ["/opt/homebrew/bin/npx", "localtunnel", "--port", str(port), "--subdomain", subdomain],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            # ── 修正：使用 Thread 進行嚴格的 10 秒 timeout ──
            import threading
            result_list = []
            def read_line():
                try:
                    result_list.append(process.stdout.readline())
                except:
                    pass
            
            t = threading.Thread(target=read_line)
            t.daemon = True
            t.start()
            t.join(10)

            if t.is_alive():
                print(f"⚠️ LocalTunnel ({subdomain}) 啟動超時或卡死，強制切換下一個子網域...")
                process.terminate()
                current_index += 1
                pytime.sleep(2)
                continue

            url_line = result_list[0] if result_list else ""

            if url_line:
                match = re.search(r'(https://[^\s]+)', url_line)
                if match:
                    actual_url = match.group(1).strip()
                    if actual_url == expected_url:
                        print(f"\n✨ [LocalTunnel 自動啟動成功] {actual_url}")
                        update_prompt_file(actual_url)
                        # 等待直到斷線
                        process.wait()
                    else:
                        print(f"⚠️ 網址不符 ({actual_url} != {expected_url})，換下一個...")
                        process.terminate()
                        current_index += 1
                        pytime.sleep(2)
                        continue
                else:
                    print(f"\n✨ [LocalTunnel 輸出異常] {url_line.strip()}")
                    process.terminate()
                    current_index += 1
                    pytime.sleep(2)
                    continue
            else:
                print(f"⚠️ LocalTunnel 沒輸出網址或直接結束，換下一個...")
                process.terminate()
                current_index += 1
                pytime.sleep(2)
                continue

            print("【系統】LocalTunnel 連線中斷，3 秒後重新連線...")
            pytime.sleep(3)

        except Exception as e:
            print(f"啟動 LocalTunnel 發生例外: {e}", file=sys.stderr)
            if process:
                process.terminate()
            pytime.sleep(5)


# ──────────────────────────────────────────────
# 每日晨報排程
# ──────────────────────────────────────────────
def daily_morning_report_scheduler():
    target_hour = 8
    target_minute = 0

    while True:
        now = datetime.now(TW_TZ)
        target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)

        if now >= target:
            target += timedelta(days=1)

        delay_seconds = (target - now).total_seconds()
        print(f"【每日晨報排程】將在 {delay_seconds:.0f} 秒後 ({target.strftime('%Y-%m-%d %H:%M:%S')}) 自動觸發晨報...")
        pytime.sleep(delay_seconds)

        try:
            print("【每日晨報排程】觸發！開始非同步抓取最新晨報資料並整理晨報...")
            threading.Thread(target=async_fetch_morning_report, daemon=True).start()
        except Exception as e:
            print(f"【每日晨報排程】觸發失敗: {e}")


# ──────────────────────────────────────────────
# 即時推播巡邏員
# ──────────────────────────────────────────────
def instant_push_patrol():
    cache_file = "seen_cache.json"

    # 啟動時等 30 秒，避免與其他任務衝突
    pytime.sleep(30)

    while True:
        print("【即時推播巡邏員】開始背景輪詢...")

        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                seen_cache = json.load(f)
        except Exception:
            seen_cache = {"gmail": [], "ntu_cool": [], "webmail": []}

        new_urgent_msgs = []

        # 1. Gmail
        try:
            important, _, _ = gmail_fetcher.fetch_gmail_data()
            for msg in important:
                msg_id = msg['id']
                if msg_id not in seen_cache.get("gmail", []):
                    seen_cache.setdefault("gmail", []).append(msg_id)
                    new_urgent_msgs.append(f"[Gmail] {msg['sender']} - {msg['subject']}\\n  內容摘要：{msg.get('snippet', '')}")
        except Exception as e:
            print(f"【巡邏員】Gmail 檢查失敗: {e}")

        # 2. NTU COOL（改用輕量 API，不啟動 Playwright）
        try:
            ntu_data = ntu_fetcher.fetch_ntu_data()
            lines = ntu_data.split('\\n')
            current_course = ""
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith("📌 ["):
                    current_course = stripped
                elif "NTU COOL 收件夾" in stripped:
                    current_course = "📌 [NTU COOL 收件夾]"
                elif stripped.startswith("🚨 [重要通知]"):
                    cache_key = stripped
                    if cache_key not in seen_cache.get("ntu_cool", []):
                        seen_cache.setdefault("ntu_cool", []).append(cache_key)
                        body = ""
                        j = i + 1
                        while j < len(lines) and (lines[j].strip().startswith("📅") or lines[j].strip().startswith("📝")):
                            body += "\\n  " + lines[j].strip()
                            j += 1
                        if not body and i + 1 < len(lines) and lines[i + 1].strip().startswith("└─"):
                            body = "\\n  " + lines[i + 1].strip()
                        prefix = f"[NTU COOL] {current_course}\\n  " if current_course else "[NTU COOL] "
                        new_urgent_msgs.append(f"{prefix}{stripped}{body}")
        except Exception as e:
            print(f"【巡邏員】NTU COOL 檢查失敗: {e}")

        # 3. Webmail
        try:
            mail_data = mail_fetcher.fetch_mail_data()
            for line in mail_data.split('\\n'):
                line = line.strip()
                if line.startswith("🚨 [重要通知]"):
                    if line not in seen_cache.get("webmail", []):
                        seen_cache.setdefault("webmail", []).append(line)
                        new_urgent_msgs.append(f"[NTU Webmail] {line}")
        except Exception as e:
            print(f"【巡邏員】Webmail 檢查失敗: {e}")

        # 儲存 cache（各類別最多保留 50 筆）
        try:
            seen_cache["gmail"] = seen_cache.get("gmail", [])[-50:]
            seen_cache["ntu_cool"] = seen_cache.get("ntu_cool", [])[-50:]
            seen_cache["webmail"] = seen_cache.get("webmail", [])[-50:]
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(seen_cache, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        # 發送推播
        if new_urgent_msgs:
            print(f"【巡邏員】發現 {len(new_urgent_msgs)} 筆新重要訊息，準備推播！")
            urgent_data_path = "urgent_data.json"
            try:
                with open(urgent_data_path, "w", encoding="utf-8") as f:
                    json.dump({"urgent_items": new_urgent_msgs}, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"【巡邏員】寫入 urgent_data.json 失敗: {e}")

            msg = (
                "【系統緊急推播】剛剛攔截到極度重要的信件或公告！"
                "詳細資料已打包寫入沙盒中。"
                "請立刻執行 openclaw:core:exec，輸入指令 `cat /sandbox/.openclaw/workspace/urgent_data.json` 查看，然後用最緊急的口吻總結並通知主人！"
            )
            try:
                requests.post("http://127.0.0.1:8888/callback", json={"content": msg}, timeout=10)
            except Exception as e:
                print(f"【巡邏員】發送 Webhook 失敗: {e}")
        else:
            print("【巡邏員】本次巡邏未發現新的重要訊息。")

        pytime.sleep(900)  # 每 15 分鐘


# ──────────────────────────────────────────────
# 主動提醒排程
# ──────────────────────────────────────────────
def check_and_send_reminders():
    print("【提醒服務】開始檢查記憶中的到期事項...")
    memory_path = "memory.json"
    if not os.path.exists(memory_path):
        return

    try:
        with open(memory_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"【提醒】讀取 memory.json 失敗: {e}")
        return

    events = data.get("events", [])
    now = datetime.now(TW_TZ)
    due_events = []

    for event in events:
        if event.get("done") or event.get("reminded"):
            continue
        due_str = event.get("due")
        if not due_str:
            continue

        try:
            clean_due = due_str.replace("Z", "+00:00")
            due_dt = datetime.fromisoformat(clean_due).astimezone(TW_TZ)
            diff_seconds = (due_dt - now).total_seconds()
            
            # 如果在 24 小時內 (86400 秒) 且尚未過期，就觸發提醒
            if 0 <= diff_seconds <= 86400:
                event["reminded"] = True
                due_events.append(event)
                print(f"【提醒】即將到期項目: {event.get('title')}，剩餘 {diff_seconds/3600:.1f} 小時")
        except Exception as ex:
            print(f"【提醒】解析事件時間失敗 {due_str}: {ex}")

    if due_events:
        # 寫入本機
        try:
            with open(memory_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"【提醒】寫入 memory.json 失敗: {e}")
            return

        # 同步寫入沙盒
        try:
            import base64
            with open(memory_path, "r", encoding="utf-8") as f:
                content = f.read()
            b64_content = base64.b64encode(content.encode('utf-8')).decode('utf-8')
            cmd = f"echo \"echo '{b64_content}' | base64 -d > /sandbox/.openclaw/workspace/memory.json\" | /opt/homebrew/bin/nemoclaw test connect"
            subprocess.run(cmd, shell=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("【提醒】已同步 memory.json 到沙盒中")
        except Exception as e:
            print(f"【提醒】同步 memory.json 到沙盒失敗: {e}")

        # 喚醒龍蝦發送提醒
        current_time_str = now.strftime("%Y-%m-%d %H:%M")
        prompt = f"""系統指示：你的名字叫「龍蝦 (NemoClaw)」，是住在沙箱裡的全能 AI 助理。
【記憶提醒任務】現在時間是 {current_time_str}（台北時間）。
以下是主人的【即將到期事項】（24小時內截止）。系統已自動標記為已提醒，你只需要寫一則口語化的提醒通知即可。
【回覆規則】：請直接回覆提醒主人的文字（大管家會幫你轉發到 Discord）。不需要更新 memory.json，不需要執行 curl。

即將到期事項：
{json.dumps(due_events, ensure_ascii=False, indent=2)}
"""
        try:
            requests.post("http://127.0.0.1:8888/callback", json={"content": prompt}, timeout=10)
            print("【提醒】已成功發送喚醒訊號給 Proxy！")
        except Exception as e:
            print(f"【提醒】發送 Webhook 失敗: {e}")


def active_reminder_scheduler():
    print("【提醒服務】主動提醒排程啟動...")
    pytime.sleep(10)  # 啟動後等 10 秒開始首次檢查
    while True:
        try:
            check_and_send_reminders()
        except Exception as e:
            print(f"【提醒服務】排程檢查失敗: {e}")
        pytime.sleep(600)  # 每 10 分鐘檢查一次


# ──────────────────────────────────────────────
# 啟動
# ──────────────────────────────────────────────
if __name__ == "__main__":
    port = 80
    print(f"【API 伺服器】啟動於 Port {port}...")
    print(f"【API 伺服器】API Key 驗證: {'✅ 已啟用' if API_SECRET_KEY else '⚠️ 未設定（開發模式）'}")

    threading.Thread(target=start_localtunnel, args=(port,), daemon=True).start()
    threading.Thread(target=daily_morning_report_scheduler, daemon=True).start()
    threading.Thread(target=instant_push_patrol, daemon=True).start()
    threading.Thread(target=active_reminder_scheduler, daemon=True).start()

    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
