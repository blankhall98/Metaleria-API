# UI / UX

Server-rendered Jinja2 + Bootstrap 5.3.3 (CDN). **Everything visual is defined by
`docs/DESIGN_SYSTEM.md`** — tokens, components, macros, copy rules — implemented in
`app/static/css/scrap360.css` (the only stylesheet; the legacy `style.css` was
retired 2026-08-06), `app/templates/_macros.html`, `app/templates/_icons.html`
and `app/static/js/app.js`. No build step, no JS framework; page-specific
behaviour is inline vanilla JS in the templates. Language: Spanish (`es-MX`).

This file covers what the design system does not: the route map, the per-role
experience, and the signature UX patterns to preserve when adding features.

Quality gates (run before calling any UI change done):

```bash
python -m scripts.check_templates            # compiles all templates; diffs Jinja inside class= vs HEAD
python -m scripts.fix_accents --check app/templates
bash scripts/check_pages.sh <path>…          # renders routes logged-in, fails on non-200/template errors
CHROME_PATH=… node scripts/check_ui.js       # 42 routes × 1440px/390px against the design-system rules
```

`check_ui.js` needs `npm install` once (playwright-core) and the dev server on
port 8010 (`uvicorn app.main:app --port 8010`).

## Navigation / route map (all under `/web`)

Layout chrome (`base.html`, blocks `title` / `content` / `head_extra`): navbar →
right-side drawer (becomes a **pinned left sidebar ≥1200px** for admin/visor
roles) → `main.page-shell` (full width) → footer; plus a fixed bottom **dock**
on phones (<992px) — admin: Inicio·Notas·Partners·Inventario·Menú; trabajador:
Inicio·Mis notas·Nueva·Menú.

Drawer per role:
- **super_admin**: Usuarios, Sucursales, Notas(+badge), Materiales, Proveedores, Clientes, Comisiones, Inventario, Aumentar materiales, Conversión de materiales, Transferencias, Contabilidad, Capital, Corte de Caja, Asistencias, Reporte de saldos, Cuentas, Cuentas Scrap360.
- **admin**: same minus Usuarios, Sucursales, Materiales, Conversión de materiales.
- **visor**: Notas, Proveedores, Clientes, Inventario, Movimientos, Contabilidad, Capital (all read-only).
- **trabajador**: Mis notas, Nueva nota.

Key route groups (prefix `/web/admin` unless noted; full detail greppable in `app/web/admin.py`):
- Notas: `/notas` (filters: folio, sucursal, proveedor, estado, pago, seguimiento/vencimiento), `/notas/{id}` (detail/review/approve), `/notas/{id}/editar` (super_admin), `/notas/{id}/factura` (PDF), `/notas/{id}/evidencias`, plus POST actions: aprobar, precios, pago, pago/{id}/deshacer, ajuste-manual, ajuste-saldo, devolucion-parcial, cancelar, devolver, eliminar (super_admin). Administrative notas (super_admin): `/notas/compra-administrativa`, `/notas/venta-administrativa`.
- Partners: `/proveedores`, `/clientes` (+`?modo=COMPRA_VENTA`), `/{id}/record`, `/{id}/estado-cuenta?format=pdf|excel`, `/{id}/asistencias`, crear-cliente / crear-proveedor (linking), ajuste-saldo. Delete = super_admin.
- Comisiones: `/comisionarios`, `/comisionarios/notas`, nota detail/PDF/pago.
- Cuentas: `/cuentas` (partner banking), `/cuentas-scrap360` (treasury, + `/ajuste`, `/estado`).
- Inventario: `/inventario`, `/inventario/valor`, `/inventario/movimientos` (+export csv/xlsx/pdf), `/inventario/ajuste`, `/inventario/aumentar`, `/conversiones-materiales` (super_admin).
- Money: `/contabilidad` (+`/export`, `/reporte`), `/capital`, `/corte-caja` (+abrir/gastos/movimientos/arqueo/cerrar/reporte), `/reporte-asistencias`, `/reporte-saldos`.
- Transferencias: `/transferencias`.
- Worker (`/web/worker`): `/notes`, `/notes/nueva`, `/notes/{id}/editar` (BORRADOR only), `/notes/{id}/enviar`, `/notes/{id}/evidencias`.

Feedback after actions is query-string driven (`?approved=1`, `?success=…`,
`?error=…`) rendered as compact alerts. Destructive actions use `data-confirm`
(or explicit `onsubmit` confirm with a full Spanish sentence describing exactly
what will be reverted); long submits use `data-submit-once` + `data-loading-text`.

## Four roles = four different apps

- **trabajador** — mobile-first capture. 4-step wizard (`worker/notes_form.html`): 1) operación + partner (venta can register as Cliente or Proveedor-con-ventas, with neteo hint) → 2) repeatable material cards with subpesajes (bruto − desc = neto auto), per-subpesaje camera upload with state chips (uploading/ready/error), live totals from a client-side `PRICE_MAP` (preview only — money is decided at approval) → 3) comentarios → 4) evidencia extra. Steps unlock progressively (controls ship `disabled` and are enabled per step — this is progressive disclosure, not read-only styling); uploads block submit while in flight; server error re-render restores state via `INITIAL_NOTE` JSON.
- **admin** — operations scoped to assigned sucursales (filters pre-constrained; single-branch admins get auto-selection and locked transfer origin). Cannot: manage usuarios/sucursales/materiales, conversiones, edit/delete notas, delete partners/cuentas, administrative notas, or **close a corte**.
- **visor** — read-only twin: capability flags (`can_manage_note`, `can_manage_partner`, `can_manage_inventory`, …) hide every mutation form/button.
- **super_admin** — everything, plus in note review: editable Kg desc/neto, **Kg reales inventario** ("No cambia la orden ni el PDF"), price/subtotal inputs; Eliminar nota; alta administrativa; invoice auto-upload + auto-open-print (`?auto_open_pdf=1`).

## Signature UX patterns (keep these when adding features)

1. **"Siguiente paso recomendado" flow card** on note detail — state-computed title/CTA (review → pay → saldo a favor → devoluciones).
2. **`anchor_nav()` section index** on long record pages (partner, comisionario, nota) — an honest jump list, never tabs that pretend to hide content.
3. **Workspace grid**: main column + sticky right rail for the approval panel; collapses on mobile.
4. **Plain-language balance direction**: "La metalería le debe" vs "Debe a la metalería" — repeated in partner record, contabilidad, reporte de saldos.
5. **Review loop**: admin "Devolver a borrador" + comment → worker sees a yellow "Comentario del administrador" alert on the edit form → re-send. Navbar/home badge (`notas_revision_count`) is the admin's entry point.
6. **Evidence page** (`note_evidencias.html`, shared worker/admin): per-material completeness badges (Completo/Parcial/Sin evidencia), tile grid, camera-vs-gallery bottom sheet, auto-submit on file pick.
7. **Corte de caja** is a 3-state page (sin abrir → abierto with section anchors → cerrado) built from canonical components: `.s-collapse` sections, `.s-cat-row-*` payment-method colour coding with its `.s-cat-legend`, `.s-method-pill` payment-reference pills, and the `.s-dockbar` floating summary bar (all in `scrap360.css` §21, reusable by any screen).

## Behaviour-only class names (hooks, not styling)

Some classes exist so page scripts can find elements; renaming them breaks
functionality silently. Besides the pricing hooks listed in
`DESIGN_SYSTEM.md` §"Classes a script holds on to" (`note-unit-input`,
`note-subtotal`, …), these are also load-bearing:

- `worker/notes_form.html` + `note_evidencias.html`: `evidence-*`, `subpesaje-*`
  (`subpesaje-bruto/desc/neto/evid`), `evid-label`, `evid-thumb` — the wizard's
  weighing/upload machinery.
- `note_edit.html`: `note-edit-*`, `note-row-*`, `note-total-value`,
  `note-iva-value`, `note-subtotal-value` — the recalculation script.
- `corte_caja.html`: `denom-count-input` + `data-denom-*` — the arqueo math.

If one of these must change, move it to a `data-` attribute and update the
selectors in the same commit.

## Known UI debt

- The `card()`, `subpanel()`, `defs()`/`def()`, `filters()` macros exist but most
  templates still hand-build those structures (macro adoption is incremental —
  new/edited screens must use them; see DESIGN_SYSTEM §4).
- `toLocaleString('en-US')` in corte's JS formats money in the en-US locale
  (visually identical to es-MX for `$1,234.56`, but the locale string is wrong).
- Bootstrap's `confirm()` dialogs are native; no focus trap in the drawer.
- Excel exports are SpreadsheetML `.xls` (format warning on open); PDFs are
  latin-1 with `errors="ignore"` (see KNOWN_ISSUES).
