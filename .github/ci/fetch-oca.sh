#!/usr/bin/env bash
# Descarga los repos OCA 20.0 en los mismos commits que los submódulos del repo privado.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
fetch() {  # nombre sha
    [ -d "oca/$1/.git" ] && return 0
    mkdir -p "oca/$1"
    git -C "oca/$1" init -q
    git -C "oca/$1" fetch -q --depth 1 "https://github.com/OCA/$1.git" "$2"
    git -C "oca/$1" checkout -q FETCH_HEAD
}
fetch server-ux fbcaea8fb59728fd780741465e7afeb293b875ab
fetch reporting-engine 87527554c82d8c846c801d5969bca3afb04c3bae
