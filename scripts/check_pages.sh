#!/usr/bin/env bash
# Render-check web pages against the local dev server.
#
#   bash scripts/check_pages.sh /web/admin/proveedores /web/admin/clientes
#
# Logs in as the seeded super admin, requests each path, and reports the status
# plus any Jinja/500 error surfaced in the body. Non-200 or an error marker in
# the HTML exits non-zero.
set -u

BASE="${BASE:-http://127.0.0.1:8010}"
JAR="$(mktemp)"
trap 'rm -f "$JAR"' EXIT

curl -s -c "$JAR" "$BASE/web/login" -o /dev/null
curl -s -b "$JAR" -c "$JAR" -X POST "$BASE/web/login" \
    --data-urlencode "username=${S360_USER:-AVRC}" \
    --data-urlencode "password=${S360_PASS:-scrap360\$1123}" \
    -o /dev/null

fail=0
for path in "$@"; do
    body="$(curl -s -b "$JAR" -w '\n__STATUS__%{http_code}' "$BASE$path")"
    code="${body##*__STATUS__}"
    html="${body%__STATUS__*}"

    note=""
    case "$html" in
        *"jinja2.exceptions"*|*"TemplateSyntaxError"*|*"UndefinedError"*)
            note=" <-- TEMPLATE ERROR" ;;
        *"Internal Server Error"*)
            note=" <-- 500" ;;
    esac

    if [ "$code" != "200" ] || [ -n "$note" ]; then
        fail=1
        printf '%-58s %s%s\n' "$path" "$code" "$note"
    else
        printf '%-58s %s ok\n' "$path" "$code"
    fi
done

exit "$fail"
