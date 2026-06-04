#!/usr/bin/env bash
# 打包完整客户端（拷到 Windows / Mac 本机运行 GUI）
set -e
cd "$(dirname "$0")"
OUT="static/playgame-client.zip"
mkdir -p static

zip -r "$OUT" . \
  -x "./.git/*" \
  -x "./__pycache__/*" \
  -x "./*/__pycache__/*" \
  -x "./web.log" \
  -x "./web.pid" \
  -x "./.venv/*" \
  -x "./static/playgame-client.zip" \
  -x "./static/mac-client.zip" \
  -x "./nohup.out" \
  -x "./*.pyc" \
  -x "./*/*.pyc" \
  > /dev/null

echo "已生成: $OUT ($(du -h "$OUT" | cut -f1))"
echo "浏览器下载: 见 public_url.txt 中的域名 + /static/playgame-client.zip"
