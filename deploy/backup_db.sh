#!/usr/bin/env bash
# =============================================================================
#  每日数据库备份
#
#  为什么用 sqlite3 的 .backup 而不是直接 cp：
#      服务在跑的时候直接复制文件，可能正好复制到"写了一半"的状态，备份是坏的。
#      .backup 走 SQLite 的在线备份接口，拿到的一定是一个自洽的快照。
#
#  保留策略：14 天。超过的自动删掉，不会把硬盘撑满。
#
#  手工跑一次：      bash /opt/kaoyan/deploy/backup_db.sh
#  看备份：          ls -lh /opt/kaoyan/backups/
#  恢复（服务先停）： gunzip -c /opt/kaoyan/backups/app-2026-10-05.db.gz > /opt/kaoyan/data/app.db
#                    systemctl start kaoyan
# =============================================================================
set -euo pipefail

APP_DIR="/opt/kaoyan"
DB="$APP_DIR/data/app.db"
DEST="$APP_DIR/backups"
KEEP_DAYS=14

[[ -f "$DB" ]] || { echo "[backup] 找不到数据库：$DB" >&2; exit 1; }
mkdir -p "$DEST"

STAMP="$(date +%Y-%m-%d)"
TMP="$DEST/.app-$STAMP.db"
OUT="$DEST/app-$STAMP.db.gz"

# 在线备份 → 压缩（-9 压得小一点，单人库本来就不大）
sqlite3 "$DB" ".backup '$TMP'"
gzip -9 -c "$TMP" > "$OUT"
rm -f "$TMP"

# 备份也要顺手验一下，别攒一堆坏文件自己还不知道
TMPV="$DEST/.verify.db"
gunzip -c "$OUT" > "$TMPV"
if ! sqlite3 "$TMPV" "PRAGMA integrity_check;" | grep -q '^ok$'; then
  rm -f "$TMPV"
  echo "[backup] 备份校验失败，文件可能已损坏：$OUT" >&2
  exit 1
fi
rm -f "$TMPV"

# 清理过期
DELETED="$(find "$DEST" -maxdepth 1 -name 'app-*.db.gz' -mtime "+$KEEP_DAYS" -print -delete | wc -l)"

echo "[backup] $(date '+%F %T')  已备份 → $OUT  ($(du -h "$OUT" | cut -f1))，清理过期 $DELETED 个"
