#!/bin/sh
# 사용: render.sh <html 이름(확장자 없이)> <높이 px>
D="$(cd "$(dirname "$0")" && pwd -W 2>/dev/null || pwd)"
"/c/Program Files/Google/Chrome/Application/chrome.exe" --headless=new --disable-gpu --hide-scrollbars \
  --force-device-scale-factor=2 --window-size=1200,"$2" --default-background-color=ffffffff \
  --screenshot="$D/$1.png" "file:///$D/$1.html" >/dev/null 2>&1
