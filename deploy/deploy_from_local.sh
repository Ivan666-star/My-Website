#!/usr/bin/env bash
# =============================================================================
#  一键部署：从你电脑推到服务器
#
#  在**你电脑上**（Git Bash）执行，不是在服务器上执行。
#
#  用法：
#     bash deploy/deploy_from_local.sh --host 1.2.3.4 --domain kaoyan.example.com
#     bash deploy/deploy_from_local.sh --host 1.2.3.4 --domain kaoyan.example.com --skip-db
#
#  它会做四件事：
#     1) 把代码打包上传（**不含 .env 和 data/**，避免泄露密钥、覆盖线上数据）
#     2) 触发服务器上的环境初始化
#     3) 把本机的学习数据迁上去（SQLite 在线备份，保证快照自洽）
#     4) 重启服务并做一次自检
#
#  前置条件（只做一次）：
#     1) 域名解析：在你的域名管理后台加一条 A 记录，记录值 = 服务器公网 IP。
#        解析生效前 HTTPS 证书签不下来 —— 脚本会先预检并明确提示。
#     2) 把你电脑的公钥加到服务器上，免密登录。这样后面所有操作都不用输密码：
#         ssh-keygen -t ed25519 -f ~/.ssh/kaoyan_deploy -N ""
#         # 把 ~/.ssh/kaoyan_deploy.pub 的内容贴到服务器 ~/.ssh/authorized_keys
#         ssh -i ~/.ssh/kaoyan_deploy root@服务器IP 'echo ok'
# =============================================================================
set -euo pipefail

HOST=""
DOMAIN=""
EMAIL=""
SSH_USER="root"
SSH_PORT="22"
SSH_KEY=""
APP_DIR="/opt/kaoyan"
SKIP_DB=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)     HOST="${2:-}"; shift 2 ;;
    --domain)   DOMAIN="${2:-}"; shift 2 ;;
    --email)    EMAIL="${2:-}"; shift 2 ;;
    --user)     SSH_USER="${2:-}"; shift 2 ;;
    --port)     SSH_PORT="${2:-}"; shift 2 ;;
    --key)      SSH_KEY="${2:-}"; shift 2 ;;
    --app-dir)  APP_DIR="${2:-}"; shift 2 ;;
    --skip-db)  SKIP_DB=1; shift ;;
    -h|--help)  awk 'NR>1{ if (/^#/) print; else exit }' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

[[ -n "$HOST" ]] || { echo "必须给 --host 服务器IP" >&2; exit 2; }

log()  { printf '\033[36m[deploy]\033[0m %s\n' "$*"; }
die()  { printf '\033[31m[失败]\033[0m %s\n' "$*" >&2; exit 1; }

# 回到项目根（脚本在 deploy/ 下）
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
[[ -f "app/main.py" ]] || die "没找到 app/main.py，请在项目根目录执行"

SSH_OPTS=(-p "$SSH_PORT" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15)
[[ -n "$SSH_KEY" ]] && SSH_OPTS+=(-i "$SSH_KEY")
TARGET="$SSH_USER@$HOST"

rsh() { ssh "${SSH_OPTS[@]}" "$TARGET" "$@"; }

log "先确认能连上 $TARGET …"
rsh 'echo connected' >/dev/null || die "连不上服务器。检查 IP / 端口 / 密钥是否配好"
log "连接正常"

# -----------------------------------------------------------------------------
# 0. 预检：域名解析
#    这是新手最容易漏的一步，也是唯一「漏了会让人完全看不懂报错」的一步：
#    解析没生效 → Caddy 签不下 HTTPS 证书 → 浏览器报证书错误，
#    而背后真正的原因（DNS）在报错信息里根本看不出来。
#    在这里拦住，比让人去猜证书问题划算得多。
# -----------------------------------------------------------------------------
if [[ -n "$DOMAIN" ]]; then
  log "预检：$DOMAIN 是否已解析到 $HOST …"
  RESOLVED="$(rsh "getent hosts $DOMAIN 2>/dev/null | awk '{print \$1}' | head -1" || true)"
  RESOLVED="${RESOLVED//[$'\r\n\t ']/}"   # 去掉可能带回车/空白

  if [[ -z "$RESOLVED" ]]; then
    log ""
    log "⚠️  服务器上解析不出 $DOMAIN —— 域名还没有指向这台服务器"
    log "    去你的域名管理后台加一条解析记录："
    log "        记录类型  A"
    log "        主机记录  @          （@ 表示根域名；想用 www 就填 www）"
    log "        记录值    $HOST"
    log "        TTL       600"
    log "    保存后一般几分钟生效，最长几小时。生效后重新跑本脚本即可（脚本可重复执行）。"
    log ""
    if [[ -t 0 ]]; then
      read -r -p "    仍然继续吗？这一轮 HTTPS 证书基本一定失败 [y/N] " _ans
      [[ "$_ans" == "y" || "$_ans" == "Y" ]] || die "已停止：等解析生效后再来。"
    else
      log "    （非交互执行，继续；请自行确认解析已生效）"
    fi
  elif [[ "$RESOLVED" != "$HOST" ]]; then
    log "⚠️  $DOMAIN 解析到 $RESOLVED，但这台服务器是 $HOST"
    log "    如果你用了 CDN / 反向代理（例如 Cloudflare 开着代理），这是正常的；"
    log "    否则说明解析记录填错了 IP。"
    if [[ -t 0 ]]; then
      read -r -p "    继续吗？[y/N] " _ans
      [[ "$_ans" == "y" || "$_ans" == "Y" ]] || die "已停止：请先核对解析记录。"
    fi
  else
    log "解析正确：$DOMAIN -> $HOST"
  fi
fi

# -----------------------------------------------------------------------------
# 1. 打包上传（刻意不含 .env 和 data/）
# -----------------------------------------------------------------------------
# 暂存目录刻意放在项目内而不是 /tmp：
# Git Bash 的 /tmp 和 Windows 原生程序看到的路径不是一回事，
# scp 走 MSYS 路径转换时容易踩坑。放项目里最稳，跑完自动删掉。
STAGE="$ROOT/.deploy_stage"
rm -rf "$STAGE"
mkdir -p "$STAGE"
trap 'rm -rf "$STAGE"' EXIT

log "打包代码…"
mkdir -p "$STAGE/pkg"
for item in app web seeds tools deploy requirements.txt README.md; do
  [[ -e "$item" ]] && cp -r "$item" "$STAGE/pkg/"
done
# 清掉本机缓存和测试数据，别带上去
find "$STAGE/pkg" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE/pkg" -name '*.pyc' -delete 2>/dev/null || true
rm -rf "$STAGE/pkg/data" 2>/dev/null || true

tar -czf "$STAGE/pkg.tgz" -C "$STAGE" pkg
log "包大小：$(du -h "$STAGE/pkg.tgz" | cut -f1)"

rsh "mkdir -p '$APP_DIR'"
log "上传代码…"
scp "${SSH_OPTS[@]}" -q "$STAGE/pkg.tgz" "$TARGET:/tmp/kaoyan_pkg.tgz"
rsh "tar -xzf /tmp/kaoyan_pkg.tgz -C '$APP_DIR' --strip-components=1 && rm -f /tmp/kaoyan_pkg.tgz"
log "代码已就位：$APP_DIR"

# -----------------------------------------------------------------------------
# 2. 迁移数据（用 SQLite 在线备份，拿到自洽快照，不受本机服务在跑的影响）
# -----------------------------------------------------------------------------
if [[ "$SKIP_DB" -eq 0 ]]; then
  if [[ -f "data/app.db" ]]; then
    log "导出本机数据库快照…"
    SNAP="$STAGE/app.db"
    if command -v sqlite3 >/dev/null 2>&1; then
      sqlite3 "data/app.db" ".backup '$SNAP'"
    else
      # 没有 sqlite3 命令行就退回用 Python（项目本来就依赖 Python）
      python - "$ROOT" "$SNAP" <<'PY'
import sqlite3, sys, pathlib
src = pathlib.Path(sys.argv[1]) / "data" / "app.db"
dst = sys.argv[2]
s = sqlite3.connect(str(src)); d = sqlite3.connect(dst)
with d: s.backup(d)
d.close(); s.close()
print("snapshot ok")
PY
    fi
    [[ -s "$SNAP" ]] || die "数据库快照导出失败"
    log "快照大小：$(du -h "$SNAP" | cut -f1)，上传…"
    rsh "systemctl stop kaoyan 2>/dev/null || true"
    scp "${SSH_OPTS[@]}" -q "$SNAP" "$TARGET:$APP_DIR/data/app.db"
    rsh "chown kaoyan:kaoyan '$APP_DIR/data/app.db' 2>/dev/null || true; \
         sqlite3 '$APP_DIR/data/app.db' 'PRAGMA integrity_check;'"
    log "数据已迁移并校验"
  else
    log "本机没有 data/app.db，跳过数据迁移（这是全新开始）"
  fi
fi

# -----------------------------------------------------------------------------
# 3. 服务器初始化 + 依赖安装
# -----------------------------------------------------------------------------
log "初始化服务器环境（幂等，可重复跑）…"
PROXY_ARGS=""
[[ -n "$DOMAIN" ]] && PROXY_ARGS="--domain $DOMAIN"
[[ -n "$EMAIL"  ]] && PROXY_ARGS="$PROXY_ARGS --email $EMAIL"
rsh "bash '$APP_DIR/deploy/setup_server.sh' $PROXY_ARGS"

# -----------------------------------------------------------------------------
# 4. 配置检查 + 重启 + 自检
# -----------------------------------------------------------------------------
if ! rsh "test -f '$APP_DIR/.env'"; then
  log "服务器上还没有 .env"
  log "把本机配置传过去（AI_API_KEY 必须有；登录初始化码可留空，系统会自动生成）"
  if [[ -f ".env" ]]; then
    scp "${SSH_OPTS[@]}" -q ".env" "$TARGET:$APP_DIR/.env"
    rsh "chown kaoyan:kaoyan '$APP_DIR/.env'; chmod 600 '$APP_DIR/.env'"
    log "已从本机复制 .env 到服务器"
  fi
fi

log "启动服务…"
rsh "systemctl daemon-reload; systemctl enable kaoyan >/dev/null 2>&1 || true; systemctl restart kaoyan"
sleep 3
rsh "systemctl is-active kaoyan" | sed 's/^/  服务状态: /'

log "本机自检（应用层，不经代理）…"
rsh "curl -s -o /dev/null -w '  /api/health -> %{http_code}\n' http://127.0.0.1:8848/api/health"
rsh "curl -s -o /dev/null -w '  / -> %{http_code} (303/401 说明登录门生效；200 说明还没设防)\n' http://127.0.0.1:8848/"

# 登录门状态：有没有账号 / 初始化码是多少。
# 这是「上线前唯一必须亲眼确认」的一件事，脚本直接给结论，别让人去猜。
log "登录门状态…"
USERS_N="$(rsh "sqlite3 '$APP_DIR/data/app.db' 'select count(*) from users;' 2>/dev/null" || true)"
USERS_N="${USERS_N//[$'\r\n\t ']/}"
if [[ -z "$USERS_N" || "$USERS_N" == "0" ]]; then
  SETUP="$(rsh "cat '$APP_DIR/data/setup_code.txt' 2>/dev/null" || true)"
  SETUP="${SETUP//[$'\r\n\t ']/}"
  log "还没有账号 —— 第一次打开网站会要你输初始化码来创建管理员账号"
  if [[ -n "$SETUP" ]]; then
    log "  初始化码：$SETUP"
  else
    log "  （没读到初始化码：ssh 上去看 journalctl -u kaoyan 的开头几行）"
  fi
  log "⚠️  建好账号之前，先别把网址公开出去"
else
  log "已有 $USERS_N 个账号，登录门生效中（每人数据互相独立）"
fi

if [[ -n "$DOMAIN" ]]; then
  log "外网自检（走 HTTPS，证书首次签发可能要等十几秒）…"
  rsh "sleep 8; curl -s -o /dev/null -w '  https://$DOMAIN/ -> %{http_code}\n' https://$DOMAIN/" || true
fi

cat <<EOF

============================================================
  部署完成
============================================================
  地址        : ${DOMAIN:+https://$DOMAIN}${DOMAIN:-（未设域名，仅本机 127.0.0.1:8848）}
  应用目录    : $APP_DIR
  看日志      : ssh $TARGET 'journalctl -u kaoyan -n 100 --no-pager'
  重启服务    : ssh $TARGET 'systemctl restart kaoyan'
  第一个账号  : 打开网址 → 用初始化码创建管理员（本机直连访问免码）
  加人 / 减人 : 设置 → 用户管理
============================================================
EOF
