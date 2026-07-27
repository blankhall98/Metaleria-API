# Known Issues & Tech Debt

Confirmed findings from the July 2026 full-codebase audit. Ordered by severity. When one is fixed, remove it from this file.

## Bugs (confirmed, reachable)

1. ~~Contabilidad report download crashes~~ — **FIXED 2026-07-26**: `_get_note_balance_adjustment_totals_map` moved from `app/web/admin.py` into `app/services/note_service.py`; admin.py keeps a module-level alias so its 16 call sites are unchanged. Verified end-to-end (build_report_data + Excel + PDF).

2. **Contabilidad date filter is off.** `build_report_data` compares `date_to` as naive local midnight with `<=` against UTC-stored timestamps: the final day of the range is effectively excluded, and the tz offset is ignored. Corte de caja does this correctly (`_corte_local_day_bounds`) — reuse that approach.

3. ~~Local dev DB behind head~~ — **FIXED 2026-07-26**: upgraded to `ff5a6b7c8d90`. Note for Windows dev: the venv needs the `tzdata` package (installed 2026-07-26; it is NOT in requirements.txt) or `ZoneInfo` cannot resolve any timezone and every datetime render crashes locally. Heroku/Linux is unaffected (system tz database).

4. **Invoice saldo ignores balance adjustments.** `invoice_service.build_invoice_pdf` computes `saldo = total − pagado`, while the canonical formula everywhere else adds `Σ NotaAjusteSaldo.monto_delta`. Invoices on notes with balance adjustments show a different saldo than the UI.

## Security

5. **Hardcoded super-admin credentials.** `scripts/seed_super_admin_env.py:17-19` defaults to `AVRC` / `scrap360$1123` when env vars are absent, and the same credentials are committed in `.env.example`. The script **upserts and forces the password** — running it in prod without env vars silently resets the super admin to a publicly known password. Rotate the real credential; remove the defaults.

6. **JSON API has no role/branch checks.** Any logged-in user (including visor and trabajador) can call every `/api/*` endpoint — create materials, prices, partners, notes — bypassing web-layer guards. Since the API is vestigial, the cheapest fix is to gate it super_admin-only or remove the routers.

7. **All Firebase uploads are public.** Evidence photos and invoices are `make_public()` — readable by anyone with the (unguessable) URL. Acceptable for MVP; revisit with signed URLs if the client raises confidentiality.

8. **Session cookie**: `https_only=False` (prod runs HTTPS on Heroku — should be True), no CSRF protection on any form, role baked into cookie until re-login.

## Operational

9. **Local git repo has zero commits.** HEAD is unborn (`git log` → "branch appears to be broken") while remotes `origin` (GitHub `blankhall98/Metaleria-API`) and `heroku` exist and hold the deployed history. Everything is staged but nothing committed locally. Re-establish history (fetch from origin and reset, or commit fresh) before further deploys.

10. **No tests, no test tooling.** `tests/` is empty; pytest isn't in requirements. ~24k lines of business logic are unverified. If adding tests, start with `note_service` state transitions and the balance formula.

11. **Seed data is from a different business.** `seed_initial_materials_and_prices.py` seeds construction materials (Varilla, Cemento, Arena…) and fictional sucursal addresses — leftovers from an earlier framing. Harmless (idempotent, prod already has real data) but don't run blindly.

## Consistency traps (by design or debt — don't "fix" casually, but know them)

12. `InventarioMovimiento.cantidad_kg` is absolute for `compra|venta`, signed for `ajuste|conversion`.
13. `metodo_pago` uses singular `cheque`; `CuentaScrap360.tipo` uses plural `cheques` — validators map them explicitly.
14. Enum storage asymmetry (by value vs by name) — see DATA_MODEL.md.
15. `NotaPago` undo = zeroed rows kept for audit — always filter `monto > 0`.
16. `"Pago inicial"` comment prefix is a functional key for `adjust_initial_payment`.
17. `Inventario(sucursal, material)` has no unique constraint; get-or-create in service code only.
18. Excel exports are SpreadsheetML XML with `.xls` extension → Excel format warning on open. PDFs are latin-1 with `errors="ignore"` → non-latin-1 chars silently dropped.
19. CSS: undefined vars `--success`/`--danger`/`--text-dark` referenced in style.css; unclosed-scope effect — the `@media (max-width: 768px)` block starting ~style.css:2857 swallows all `.cash-count-*`/`.corte-caja-*` rules (lines ~2874–3164), so they only apply below 768px; mojibake `â†”`/`Â·` in `notes_list.html:340` and `contabilidad_list.html:87`.
20. Stock is not enforced at normal venta approval — negative inventory is possible on purpose (approve first, fix stock later); only transfers hard-block.
