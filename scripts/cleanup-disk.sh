#!/usr/bin/env bash
# Очистка кэша сборки Docker и журналов systemd, чтобы диск не забивался.
# Тома, запущенные контейнеры и образы работающих сервисов не трогаем.
set -euo pipefail

LOG_TAG="${LOG_TAG:-cdek-disk-cleanup}"
JOURNAL_MAX="${JOURNAL_MAX:-80M}"

log() {
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"
  logger -t "$LOG_TAG" -- "$*" 2>/dev/null || true
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

log "start df=$(df -P / | awk 'NR==2 {print $5 " used, " $4 " avail"}')"

if need_cmd docker; then
  log "docker builder prune"
  docker builder prune -af >/dev/null
  log "docker image prune (dangling)"
  docker image prune -f >/dev/null
else
  log "docker not found, skip"
fi

if need_cmd journalctl; then
  log "journalctl --vacuum-size=${JOURNAL_MAX}"
  journalctl --vacuum-size="$JOURNAL_MAX" >/dev/null
else
  log "journalctl not found, skip"
fi

if need_cmd apt-get; then
  apt-get clean >/dev/null 2>&1 || true
fi

log "done df=$(df -P / | awk 'NR==2 {print $5 " used, " $4 " avail"}')"
