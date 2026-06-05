#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import json
import time
import os

MEMORY_PATH = "/sandbox/.openclaw/workspace/memory.json"
FOOD_LIST_PATH = "/sandbox/.openclaw/workspace/food_list.json"

def load_memory():
    if not os.path.exists(MEMORY_PATH):
        return {"events": [], "food_history": [], "email_log": {"last_checked": "", "recent_3days": []}}
    try:
        with open(MEMORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"events": [], "email_log": {"last_checked": "", "recent_3days": []}}

def save_memory(data):
    try:
        with open(MEMORY_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"Error saving memory: {e}", file=sys.stderr)
        return False

def add_event(date_str, description, event_type="task", note=""):
    data = load_memory()
    if "events" not in data:
        data["events"] = []
    
    # Check if event already exists to prevent duplicates
    for event in data["events"]:
        if event.get("due", "").startswith(date_str) and event.get("title") == description:
            print(f"Event already exists: {description} on {date_str}")
            return True

    # Parse due date (standardize to ISO format)
    # If date_str is just YYYY-MM-DD, append T23:59:59+08:00
    due_val = date_str
    if len(date_str) == 10:
        due_val = f"{date_str}T23:59:59+08:00"

    event_id = str(int(time.time() * 1000))
    new_event = {
        "id": event_id,
        "type": event_type,
        "source": "manual",
        "title": description,
        "due": due_val,
        "note": note,
        "link": "",
        "done": False,
        "reminded": False,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }
    data["events"].append(new_event)
    if save_memory(data):
        print(f"Successfully added event: {description} on {due_val}")
        return True
    return False

def list_events():
    data = load_memory()
    print(json.dumps(data, ensure_ascii=False, indent=2))

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 manage_memory.py <add|list> [args...]")
        sys.exit(1)
        
    cmd = sys.argv[1]
    if cmd == "add":
        if len(sys.argv) < 4:
            print("Usage: python3 manage_memory.py add <YYYY-MM-DD> <description> [type] [note]")
            sys.exit(1)
        date_str = sys.argv[2]
        description = sys.argv[3]
        event_type = sys.argv[4] if len(sys.argv) > 4 else "task"
        note = sys.argv[5] if len(sys.argv) > 5 else ""
        add_event(date_str, description, event_type, note)
    elif cmd == "list":
        list_events()
    elif cmd == "food":
        if len(sys.argv) < 3:
            print("Usage: python3 manage_memory.py food <food_name>")
            sys.exit(1)
        food_name = sys.argv[2]
        data = load_memory()
        if "food_history" not in data:
            data["food_history"] = []
        now_str = time.strftime("%Y-%m-%dT%H:%M:%S+08:00",
                                time.localtime())
        # 避免同一天同一個東西重複寫入
        today = now_str[:10]
        already = any(
            e.get("name") == food_name and e.get("eaten_at", "")[:10] == today
            for e in data["food_history"]
        )
        if already:
            print(f"今天已經記錄過吃 {food_name} 了，略過。")
        else:
            data["food_history"].append({"name": food_name, "eaten_at": now_str})
            # 只保留最近 30 筆
            data["food_history"] = data["food_history"][-30:]
            if save_memory(data):
                print(f"✅ 已記錄：{food_name}（{now_str}）")
    elif cmd == "fav-add":
        # manage_memory.py fav-add <店名> [tags...] [meal=lunch]
        if len(sys.argv) < 3:
            print("Usage: python3 manage_memory.py fav-add <店名> [tags: 台式,便宜,...] [meal: lunch|dinner|...]")
            sys.exit(1)
        fav_name = sys.argv[2]
        tags_str = sys.argv[3] if len(sys.argv) > 3 else ""
        meal_str = sys.argv[4] if len(sys.argv) > 4 else ""

        # 解析 tags
        tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []
        # 解析 meal（支援逗號分隔，或預設全餐）
        valid_meals = ["breakfast", "lunch", "afternoon", "dinner", "latenight"]
        if meal_str:
            meals = [m.strip() for m in meal_str.split(",") if m.strip() in valid_meals]
        else:
            meals = ["breakfast", "lunch", "afternoon", "dinner", "latenight"]

        # 讀取 food_list.json
        if os.path.exists(FOOD_LIST_PATH):
            try:
                with open(FOOD_LIST_PATH, "r", encoding="utf-8") as f:
                    food_data = json.load(f)
            except Exception:
                food_data = {"favorites": []}
        else:
            food_data = {"favorites": []}

        favorites = food_data.get("favorites", [])
        # 避免重複
        if any(fav.get("name") == fav_name for fav in favorites):
            print(f"⚠️ '{fav_name}' 已經在常用清單中了，不重複加入。")
        else:
            favorites.append({
                "name": fav_name,
                "tags": tags,
                "meal": meals
            })
            food_data["favorites"] = favorites
            try:
                with open(FOOD_LIST_PATH, "w", encoding="utf-8") as f:
                    json.dump(food_data, f, ensure_ascii=False, indent=2)
                print(f"✅ 已將 '{fav_name}' 加入常用餐廳清單！（標籤：{tags}，時段：{meals}）")
            except Exception as e:
                print(f"Error saving food_list: {e}", file=sys.stderr)
                sys.exit(1)

    elif cmd == "fav-list":
        if os.path.exists(FOOD_LIST_PATH):
            try:
                with open(FOOD_LIST_PATH, "r", encoding="utf-8") as f:
                    food_data = json.load(f)
                favorites = food_data.get("favorites", [])
                if favorites:
                    print(f"📋 目前有 {len(favorites)} 家常用餐廳：")
                    for i, fav in enumerate(favorites, 1):
                        tags = ", ".join(fav.get("tags", [])) or "無標籤"
                        print(f"  {i}. {fav['name']}（{tags}）")
                else:
                    print("常用餐廳清單目前是空的。")
            except Exception as e:
                print(f"Error reading food_list: {e}")
        else:
            print("常用餐廳清單目前是空的。")

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)

if __name__ == "__main__":
    main()
