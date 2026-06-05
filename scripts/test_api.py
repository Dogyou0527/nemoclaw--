#!/usr/bin/env python3
"""
NemoClaw API 測試腳本
用途：測試本機所有的 API 端點是否正常回應
用法：cd 到專案根目錄，然後執行 python3 scripts/test_api.py
"""

import os
import sys
import requests
import json
import time
from datetime import datetime, timedelta

# 確保在專案根目錄執行，以便讀取 .env
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.chdir(PROJECT_ROOT)

# 讀取 API Key
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("⚠️ 尚未安裝 python-dotenv，請先執行: pip install python-dotenv")
    sys.exit(1)

API_KEY = os.getenv("API_SECRET_KEY")
if not API_KEY:
    print("⚠️ 警告：.env 中未找到 API_SECRET_KEY！")
    API_KEY = "your_random_api_secret_key_here"

BASE_URL = "http://127.0.0.1:80"
HEADERS = {
    "X-API-Key": API_KEY,
    "Content-Type": "application/json"
}

def test_endpoint(method, path, payload=None, timeout=15):
    url = f"{BASE_URL}{path}"
    print(f"\n[{method}] {url}")
    if payload:
        print(f"   Payload: {json.dumps(payload, ensure_ascii=False)}")
    
    try:
        start_time = time.time()
        if method == "GET":
            response = requests.get(url, headers=HEADERS, timeout=timeout)
        elif method == "POST":
            response = requests.post(url, headers=HEADERS, json=payload, timeout=timeout)
        else:
            print(f"   ❌ 不支援的方法: {method}")
            return False

        elapsed = time.time() - start_time
        
        if response.ok:
            print(f"   ✅ 狀態碼: {response.status_code} ({elapsed:.2f}s)")
            try:
                # 嘗試格式化 JSON 輸出
                data = response.json()
                print(f"   回傳: {json.dumps(data, ensure_ascii=False, indent=2)[:500]}...")
            except ValueError:
                # 純文字輸出
                text = response.text.strip()
                if len(text) > 100:
                    print(f"   回傳: {text[:100]}... (總字數: {len(text)})")
                else:
                    print(f"   回傳: {text}")
            return True
        else:
            print(f"   ❌ 狀態碼: {response.status_code}")
            print(f"   錯誤內容: {response.text}")
            return False

    except requests.exceptions.Timeout:
        print(f"   ❌ 請求超時 ({timeout}s)")
        return False
    except requests.exceptions.ConnectionError:
        print("   ❌ 連線失敗！請確認 sudo python api_server.py 是否有在運行。")
        return False
    except Exception as e:
        print(f"   ❌ 發生未知錯誤: {e}")
        return False


if __name__ == "__main__":
    print("==============================================")
    print("   🦞 NemoClaw 內部 API 端點自動測試")
    print("==============================================")
    
    print("\n1. 測試系統健康狀態")
    if not test_endpoint("GET", "/health"):
        print("\n🚨 API Server 未啟動，無法繼續測試！請先執行 ./scripts/start_proxy.sh")
        sys.exit(1)

    print("\n2. 測試天氣查詢 (同步)")
    test_endpoint("GET", "/weather")

    print("\n3. 測試食物查詢 (OSM Overpass API)")
    test_endpoint("GET", "/food-nearby?lat=25.017&lon=121.54&radius=500")

    print("\n4. 測試設定鬧鐘 (排程)")
    # 設定一個 5 分鐘後的假鬧鐘
    future_time = (datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    test_endpoint("POST", "/schedule", {"time": future_time, "message": "測試鬧鐘"})

    print("\n--- 以下為非同步端點 (觸發背景任務) ---")
    
    print("\n5. 觸發晨報抓取")
    test_endpoint("GET", "/morning-report")

    print("\n6. 觸發 NTU COOL 抓取")
    test_endpoint("GET", "/ntu-cool")

    print("\n7. 觸發 Gmail 信件抓取")
    test_endpoint("GET", "/mail")

    print("\n==============================================")
    print("🎉 測試腳本執行完畢！")
    print("請檢查終端機的 api_server.py 執行日誌，")
    print("或者查看 proxy.py 的畫面是否有收到 Webhook 的回呼通知。")
