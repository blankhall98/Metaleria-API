# Scrap360 — Design System

The contract for every screen in this app. If you are adding or changing UI,
read this first and follow it; if you need something that isn't here, add it
here before you use it twice.

- Tokens and components: `app/static/css/scrap360.css`
- Behaviour: `app/static/js/app.js`
- Macros: `app/templates/_macros.html`
- Icons: `app/templates/_icons.html`

Load order is `bootstrap → style.css (legacy) → scrap360.css`. The legacy sheet
is being retired; **never add rules to it**. `scrap360.css` owns every visual
decision and remaps the legacy class names onto the canonical components, so
old markup inherits the new look automatically.

---

## 1. Direction

**Instrumento industrial.** This is a measuring instrument for a scrap yard, not
a marketing site. Admins reconcile money on a desktop; workers capture weights
on a phone, outdoors, one-handed. The design follows from that:

- **Data is the product.** Tables, figures and status get the visual budget.
  Chrome gets as little as possible.
- **Flat surfaces, hairline borders.** No gradient washes, no pastel fills, no
  decorative shadows. Depth comes from a 1px border and a white surface on a
  grey canvas.
- **Decisive typography.** A mechanical display face for headings, a technical
  sans for the interface, tabular figures everywhere a number appears.
- **One accent.** Steel blue means "action". Brass is the brand mark. Green,
  amber and red are reserved for meaning, never for decoration.

What this replaced: three radial-gradient background washes, 86 gradients, 59
distinct hex colours, 20 border-radius values, five mobile breakpoints, ~280
bespoke class names including eight separate implementations of a KPI card.

---

## 2. Tokens

Use the custom properties. **Never write a raw hex value in a template or a new
rule.**

### Colour

| Role | Token | Value |
|---|---|---|
| Canvas | `--s-canvas` | `#f4f6f8` |
| Surface | `--s-surface` | `#ffffff` |
| Sunken (region inside a card) | `--s-sunken` | `#f4f6f8` |
| Border | `--s-line` / `--s-line-strong` | `#dbe1e8` / `#c2cad4` |
| Text | `--s-text` / `--s-text-soft` / `--s-text-muted` | `#131a24` / `#4d5765` / `#6b7683` |
| Action | `--s-p500` … `--s-p700` | `#1f5c9e` … `#123a68` |
| Brand mark | `--s-brass300` / `--s-brass500` | `#d9a93f` / `#a9761b` |
| Success | `--s-ok` + `--s-ok-tint` + `--s-ok-line` | `#11785a` |
| Warning | `--s-warn` + tint + line | `#b45309` |
| Danger | `--s-bad` + tint + line | `#b42318` |

Neutrals run `--s-n0` (white) → `--s-n900` (near-black) on a cool graphite cast.

**Semantic rules, non-negotiable:**

- Green = settled, approved, positive confirmation. **Not** "Excel export".
- Red = a problem needing action: overdue, cancelled, destructive.
- Amber = needs attention but is not yet a failure: pending review, due soon.
- Blue = navigation and neutral action.
- **A zero is not an error.** `$0.00` renders muted (`.s-zero`), never red and
  never green. Use the `money()` macro and it happens for you.
- Liabilities are not errors. "Por pagar" is a neutral figure.

### Typography

| | Family | Use |
|---|---|---|
| Display | **Archivo** 600/700 | headings, page titles, stat values |
| Body | **IBM Plex Sans** 400/500/600 | everything else |
| Mono | **IBM Plex Mono** 500 | folios, account numbers, ids (`.s-mono`, `.s-folio`) |

Scale: `--s-fs-micro` 11px (micro-caps labels) · `xs` 12 · `sm` 13 (table body) ·
`base` 15 (UI default) · `md` 16 (**inputs — below this iOS zooms the page**) ·
`lg` 18 · `xl` 22 · `2xl` 28 · `3xl` 34.

All numbers are tabular by default so columns of money align.

### Space, radius, elevation, motion

- Space: a 4px scale, `--s-1` (4px) → `--s-12` (48px).
- Radius: exactly four — `--s-r-sm` 6px (controls), `--s-r-md` 10px (cards,
  tables), `--s-r-lg` 14px (page panels), `--s-r-pill` (status pills only).
- Elevation: exactly three — `--s-e-1/2/3`, all restrained. Cards use **no**
  shadow; borders do the work.
- Motion: `--s-dur` 160ms, `--s-dur-lg` 240ms, `--s-ease`. Everything respects
  `prefers-reduced-motion`.

### Breakpoints

Three, and only these: **640px**, **768px** (the phone/desktop divide, where
tables become cards), **1024px**. Plus **1400px**, where the navigation becomes
a permanent sidebar.

---

## 3. Layout

Every page is:

```
navbar (sticky)  ·  sidebar ≥1400px / drawer below  ·  main.page-shell  ·  footer
```

`body` is a flex column so the footer sits at the bottom of short pages instead
of floating above a band of empty canvas.

`.page-shell` is capped at `--s-shell-max` (1240px). Table-heavy screens add
`.page-shell-notes` for the wider cap. On phones the shell reserves bottom
padding for the fixed dock — **content is never hidden behind it**.

---

## 4. Components

### Page header — every screen opens the same way

```jinja
{% from "_macros.html" import page_header, icon %}
{% call page_header('Proveedores', eyebrow='Relaciones',
                    sub='Búsqueda, saldo neto y proveedores que también compran material.') %}
    <a href="/web/admin/proveedores/nuevo" class="btn btn-primary">
        {{ icon('plus', 's-icon--sm') }} Nuevo proveedor
    </a>
{% endcall %}
```

Eyebrow = the section (Operación, Relaciones, Inventario, Finanzas, Reportes,
Administración). Title = sentence case. Sub = one line saying what the screen is
for. Actions right-aligned; **exactly one `.btn-primary`**.

### Buttons

| Variant | Meaning | Rule |
|---|---|---|
| `.btn-primary` | the one main action | **at most one per screen** |
| `.btn-outline-primary` | secondary action | |
| `.btn-outline-secondary` | tertiary / neutral / back | |
| `.btn-outline-danger` | destructive | always for irreversible operations |

Sizes: `.btn` (40px), `.btn-sm` (34px, table rows), `.btn-lg` (48px, mobile
submit). Icon-only buttons use `.btn-icon` and **must** carry an `aria-label`.

**Labels — use these exact words:**

| Action | Label |
|---|---|
| Create a record | `Crear <entidad>` |
| Save an existing record | `Guardar cambios` |
| Apply a filter | `Filtrar` |
| Leave without saving | `Cancelar` |
| Go back | `Volver` |
| Open a detail | `Ver` |
| Export | `Exportar PDF` / `Exportar Excel` |

Never both `Cancelar` and `Volver` on the same screen.

### Cards

`.card` / `.s-card` — a border and a white fill. **A card never nests in another
card.** For a grouped region inside a card use `.s-subpanel` (sunken fill). A
card that would wrap a single control is not a card: drop it.

### Stat tiles — one implementation

```jinja
{% from "_macros.html" import stat, money %}
<div class="s-stats">
    {{ stat('En revisión', 3, 'Notas esperando aprobación', 'warn') }}
    {{ stat('Por pagar', money(total, 'plain'), 'Saldo vivo de compras') }}
</div>
```

Tone (`ok` `warn` `bad` `info` `brass`) colours a 3px left spine and the value —
never a filled background. Two per row on phones. **Never repeat a figure that
is already visible elsewhere on the same page.**

### Tables

```jinja
<div class="table-responsive">
  <table class="table table-hover align-middle">
    <thead><tr>
      <th data-title-col>Nombre</th>
      <th class="text-end">Saldo</th>
      <th><span class="s-visually-hidden">Acciones</span></th>
    </tr></thead>
    ...
  </table>
</div>
```

`app.js` annotates every table on load, so you get for free:

- micro-caps header, hairline rules, hover, tabular figures;
- sticky header, and a pinned first column past six columns;
- edge shadows that appear only on the side with more content — **do not add
  "arrastra con el mouse" instructions, ever**;
- **below 768px each row becomes a record card**: cells are labelled from their
  column headers, ids are dropped, actions move to the bottom full-width, and on
  wide tables the fields past the fourth fold behind a "Ver N campos más"
  toggle.

Controls you have:

- `data-title-col` on a `<th>` — pick the column that names the record. Without
  it, the first column that isn't an id is chosen.
- `class="text-end"` on numeric columns (headers and cells).
- An empty table renders the `empty_row()` macro, never a bare header.

### Section index — long record pages

A record page that stacks Resumen / Estado de cuenta / Notas / Pagos gets an
**honest** jump list, never a strip of pills pretending to be tabs while every
section renders at once:

```jinja
<nav class="s-anchor-nav" aria-label="Secciones">
    <a href="#partner-summary" class="btn btn-sm btn-outline-secondary">Resumen</a>
    <a href="#partner-ledger"  class="btn btn-sm btn-outline-secondary">Estado de cuenta</a>
</nav>
```

Every entry must point at an `id=` that exists on the page. Do not build JS
tabs, and never put an off-page link in this nav — that belongs in the header
actions.

### Badges

`{{ badge('Aprobada', 'ok') }}` — tones `ok` `warn` `bad` `info` `muted`. A
tinted pill with readable text, never a saturated block. Bootstrap's
`.bg-success`/`.bg-danger`/etc. are remapped, so existing markup is already
correct.

Two different states must never share a tone (`Aprobada` and `Pagada` were both
green — they are not the same thing).

### Forms

```jinja
<fieldset class="s-fieldset">
    <legend>Identidad</legend>
    <div class="s-form-grid">
        <div>
            <label for="nombre" class="form-label">Nombre completo <span class="s-req">*</span></label>
            <input id="nombre" name="nombre" class="form-control" required>
            <p class="form-text">Aparece en la orden y en el estado de cuenta.</p>
        </div>
    </div>
</fieldset>
<div class="s-form-actions">
    <a href="…" class="btn btn-outline-secondary">Cancelar</a>
    <button class="btn btn-primary">Crear proveedor</button>
</div>
```

- Every field has a **visible label**. A placeholder is an example, never a
  label and never where you state that a field is required.
- Required fields carry `<span class="s-req">*</span>`.
- Inputs are 16px so iOS doesn't zoom; 42px tall so they're comfortable to tap.
- Helper text is consistent within a fieldset: give it to every field that needs
  it or to none.
- Read-only values are **not** rendered as inputs. Use a definition row.
- Group with `<fieldset>`/`<legend>`, not with nested cards.
- Destructive submits get `data-confirm="…"`; long submits get
  `data-submit-once="1"` and `data-loading-text`.

### Filters

Wrap filters in `.s-filters-collapse` so they fold behind a summary on phones
and the data, not the form, is the first thing on screen. Never ship a `<select>`
and a pill row that filter the same field. Non-interactive labels must not look
like interactive pills — use `.s-section-label`.

### Empty states

`{{ empty('Sin movimientos', 'Amplía el rango de fechas…') }}` — icon, what
happened, and how to recover. Never a bare sentence in a tall blank card.

### Icons

From the sprite only: `{{ icon('truck') }}`, `{{ icon('plus', 's-icon--sm') }}`.
**Emoji are never icons.** Add new glyphs to `_icons.html` as 24×24 stroke
symbols drawn with `currentColor`.

---

## 5. Spanish copy

The interface is `es-MX`. The data was correctly accented while the chrome was
not, which made the app look unfinished.

- **Accents are mandatory**, including on uppercase: `OPERACIÓN`, `REVISIÓN`,
  `ADMINISTRACIÓN`, `CATÁLOGO`, `DIRECCIÓN`, `TELÉFONO`, `NÚMERO`, `VÍNCULO`,
  `MÉTODO`. And `Contraseña`, `Búsqueda`, `Comisión`, `Devolución`, `Conversión`,
  `días`, `también`, `Aún`, `Últimos`, `automático`, `valuación`, `período`.
- **No English** in the UI: `Registro` not `Record`, `Socios`/`Proveedores y
  clientes` not `Partners`, `Indicadores` not `KPIs`, `Existencias`/`Inventario`
  not `Stock`, `Contabilidad` not `Log contable`.
- **No raw enum values.** `COMPRA` → `Compra`, `PAGO COMISION` → `Pago de
  comisión`, `cuenta bancaria` → `Cuenta bancaria`.
- **No internal jargon**: not `Total (firmado)`, `LEGADO`, `Neteo`, a bare user
  id, or the storage backend's name.
- **Sentence case** for titles and labels. Not `Actualizar Stock`.
- **Gender-neutral**: `Cuando termines, abre la caja`, not `Cuando estés lista`.
- Dates go through `datetime_local` / `date_local`. Never print a raw
  `2026-01-10 10:51:08.384302`.
- Numbers: money as `$1,234.56`, negatives as `-$1,234.56` (sign inside), weight
  as `1,234.56 kg`. Use the `money()` and `kg()` macros.

---

## 6. Accessibility floor

Every screen must meet this before it ships:

- Text contrast ≥ 4.5:1; the token pairs above already do.
- Focus is always visible — `:focus-visible` is styled globally; **never remove
  an outline**.
- Touch targets ≥ 44px; table-row buttons are 34px on desktop and stretch to
  full width in mobile card mode.
- Icon-only controls carry `aria-label`; decorative SVG carries `aria-hidden`.
- Colour is never the only signal — pair it with text or an icon.
- One `<h1>` per page (the page title), then a sensible heading order.
- `prefers-reduced-motion` is respected globally.

---

## 7. Adding a screen

1. Extend `base.html`; import from `_macros.html`.
2. Open with `page_header(...)`.
3. Stats (if any) in one `.s-stats` row — figures that appear nowhere else.
4. Filters in `.s-filters-collapse`.
5. Content in one `.card`, tables in `.table-responsive`.
6. Empty state via `empty()`; empty table rows via `empty_row()`.
7. Check it at **390px** and **1440px** before you call it done: no horizontal
   scroll, nothing behind the dock, one primary button, accents intact.
