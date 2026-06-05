"""
gmail_fetcher.py - 使用 Gmail API 抓取未讀信件並過濾垃圾/廣告
零 AI Token 消耗：由 Python 直接根據 Gmail 標籤判斷信件重要性

首次執行：python gmail_fetcher.py
  → 會開啟瀏覽器請你授權，之後自動儲存 token 不再需要重複授權

輸出格式（供 proxy.py 解析）：
========================================
{"important": [...], "digest": [...], "skipped": N}
========================================
"""
import os
import json
import sys
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH    = os.path.join(BASE_DIR, "gmail_token.json")
CREDS_PATH    = os.path.join(BASE_DIR, "credentials.json")
CONFIG_PATH   = os.path.join(BASE_DIR, "config.json")

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Gmail 垃圾分類標籤 → 直接丟棄，不通知、不進晨報
JUNK_LABELS = {
    "CATEGORY_PROMOTIONS",  # 廣告/促銷
    "CATEGORY_SOCIAL",      # 社群通知（IG, YouTube, etc.）
    "CATEGORY_FORUMS",      # 論壇/群組
    "CATEGORY_UPDATES",     # 訂閱快訊
    "SPAM",                 # 垃圾郵件
    "TRASH",                # 垃圾桶
}

def get_gmail_service():
    """取得已授權的 Gmail API service，自動處理 token 刷新"""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        print("【Gmail】缺少套件，請執行：pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client")
        sys.exit(1)

    creds = None

    if os.path.exists(TOKEN_PATH):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None  # token 失效，需重新授權

        if not creds or not creds.valid:
            if not os.path.exists(CREDS_PATH):
                print(f"【Gmail】找不到 credentials.json，請先從 Google Cloud Console 下載並放到：\n  {CREDS_PATH}")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(CREDS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())

    from googleapiclient.discovery import build
    return build("gmail", "v1", credentials=creds)


def classify_gmail(sender: str, subject: str, label_ids: set, gmail_cfg: dict = None) -> str:
    """
    判斷 Gmail 信件類型（完全 Python 邏輯，零 AI Token）
    回傳：'urgent'（立刻推播） / 'digest'（進晨報佇列） / 'skip'（丟棄）
    gmail_cfg: 已讀取的 config.json gmail 節點，如果為 None 則在函式內讀取（相容舊行為）
    """
    # 1. 被 Gmail 標記為垃圾分類 → 直接丟棄
    if label_ids & JUNK_LABELS:
        return "skip"

    # 2. Gmail 標記為 IMPORTANT → 立刻推播
    if "IMPORTANT" in label_ids:
        return "urgent"

    # 3. 關鍵字判斷（自傳入的 gmail_cfg，避免重複讀檔）
    text = (sender + " " + subject).lower()
    if gmail_cfg is None:
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
            gmail_cfg = config.get("gmail", {})
        except Exception:
            gmail_cfg = {}

    # 使用者設定的緊急寄件人
    urgent_senders = [s.lower() for s in gmail_cfg.get("urgent_senders", [])]
    if any(s in sender.lower() for s in urgent_senders):
        return "urgent"

    # 使用者設定的封鎖關鍵字（→ skip，不進晨報）
    block_keywords = [k.lower() for k in gmail_cfg.get("block_keywords", [])]
    if any(k in text for k in block_keywords):
        return "skip"

    # 4. 預設關鍵字：作業/考試/重要截止/停課/宿舍 → 立刻推播
    urgent_kw = ["作業", "due", "deadline", "考試", "exam", "截止", "繳交", "重要", "urgent", "invoice", "payment", "停課", "取消", "cancel", "宿舍"]
    if any(k in text for k in urgent_kw):
        return "urgent"

    # 5. 其餘未分類 → 晨報佇列
    return "digest"


def fetch_gmail_data(max_results: int = 30):
    """
    抓取最新未讀 Gmail 並分類
    回傳 (important_list, digest_list, skipped_count)
    """
    service = get_gmail_service()

    # 只抓 INBOX 中的 UNREAD 信件
    result = service.users().messages().list(
        userId="me",
        labelIds=["INBOX", "UNREAD"],
        maxResults=max_results
    ).execute()

    messages = result.get("messages", [])
    if not messages:
        return [], [], 0

    # 一次讀取 config.json（避免每封郵件重複 I/O）
    gmail_cfg = {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        gmail_cfg = config.get("gmail", {})
    except Exception:
        pass

    important_list = []
    digest_list = []
    skipped_count = 0

    for msg in messages:
        try:
            # 只抓 metadata（寄件人、主旨、日期），不下載信件內文，節省流量
            msg_data = service.users().messages().get(
                userId="me",
                id=msg["id"],
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"]
            ).execute()

            label_ids = set(msg_data.get("labelIds", []))
            headers = {h["name"]: h["value"] for h in msg_data.get("payload", {}).get("headers", [])}

            sender  = headers.get("From", "（未知寄件人）")
            subject = headers.get("Subject", "（無主旨）")
            date    = headers.get("Date", "")

            entry = {
                "id":      msg["id"],
                "sender":  sender,
                "subject": subject,
                "date":    date,
                "snippet": msg_data.get("snippet", ""),
            }

            # 傳入已讀取的 gmail_cfg，避免重複讀檔
            cat = classify_gmail(sender, subject, label_ids, gmail_cfg)
            if cat == "urgent":
                important_list.append(entry)
            elif cat == "digest":
                digest_list.append(entry)
            else:
                skipped_count += 1

        except Exception:
            skipped_count += 1
            continue

    return important_list, digest_list, skipped_count


if __name__ == "__main__":
    print("【Gmail】正在連接 Gmail API...")
    try:
        important, digest, skipped = fetch_gmail_data()

        output = {
            "important": important,
            "digest": digest,
            "skipped": skipped,
        }

        print("\n" + "=" * 40)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        print("=" * 40 + "\n")

        print(f"✅ 重要信件：{len(important)} 封  |  晨報佇列：{len(digest)} 封  |  已過濾：{skipped} 封")
    except SystemExit:
        raise
    except Exception as e:
        print(f"【Gmail】抓取失敗: {e}")
        import traceback
        traceback.print_exc()
