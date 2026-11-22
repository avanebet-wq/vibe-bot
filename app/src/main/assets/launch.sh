#!/bin/bash
# Стартовый скрипт терминала: открывает папку проектов и запускает Claude Code.
export PATH="$HOME/.local/bin:$PATH"
mkdir -p ~/work
cd ~/work || cd ~

if command -v claude >/dev/null 2>&1; then
    claude
    echo
    echo "Claude Code завершил работу."
    echo "  claude  — запустить снова,   exit  — закрыть сессию"
else
    echo "Claude Code не найден. Открой меню ⋮ → «Переустановить»."
fi
exec bash -l
