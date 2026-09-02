#!/usr/bin/env bash
# Ставит ежедневный cron и лимит размера journald.
# Запускать с sudo: sudo bash scripts/install-disk-cleanup.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CLEANUP="$ROOT/scripts/cleanup-disk.sh"
CRON_FILE="/etc/cron.d/cdek-disk-cleanup"
JOURNAL_DROPIN_DIR="/etc/systemd/journald.conf.d"
JOURNAL_DROPIN="$JOURNAL_DROPIN_DIR/cdek-size.conf"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Нужен root: sudo bash $0" >&2
  exit 1
fi

chmod +x "$CLEANUP"

mkdir -p "$JOURNAL_DROPIN_DIR"
cat >"$JOURNAL_DROPIN" <<'EOF'
[Journal]
SystemMaxUse=100M
RuntimeMaxUse=50M
MaxRetentionSec=7day
EOF

if command -v systemctl >/dev/null 2>&1; then
  systemctl restart systemd-journald
fi

cat >"$CRON_FILE" <<EOF
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
# Ежедневно в 04:00 UTC: кэш Docker + журналы
0 4 * * * root $CLEANUP
EOF
chmod 644 "$CRON_FILE"

echo "Установлено:"
echo "  journald: $JOURNAL_DROPIN"
echo "  cron:     $CRON_FILE"
echo "  script:   $CLEANUP"
echo "Прогон сейчас..."
"$CLEANUP"
echo "Готово."
