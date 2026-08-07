# Scrap360 — Design System

The contract for every screen in this app. If you are adding or changing UI,
read this first and follow it; if you need something that isn't here, add it
here before you use it twice.

- Tokens and components: `app/static/css/scrap360.css`
- Behaviour: `app/static/js/app.js`
- Macros: `app/templates/_macros.html`
- Icons: `app/templates/_icons.html`

Load order is `bootstrap → scrap360.css`. The legacy `style.css` was retired on
2026-08-06 — `scrap360.css` owns every visual decision. Do not create page-level
`<style>` blocks or a second sheet: if a screen needs something new, it becomes
a token or component here first.

---

## 1. Direction

**Classic CRM.** This is business software in the Salesforce/Zoho register:
dense but comfortable, every component a declared size, and the data grid as the
centre of gravity. Admins reconcile money on a desktop; workers capture weights
on a phone, outdoors, one-handed.

- **The table is the product.** 40px rows, zebra striping, column rules, a
  sticky header, tabular figures. Everything else defers to it.
- **Defined sizes, not eyeballed padding.** Every control and row height comes
  from a token (§2), so parts built separately still line up.
- **The app fills the monitor.** No page-width cap — a capped shell left unused
  space on the right while columns were cut off on the left.
- **Flat surfaces, visible borders.** No gradient washes or decorative shadows;
  containers are defined by a 1px border on a light grey canvas.
- **One accent.** Blue means "action". Brass is the brand mark. Green, amber and
  red are reserved for meaning, never decoration.

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
| Action | `--s-p500` … `--s-p700` | `#0b6bcb` … `#084681` |
| Zebra row | `--s-zebra` | `#f4f7fa` |
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
- **Links never underline** — at rest or on hover. A link is blue and medium
  weight; hover darkens it. Underlines and border-bottom "underline effects"
  read as web-page, not product.
- **A zero is not an error.** `$0.00` renders muted (`.s-zero`), never red and
  never green. Use the `money()` macro and it happens for you.
- Liabilities are not errors. "Por pagar" is a neutral figure.

### Typography

One family across the product, the way business software reads. IBM Plex Sans is
an enterprise UI face with true tabular figures.

| | Family | Use |
|---|---|---|
| UI | **IBM Plex Sans** 400/500/600/700 | everything |
| Mono | **IBM Plex Mono** 500 | folios, account numbers, ids (`.s-mono`, `.s-folio`) |

Scale: `--s-fs-micro` 11px (column headers, eyebrow) · `xs` 12 · `sm` 13 (table
body) · `base` 14 (UI default) · `md` 16 (mobile inputs — **below 16px iOS zooms
the page**) · `lg` 17 · `xl` 20 · `2xl` 24 · `3xl` 30.

All numbers are tabular by default so columns of money align.

### Defined sizes

Every control and row has one declared height. Do not invent padding to reach a
size — use the token.

| Token | Value | Use |
|---|---|---|
| `--s-row-h` | 40px | table body row |
| `--s-row-head-h` | 36px | table header row |
| `--s-control-h` | 36px | input, select, button |
| `--s-control-h-sm` | 30px | controls inside a table row |
| `--s-control-h-lg` | 44px | primary submit; **all inputs on mobile** |
| `--s-navbar-h` | 56px | top bar |
| `--s-sidebar-w` | 240px | pinned navigation |

### Space, radius, elevation, motion

- Space: a 4px scale, `--s-1` (4px) → `--s-12` (48px).
- Radius: exactly four — `--s-r-sm` 4px (controls), `--s-r-md` 6px (cards,
  tables), `--s-r-lg` 8px (page panels), `--s-r-pill` (status pills only).
- Elevation: exactly three — `--s-e-1/2/3`, all restrained. Cards use **no**
  shadow; borders do the work.
- Motion: `--s-dur` 160ms, `--s-dur-lg` 240ms, `--s-ease`. Everything respects
  `prefers-reduced-motion`.

### Breakpoints

Three, and only these: **640px**, **768px** (the phone/desktop divide, where
tables become cards), **1024px**. Plus **1200px**, where the navigation becomes
a permanent sidebar.

---

## 3. Layout

Every page is:

```
navbar (sticky)  ·  sidebar ≥1200px / drawer below  ·  main.page-shell  ·  footer
```

`body` is a flex column so the footer sits at the bottom of short pages instead
of floating above a band of empty canvas.

**`.page-shell` is not width-capped** — the app fills the monitor, because a cap
wasted space on the right while cutting columns off on the left. Reading-width
content (a login card, a single narrow form) opts in with `.s-narrow`.

On phones the shell reserves bottom padding for the fixed dock — **content is
never hidden behind it**.

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

**Action order is fixed**, so the same button sits in the same place on every
screen: exports and other secondary actions first, then the destructive one, then
`Volver`, and the single primary action last (nearest the right edge, where the
eye lands). A screen that reads `Volver · Exportar PDF · Editar` and another that
reads `Editar · Exportar PDF · Volver` are the same screen to the user's hand and
two different ones to their eye.

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

```jinja
{% from "_macros.html" import card, subpanel %}
{% call card('Estado de cuenta', 'Movimientos cronológicos con saldo acumulado.') %}
    …table…
{% endcall %}
```

`.card` / `.s-card` — a border and a white fill. **A card never nests in another
card.** For a grouped region inside a card use `subpanel()` / `.s-subpanel`
(sunken fill). A card that would wrap a single control is not a card: drop it.

Use the macro. Hand-building the container is how the titles drifted apart —
`h2.h6`, `h3.section-title` and `.note-form-card-title` were three spellings of
one thing.

### Read-only values

```jinja
{% from "_macros.html" import defs, def %}
{% call defs() %}
    {{ def('Teléfono', partner.telefono) }}
    {{ def('Saldo', money(saldo), 'Calculado con notas aprobadas.') }}
{% endcall %}
```

A value you display but cannot edit is **never** a disabled input and **never** a
bordered mini-card — a grid of boxes reads as a set of controls the user can act
on, and the boxes nest a border inside the card that already draws one. An empty
value renders an em dash by itself.

This replaces `note-meta-grid` / `note-meta-item` / `note-meta-label`, which had
leaked from the notes screens into proveedores, comisionarios and cuentas.

### Stat band — one implementation

```jinja
{% from "_macros.html" import stat, money %}
<div class="s-stats">
    {{ stat('En revisión', 3, 'Notas esperando aprobación', 'warn') }}
    {{ stat('Por pagar', money(total, 'plain'), 'Saldo vivo de compras') }}
</div>
```

Indicators are **one reading band**, not a grid of loose cards: a single
bordered surface where cells are separated by 1px hairlines, like the panel of
an instrument. Tone (`ok` `warn` `bad` `info` `brass`) is a 6px dot beside the
label plus the value colour — never a filled background, never a spine. Two per
row on phones. **Never repeat a figure that is already visible elsewhere on the
same page.**

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

- 40px rows, zebra striping, column rules, hover, tabular figures;
- a **sticky header** that pins under the navbar as you scroll a long list;
- a pinned first column past six columns;
- **fit-first sizing**: if the table would be cut off, the layer tightens the
  cells (`.s-table-compact`) and only falls back to horizontal scrolling if even
  that does not fit. Do not pre-emptively add scroll wrappers;
- a **row action menu**: past the inline budget (two actions, one on an 8-column
  table, none at 10+) the remainder move into a `⋯` menu, with destructive items
  in red. This is why the actions column no longer pushes lists off screen — do
  not hand-build per-row dropdowns;
- edge shadows that appear only on the side with more content — **do not add
  "arrastra con el mouse" instructions, ever**;
- **below 768px each row becomes a record card**: cells are labelled from their
  column headers, ids are dropped, actions move to the bottom full-width, and on
  wide tables the fields past the fourth fold behind a "Ver N campos más"
  toggle.

Controls you have:

- `data-title-col` on a `<th>` — pick the column that names the record. Without
  it, the first column that isn't an id is chosen.
- `data-mobile-primary` on a `<th>` — the columns a phone user came for. Marked
  columns always show in card mode; the rest fold behind "Ver N campos más".
  Without any marks the first four columns show, which is the wrong guess for a
  ledger where the money sits in column eight.
- `class="text-end"` on numeric columns (headers and cells).
- `class="s-col-fit"` on a `<th>` to shrink a column to its content.
- An empty table renders the `empty_row()` macro, never a bare header.

**Keep tables to about ten columns.** Past that they stop fitting even at full
width. Merge columns that tell one story rather than adding another — the notes
list folds the payment badge in with the status badge, and the capture date in
under the folio, which is how it fits.

### Section index — long record pages

A record page that stacks Resumen / Estado de cuenta / Notas / Pagos gets an
**honest** jump list, never a strip of pills pretending to be tabs while every
section renders at once:

```jinja
{% from "_macros.html" import anchor_nav %}
{{ anchor_nav([('#partner-summary', 'Resumen'),
               ('#partner-ledger',  'Estado de cuenta')]) }}
```

Every entry must point at an `id=` that exists on the page. Do not build JS
tabs, and never put an off-page link in this nav — that belongs in the header
actions.

This replaces `ops-inline-pill` / `ops-inline-toolbar`, which rendered a strip of
pills with one marked `active` — the exact thing this section forbids, since
nothing was hidden and clicking only scrolled.

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

### Classes a script holds on to

A handful of class names carry no style at all — they exist so the pricing and
weighing scripts can find a field: `note-unit-input`, `note-subtotal`,
`note-kg-mode`, `note-precio-mode`, `note-tipo-precio`, `note-return-kg-input`
and a few more, 21 uses in `note_detail.html` and `note_edit.html`.

**Leave them.** They are behaviour, not presentation: renaming one silently
breaks the total a nota is approved with, and there is nothing to gain visually.
If you touch them, move them to `data-` attributes and update the selectors in
the same commit — do not rename them as if they were styling.

The same applies to the other behaviour hooks catalogued in `docs/UI_UX.md`
§"Behaviour-only class names": `evidence-*`/`subpesaje-*`/`evid-*` (worker
wizard and evidence page), `note-edit-*` (edit recalculation) and
`denom-count-input` (arqueo). Everything else that is not in this document is a
leftover and a regression.

### Icons

From the sprite only: `{{ icon('truck') }}`, `{{ icon('plus', 's-icon--sm') }}`.
**Emoji are never icons.** Add new glyphs to `_icons.html` as 24×24 stroke
symbols drawn with `currentColor`.

### Brand

The mark is a **scale dial**: a graphite instrument arc with a brass needle,
which also reads as "360°". One drawing for every context:

```jinja
{{ brand_mark() }}                                  {# navbar, footer, cajón #}
{{ brand_mark('brand-mark--lg brand-mark--invert') }}  {# fondos oscuros #}
```

The wordmark is two-tone — `Scrap` inherits the container's colour, `360` is
always brass — via `{{ brand_word() }}`. Never write "Scrap360" as plain text
next to the mark, and never rebuild the mark as a box, an image or an emoji.
The mark does not change colour on hover: it is identity, not an action.

### Login (panel dividido)

`/web/login` is the product's front door: a split panel (`.s-auth__panel`) with
the form first in the DOM (`.s-auth__main`, focus and screen readers) and a
graphite brand canvas (`.s-auth__aside`) that only renders ≥992px — eyebrow,
one headline, one line of support, the dial as a cropped technical drawing,
and a mono version line. Phones get the form alone. `home.html` logged-out
keeps the simple `.s-auth__card`.

### Footer

Brand block left (mark + two-tone wordmark + one-line descriptor); meta right:
environment chip (`.footer-chip--env`, amber, only outside prod), version chip
(`.footer-chip`, mono), a hairline separator and a `Soporte` mailto link, then
the credit line. On phones the footer clears the mobile dock — its last line
is never buried under the fixed bar.

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
