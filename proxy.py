import os
import sys
import json
import time
import queue
import requests
import subprocess
import shlex
import base64
import threading
import re
from flask import Flask, request
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────
# 設定
# ──────────────────────────────────────────────
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID", "")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TW_TZ = timezone(timedelta(hours=8))

# ── P1-2 修正：seen_message_ids 改用有限佇列，避免記憶體無限膨脹 ──
_SEEN_MAX = 1000
seen_message_ids: set = set()
seen_message_ids_order: list = []  # 用於 LRU-like 清理

# ── P1-1 修正：訊息佇列，讓 Discord 輪詢不會因 AI 推理而阻塞 ──
_msg_queue: queue.Queue = queue.Queue()


def get_sandbox_name():
    sandboxes_path = os.path.expanduser("~/.nemoclaw/sandboxes.json")
    if os.path.exists(sandboxes_path):
        try:
            with open(sandboxes_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("defaultSandbox", "nemosandbox")
        except Exception:
            pass
    return "nemosandbox"

SANDBOX_NAME = get_sandbox_name()


# ──────────────────────────────────────────────
# 工具函式
# ──────────────────────────────────────────────
def _track_seen_id(msg_id: str):
    """追蹤已見訊息 ID，並自動清理超過上限的舊紀錄"""
    if msg_id not in seen_message_ids:
        seen_message_ids.add(msg_id)
        seen_message_ids_order.append(msg_id)
        if len(seen_message_ids_order) > _SEEN_MAX:
            oldest = seen_message_ids_order.pop(0)
            seen_message_ids.discard(oldest)


def direct_send_discord(content):
    if not content or not content.strip():
        return False
    if len(content) > 1950:
        content = content[:1950] + "\n\n...（訊息過長，已截斷）"
    url = f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages"
    headers = {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {"content": content}
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        print(f"【Discord 代理】成功發送訊息到 Discord: {response.json().get('id')}")
        return True
    except requests.exceptions.RequestException as e:
        print(f"【Discord 代理】發送 Discord 失敗: {e}")
        return False


# ── P2-3 修正：使用正則快速定位 JSON，O(n) 而非 O(n²) ──
def extract_payloads_from_stdout(stdout_text):
    if not stdout_text:
        return None
    JUNK_PATTERNS = [
        "(see attached image)", "[see attached image]",
        "(attached image)", "[image]", "(image)", "see attached"
    ]
    # 正則快速定位所有 { 開頭的位置
    decoder = json.JSONDecoder()
    for m in re.finditer(r'\{', stdout_text):
        try:
            obj, _ = decoder.raw_decode(stdout_text, m.start())
            if not isinstance(obj, dict):
                continue
            payloads = obj.get("payloads", [])
            if not payloads and "result" in obj and isinstance(obj["result"], dict):
                payloads = obj["result"].get("payloads", [])
            if payloads:
                texts = [p.get("text", "") for p in payloads if p.get("text")]
                if texts:
                    filtered = [
                        t for t in texts
                        if not t.strip().startswith("⚠️")
                        and not any(jp.lower() in t.lower() for jp in JUNK_PATTERNS)
                    ]
                    if filtered:
                        return "\n".join(filtered).strip()
        except (ValueError, KeyError):
            pass
    return None


def process_and_send_to_sandbox(combined_msg, is_system_callback=False):
    # 讀取系統 Prompt
    system_prompt = ""
    prompt_path = os.path.join(BASE_DIR, "prompts", "catchup_prompt.txt")
    if os.path.exists(prompt_path):
        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                system_prompt = f.read().strip() + "\n\n"
        except Exception as e:
            print(f"【Discord 代理】讀取系統設定檔失敗: {e}")

    # ── P1-3 修正：讀取 memory.json 並驗證 JSON 格式 ──
    memory_path = os.path.join(BASE_DIR, "memory.json")
    memory_content = "{}"
    if os.path.exists(memory_path):
        try:
            with open(memory_path, "r", encoding="utf-8") as f:
                raw = f.read()
            # 驗證 JSON 格式，損壞時回退到 {}
            json.loads(raw)
            memory_content = raw
        except (json.JSONDecodeError, Exception) as e:
            print(f"【Discord 代理】memory.json 格式無效（{e}），使用空記憶")
            memory_content = "{}"
    b64_memory = base64.b64encode(memory_content.encode('utf-8')).decode('utf-8')

    # 注入所有資料檔與執行腳本 (絕對不包含 .env)
    # 格式：(本機路徑, 沙盒內的檔名)
    data_files = [
        ("morning_data.json",          "morning_data.json"),
        ("mail_data.json",             "mail_data.json"),
        ("ntu_data.json",              "ntu_data.json"),
        ("urgent_data.json",           "urgent_data.json"),
        ("tools/food_picker.py",       "food_picker.py"),
        ("tools/manage_memory.py",     "manage_memory.py"),
    ]
    data_injections = ""
    
    # 單獨注入內部通訊用的 API_SECRET_KEY，保護真正的 .env 不外洩
    internal_api_key = os.getenv("API_SECRET_KEY", "")
    if internal_api_key:
        b64_key = base64.b64encode(internal_api_key.encode('utf-8')).decode('utf-8')
        data_injections += f"echo '{b64_key}' | base64 -d > /sandbox/.openclaw/workspace/.api_key\n"
        
    for local_name, sandbox_name in data_files:
        df_path = os.path.join(BASE_DIR, local_name)
        if os.path.exists(df_path):
            try:
                with open(df_path, "r", encoding="utf-8") as f:
                    content = f.read()
                b64_content = base64.b64encode(content.encode('utf-8')).decode('utf-8')
                data_injections += f"echo '{b64_content}' | base64 -d > /sandbox/.openclaw/workspace/{sandbox_name}\n"
            except Exception:
                pass

    # 注入台灣時間
    tw_time_str = datetime.now(TW_TZ).strftime('%Y-%m-%d %H:%M:%S')
    time_context = f"【系統當前時間：{tw_time_str} (台灣時間 UTC+8)】\n"

    if is_system_callback:
        full_message = f"{system_prompt}{time_context}[系統自動回傳]：\n{combined_msg}"
    else:
        full_message = f"{system_prompt}{time_context}[使用者 Discord 訊息]：{combined_msg}"

    b64_msg = base64.b64encode(full_message.encode('utf-8')).decode('utf-8')

    sandbox_cmd = (
        "rm -f /sandbox/.openclaw/agents/main/sessions/temp_*.jsonl\n"
        "rm -f /sandbox/.openclaw/agents/main/sessions/*proxy_auto*.jsonl\n"
        "rm -f /sandbox/.openclaw/agents/main/sessions/*.lock\n"
        "echo '{}' > /sandbox/.openclaw/agents/main/sessions/sessions.json\n"
        f"echo '{b64_msg}' | base64 -d > /tmp/discord_msg.txt\n"
        f"echo '{b64_memory}' | base64 -d > /sandbox/.openclaw/workspace/memory.json\n"
        f"{data_injections}"
        "openclaw agent --agent main -m \"$(cat /tmp/discord_msg.txt)\" --json --verbose on > /tmp/openclaw_debug.log 2>&1\n"
        "cat /tmp/openclaw_debug.log\n"
        "echo '===MEMORY_START==='\n"
        "cat /sandbox/.openclaw/workspace/memory.json || echo '{}'\n"
        "echo '===MEMORY_END==='\n"
        "rm -f /sandbox/.openclaw/agents/main/sessions/temp_*.jsonl\n"
        "echo '{}' > /sandbox/.openclaw/agents/main/sessions/sessions.json\n"
        "exit\n"
    )

    print("開始傳送指令到沙箱內...")
    proc = subprocess.run(
        ["/opt/homebrew/bin/nemoclaw", SANDBOX_NAME, "connect"],
        input=sandbox_cmd.encode('utf-8'),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=300  # 5 分鐘超時保護
    )
    stdout_text = proc.stdout.decode('utf-8', errors='ignore')
    print("--- stdout ---")
    print(stdout_text)
    print("--- stderr ---")
    print(proc.stderr.decode('utf-8', errors='ignore'))

    # 抽取並備份 memory
    memory_match = re.search(r'===MEMORY_START===\r?\n(.*?)\r?\n===MEMORY_END===', stdout_text, re.DOTALL)
    if memory_match:
        new_memory = memory_match.group(1).strip()
        if new_memory:
            try:
                json.loads(new_memory)  # 確認 AI 輸出的是合法 JSON
                with open(memory_path, "w", encoding="utf-8") as f:
                    f.write(new_memory)
                print("【系統】已將沙盒記憶備份回本機 memory.json")
            except (json.JSONDecodeError, Exception) as e:
                print(f"【系統】AI 回傳的 memory 格式無效，不覆寫: {e}")

    merged_reply = extract_payloads_from_stdout(stdout_text)
    if merged_reply:
        direct_send_discord(merged_reply)
    else:
        print("【Discord 回覆】未捕獲到 AI 的有效回覆內容")
        with open(os.path.join(BASE_DIR, "failed_stdout.log"), "w", encoding="utf-8") as f:
            f.write(stdout_text)


# ── P1-1 修正：獨立的 Worker Thread 處理 AI 推理，不阻塞輪詢 ──
def sandbox_worker():
    """從訊息佇列取出訊息並傳送到沙箱，確保序列處理不會競爭"""
    print("【Worker】沙箱訊息處理器啟動")
    while True:
        try:
            item = _msg_queue.get(timeout=5)
            combined_msg, is_system = item
            try:
                process_and_send_to_sandbox(combined_msg, is_system)
            except subprocess.TimeoutExpired:
                print("【Worker】沙箱推理超時（5 分鐘），跳過本次")
                direct_send_discord("⚠️ 龍蝦這次思考超時了，請稍後再試！")
            except Exception as e:
                print(f"【Worker】處理訊息時發生錯誤: {e}")
            finally:
                _msg_queue.task_done()
        except queue.Empty:
            continue


def enqueue_message(combined_msg, is_system_callback=False):
    """將訊息放入佇列，立即回傳不阻塞"""
    _msg_queue.put((combined_msg, is_system_callback))
    print(f"【佇列】訊息已加入處理佇列（目前佇列長度: {_msg_queue.qsize()}）")


# ──────────────────────────────────────────────
# Discord 輪詢
# ──────────────────────────────────────────────
def poll_discord():
    print(f"\n🛡️ 啟動 Discord 代理機器人... (目標頻道: {CHANNEL_ID})")
    # 預載舊訊息，避免重啟後重複處理
    try:
        _url = f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages?limit=50"
        _headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
        _resp = requests.get(_url, headers=_headers, timeout=10)
        if _resp.ok:
            for _msg in _resp.json():
                _mid = _msg.get("id")
                if _mid:
                    _track_seen_id(_mid)
            print(f"【Discord 輪詢】已預載 {len(seen_message_ids)} 則舊訊息 ✅")
    except Exception as _e:
        print(f"【Discord 輪詢】預載失敗（無影響）: {_e}")

    while True:
        url = f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages?limit=15"
        headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            messages = response.json()
            now = datetime.now(timezone.utc)
            fifteen_mins_ago = now - timedelta(minutes=15)
            recent_messages = []

            for msg in messages:
                msg_id = msg.get("id")
                if msg_id in seen_message_ids:
                    continue
                ts_str = msg.get("timestamp", "")
                if not ts_str:
                    continue
                if ts_str.endswith('Z'):
                    ts_str = ts_str[:-1] + '+00:00'
                try:
                    msg_time = datetime.fromisoformat(ts_str)
                except ValueError:
                    continue
                if msg_time >= fifteen_mins_ago:
                    if msg.get("author", {}).get("bot") is True:
                        _track_seen_id(msg_id)
                        continue
                    author_name = msg.get("author", {}).get("username", "Unknown")
                    content = msg.get("content", "")
                    recent_messages.append(f"[{author_name}]: {content}")
                    _track_seen_id(msg_id)

            recent_messages.reverse()
            if recent_messages:
                combined_msg = " \n ".join(recent_messages)
                print(f"\n【代理機器人截獲新訊息】: {combined_msg}")
                # ── P1-1 修正：改用佇列，不直接呼叫阻塞函式 ──
                enqueue_message(combined_msg)

        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                retry_after = int(e.response.headers.get("Retry-After", 30))
                print(f"【Discord 輪詢】觸發 Rate Limit，等待 {retry_after} 秒後重試...")
                time.sleep(retry_after + 1)
            else:
                print(f"【Discord 輪詢】HTTP 錯誤: {e}")
                time.sleep(10)
        except Exception as e:
            print(f"【Discord 輪詢】例外錯誤: {e}")
            time.sleep(10)
        time.sleep(2)


# ──────────────────────────────────────────────
# Webhook 接收（api_server → proxy）
# ──────────────────────────────────────────────
app = Flask(__name__)

@app.route('/callback', methods=['POST'])
def callback():
    data = request.json
    if not data or 'content' not in data:
        return {"error": "Missing content"}, 400

    content = data['content']
    msg = f"爬蟲資料已收集完畢，請根據以下資料整理並總結給主人：\n\n{content}"
    print("【收到 API Webhook】: 已將資料加入處理佇列...")
    # ── P1-1 修正：同樣改用佇列 ──
    enqueue_message(msg, is_system_callback=True)
    return {"status": "ok"}


def start_webhook_server():
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    print("【Webhook 伺服器】啟動於 127.0.0.1:8888")
    app.run(host='127.0.0.1', port=8888, debug=False, use_reloader=False)


# ──────────────────────────────────────────────
# 主程式
# ──────────────────────────────────────────────
if __name__ == '__main__':
    # Webhook 伺服器
    threading.Thread(target=start_webhook_server, daemon=True).start()
    # ── P1-1 修正：啟動獨立的沙箱 Worker Thread ──
    threading.Thread(target=sandbox_worker, daemon=True).start()
    # Discord 輪詢（主執行緒）
    poll_discord()
