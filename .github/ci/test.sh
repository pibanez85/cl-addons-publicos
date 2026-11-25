#!/usr/bin/env bash
# Uso: scripts/test.sh <modulo>[,<modulo>...]
# Crea una base nueva, instala los módulos sin demo, corre sus pruebas y
# devuelve código != 0 si alguna prueba falla o hay errores en el log.
set -uo pipefail

if [ $# -ne 1 ] || [ -z "$1" ]; then
    echo "Uso: $0 <modulo>[,<modulo>...]" >&2
    exit 2
fi
MODULES="$1"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE=(docker compose -f "$ROOT/.github/ci/compose.yml")
DB="test_$(date +%Y%m%d%H%M%S)_$$"
LOG="$(mktemp)"

cleanup() {
    "${COMPOSE[@]}" exec -T db dropdb -U odoo --if-exists "$DB" >/dev/null 2>&1
    rm -f "$LOG"
}
trap cleanup EXIT

"${COMPOSE[@]}" up -d --wait db >/dev/null || { echo "No se pudo iniciar la base de datos" >&2; exit 1; }
"${COMPOSE[@]}" exec -T db createdb -U odoo "$DB" || exit 1

TAGS=$(echo "$MODULES" | sed 's#\([^,]\+\)#/\1#g')

"${COMPOSE[@]}" run --rm -T --entrypoint odoo odoo server \
    -c /etc/odoo/odoo.conf -d "$DB" -i "$MODULES" \
    --without-demo=all --test-enable --test-tags "$TAGS" \
    --stop-after-init --log-level=info 2>&1 | tee "$LOG"
RC=${PIPESTATUS[0]}

# Odoo puede salir con 0 aunque fallen pruebas: revisar el log.
FAILED=0
[ "$RC" -ne 0 ] && FAILED=1
grep -qE ' (ERROR|CRITICAL) ' "$LOG" && FAILED=1
grep -qE '[1-9][0-9]* failed|[0-9]+ failed, [1-9][0-9]* error' "$LOG" && FAILED=1
grep -qE '[0-9]+ failed, [0-9]+ error' "$LOG" || FAILED=1   # sin resumen = no se ejecutaron pruebas
# Cada módulo pedido debe haber ejecutado al menos una prueba
# (línea "odoo.tests.stats: <modulo>: N tests"); evita falsos verdes por
# módulos inexistentes o sin pruebas.
for m in ${MODULES//,/ }; do
    N=$(grep -E "odoo\.tests\.stats: $m: [0-9]+ tests" "$LOG" | tail -1 | sed -E "s/.*: $m: ([0-9]+) tests.*/\1/")
    if [ -z "$N" ] || [ "$N" -eq 0 ]; then
        echo "== ERROR: el modulo '$m' no existe, no se instalo o no ejecuto ninguna prueba" >&2
        FAILED=1
    fi
done
grep -qE 'of 0 tests' "$LOG" && { echo "== ERROR: se ejecutaron 0 pruebas" >&2; FAILED=1; }

SUMMARY=$(grep -E '[0-9]+ failed, [0-9]+ error' "$LOG" | tail -1 | sed 's/^.*odoo.tests.result: //')
echo "== Resumen: ${SUMMARY:-sin resumen de pruebas} (rc odoo=$RC)"
if [ "$FAILED" -ne 0 ]; then echo "== PRUEBAS FALLIDAS" >&2; exit 1; fi
exit 0
