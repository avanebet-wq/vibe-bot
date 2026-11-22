#!/bin/bash
# Выполняется ВНУТРИ Ubuntu (через proot) при нажатии «Установить всё».
set -e
export DEBIAN_FRONTEND=noninteractive
export LC_ALL=C.UTF-8
export PATH="/root/.local/bin:$PATH"

echo "==> Обновляю список пакетов"
apt-get update -q

echo "==> Ставлю базовые инструменты"
apt-get install -y -q --no-install-recommends \
    ca-certificates curl git nano less ripgrep jq procps openssh-client \
    unzip zip xz-utils gnupg python3 file findutils

echo "==> Ставлю Claude Code"
ok=0
for i in 1 2 3; do
    if curl -fsSL https://claude.ai/install.sh | bash; then ok=1; break; fi
    echo "Попытка $i не удалась, повторяю через 5 секунд…"
    sleep 5
done
[ "$ok" = 1 ] || { echo "Не удалось установить Claude Code"; exit 1; }

mkdir -p /root/work
echo "==> Готово"
