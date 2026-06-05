#!/bin/bash
# 檔名: scripts/start_tui.sh
# 說明: 啟動 NemoClaw TUI 介面，並防止環境變數 HTTP_PROXY 污染導致連線失敗

echo "啟動 NemoClaw 終端機介面 (TUI)..."
# 使用 env -u HTTP_PROXY 清除可能干擾 WebSocket 的代理設定
env -u HTTP_PROXY openclaw tui
