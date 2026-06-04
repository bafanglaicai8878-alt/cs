#!/usr/bin/env bash
# 修复 Linux 服务器用户 irm|iex 激活失败（无 Steam 时用本地沙箱生成插件）
set -e
cd "$(dirname "$0")"

echo "[*] 创建插件沙箱并更新 config.json ..."
python3 setup_deploy.py

echo "[*] 重启 Web 服务 ..."
bash "$(dirname "$0")/stop_web.sh"
bash 后台运行.sh

echo ""
echo "[*] 测试兑换接口（把 CDK 换成你的）："
echo 'curl -s -X POST http://127.0.0.1:8787/api/redeem -H "Content-Type: application/json" -d '"'"'{"cdk":"你的CDK","machine":"test"}'"'"' | python3 -m json.tool'
