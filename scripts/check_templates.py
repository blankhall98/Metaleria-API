"""Guarda de integridad de las plantillas.

    python -m scripts.check_templates

Hace dos cosas que ninguna otra herramienta del repo hacía:

1. **Compila** las 51 plantillas. Un error de sintaxis de Jinja solo se
   descubría al abrir la pantalla, y `check_pages.sh` únicamente cubre las
   rutas que se le pasan a mano.

2. **Compara contra HEAD las expresiones Jinja que viven dentro de un
   atributo `class`.** Una edición masiva de nombres de clase no debe alterar
   ni una sola: reconstruir `class="..."` partiendo por espacios destruye
   `{% if x %}foo{% endif %}`, y si las llaves quedan balanceadas el resultado
   compila igual y el daño pasa desapercibido. Así se perdió una vez
   `{% elif (diferencia_provisional or 0) < 0 %}`, que quedó como `{% elif < %}`.

La comparación es **contra HEAD**, así que un cambio deliberado a una de esas
expresiones aparece listado hasta que lo commiteas. Eso es a propósito: el
punto es que ninguna se altere sin que alguien lo mire y diga que sí.

Sale distinto de cero si algo falla, para poder frenar un cambio.
"""

from __future__ import annotations

import collections
import pathlib
import re
import subprocess
import sys

from jinja2 import Environment, FileSystemLoader

RAIZ = pathlib.Path("app/templates")
CLASE = re.compile(r'class="([^"]*)"')
JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)


def compilar() -> list[str]:
    env = Environment(loader=FileSystemLoader(str(RAIZ)))
    # Los mismos filtros que registra app/web/template_utils.py: sin ellos,
    # cualquier plantilla que los use fallaría aquí con un falso positivo.
    from app.core.datetime_utils import format_date_iso, format_date_local, format_datetime_local
    from app.web.template_utils import format_precio

    env.filters["datetime_local"] = format_datetime_local
    env.filters["date_local"] = format_date_local
    env.filters["date_iso"] = format_date_iso
    env.filters["precio"] = format_precio
    fallos = []
    for f in sorted(RAIZ.rglob("*.html")):
        rel = f.relative_to(RAIZ).as_posix()
        try:
            env.get_template(rel)
        except Exception as e:  # noqa: BLE001 - cualquier fallo debe reportarse
            fallos.append(f"{rel}:{getattr(e, 'lineno', '?')}  {type(e).__name__}: {e}")
    return fallos


def _expresiones(texto: str) -> collections.Counter:
    out: collections.Counter = collections.Counter()
    for m in CLASE.finditer(texto):
        for e in JINJA.findall(m.group(1)):
            out[" ".join(e.split())] += 1
    return out


def _version_en_head(rel: str) -> str | None:
    r = subprocess.run(
        ["git", "show", f"HEAD:app/templates/{rel}"],
        capture_output=True,
    )
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", errors="replace")


def comparar_con_head() -> list[str]:
    fallos = []
    for f in sorted(RAIZ.rglob("*.html")):
        rel = f.relative_to(RAIZ).as_posix()
        viejo = _version_en_head(rel)
        if viejo is None:  # plantilla nueva, no hay con qué comparar
            continue
        antes = _expresiones(viejo)
        ahora = _expresiones(f.read_text(encoding="utf-8"))
        if antes == ahora:
            continue
        detalle = [f"{rel}"]
        for e, n in (antes - ahora).items():
            detalle.append(f"     perdida x{n}: {e[:100]}")
        for e, n in (ahora - antes).items():
            detalle.append(f"     nueva   x{n}: {e[:100]}")
        fallos.append("\n".join(detalle))
    return fallos


def main() -> int:
    total = len(list(RAIZ.rglob("*.html")))
    sintaxis = compilar()
    print(f"Compiladas {total} plantillas.")
    for f in sintaxis:
        print("  " + f)

    jinja = comparar_con_head()
    if jinja:
        print("\nExpresiones Jinja alteradas dentro de un atributo class:")
        for f in jinja:
            print("  " + f)

    if sintaxis or jinja:
        print(f"\n{len(sintaxis) + len(jinja)} problema(s).")
        return 1
    print("Sintaxis correcta y sin Jinja alterado dentro de class=.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
