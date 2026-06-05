#!/usr/bin/env python3
"""
檔名: scripts/patch_gemini_bug.py
說明: 修補 OpenClaw 沙盒內部對於 Gemini thoughtSignature 的錯誤過濾邏輯，
      解決 AI 回傳 400 Bad Request 的問題。
      此腳本會透過 nemoclaw 連線進沙盒，並修改 /usr/local/lib/node_modules/openclaw/dist/ 內的程式碼。
"""

import subprocess
import time

def run_patch():
    print("==========================================")
    print("  🩹 NemoClaw 沙盒 Gemini 400 Bug 修補程式")
    print("==========================================")
    print("正在將修補指令注入沙盒，請稍候...")

    # 注入沙盒的 Shell 指令
    # 尋找 openclaw/dist/ 底下的 js 檔，並把刪除 thoughtSignature 的邏輯註解掉或替換掉
    # 這裡使用 perl -pi -e 來進行正則表達式替換
    sandbox_cmd = """
    echo '開始搜尋並修補 JS 檔案...'
    cd /usr/local/lib/node_modules/openclaw/dist/ || exit 1
    
    # 備份原始檔案（如果還沒備份過）
    for file in $(find . -name "*.js"); do
        if [ ! -f "${file}.bak" ]; then
            cp "$file" "${file}.bak"
        fi
    done

    # 尋找並取代過濾 thoughtSignature 的相關邏輯
    # 尋找像是 delete object.thoughtSignature; 或 if (key === 'thoughtSignature') 的語法並使其無效
    find . -name "embedded-agent-helpers*.js" -exec perl -pi -e 's/delete\\s+[a-zA-Z0-9_.]+\\.thoughtSignature\\s*;//g' {} +
    find . -name "openai-transport-stream*.js" -exec perl -pi -e 's/delete\\s+[a-zA-Z0-9_.]+\\.thoughtSignature\\s*;//g' {} +
    
    # 另外一種常見的防禦性替換：把字串 "thoughtSignature" 從黑名單中移除
    # 這裡用較暴力的替換確保繞過
    find . -name "*.js" -exec perl -pi -e 's/(===|==)\\s*[\"\\x27]thoughtSignature[\"\\x27]/(===) "NEVER_MATCH_ME"/g' {} +
    
    echo '修補完成！'
    exit
    """

    try:
        proc = subprocess.run(
            ["/opt/homebrew/bin/nemoclaw", "test", "connect"],
            input=sandbox_cmd.encode('utf-8'),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60
        )
        
        stdout_text = proc.stdout.decode('utf-8', errors='ignore')
        if "修補完成" in stdout_text:
            print("✅ 成功！Gemini Bug 修補程式已執行完畢。")
        else:
            print("⚠️ 修補可能未完全成功，以下是執行輸出：")
            print(stdout_text)
            print("錯誤輸出：")
            print(proc.stderr.decode('utf-8', errors='ignore'))
            
    except subprocess.TimeoutExpired:
        print("❌ 執行超時，請檢查沙盒是否正常運作。")
    except FileNotFoundError:
        print("❌ 找不到 /opt/homebrew/bin/nemoclaw，請確認安裝路徑。")
    except Exception as e:
        print(f"❌ 發生未知錯誤: {e}")

if __name__ == "__main__":
    run_patch()
