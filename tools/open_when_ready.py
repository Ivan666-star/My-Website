"""等服务真正起来之后再自动打开浏览器。

启动脚本（启动.bat）会把这个脚本丢到后台先跑着，然后再拉起 uvicorn。
这样双击之后不用自己数秒、也不用手动输网址，页面会自己弹出来。

单独运行也可以：python tools/open_when_ready.py
"""
from __future__ import annotations

import sys
import time
import urllib.request
import webbrowser

URL = "http://127.0.0.1:8848"
WAIT_MAX = 120        # 最多轮询次数
INTERVAL = 0.5        # 每次间隔秒数 → 上限约 60 秒
TIMEOUT = 1.0         # 单次请求超时


def main() -> int:
    # 本机的 HTTP_PROXY 会把 127.0.0.1 也代理走，必须显式绕过
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(WAIT_MAX):
        try:
            opener.open(URL + "/api/health", timeout=TIMEOUT).read()
            break
        except Exception:
            time.sleep(INTERVAL)
    webbrowser.open(URL)
    return 0


if __name__ == "__main__":
    sys.exit(main())
