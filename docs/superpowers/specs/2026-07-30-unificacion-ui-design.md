# Unificación de la interfaz — un solo vocabulario, pantalla por pantalla

Fecha: 2026-07-30 · Estado: fases 1 y 2 parciales desplegadas; 3 y 4 pendientes

## Avance

| | |
|---|---:|
| Usos de vocabulario bespoke | 454 → **275** |
| Plantillas totalmente migradas | 4 de 11 |
| Reglas mecánicas verificadas | 10 → **14** |
| Vistas auditadas sin incumplimientos | **84** |

**Hecho:** macros `card`/`subpanel`/`defs`/`def`/`anchor_nav`; `note-meta-*`
(105 usos) → `.s-defs`; pestañas falsas → `.s-anchor-nav`; `notes-kpi-card` →
`stat()`; el bloque `<style>` de corte de caja (136 líneas, ~40 hexadecimales)
→ `scrap360.css` sobre tokens; coloreado de filas → guarda lateral; 10
instrucciones de "arrastra con el mouse" borradas del marcado (estaban ocultas
por CSS, no eliminadas); 6 anchos mínimos forzados de tabla retirados; 4 reglas
nuevas en `check_ui.js`.

**Pendiente:** `note_detail` (136), `corte_caja` (62), `note_edit` (39),
`note_evidencias` (25), `worker/notes_form` (9); la pasada de columnas de la
fase 3; y el retiro de las familias del legado en la fase 4.

## El problema

El sistema de diseño existe, está documentado en `docs/DESIGN_SYSTEM.md` y está
mayormente adoptado: `s-card-title` (75 usos), `s-card-sub` (88), `s-def*` (102).
Las 42 pantallas pasan las 84 verificaciones mecánicas de `scripts/check_ui.js`.

Lo que rompe la unidad no es la falta de estilo: es que **existe un vocabulario
privado en paralelo**, con 142 clases y 454 usos, concentrado en las pantallas de
más tráfico y sostenido por `style.css` — la hoja que el propio sistema declara
retirada.

| Plantilla | Usos bespoke |
|---|---:|
| `admin/note_detail.html` | 224 |
| `admin/note_edit.html` | 69 |
| `admin/corte_caja.html` | 62 |
| `note_evidencias.html` | 25 |
| `admin/cuenta_detail.html` | 19 |
| `admin/partner_record.html` | 18 |
| `admin/comisionario_record.html` | 15 |
| `admin/comisionario_nota_detail.html` | 9 |
| `worker/notes_form.html` | 9 |
| `admin/notes_list.html` | 3 |
| `admin/contabilidad_list.html` | 1 |

La fuga está probada: `note-meta-grid` / `note-meta-item` / `note-meta-label`
(105 usos) se usa en pantallas de proveedores, comisionarios y cuentas, que no
tienen nada que ver con notas — mientras `.s-defs` / `.s-def__label` /
`.s-def__value` ya existe en el sistema y hace exactamente eso, sin el borde.

78 clases están definidas **solo** en `style.css`, en cinco familias:
`note-*`/`notes-*` (~40), `corte-caja-*` (9), `evidence-*` (14), `note-hero-*` y
`note-kpi-*` (~8), más `ops-inline-*`.

### Defectos concretos que esto produce

- `notes-kpi-card` es una **segunda implementación de tarjeta KPI** compitiendo
  con `stat()` — justo lo que el sistema dice haber eliminado.
- `ops-inline-pill` son **pestañas falsas**: una fila de píldoras que simula tabs
  mientras todas las secciones se renderizan a la vez. `DESIGN_SYSTEM.md` §4 lo
  prohíbe y manda usar `.s-anchor-nav` (que existe, con 3 usos).
- **Tarjeta dentro de tarjeta** en detalle de nota y corte de caja, contra la
  regla explícita "a card never nests in a card".
- **Tablas cortadas**: en detalle de nota, "Materiales registrados" y "Pagos y
  abonos" viven dentro de columnas angostas y pierden su última columna.
- **Secciones fuera de tarjeta** en corte de caja ("Vista previa del día",
  "MONEDAS", "BILLETES"), mientras en todo el resto del sitio la tabla va dentro
  de una tarjeta.
- `corte_caja.html` es la única plantilla con un bloque `<style>` y con `style=`
  en línea (6).
- 6 plantillas se saltan `page_header`: `notes_list`, `note_detail`, `note_edit`,
  `precios_material`, `home`, `login`.
- Lecturas de solo lectura como mini-tarjetas con borde, reinventadas por
  pantalla.

## Restricción principal

**No se modifica el comportamiento.** Mismas rutas, mismos campos de formulario
con los mismos `name`, mismos `action`, mismos permisos, misma lógica de negocio.
Este trabajo toca plantillas, CSS y macros. Nada en `app/services/`, `app/web/`
ni `app/models/`.

## Decisiones tomadas

1. **Detalle de nota**: normalizar componentes en su lugar. No se replantea la
   arquitectura de la pantalla ni el orden de sus secciones.
2. **Tablas**: sí se revisa qué columnas muestra cada una — columnas vacías o
   redundantes, alineación de números, cuál nombra el registro y cuáles son
   prioritarias en teléfono.
3. **`style.css`**: se retira solo lo que quede sin uso al migrar. No se borra la
   hoja completa: el inventario de clases no ve reglas por elemento o atributo, y
   apostar contra eso rompería pantallas fuera de las 42 auditadas.

## Plan

### Fase 1 — Cerrar los huecos del vocabulario

El vocabulario privado nació porque el sistema tiene el CSS pero no el macro, así
que cada pantalla se lo construyó a mano. Se agregan a `_macros.html`:

- `card(title, sub)` — el panel estándar sobre `.s-card-title` / `.s-card-sub`.
- `defs()` + `def(label, value)` — lecturas de solo lectura sobre `.s-defs`.
  Reemplaza los 105 usos de `note-meta-*`.
- `anchor_nav(items)` — índice de saltos honesto sobre `.s-anchor-nav`.
  Reemplaza los 10 usos de `ops-inline-pill`.
- `toolbar()` — acciones de cabecera con orden fijo: primaria, secundarias,
  destructiva, `Volver` al final.

Se documentan en `DESIGN_SYSTEM.md` antes de usarse.

### Fase 2 — Migrar las 11 plantillas

En orden de riesgo creciente, verificando después de cada una:
`contabilidad_list` → `notes_list` → `worker/notes_form` →
`comisionario_nota_detail` → `comisionario_record` → `partner_record` →
`cuenta_detail` → `note_evidencias` → `corte_caja` → `note_edit` → `note_detail`.

En el camino se corrigen los defectos listados arriba: tarjeta en tarjeta,
pestañas falsas, tablas cortadas, secciones fuera de tarjeta, el `<style>`, los
`style=` en línea y los `page_header` faltantes.

### Fase 3 — Pasada de tablas

Sobre todas las pantallas de listado: quitar columnas vacías o redundantes,
`text-end` en numéricas, `data-title-col` en la que nombra el registro,
`data-mobile-primary` en las que un teléfono necesita ver, `s-col-fit` donde
sobra ancho.

### Fase 4 — Retirar el legado y verificar

Borrar de `style.css` las familias que queden sin uso, comprobando con las 84
vistas después de cada borrado. Extender `check_ui.js` con las reglas que hoy no
verifica y que habrían atrapado esto:

- ninguna clase de las familias bespoke,
- ningún `style=` en línea ni bloque `<style>`,
- `page_header` presente en toda pantalla,
- ninguna tarjeta anidada en otra tarjeta.

## Verificación

Ninguna afirmación de "listo" sin estas cuatro:

1. `node scripts/check_ui.js` — 84 vistas, desktop y móvil, sin incumplimientos.
2. `bash scripts/check_pages.sh …` — toda ruta responde 200 sin error de plantilla.
3. `python -m scripts.fix_accents --check app/templates` — sin regresiones.
4. Capturas antes/después de las 68 vistas, revisadas una por una.

Más el censo de clases bespoke, que debe terminar en cero.
