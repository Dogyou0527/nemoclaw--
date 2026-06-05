#!/bin/bash
# 檔名: send_discord.sh
# 說明: 提供給 NemoClaw 呼叫的 Discord 發送腳本
# 用法: ./send_discord.sh "你好，這是我要說的話"

MESSAGE="$1"

if [ -z "$MESSAGE" ]; then
    echo "錯誤: 請提供要發送的訊息內容"
    exit 1
fi

# 將訊息發送給 "本地代理閘道服務 (Host Server)"
# 通過 host.docker.internal，沙箱可以穿透回 MacOS 主機
PROXY_URL="http://host.docker.internal:8085/discord-proxy"

curl -sS -X POST "$PROXY_URL" \
     -H "Content-Type: application/json" \
     -d "{\"content\": \"$MESSAGE\"}"

echo "訊息已提交給本地代理處理。"
