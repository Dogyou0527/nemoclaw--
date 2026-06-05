#!/bin/bash
# 檔名: scripts/start_proxy.sh
# 說明: 一鍵啟動 NemoClaw 龍蝦 AI 助理系統（api_server + proxy）

echo "=========================================="
echo "  🦞 NemoClaw 龍蝦 AI 助理系統啟動中..."
echo "=========================================="

# 確保切換到專案根目錄（scripts/ 的上一層）
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
cd "$PROJECT_ROOT"

# 啟用虛擬環境
source venv/bin/activate

# 確認 .env 存在
if [ ! -f ".env" ]; then
    echo "❌ 找不到 .env 檔案！請先複製 .env.example 並填入設定。"
    exit 1
fi

# 確認 API_SECRET_KEY 有設定
API_KEY=$(grep "API_SECRET_KEY" .env | cut -d'=' -f2)
if [ -z "$API_KEY" ] || [ "$API_KEY" = "your_random_api_secret_key_here" ]; then
    echo "⚠️  警告：API_SECRET_KEY 未設定！API 將以無驗證模式運行。"
fi

# 關閉舊的 api_server（如果在跑）
OLD_PID=$(sudo lsof -ti:80 2>/dev/null)
if [ -n "$OLD_PID" ]; then
    echo "🔄 關閉舊的 API Server (PID: $OLD_PID)..."
    sudo kill -9 $OLD_PID 2>/dev/null
    sleep 1
fi

# 關閉舊的 proxy（如果在跑）
OLD_PROXY_PID=$(lsof -ti:8888 2>/dev/null)
if [ -n "$OLD_PROXY_PID" ]; then
    echo "🔄 關閉舊的 Proxy Server (PID: $OLD_PROXY_PID)..."
    kill -9 $OLD_PROXY_PID 2>/dev/null
    sleep 1
fi

# 啟動 API 伺服器（背景執行）
echo "☀️  正在啟動 API 伺服器 (Port 80)..."
echo "   ⚠️  可能需要 sudo 密碼（Port 80 需要管理員權限）"
sudo caffeinate -i python api_server.py > /tmp/api_server.log 2>&1 &
API_PID=$!
echo "   ✅ API Server 已啟動 (PID: $API_PID)，日誌: /tmp/api_server.log"

# 等待 API Server 準備好
sleep 3

# 啟動 Discord 代理伺服器（前景執行，顯示日誌）
echo "🚀 正在啟動 Discord 代理伺服器..."
echo "   按 Ctrl+C 可停止所有服務"
caffeinate -i python proxy.py

