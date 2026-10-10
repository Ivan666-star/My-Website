# Cloudflare 公网入口工具

Prism 有两个公网入口方案：**固定域名**（日常使用，默认）和**临时地址**（应急备用）。

## 一、固定域名（日常使用）

网址：**https://myprism.click**，`https://www.myprism.click` 同样可用。

- 域名 `myprism.click` 在阿里云注册，**解析托管在 Cloudflare**（NS：`ada.ns.cloudflare.com`、`corey.ns.cloudflare.com`）。域名所有权、续费、实名仍在阿里云。
- 本机用 Cloudflare **命名隧道**（named tunnel）连接：

| 项目 | 值 |
| --- | --- |
| 隧道名 | `prism` |
| 隧道 ID | `23d704ed-6557-420e-a595-04ecf815a643` |
| 配置文件 | `%USERPROFILE%\.cloudflared\config.yml` |
| 凭据文件 | `%USERPROFILE%\.cloudflared\23d704ed-6557-420e-a595-04ecf815a643.json` |
| 本地源站 | `http://127.0.0.1:8848` |
| 开机自启 | 启动项 `Prism 固定域名.vbs` |

凭据文件与 `cert.pem` 属于机密，**不要提交到仓库、不要发给别人**；需要吊销时在 Cloudflare 删除该隧道即可。

### 日常操作

- 开机登录 Windows 后自动启动，无需点击。必须保持电脑开机、联网，且本机网站服务在运行。
- 手动启动 / 停止：桌面 `Prism/运行工具/` 里的 `启动Prism固定域名.cmd`、`停止Prism固定域名.cmd`；停止只关公网入口，本机网站继续运行。
- 启动后会验证 `https://myprism.click/api/health`，结果写进 `运行工具/Prism网站地址.txt`。

### 脚本

| 脚本 | 作用 |
| --- | --- |
| `start-fixed-tunnel.ps1` | 确认本机网站在运行 → 启动命名隧道（已有则复用）→ 验证固定地址 → 更新地址文件 |
| `stop-fixed-tunnel.ps1` | 停止隧道进程，清理状态文件，更新地址文件 |
| `fixed-tunnel-common.ps1` | 共用函数：状态读写、进程身份校验、健康检查、控制文件锁 |

参数：`-RuntimeDir`（程序、日志、状态文件所在目录，默认 `data/tunnel`）、`-AddressFile`（地址文件）、`-Hostname`、`-ConfigPath`。本机实际使用桌面 `Prism/.tunnel` 作为运行目录，日志为 `named-tunnel.stdout.log` 与 `named-tunnel.stderr.log`。

### 自检为什么优先用 curl

本机 PowerShell 校验 HTTPS 时会去查询证书吊销列表（CRL/OCSP），国内网络常常连不上，于是报 `CRYPT_E_REVOCATION_OFFLINE`，把"吊销服务器不可达"误判成"网站不可访问"。脚本因此优先调用系统自带 `curl.exe --ssl-no-revoke` 做健康检查；浏览器不受这个问题影响。

## 二、临时地址（应急备用）

如果域名解析或 Cloudflare 账号出问题，可以临时用随机地址对外：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/cloudflare/start-tunnel.ps1
```

- 地址形如 `https://xxxx.trycloudflare.com`，**每次重启都会变**，只能应急。
- `start-tunnel.ps1` / `stop-tunnel.ps1` / `tunnel-common.ps1` 即此方案，运行目录与固定域名方案相同（`state.json`、`tunnel.*.log`）。
- 需要先在本机网站目录或运行目录放置 Cloudflare 官方、数字签名有效的 `cloudflared.exe`。

## 三、编码约定

脚本以**带 BOM 的 UTF-8** 保存，让 Windows PowerShell 5.1 正确识别中文；`.cmd`、`.vbs` 用纯 ASCII 内容。改动脚本后如需重新保存编码：

```powershell
$p = '项目\tools\cloudflare\start-fixed-tunnel.ps1'
$c = [System.IO.File]::ReadAllText($p, [System.Text.Encoding]::UTF8)
[System.IO.File]::WriteAllText($p, $c, (New-Object System.Text.UTF8Encoding($true)))
```
