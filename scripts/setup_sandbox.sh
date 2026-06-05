#!/bin/bash
# 檔名: scripts/setup_sandbox.sh
# 說明: 安裝 NemoClaw 防火牆白名單政策

echo "=========================================="
echo "  🛡️ NemoClaw 沙盒防火牆初始化設定"
echo "=========================================="

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROJECT_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)

# 確認 policy 檔案存在
if [ ! -f "$PROJECT_ROOT/config/allow_local.yaml" ] || [ ! -f "$PROJECT_ROOT/config/allow_weather.yaml" ]; then
    echo "❌ 找不到設定檔！請確定 config/allow_local.yaml 和 config/allow_weather.yaml 存在。"
    exit 1
fi

echo "✅ 正在套用 本機 Port 80 連線白名單 (allow_local.yaml)..."
nemoclaw nemo-sandbox policy-add "$PROJECT_ROOT/config/allow_local.yaml"
if [ $? -ne 0 ]; then
    echo "⚠️ 套用 allow_local.yaml 失敗，請確認 NemoClaw 是否安裝並正常運作。"
fi

echo "✅ 正在套用 天氣 API 連線白名單 (allow_weather.yaml)..."
nemoclaw nemo-sandbox policy-add "$PROJECT_ROOT/config/allow_weather.yaml"
if [ $? -ne 0 ]; then
    echo "⚠️ 套用 allow_weather.yaml 失敗。"
fi

echo ""
echo "🎉 沙盒防火牆設定完成！AI 現在可以安全地與 Mac 主機及天氣 API 連線了。"
