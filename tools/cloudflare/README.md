# Cloudflare 公网测试工具

## 当前电脑的日常操作

1. 开机并登录 Windows，等待 Prism 本机服务启动。
2. 打开桌面 `Prism/运行工具`，双击 `启动Prism公网测试.cmd`。
3. 等到窗口显示“公网测试已就绪”及完整 HTTPS 网址，再查看同目录的 `Prism公网测试地址.txt`。
4. 将文件里的当前网址发给测试者，电脑保持开机、联网，避免休眠。

重复点击启动时，会检查已有 Tunnel 的网址是否可访问，并重新写入地址文件。如果 Tunnel 仍在运行，会沿用当前地址。需要重新申请地址时，先点击停止，再点击启动。停止按钮只关闭公网入口，本机网站继续运行。

连接失败时，地址文件会明确显示失败原因，不会保留旧的成功提示；60 秒内未能连通的 Tunnel 会被关闭，之后可再次点击启动。如果启动窗口被提前关闭，下次点击启动会尝试从日志恢复网址。

## 源码和私有运行文件

这个目录里的脚本是公开源码，可以同步到 GitHub。当前电脑 `.tunnel` 里的脚本仅负责调用这里的脚本，实际程序、进程状态和日志仍保存在桌面 `Prism/.tunnel`。

在其他 Windows 电脑使用时，需要先准备本机网站，并在 `项目/data/tunnel` 放置 Cloudflare 官方的、数字签名有效的 `cloudflared.exe`。默认启动方式：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/cloudflare/start-tunnel.ps1
```

默认地址文件位于 `data/tunnel/Prism公网测试地址.txt`。也可通过 `-RuntimeDir` 和 `-AddressFile` 指定运行目录、地址文件。程序、日志和状态文件不进入公开仓库。脚本以带 BOM 的 UTF-8 编码保存，让 Windows PowerShell 5.1 正确识别中文。
