import json
import os
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

COOKIE_FILE = "mail_cookies.json"

def fetch_mail_data():
    with sync_playwright() as p:
        # 使用獨立的 Profile 資料夾避免跟 NTU COOL 衝突
        context = p.chromium.launch_persistent_context(
            user_data_dir="./ntu-mail-profile",
            headless=True,
            viewport={'width': 1280, 'height': 720}
        )
        
        if os.path.exists(COOKIE_FILE):
            try:
                with open(COOKIE_FILE, "r") as f:
                    cookies = json.load(f)
                    context.add_cookies(cookies)
                print("【系統】已載入 Webmail 登入狀態")
            except Exception as e:
                print(f"【系統】載入 Cookie 失敗: {e}")

        page = context.pages[0] if context.pages else context.new_page()
        
        print("【系統】正在檢查台大信箱登入狀態...")
        page.goto("https://wmail1.cc.ntu.edu.tw/rc/index.php")
        
        # 等待網頁載入
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(2000)
        
        # 萬用登入偵測：如果畫面上看得到密碼輸入框，代表尚未登入
        if page.locator('input[type="password"]').is_visible():
            print("【系統】偵測到信箱登入畫面！正在嘗試自動登入...")
            
            auto_logged_in = False
            # 從環境變數讀取帳號密碼（已從 config.json 移出）
            mail_user = os.getenv("WEBMAIL_USERNAME")
            mail_pass = os.getenv("WEBMAIL_PASSWORD")

            if mail_user and mail_pass:
                try:
                    print("【系統】讀取到環境變數，正在自動填入信筱帳號密碼...")
                    page.locator('input[name="_user"], input[type="text"]').first.fill(mail_user)
                    page.locator('input[type="password"]').first.fill(mail_pass)
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(3000)

                    if not page.locator('input[type="password"]').is_visible():
                        auto_logged_in = True
                        print("【系統】自動登入成功！")
                    else:
                        print("【系統】自動登入似乎失敗了，退回手動模式。")
                except Exception as e:
                    print(f"【系統】自動登入發生錯誤: {e}")

            if not auto_logged_in:
                print("【系統】請在彈出的視窗中登入您的台大信箱！(只用學號，免加@)")
                print("【系統】登入完成後程式會自動繼續...")
                
                # 每秒檢查密碼框是否消失 (代表登入成功跳轉)
                while page.locator('input[type="password"]').is_visible():
                    page.wait_for_timeout(1000)
                    
                page.wait_for_timeout(3000) # 等待收件匣完全載入
                print("【系統】手動登入成功！")
            
            cookies = context.cookies()
            with open(COOKIE_FILE, "w") as f:
                json.dump(cookies, f)
            print("【系統】Cookie 已強制寫入儲存檔，下次不用再登入！")
        
        print("【系統】開始抓取最新信件...")
        
        # 萬用信件表格萃取：尋找所有 Frame 裡面的 table rows
        js_code = """
        () => {
            let rows = [];
            const urgentKeywords = ["作業", "due", "deadline", "考試", "exam", "截止", "重要", "urgent", "停課", "取消", "cancel", "宿舍"];
            
            const extractRows = (doc) => {
                doc.querySelectorAll('tr').forEach(tr => {
                    // 把換行換成 |，讓 AI 更好讀
                    let text = tr.innerText.replace(/\\n/g, ' | ').replace(/\\s+/g, ' ').trim();
                    // 太短的列通常是排版用的，過濾掉
                    if (text.length > 15) {
                        let isUrgent = urgentKeywords.some(kw => text.toLowerCase().includes(kw));
                        let prefix = isUrgent ? "🚨 [重要通知] " : "- ";
                        rows.push(prefix + text);
                    }
                });
            };
            
            extractRows(document);
            for(let i=0; i<window.frames.length; i++) {
                try { extractRows(window.frames[i].document); } catch(e) {}
            }
            
            // 如果抓不到 table，退而求其次抓全部文字
            if (rows.length === 0) {
                let allText = document.body.innerText;
                for(let i=0; i<window.frames.length; i++) {
                    try { allText += '\\n' + window.frames[i].document.body.innerText; } catch(e) {}
                }
                return allText;
            }
            
            return rows.slice(0, 30).join('\\n'); // 取前 30 行
        }
        """
        
        try:
            emails_text = page.evaluate(js_code)
        except Exception as e:
            emails_text = f"抓取信件 DOM 失敗: {e}"
            
        output = "【NTU Webmail 最新信件一覽】\\n\\n"
        if not emails_text or emails_text.strip() == "":
            output += "收件匣似乎是空的，或者無法讀取信件列表。\\n"
        else:
            lines = emails_text.strip().split('\\n')
            urgent_lines = []
            normal_lines = []
            for line in lines:
                if "🚨 [重要通知]" in line:
                    urgent_lines.append(line)
                else:
                    normal_lines.append(line)
                    
            if urgent_lines:
                output += "\\n".join(urgent_lines) + "\\n"
                
            if normal_lines:
                output += "\\n".join(normal_lines[:5]) + "\\n"
                if len(normal_lines) > 5:
                    output += f"... 以及其他 {len(normal_lines) - 5} 封一般信件\\n"
                    
        context.close()
        return output

if __name__ == "__main__":
    try:
        result = fetch_mail_data()
        print("\\n" + "="*40)
        print(result)
        print("="*40 + "\\n")
    except Exception as e:
        print(f"發生錯誤: {e}")
