"""Repair Spanish orthography in the templates.

The interface is es-MX but the chrome was written without accents while the data
was accented, which made the app look unfinished. This restores the accents and
removes English/jargon that leaked into user-facing strings.

    python -m scripts.fix_accents --check          # report only, exit 1 if dirty
    python -m scripts.fix_accents app/templates    # rewrite in place

Only whole words are replaced, and only outside Jinja expressions/statements, so
variable names, filters and URLs are never touched.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# word -> corrected word. Capitalised and upper-case variants are derived.
WORDS: dict[str, str] = {
    # -ción / -sión
    "operacion": "operación",
    "operaciones": "operaciones",
    "revision": "revisión",
    "administracion": "administración",
    "seleccion": "selección",
    "direccion": "dirección",
    "descripcion": "descripción",
    "denominacion": "denominación",
    "relacion": "relación",
    "conversion": "conversión",
    "conversiones": "conversiones",
    "devolucion": "devolución",
    "devoluciones": "devoluciones",
    "cancelacion": "cancelación",
    "comision": "comisión",
    "comisiones": "comisiones",
    "aprobacion": "aprobación",
    "configuracion": "configuración",
    "informacion": "información",
    "valuacion": "valuación",
    "eliminacion": "eliminación",
    "atencion": "atención",
    "reversion": "reversión",
    "disminucion": "disminución",
    "edicion": "edición",
    "version": "versión",
    "versiones": "versiones",
    "sesion": "sesión",
    # accented nouns
    "catalogo": "catálogo",
    "catalogos": "catálogos",
    "telefono": "teléfono",
    "numero": "número",
    "vinculo": "vínculo",
    "vinculos": "vínculos",
    "metodo": "método",
    "metodos": "métodos",
    "credito": "crédito",
    "codigo": "código",
    "dia": "día",
    "dias": "días",
    "periodo": "período",
    "modulo": "módulo",
    "auditoria": "auditoría",
    "galeria": "galería",
    "camara": "cámara",
    "metaleria": "metalería",
    "deposito": "depósito",
    "depositos": "depósitos",
    "electronico": "electrónico",
    "electronica": "electrónica",
    # adjectives / adverbs
    "automatico": "automático",
    "automaticos": "automáticos",
    "automatica": "automática",
    "automaticas": "automáticas",
    "automaticamente": "automáticamente",
    "historico": "histórico",
    "historicos": "históricos",
    "historica": "histórica",
    "historicas": "históricas",
    "cronologico": "cronológico",
    "cronologicos": "cronológicos",
    "especifico": "específico",
    "especifica": "específica",
    "rapido": "rápido",
    "rapida": "rápida",
    "fisico": "físico",
    "fisicamente": "físicamente",
    "publico": "público",
    "maximo": "máximo",
    "minimo": "mínimo",
    "ultimo": "último",
    "ultimos": "últimos",
    "ultima": "última",
    "ultimas": "últimas",
    "unico": "único",
    "practico": "práctico",
    "util": "útil",
    "vacio": "vacío",
    "vacia": "vacía",
    "tambien": "también",
    "aun": "aún",
    "aqui": "aquí",
    "asi": "así",
    "despues": "después",
    "mas": "más",
    "solo": "sólo",
    "estas": "estás",
    "estes": "estés",
    # verbs (future / preterite)
    "sera": "será",
    "seran": "serán",
    "creara": "creará",
    "llevara": "llevará",
    "impactara": "impactará",
    "registrara": "registrará",
    "registraran": "registrarán",
    "bloqueara": "bloqueará",
    "permitira": "permitirá",
    "quedara": "quedará",
    "afectara": "afectará",
    "aplicara": "aplicará",
    "genero": "generó",
    "aprobo": "aprobó",
    "detecto": "detectó",
    "recalculo": "recalculó",
    "regreso": "regresó",
    "quedo": "quedó",
    "existia": "existía",
    "dejala": "déjala",
    "dejalo": "déjalo",
    "usala": "úsala",
    "usalo": "úsalo",
    "seleccionala": "selecciónala",
    "seleccionalo": "selecciónalo",
}

# Words above that are too risky to auto-replace because they are frequently
# correct without the accent, or collide with code identifiers.
SKIP = {"mas", "solo", "version", "versiones", "sesion", "operaciones",
        "comisiones", "conversiones", "devoluciones", "catalogos"}

PHRASES: list[tuple[str, str]] = [
    ("Log contable", "Contabilidad"),
    ("Total (firmado)", "Total neto"),
    ("Total firmado", "Total neto"),
    ("Se guarda en Firebase", "Formatos JPG o PNG"),
    ("Cuando estes lista", "Cuando termines"),
    ("Cuando estés lista", "Cuando termines"),
    ("por que se modifica", "por qué se modifica"),
    ("Max 8MB", "Máx. 8 MB"),
    ("Max 8 MB", "Máx. 8 MB"),
]

# Jinja expressions, statements, comments, HTML entities and attribute-ish
# fragments we must not rewrite.
PROTECTED = re.compile(
    r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}|&[a-zA-Z]+;|&#\d+;"
    r'|(?:href|src|action|formaction|name|id|for|class|value|type|data-[\w-]+)\s*=\s*"[^"]*"'
    r"|<script\b.*?</script>|<style\b.*?</style>",
    re.DOTALL | re.IGNORECASE,
)


def _variants(word: str, fixed: str) -> list[tuple[str, str]]:
    return [
        (word, fixed),
        (word.capitalize(), fixed.capitalize()),
        (word.upper(), fixed.upper()),
    ]


PATTERNS: list[tuple[re.Pattern[str], str]] = []
for _w, _f in WORDS.items():
    if _w in SKIP:
        continue
    for _src, _dst in _variants(_w, _f):
        PATTERNS.append((re.compile(rf"(?<![\w\-áéíóúñÁÉÍÓÚÑ]){re.escape(_src)}(?![\w\-áéíóúñÁÉÍÓÚÑ])"), _dst))


def fix_text(text: str) -> str:
    for src, dst in PHRASES:
        text = text.replace(src, dst)
    for pattern, dst in PATTERNS:
        text = pattern.sub(dst, text)
    return text


def fix_document(source: str) -> str:
    out: list[str] = []
    last = 0
    for match in PROTECTED.finditer(source):
        out.append(fix_text(source[last:match.start()]))
        out.append(match.group(0))
        last = match.end()
    out.append(fix_text(source[last:]))
    return "".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", default=["app/templates"])
    parser.add_argument("--check", action="store_true", help="report without writing")
    args = parser.parse_args()

    targets: list[Path] = []
    for raw in args.paths or ["app/templates"]:
        path = Path(raw)
        targets.extend(sorted(path.rglob("*.html")) if path.is_dir() else [path])

    changed = 0
    for file in targets:
        original = file.read_text(encoding="utf-8")
        updated = fix_document(original)
        if updated == original:
            continue
        changed += 1
        diff = sum(1 for a, b in zip(original.split(), updated.split()) if a != b)
        print(f"{'would fix' if args.check else 'fixed'} {file} ({diff} words)")
        if not args.check:
            file.write_text(updated, encoding="utf-8")

    print(f"\n{changed} file(s) {'need fixing' if args.check else 'updated'} of {len(targets)}")
    return 1 if (args.check and changed) else 0


if __name__ == "__main__":
    sys.exit(main())
