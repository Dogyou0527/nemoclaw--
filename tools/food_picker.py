#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
food_picker.py — 龍蝦專屬「幫我決定吃什麼」工具

用法：
  python3 food_picker.py [--meal lunch|dinner|breakfast|latenight] [--mood "不想吃辣"] [--location 公館] [--lat 25.017] [--lon 121.540] [--radius 500]

功能：
1. 讀取 food_list.json（私人常吃清單）
2. 呼叫 OSM Overpass API 查詢附近餐廳
3. 讀取 memory.json 排除最近吃過的
4. 依心情關鍵字篩選
5. 隨機選出推薦並輸出
"""

import os
import sys
import json
import random
import argparse
import re
import urllib.request
import urllib.error
import subprocess
import random
import time
import math
from datetime import datetime, timezone, timedelta

# ──────────────────────────────────────────────
# 路徑設定
# ──────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FOOD_LIST_PATH = os.path.join(SCRIPT_DIR, "food_list.json")
MEMORY_PATH = "/sandbox/.openclaw/workspace/memory.json"

# 內部 API 代理設定
HOST_API_KEY = "40d74f5055f15be1986d219e5639bdc34942aed3511b7cf873b2fc516ec32ae8"
HOST_API_BASE = "https://dogyou-api-v1.loca.lt"

# 如果環境變數沒有，從專屬的 .api_key 檔案讀取 (不暴露真實的 .env)
if not os.environ.get("NEMO_API_KEY"):
    _KEY_PATH = os.path.join(SCRIPT_DIR, ".api_key")
    if os.path.exists(_KEY_PATH):
        HOST_API_KEY = open(_KEY_PATH).read().strip()

# 等待 api_server.py 啟動時自動注入最新的 loca.lt 網址（與晨報機制相同）
if os.path.exists("/sandbox"):
    HOST_API_BASE = "https://your-nemoclaw-api-v1.loca.lt"
else:
    HOST_API_BASE = "http://127.0.0.1:80"

# 台大正門附近座標（預設）
DEFAULT_LAT = 25.0170
DEFAULT_LON = 121.5400
DEFAULT_RADIUS = 600  # 公尺

# ──────────────────────────────────────────────
# 命名地點對照表（可自行擴充）
# ──────────────────────────────────────────────
NAMED_LOCATIONS = {
    "台大正門":   (25.0170, 121.5400),
    "台大":       (25.0170, 121.5400),   # 別名
    "博雅館":     (25.0196, 121.5421),
    "新生南路":   (25.0215, 121.5415),
    "水源市場":   (25.0121, 121.5326),
    "男一宿舍":   (25.0181, 121.5368),
    "男一":       (25.0181, 121.5368),   # 別名
}

def resolve_location(location_str, default_lat, default_lon):
    """把地名字串解析成 (lat, lon)，找不到就回傳預設值"""
    if not location_str:
        return default_lat, default_lon
    # 完全比對
    if location_str in NAMED_LOCATIONS:
        return NAMED_LOCATIONS[location_str]
    # 模糊比對：只要輸入的字串包含在地名中，或地名包含在輸入中
    for name, coords in NAMED_LOCATIONS.items():
        if name in location_str or location_str in name:
            return coords
    print(f"[location] 找不到 '{location_str}'，使用預設座標。", file=sys.stderr)
    print(f"[location] 可用地點：{', '.join(NAMED_LOCATIONS.keys())}", file=sys.stderr)
    return default_lat, default_lon

# ──────────────────────────────────────────────
# 心情關鍵字 → tag 對應表
# ──────────────────────────────────────────────
MOOD_RULES = {
    "不想吃辣": {"exclude_tags": ["辣", "麻辣", "川菜"]},
    "想吃辣":   {"require_tags": ["辣", "麻辣", "辣味"]},
    "想吃輕食": {"require_tags": ["輕食", "沙拉", "健康", "清淡"]},
    "不想吃重口味": {"exclude_tags": ["辣", "重口", "油膩", "油炸"]},
    "想吃熱的":  {"exclude_tags": ["冷食", "沙拉", "生食"]},
    "想吃便宜的": {"require_tags": ["便宜", "平價", "學生"]},
    "不想走太遠": {"max_distance": 300},
    "想吃甜食":  {"require_tags": ["甜點", "蛋糕", "飲料", "dessert"]},
    "吃素":     {"require_tags": ["素食", "蔬食", "vegan"]},
    "不吃豬":   {"exclude_tags": ["豬肉", "豬排", "滷肉"]},
    "想吃日式":  {"require_tags": ["日式", "日本", "拉麵", "壽司"]},
    "想吃台式":  {"require_tags": ["台式", "便當", "小吃", "滷肉"]},
    "想吃西式":  {"require_tags": ["西式", "漢堡", "義大利", "pizza"]},
}

# 時段對應
MEAL_TIME_RANGES = {
    "breakfast": (5, 10),
    "lunch":     (10, 14),
    "afternoon": (14, 17),
    "dinner":    (17, 21),
    "latenight": (21, 5),
}


def load_food_list():
    if not os.path.exists(FOOD_LIST_PATH):
        return []
    try:
        with open(FOOD_LIST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("favorites", [])
    except Exception:
        return []


def load_recent_food():
    """從 memory.json 取出最近 3 天吃過的東西，回傳名稱 set"""
    recent = set()
    try:
        if not os.path.exists(MEMORY_PATH):
            return recent
        with open(MEMORY_PATH, "r", encoding="utf-8") as f:
            mem = json.load(f)
        now = datetime.now(timezone(timedelta(hours=8)))
        cutoff = now - timedelta(days=3)
        for entry in mem.get("food_history", []):
            try:
                eaten_at = datetime.fromisoformat(entry["eaten_at"])
                if eaten_at > cutoff:
                    recent.add(entry["name"])
            except Exception:
                pass
    except Exception:
        pass
    return recent


def guess_meal_from_time():
    """根據現在時間猜測是哪一餐"""
    hour = datetime.now(timezone(timedelta(hours=8))).hour
    if 5 <= hour < 10:
        return "breakfast"
    elif 10 <= hour < 14:
        return "lunch"
    elif 14 <= hour < 17:
        return "afternoon"
    elif 17 <= hour < 21:
        return "dinner"
    else:
        return "latenight"


def parse_mood_filters(mood_str: str):
    """把 mood 字串轉換成 require_tags / exclude_tags 集合"""
    require_tags = set()
    exclude_tags = set()
    for keyword, rules in MOOD_RULES.items():
        if keyword in mood_str:
            for t in rules.get("require_tags", []):
                require_tags.add(t.lower())
            for t in rules.get("exclude_tags", []):
                exclude_tags.add(t.lower())
    return require_tags, exclude_tags


def query_nearby_restaurants(lat, lon, radius):
    """透過 host api_server 代理 Foursquare 查詢附近餐廳（含評分）"""
    url = f"{HOST_API_BASE}/food-nearby?lat={lat}&lon={lon}&radius={radius}"
    try:
        cmd = [
            "curl", "-sS", "--max-time", "60",
            "-H", f"X-API-Key: {HOST_API_KEY}",
            url
        ]
        
        # 執行 curl 並取得結果
        resp = subprocess.check_output(cmd, text=True)
        try:
            result = json.loads(resp)
        except json.JSONDecodeError:
            print(f"[/food-nearby 代理] 查詢失敗，伺服器回傳了非預期的資料: {resp[:100]}...")
            return []
            
        restaurants = []
        for el in result.get("elements", []):
            name = el.get("name", "").strip()
            if not name:
                continue
            restaurants.append({
                "name":         name,
                "source":       "foursquare",
                "tags":         el.get("tags", []),
                "meal":         ["breakfast", "lunch", "dinner", "latenight"],
                "distance_m":   el.get("distance_m", 0),
                "rating":       el.get("rating"),        # 0～10，沒有則 None
                "ratings_count": el.get("ratings_count", 0),
                "address":      el.get("address", ""),
                "lat":          el.get("lat"),
                "lon":          el.get("lon"),
            })
        return restaurants
    except subprocess.CalledProcessError as e:
        print(f"[/food-nearby 代理] 查詢失敗: {e}", file=sys.stderr)
        return []


def _haversine(lat1, lon1, lat2, lon2):
    """計算兩點間距離（公尺）"""
    from math import radians, cos, sin, asin, sqrt
    R = 6371000
    φ1, φ2 = radians(lat1), radians(lat2)
    Δφ = radians(lat2 - lat1)
    Δλ = radians(lon2 - lon1)
    a = sin(Δφ/2)**2 + cos(φ1)*cos(φ2)*sin(Δλ/2)**2
    return R * 2 * asin(min(1, sqrt(a)))


def filter_candidates(candidates, meal, require_tags, exclude_tags, recent_eaten):
    """依條件篩選候選清單"""
    filtered = []
    for c in candidates:
        name = c.get("name", "")
        # 排除最近吃過的
        if name in recent_eaten:
            continue
        # 排除不支援此時段的（私人清單才有 meal 限制）
        if c.get("source") != "osm" and meal not in c.get("meal", [meal]):
            continue
        # 標籤篩選
        c_tags = set(t.lower() for t in c.get("tags", []))
        c_full = (name + " " + " ".join(c.get("tags", []))).lower()

        if exclude_tags and any(ex in c_full for ex in exclude_tags):
            continue
        if require_tags:
            if not any(req in c_full for req in require_tags):
                continue
        filtered.append(c)
    return filtered


def pick(candidates, n=3):
    """選取推薦：有評分的先按評分高低排序，私人清單加權"""
    if not candidates:
        return []

    def sort_key(c):
        # 評分（Foursquare 0~10）存在就用，私人清單額外 +1加分
        rating = c.get("rating") or 0
        bonus  = 1.0 if c.get("source") not in ("osm", "foursquare") else 0.0
        return rating + bonus

    # 有評分的排前面，沒評分的隨機插入後面
    rated   = sorted([c for c in candidates if c.get("rating")], key=sort_key, reverse=True)
    unrated = [c for c in candidates if not c.get("rating")]
    random.shuffle(unrated)
    pool = rated + unrated

    chosen = []
    used_names = set()
    for c in pool:
        if c["name"] not in used_names:
            chosen.append(c)
            used_names.add(c["name"])
        if len(chosen) >= n:
            break
    return chosen


    pass


def format_single_pick(i, p):
    rating       = p.get("rating")
    ratings_cnt  = p.get("ratings_count", 0)
    tags         = "、".join(p.get("tags", [])[:3])
    addr         = p.get("address", "")

    if rating is not None:
        stars = "⭐" * round(rating / 2)  # 10分轉成 5 顆星
        rating_str = f"{stars} {rating:.1f}/10（{ratings_cnt} 則）"
    else:
        rating_str = "（尚無評分）"

    detail = addr

    lines = []
    lines.append(f"  {i}. **{p['name']}**  {rating_str}")
    if tags:
        lines.append(f"     🏷️ {tags}")
    if detail:
        lines.append(f"     📍 {detail}")
    return "\n".join(lines)


def format_output(picks, meal, mood, location_label="", grouped=False):
    """格式化輸出給龍蝦讀"""
    meal_label = {
        "breakfast": "早餐", "lunch": "午餐",
        "afternoon": "下午茶", "dinner": "晚餐", "latenight": "宵夜"
    }.get(meal, meal)

    lines = [f"【食物推薦結果】時段：{meal_label}"]
    if location_label:
        lines.append(f"地點：{location_label}")
    if mood:
        lines.append(f"心情偏好：{mood}")
    lines.append("")

    if not picks:
        lines.append("⚠️ 沒有找到符合條件的餐廳，建議放寬條件或直接問主人想吃什麼！")
        return "\n".join(lines)
    lines.append(f"🎯 主推（共 {len(picks)} 個）：")
    for i, p in enumerate(picks, 1):
        lines.append(format_single_pick(i, p))

    lines.append("")
    lines.append("👉 主人，你想去哪一家呢？或者我重新挑一個？如果你決定了，告訴我你去吃了，我來幫你記下來！")
    lines.append("")
    lines.append("【系統強烈指令】：請務必將上方【所有的餐廳名稱、評分、地址與距離】以「純文字」格式一字不漏地直接輸出回覆給使用者！絕對禁止使用任何 `[embed]` 標籤、終端機截圖或折疊元件，否則使用者會看不到內容！")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="幫你決定吃什麼")
    parser.add_argument("--meal", choices=["breakfast", "lunch", "afternoon", "dinner", "latenight"],
                        default=None, help="時段")
    parser.add_argument("--mood", default="", help="心情或偏好關鍵字")
    parser.add_argument("--location", default="", help="地點名稱（如：公館、師大夜市、東區）")
    parser.add_argument("--lat",  type=float, default=DEFAULT_LAT, help="緯度（優先於 --location）")
    parser.add_argument("--lon",  type=float, default=DEFAULT_LON, help="經度（優先於 --location）")
    parser.add_argument("--radius", type=int, default=DEFAULT_RADIUS, help="搜尋半徑（公尺）")
    args = parser.parse_args()

    # 解析地點：若有傳 --location，用地名查座標；若有明確 --lat/--lon 則直接用
    lat, lon = args.lat, args.lon
    location_label = ""
    if args.location:
        lat, lon = resolve_location(args.location, DEFAULT_LAT, DEFAULT_LON)
        location_label = args.location
    elif args.lat == DEFAULT_LAT and args.lon == DEFAULT_LON:
        location_label = "台大附近"
    else:
        location_label = f"({args.lat:.4f}, {args.lon:.4f})"

    # 判斷時段
    meal = args.meal or guess_meal_from_time()

    # 解析心情
    require_tags, exclude_tags = parse_mood_filters(args.mood)

    # 載入私人清單和最近吃過的
    favorites = load_food_list()
    for f in favorites:
        f["source"] = "personal"
    recent_eaten = load_recent_food()

    osm_results = query_nearby_restaurants(lat, lon, args.radius)
            
    all_candidates = favorites + osm_results
    filtered = filter_candidates(all_candidates, meal, require_tags, exclude_tags, recent_eaten)
    if not filtered:
        filtered = filter_candidates(all_candidates, meal, set(), set(), recent_eaten)
    picks = pick(filtered, n=3)
    print(format_output(picks, meal, args.mood, location_label, grouped=False))


if __name__ == "__main__":
    main()
