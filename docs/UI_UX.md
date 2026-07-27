# UI / UX

Server-rendered Jinja2 + Bootstrap 5.3.3 (CDN) + one large custom stylesheet (`app/static/css/style.css`, ~3.5k lines). No build step, no JS framework — all interactivity is inline vanilla JS in the templates. Language: Spanish (`es-MX`).

## Design system

### Tokens (`:root` in style.css)
- Ink/slate text scale: `--ink #0f172a`, `--slate #334155`, `--text-muted #475569`
- **Brand pairing: blue → gold.** `--blue #1d4ed8` / `--blue-soft #2f6feb` (primary) + `--gold #d7a13b` (accent). Used in the brand mark, card top-rules, avatars, icons, drawer dots, page-background radial wash.
- Surfaces: `--cloud #f8fafc` page bg, `--border #e2e8f0`, white cards.
- Semantic colors are ad-hoc Tailwind-palette hexes (success `#16a34a`, danger `#dc2626`, amber `#f59e0b`, teal `#0f766e` for visor) — not tokens.

### Typography
- **Plus Jakarta Sans** (Google Fonts; fallback Segoe UI/system).
- Page header pattern: eyebrow (`text-uppercase text-muted small`) + `h1.h4` + muted sub-line. Section: `h2.h6`.
- **Micro-label convention** for KPI/meta labels: ~0.72rem, uppercase, letter-spacing 0.06–0.10em, weight 700–800, muted. Big numbers: weight 800, tight letter-spacing.
- Tables: `font-variant-numeric: tabular-nums`, headers 0.70rem uppercase.

### Money/number formatting
No Jinja filters — inline everywhere:
```jinja
${{ "{:,.2f}".format(n.total_monto or 0) }}     {# money #}
{{ "{:,.3f}".format(kg) }} kg                    {# precise kg #}
{{ "{:,.5f}".format(precio_unitario) }}          {# unit prices #}
```
Only two custom filters exist: `datetime_local` and `date_local` (see template_utils.py — 12 lines total).

### Components (the vocabulary)
- **Cards**: `.card` (1rem radius, soft big shadow), `.card.soft`, `.card.hover-lift` (applied almost everywhere).
- **Estado badges**: Borrador `bg-secondary` · En revisión `bg-warning text-dark` · Aprobada `bg-success` · Cancelada `bg-danger`. Pago: Pendiente `bg-warning` · Pagada `bg-success` · Saldo a favor `bg-info`.
- **Op chips**: `.notes-op-chip.buy` (red tint, Compra) / `.sale` (green tint, Venta).
- **Pills**: `.chip-strong` (section eyebrow), `.ops-inline-pill[.active]` (filter/anchor pills), `.note-state-tab` (state tabs with counts), `.placa-chip`, `.partner-role-badge[.dual|.unified]`, `.user-role-chip.role-*`, `.corte-status-pill`.
- **KPI cards**: three competing systems coexist — `.ops-kpi-*`, `.notes-kpi-*` (tone-blue/green/amber/red), `.scrap360-kpi-*` — plus `.note-meta-grid` for read-only detail grids. Pick the one used by the page family you're editing.
- **Tables**: base `.table`, plus two premium skins `.report-table` and `.data-table`. Wide tables set explicit `min-width` and get sticky first column (`notes-state-table`, `note-materials-table`). Base JS auto-wraps `.table-responsive` with scroll shells, drag-to-scroll, edge fades, and a floating bottom scrollbar.
- **Modals**: Bootstrap modal is never used. The only modal is the custom evidence picker (camera/gallery bottom-sheet on mobile). Destructive actions use `onsubmit="return confirm('…')"` with explicit Spanish sentences describing exactly what will be reverted/restored — keep that convention.
- **Alerts**: `alert alert-* py-2 small`, driven by query-string flags (`?approved=1`, `?success=open|gasto|...`, `?error=tipo|peso|vacio|estado|upload`, worker `?success=0..3`).
- **Empty states**: `.notes-empty-state` (dashed border) or a muted small paragraph.

### Layout chrome (base.html — the only layout, two blocks: `title`, `content`)
1. Navbar (translucent + blur; sticky on mobile) with brand mark + user chip + `Menú` hamburger.
2. **Right-side drawer** = the app's whole navigation (per role, below). Escape/overlay closes.
3. **Mobile dock** (≤991.98px): floating bottom bar — admin: Inicio·Notas·Partners·Inventario·Menú; visor: swaps Partners→Saldos; trabajador: Inicio·Mis notas·Nueva·Menú.
4. `main.page-shell` max-width 1200px (1680px on nota pages via `page-shell-notes`).
5. Footer: env, version, support email.

### Responsive rules
Mobile line at **991.98px** (dock appears, grids collapse to 1 col, 44px+ touch targets, iOS safe-area insets). Worker notes list dual-renders: table ≥md, stacked cards <md (the only list that does; admin lists rely on horizontal scroll). No `@media print` anywhere — printing = server-generated PDFs.

## Navigation / route map (all under `/web`)

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

## Four roles = four different apps

- **trabajador** — mobile-first capture. 4-step wizard (`worker/notes_form.html`): 1) operación + partner (venta can register as Cliente or Proveedor-con-ventas, with neteo hint) → 2) repeatable material cards with subpesajes (bruto − desc = neto auto), per-subpesaje camera upload with state chips (uploading/ready/error), live totals from a client-side `PRICE_MAP` (preview only — money is decided at approval) → 3) comentarios → 4) evidencia extra. Steps unlock progressively; uploads block submit while in flight; server error re-render restores state via `INITIAL_NOTE` JSON.
- **admin** — operations scoped to assigned sucursales (filters pre-constrained; single-branch admins get auto-selection and locked transfer origin). Cannot: manage usuarios/sucursales/materiales, conversiones, edit/delete notas, delete partners/cuentas, administrative notas, or **close a corte**.
- **visor** — read-only twin: capability flags (`can_manage_note`, `can_manage_partner`, `can_manage_inventory`, …) hide every mutation form/button.
- **super_admin** — everything, plus in note review: editable Kg desc/neto, **Kg reales inventario** ("No cambia la orden ni el PDF"), price/subtotal inputs; Eliminar nota; alta administrativa; invoice auto-upload + auto-open-print (`?auto_open_pdf=1`).

## Signature UX patterns (keep these when adding features)

1. **"Siguiente paso recomendado" flow card** on note detail — state-computed title/CTA (review → pay → saldo a favor → devoluciones).
2. **Anchor-pill toolbars** (`.ops-inline-toolbar`, often `.sticky-tools`) sectioning long pages (Resumen · Aprobación · Pagos · …).
3. **Workspace grid**: main column + sticky right rail (`clamp(320px, 25vw, 410px)`) for the approval panel; collapses on mobile.
4. **Plain-language balance direction**: "La metalería le debe" (red) vs "Debe a la metalería" (green) — repeated in partner record, contabilidad, reporte de saldos.
5. **Review loop**: admin "Devolver a borrador" + comment → worker sees a yellow "Comentario del administrador" alert on the edit form → re-send. Navbar/home badge (`notas_revision_count`) is the admin's entry point.
6. **Evidence page** (`note_evidencias.html`, shared worker/admin): per-material completeness badges (Completo/Parcial/Sin evidencia), tile grid, camera-vs-gallery bottom sheet, auto-submit on file pick.
7. **Corte de caja** has its own deliberate "financial terminal" sub-theme (~380 lines page-scoped CSS: dark slate header, 6px radii, flat borders) with a 3-state page (sin abrir → abierto with sticky section nav → cerrado) and denomination count sheets. `data-submit-once` double-submit protection on all its forms.

## Known UI debt (see also KNOWN_ISSUES.md)

- Zero `{% include %}`/`{% macro %}` — every pattern is copy-pasted across ~45 templates. Highest-leverage refactor: extract `money`, `badge_estado`, `page_header`, `kpi_card` macros.
- Legacy dark-theme residue: `text-white` headings neutralized by a `.text-white { color: var(--text-main) !important }` override; newer pages use `text-dark`.
- Three KPI systems, two premium table skins; corte's sub-theme not back-ported.
- `toLocaleString('en-US')` in JS despite es-MX.
- Accessibility: emoji-as-icons without aria-hidden, inline onclick, `confirm()` dialogs, no focus trap in drawer.
