#!/bin/bash
# Устанавливает навыки Claude из git-репозитория. Запускается внутри Ubuntu.
# Аргументы: URL ВЕТКА ПОДПАПКА ИМЯ_РЕПОЗИТОРИЯ (ветка и подпапка могут быть пустыми)
set -e
url="$1"; branch="$2"; sub="$3"; repo="$4"
src=$(mktemp -d)
trap 'rm -rf "$src"' EXIT

if [ -n "$branch" ]; then
    git clone --depth 1 --branch "$branch" "$url" "$src/r" 2>&1
else
    git clone --depth 1 "$url" "$src/r" 2>&1
fi

root="$src/r"
[ -n "$sub" ] && root="$src/r/$sub"
[ -d "$root" ] || { echo "ERR:Папка не найдена в репозитории"; exit 2; }

dest="$HOME/.claude/skills"
mkdir -p "$dest"

install_one() {
    local n d
    n=$(printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_'); d="$2"
    [ -n "$n" ] || n="skill"
    rm -rf "${dest:?}/$n"
    mkdir -p "$dest/$n"
    (cd "$d" && tar cf - --exclude=.git .) | (cd "$dest/$n" && tar xf -)
    echo "SKILL:$n"
}

if [ -f "$root/SKILL.md" ]; then
    n=$(basename "$root")
    [ -z "$sub" ] && n="$repo"
    install_one "$n" "$root"
else
    found=0
    while IFS= read -r f; do
        d=$(dirname "$f")
        install_one "$(basename "$d")" "$d"
        found=1
    done < <(find "$root" -maxdepth 4 -name SKILL.md -not -path "*/node_modules/*" -not -path "*/.git/*")
    [ "$found" = 1 ] || { echo "ERR:В репозитории нет файла SKILL.md — это не навык"; exit 3; }
fi
