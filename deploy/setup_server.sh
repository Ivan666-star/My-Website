#!/usr/bin/env bash
# =============================================================================
#  AI 考研英语一训练系统 · 服务器初始化
#
#  设计原则：**幂等**。跑一次是这样，跑十次还是这样，不会重复装、不会覆盖已有配置。
#  所以中途失败了，改完问题直接重跑就行，不用担心跑坏。
#
#  用法：
#     bash setup_server.sh --domain kaoyan.example.com --email you@example.com
#     bash setup_server.sh --domain kaoyan.example.com           # 不填邮箱也能跑
#     bash setup_server.sh --no-proxy                            # 只装应用层，不装 Caddy
#
#  支持：Ubuntu 20.04+ / Debian 11+（apt）；CentOS / Rocky / Alma（dnf/yum）
# =============================================================================
set -euo pipefail

APP_USER="kaoyan"
APP_DIR="/opt/kaoyan"
LOG_DIR="/var/log/kaoyan"
PY_VER=""

DOMAIN=""
EMAIL=""
WITH_PROXY="caddy"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain)   DOMAIN="${2:-}"; shift 2 ;;
    --email)    EMAIL="${2:-}";  shift 2 ;;
    --no-proxy) WITH_PROXY="none"; shift ;;
    -h|--help)  sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

log()  { printf '\033[36m[setup]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[注意]\033[0m %s\n' "$*"; }
die()  { printf '\033[31m[失败]\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "请用 root 运行：sudo bash $0 ..."

# -----------------------------------------------------------------------------
# 1. 识别发行版
# -----------------------------------------------------------------------------
if command -v apt-get >/dev/null 2>&1; then
  PKG="apt"
elif command -v dnf >/dev/null 2>&1; then
  PKG="dnf"
elif command -v yum >/dev/null 2>&1; then
  PKG="yum"
else
  die "没识别出包管理器（不是 apt / dnf / yum），这台机器的系统暂不支持"
fi
log "包管理器：$PKG"

# -----------------------------------------------------------------------------
# 2. 装基础依赖
# -----------------------------------------------------------------------------
log "安装基础依赖（Python / 工具链）…"
if [[ "$PKG" == "apt" ]]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq python3 python3-venv python3-pip curl ca-certificates rsync sqlite3 tzdata
else
  $PKG install -y python3 python3-pip curl ca-certificates rsync sqlite tzdata
  # 部分发行版需要单独装 venv
  $PKG install -y python3-virtualenv 2>/dev/null || true
fi
PY_VER="$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
log "Python 版本：$PY_VER"
python3 -c 'import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)' \
  || die "Python 版本过低（需要 3.9+，建议 3.11+）：当前 $PY_VER"

# -----------------------------------------------------------------------------
# 3. 建运行账号（不给登录 shell，最小权限）
# -----------------------------------------------------------------------------
if ! id -u "$APP_USER" >/dev/null 2>&1; then
  log "创建运行账号 $APP_USER…"
  useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER" 2>/dev/null \
    || useradd --system --create-home --shell /sbin/nologin "$APP_USER"
else
  log "运行账号 $APP_USER 已存在，跳过"
fi

# -----------------------------------------------------------------------------
# 4. 建目录
# -----------------------------------------------------------------------------
log "准备目录 $APP_DIR / $LOG_DIR …"
mkdir -p "$APP_DIR/data" "$APP_DIR/backups" "$LOG_DIR"
chown -R "$APP_USER:$APP_USER" "$APP_DIR" "$LOG_DIR"
chmod 750 "$APP_DIR" "$LOG_DIR"

# -----------------------------------------------------------------------------
# 5. Python 虚拟环境 + 依赖
# -----------------------------------------------------------------------------
if [[ -f "$APP_DIR/requirements.txt" ]]; then
  if [[ ! -x "$APP_DIR/venv/bin/python" ]]; then
    log "创建虚拟环境…"
    sudo -u "$APP_USER" python3 -m venv "$APP_DIR/venv"
  else
    log "虚拟环境已存在，跳过创建"
  fi
  log "安装 Python 依赖…"
  sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -q --upgrade pip
  sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
  log "依赖安装完成"
else
  warn "还没上传代码（找不到 $APP_DIR/requirements.txt）。"
  warn "先把代码传上去，然后再跑一次本脚本即可补齐依赖。"
fi

# -----------------------------------------------------------------------------
# 6. systemd 开机自启
# -----------------------------------------------------------------------------
if [[ -f "$APP_DIR/deploy/kaoyan.service" ]]; then
  log "安装 systemd 服务…"
  cp "$APP_DIR/deploy/kaoyan.service" /etc/systemd/system/kaoyan.service
elif [[ -f "$(dirname "$0")/kaoyan.service" ]]; then
  cp "$(dirname "$0")/kaoyan.service" /etc/systemd/system/kaoyan.service
else
  warn "找不到 kaoyan.service，跳过（代码上传后再跑一次）"
fi

systemctl daemon-reload
if [[ -f /etc/systemd/system/kaoyan.service ]]; then
  systemctl enable kaoyan >/dev/null 2>&1 || true
fi

# -----------------------------------------------------------------------------
# 7. 反向代理 + 自动 HTTPS
# -----------------------------------------------------------------------------
if [[ "$WITH_PROXY" == "caddy" ]]; then
  if ! command -v caddy >/dev/null 2>&1; then
    log "安装 Caddy（自动申请并续期 HTTPS 证书）…"
    if [[ "$PKG" == "apt" ]]; then
      apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https
      curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
      curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        > /etc/apt/sources.list.d/caddy-stable.list
      apt-get update -qq
      apt-get install -y -qq caddy
    else
      $PKG install -y 'dnf-command(copr)' 2>/dev/null || true
      $PKG copr enable -y @caddy/caddy 2>/dev/null || true
      $PKG install -y caddy
    fi
  else
    log "Caddy 已安装，跳过"
  fi

  if [[ -n "$DOMAIN" ]]; then
    log "写入 Caddyfile（域名：$DOMAIN）…"
    if [[ -n "$EMAIL" ]]; then
      GLOBAL_TLS="{
	email $EMAIL
}"
    else
      GLOBAL_TLS=""
    fi
    cat > /etc/caddy/Caddyfile <<EOF
$GLOBAL_TLS

$DOMAIN {
	encode gzip
	reverse_proxy 127.0.0.1:8848
}
EOF
    log "Caddyfile 内容："
    sed 's/^/    /' /etc/caddy/Caddyfile
    systemctl enable caddy >/dev/null 2>&1 || true
    systemctl restart caddy
    log "Caddy 已启动"
  else
    warn "没给 --domain，Caddy 装好了但没配站点。域名解析生效后重跑："
    warn "  bash $0 --domain 你的域名 --email 你的邮箱"
  fi
fi

# -----------------------------------------------------------------------------
# 8. 防火墙（有 ufw / firewalld 才管，没有就跳过）
# -----------------------------------------------------------------------------
if command -v ufw >/dev/null 2>&1; then
  log "配置 ufw：只放行 22 / 80 / 443 …"
  ufw allow 22/tcp   >/dev/null 2>&1 || true
  ufw allow 80/tcp   >/dev/null 2>&1 || true
  ufw allow 443/tcp  >/dev/null 2>&1 || true
  warn "ufw 未自动 enable（怕把你的 SSH 挡在外面）。确认 22 已放行后再执行：ufw enable"
elif command -v firewall-cmd >/dev/null 2>&1; then
  log "配置 firewalld：放行 22 / 80 / 443 …"
  firewall-cmd --permanent --add-service=ssh   >/dev/null 2>&1 || true
  firewall-cmd --permanent --add-service=http  >/dev/null 2>&1 || true
  firewall-cmd --permanent --add-service=https >/dev/null 2>&1 || true
  firewall-cmd --reload >/dev/null 2>&1 || true
else
  warn "没检测到 ufw / firewalld —— 请自行确认云厂商控制台的安全组只开了 22 / 80 / 443。"
fi

# -----------------------------------------------------------------------------
# 9. 汇总
# -----------------------------------------------------------------------------
cat <<EOF

============================================================
  初始化完成
============================================================
  运行账号     : $APP_USER
  应用目录     : $APP_DIR
  日志目录     : $LOG_DIR
  备份目录     : $APP_DIR/backups   （保留 14 天）
  Python       : $PY_VER
  反向代理     : $WITH_PROXY${DOMAIN:+（域名 $DOMAIN）}

  接下来：
    1) 上传代码：  scp -r app web seeds requirements.txt root@本机IP:$APP_DIR/
    2) 写配置：    cp $APP_DIR/deploy/env.production.example $APP_DIR/.env
                   （务必填 AI_API_KEY；登录初始化码可留空，系统会自动生成）
    3) 重启服务：  systemctl restart kaoyan
    4) 看日志：    journalctl -u kaoyan -f
    5) 装备份：    cp $APP_DIR/deploy/kaoyan-backup.{service,timer} /etc/systemd/system/
                   systemctl enable --now kaoyan-backup.timer

  自检命令：
    systemctl status kaoyan --no-pager
    curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8848/api/health
============================================================
EOF
