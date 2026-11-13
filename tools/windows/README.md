# 登录前启动 Prism

使用 Windows 任务计划程序的开机触发器，而不是用户登录触发器。后台任务名为 `Prism-Boot`，运行账号为权限较低的 `LOCAL SERVICE`，无需保存或取消用户的 Windows 登录密码。

`boot_supervisor.py` 使用 Python 标准库管理网站和公网隧道，显式指定 Python、依赖目录、项目目录、隧道配置和凭据路径，避免后台账号使用不同用户目录。启动时网络暂未就绪会继续等待和重连；子进程退出会重启。配置、状态和日志位于 `data/boot/`，不进入 GitHub。

## 安装步骤

1. 由当前用户生成本机 `data/boot/config.json`，并对数据库进行一致性备份。
2. 管理员运行 `install-boot.ps1 -ConfigPath <本机配置绝对路径>`。
3. 安装脚本为服务账号开放项目和 Python 的读取执行权限、网站数据的修改权限，以及指定隧道配置和凭据的读取权限。
4. 先创建临时任务，在真实服务账号下预检依赖、文件读取和数据库完整性；失败时不停止旧网站。
5. 预检通过后停止旧网站及对应隧道，注册开机任务，并立即运行验证。
6. 新任务接管成功后才把两个旧登录启动项移到 `data/boot/backup-*`。其他启动项不处理。

安装结果见 `install-result.json`，运行状态见 `status.json`。开机任务运行在非交互会话中，不依赖用户进入桌面。没有实际重启前，只能验证任务配置和当前非交互运行；最后还需在下一次开机停留密码界面时用手机验收。

## 日常控制

原来的固定域名启动和停止按钮仍然可用：安装成功后通过控制文件通知后台任务，不要求普通用户直接结束服务账号的进程。停止公网入口会保留停止状态，需点启动按钮恢复。本机网站不会跟随停止。

电脑仍需开机、联网且保持唤醒。安装脚本不更改开机密码、电源策略或其他用户的启动项。

## 参考

- [微软开机与登录触发器说明](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasktrigger)
- [Cloudflare 后台运行及显式配置路径说明](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/as-a-service/windows/)
