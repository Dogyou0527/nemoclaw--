import json
import os
import re
import datetime
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

COOKIE_FILE = "ntu_cookies.json"

def fetch_ntu_data():
    with sync_playwright() as p:
        # 使用 headless=True 進行背景靜音抓取
        context = p.chromium.launch_persistent_context(
            user_data_dir="./ntu-chrome-profile",
            headless=True,
            viewport={'width': 1280, 'height': 720}
        )
        
        # 載入先前儲存的 Cookie
        if os.path.exists(COOKIE_FILE):
            try:
                with open(COOKIE_FILE, "r") as f:
                    cookies = json.load(f)
                    context.add_cookies(cookies)
                print("【系統】已載入上次的登入狀態 (Cookie)")
            except Exception as e:
                print(f"【系統】載入 Cookie 失敗: {e}")

        page = context.pages[0] if context.pages else context.new_page()
        
        print("【系統】正在檢查 NTU COOL 登入狀態...")
        page.goto("https://cool.ntu.edu.tw")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(2000)
        
        # 處理 NTU COOL 新版的登入中繼頁面 (在 Headless 模式下預設語系可能是英文)
        login_btn_tw = page.locator('text="以計中帳號登入"')
        login_btn_en = page.locator('text="NTU Account Sign in"')
        
        if login_btn_tw.is_visible():
            print("【系統】發現 NTU COOL 訪客頁面 (中文)，自動點擊登入按鈕...")
            login_btn_tw.click()
            page.wait_for_timeout(3000) # 等待跳轉至 SSO
        elif login_btn_en.is_visible():
            print("【系統】發現 NTU COOL 訪客頁面 (英文)，自動點擊登入按鈕...")
            login_btn_en.click()
            page.wait_for_timeout(3000) # 等待跳轉至 SSO
        
        # 如果網址被導向 SSO 登入頁面，或者畫面上出現密碼框
        if "sso" in page.url or "login" in page.url or page.locator('input[type="password"]').is_visible():
            print("【系統】偵測到登入畫面！正在嘗試自動登入...")
            
            auto_logged_in = False
            # 從環境變數讀取帳號密碼（已從 config.json 移出）
            ntu_user = os.getenv("NTU_USERNAME")
            ntu_pass = os.getenv("NTU_PASSWORD")

            if ntu_user and ntu_pass:
                try:
                    print(f"【系統】目前網址: {page.url}")
                    print("【系統】讀取到環境變數，正在自動填入台大帳號密碼...")
                    page.locator('input[name="user"], input[type="text"], input[type="email"]').first.fill(ntu_user)
                    page.locator('input[name="pass"], input[type="password"]').first.fill(ntu_pass)
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(3000)

                    if "sso" not in page.url:
                        auto_logged_in = True
                        print("【系統】自動登入成功！")
                    else:
                        print("【系統】自動登入似乎失敗了，退回手動模式。")
                except Exception as e:
                    print(f"【系統】自動登入發生錯誤: {e}")

            if not auto_logged_in:
                print("【系統】請在彈出的視窗中手動登入您的台大帳號！(登入後會自動繼續)")
                while "sso" in page.url or "login" in page.url:
                    page.wait_for_timeout(1000)
                page.wait_for_timeout(3000)
                print("【系統】手動登入成功！")

            # 手動將所有 Cookie 存下來
            
            # 手動將所有 Cookie 存下來 (包含 Session Cookies)
            cookies = context.cookies()
            with open(COOKIE_FILE, "w") as f:
                json.dump(cookies, f)
            print("【系統】Cookie 已強制寫入儲存檔，下次不用再登入！")
        
        print("【系統】開始抓取課程清單...")
        courses_resp = context.request.get("https://cool.ntu.edu.tw/api/v1/courses?enrollment_state=active")
        
        if courses_resp.status != 200:
            print(f"【系統】抓取課程失敗，HTTP 狀態碼: {courses_resp.status}")
            context.close()
            return "無法抓取課程資料，請檢查登入狀態或稍後再試。"
            
        courses_json = courses_resp.json()
        valid_courses = [c for c in courses_json if 'name' in c and 'id' in c]
        
        output = "【NTU COOL 真實即時資料】\\n\\n"
        output += "🚨 近期待繳作業（7 天內截止）：\\n"
        has_any_assignments = False
        
        print("【系統】開始逐課掃描作業...")
        for course in valid_courses:
            course_id = course['id']
            course_name = course['name']

            # 加上 include[]=submission 參數，才能知道這份作業交了沒
            assign_url = f"https://cool.ntu.edu.tw/api/v1/courses/{course_id}/assignments?include[]=submission"
            assign_resp = context.request.get(assign_url)

            if assign_resp.status != 200:
                continue

            assignments = assign_resp.json()
            unsubmitted_assignments = []

            now_utc = datetime.datetime.now(datetime.timezone.utc)
            
            for assign in assignments:
                # 檢查作業是否有 submission 紀錄
                submission = assign.get('submission', {})
                workflow_state = submission.get('workflow_state')

                # 'submitted' 和 'graded' 代表已經交了，'unsubmitted' 代表還沒交
                if workflow_state not in ['submitted', 'graded']:
                    due_at = assign.get('due_at') or assign.get('lock_at')

                    # 問題 3 修正：無期限作業直接略過，不顯示
                    if not due_at:
                        continue

                    try:
                        due_clean = due_at.replace("Z", "+00:00")
                        due_dt = datetime.datetime.fromisoformat(due_clean)
                        diff_days = (due_dt - now_utc).total_seconds() / 86400.0

                        # 只保留未來 7 天內截止，或過去 3 天內過期的作業
                        if diff_days < -3.0 or diff_days > 7.0:
                            continue
                    except Exception:
                        continue  # 日期格式無法解析，跳過

                    unsubmitted_assignments.append(assign)
            
            if unsubmitted_assignments:
                has_any_assignments = True
                output += f"\\n📌 [{course_name}]:\\n"
                for assign in unsubmitted_assignments:
                    title = assign.get('name', '未命名作業')
                    
                    # 處理期限邏輯：如果 due_at 是 null，就找 lock_at (關閉時間)，否則顯示無期限
                    due_at = assign.get('due_at') or assign.get('lock_at') or '無期限'
                    
                    if due_at != '無期限' and "Z" in due_at:
                        due_at = due_at.replace("T", " ").replace("Z", " (UTC)")
                    output += f"  - {title} (期限: {due_at})\\n"
        
        if not has_any_assignments:
            output += "恭喜！您這學期所有的作業都已經繳交完畢，目前沒有任何幽靈作業！\\n"

        # ── 抓取各課最新公告（重點偵測考試/測驗/作業/停課通知）──
        URGENT_KEYWORDS = [
            "考試", "測驗", "期中", "期末", "quiz", "exam", "test", "midterm", "final",
            "小考", "隨堂", "筆試", "作答", "考卷", "評量", "assessment",
            "作業", "停課", "due", "deadline", "assignment", "homework", "取消", "cancel", "宿舍"
        ]

        output += "\\n\\n📢 近期課程公告（最近 7 天，重點偵測考試通知）：\\n"
        has_any_announcements = False

        for course in valid_courses:
            course_id = course['id']
            course_name = course['name']

            # 抓該課程最新 5 則公告
            ann_url = (
                f"https://cool.ntu.edu.tw/api/v1/courses/{course_id}/discussion_topics"
                f"?only_announcements=true&order_by=posted_at&per_page=5"
            )
            ann_resp = context.request.get(ann_url)
            if ann_resp.status != 200:
                continue

            announcements = ann_resp.json()
            if not isinstance(announcements, list):
                continue

            now_utc = datetime.datetime.now(datetime.timezone.utc)
            cutoff = now_utc - datetime.timedelta(days=7)  # 問題 4 修正：7 天視窗

            course_ann_lines = []
            for ann in announcements:
                posted_at_str = ann.get("posted_at") or ann.get("created_at", "")
                title = ann.get("title", "（無標題）")
                message = ann.get("message", "") or ""

                # 取得寄件人名稱（Canvas API 的 author 物件）
                author_obj = ann.get("author", {}) or {}
                author_name = (
                    author_obj.get("display_name")
                    or ann.get("user_name")
                    or "（未知發佈者）"
                )

                # 日期轉學如台灣時間
                try:
                    posted_dt = datetime.datetime.fromisoformat(
                        posted_at_str.replace("Z", "+00:00")
                    ).astimezone(datetime.timezone(datetime.timedelta(hours=8)))
                    date_str = posted_dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    date_str = posted_at_str[:10] if posted_at_str else "?"

                # 去除 HTML tag 再整理多餘空白，保留完整內文（最多 600 字）
                clean_msg = re.sub(r'<[^>]+>', ' ', message)
                clean_msg = re.sub(r'\s+', ' ', clean_msg).strip()[:600]

                # 過濾 7 天內的公告
                try:
                    posted_dt_utc = datetime.datetime.fromisoformat(
                        posted_at_str.replace("Z", "+00:00")
                    )
                    if posted_dt_utc < cutoff:
                        continue
                except Exception:
                    pass

                # 偵測緊急關鍵字
                full_text = (title + " " + clean_msg).lower()
                is_urgent = any(kw in full_text for kw in URGENT_KEYWORDS)
                prefix = "🚨 [重要通知] " if is_urgent else "  "

                # 多行格式：標題行 + 寄件人/日期行 + 內文行
                course_ann_lines.append(f"{prefix}{title}")
                course_ann_lines.append(f"     📅 {date_str}｜👤 {author_name}")
                if clean_msg:
                    course_ann_lines.append(f"     📝 {clean_msg}")
                course_ann_lines.append("")


            if course_ann_lines:
                has_any_announcements = True
                output += f"\\n📌 [{course_name}]:\\n"
                for line in course_ann_lines:
                    output += f"  {line}\\n"

        if not has_any_announcements:
            output += "  （近 7 天內無新公告）\\n"

        # ── 抓取 NTU COOL 收件夾（私訊/系統通知，例如助教傳的課後提醒、考試提醒）──
        output += "\\n\\n📬 NTU COOL 收件夾（最近 7 天，重點偵測考試相關訊息）：\\n"
        has_inbox_msgs = False

        try:
            inbox_resp = context.request.get(
                "https://cool.ntu.edu.tw/api/v1/conversations?per_page=20&scope=inbox"
            )
            if inbox_resp.status == 200:
                inbox_msgs = inbox_resp.json()
                if isinstance(inbox_msgs, list):
                    now_utc = datetime.datetime.now(datetime.timezone.utc)
                    cutoff = now_utc - datetime.timedelta(days=7)  # 問題 4 修正：7 天視窗

                    for msg in inbox_msgs:
                        date_str = msg.get("last_message_at") or msg.get("updated_at", "")
                        subject = msg.get("subject", "（無主旨）")
                        last_msg = msg.get("last_message", "") or ""
                        clean_last = re.sub(r'<[^>]+>', ' ', last_msg)[:300].strip()

                        # 問題 5 修正：抓取所屬課程名稱（Canvas API 的 context_name 欄位）
                        context_name = msg.get("context_name", "")

                        try:
                            msg_dt = datetime.datetime.fromisoformat(
                                date_str.replace("Z", "+00:00")
                            )
                            if msg_dt < cutoff:
                                continue
                        except Exception:
                            pass

                        full_text = (subject + " " + clean_last).lower()
                        is_urgent = any(kw in full_text for kw in URGENT_KEYWORDS)
                        prefix = "🚨 [重要通知] " if is_urgent else "  "
                        short_date = date_str[:10] if date_str else "?"
                        # 若有課程資訊，標示在主旨前面
                        course_tag = f"[{context_name}] " if context_name else ""
                        output += f"{prefix}{short_date} {course_tag}{subject}\\n"
                        if clean_last:
                            output += f"     └─ {clean_last[:100]}...\\n"
                        has_inbox_msgs = True
        except Exception as inbox_err:
            output += f"  （收件夾抓取失敗：{inbox_err}）\\n"

        if not has_inbox_msgs:
            output += "  （近 7 天內無新收件夾訊息）\\n"

        output += (
            "\\n【重要提醒給 AI】：若上方有 🚨 [重要通知] 的項目，"
            "請務必主動讀取記憶（/sandbox/.openclaw/workspace/memory.json），"
            "將相關日期/時間寫入記憶並備份，避免使用者遺忘！\\n"
        )

        context.close()
        return output



if __name__ == "__main__":
    try:
        result = fetch_ntu_data()
        print("\\n" + "="*40)
        print(result)
        print("="*40 + "\\n")
    except Exception as e:
        print(f"發生錯誤: {e}")
