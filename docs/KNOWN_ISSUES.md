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

9. **Cuentas cuyo hash guarda un espacio sobrante (residuo del bug de acceso).** Hasta 2026-07-30, `POST /users/nuevo` hasheaba la contraseña **sin recortar** mientras `POST /users/{id}/editar` la recortaba, y el login verificaba sin recortar. Un alta hecha pegando la clave con un espacio al final guardó el hash de `"clave "`, así que la persona nunca puede entrar escribiendo `"clave"`. Ya está corregido para altas nuevas, y el login ahora prueba la variante recortada — pero **las cuentas ya dañadas no se pueden recuperar por código**: el espacio quedó dentro del hash y no hay forma de deducirlo. Se arreglan una por una reasignando la contraseña desde *Editar usuario*. En la bitácora aparecen como `Acceso rechazado para 'usuario': password_incorrecta`.

10. ~~Local git repo has a corrupted `main` ref~~ — **FIXED 2026-07-28**: `.git/refs/heads/main` held blanks instead of a SHA, breaking `git log`/`fetch`/`status`. Repaired by deleting the ref, fetching `origin`, pointing `main` at `f2b73a2` and resetting the index. No history was lost — the working tree was untouched and both remotes still held the real history. If it recurs, the same sequence works.

11. **Sparse tests.** First automated tests landed 2026-08-07: `tests/test_neteo.py` covers the netting engine (`note_service.build_effective_note_balance_map`), including the linked-pair cases. pytest is venv-only (NOT in requirements.txt — keep it out of the prod dyno). Everything else (~24k lines) is still unverified; next candidates: `note_service` state transitions and `pay_comisionario_fifo`. UI guards: `scripts/check_ui.js`, `check_templates`, `fix_accents --check`.

11. ~~Bootstrap JS blocked on every page~~ — **FIXED 2026-07-28**: `base.html` carried a stale `integrity` hash for `bootstrap.bundle.min.js`, so the browser refused the script and every `data-bs-toggle` silently did nothing. Corrected against the published sha384 (cross-checked against the CSS hash, which was already correct).

12. **`comisionario_form` renders an unchecked "activo" box when editing an active comisionario.** `form_data` is always truthy (the route injects `sucursal_id`), so `activo_val` reads from an empty form instead of the record. The same bug for the name/phone/email fields was fixed 2026-07-28; this one was left because correcting it changes what the form submits.

13. **Two service files still build unaccented Spanish into persisted comments**: `note_service.py` (`"Devolucion parcial nota #…"`) and `conversion_service.py` (`"Conversion #…"`, `"Reversion conversion #…"`). Nothing matches on these strings, so they are safe to accent — but they are written into the audit trail, so it was left for a deliberate change. Note `"Pago inicial"` in `note_service.py` **is** load-bearing (`admin.py` matches it with `.lower().startswith("pago inicial")`) and must not be touched.

11. **Seed data is from a different business.** `seed_initial_materials_and_prices.py` seeds construction materials (Varilla, Cemento, Arena…) and fictional sucursal addresses — leftovers from an earlier framing. Harmless (idempotent, prod already has real data) but don't run blindly.

## Consistency traps (by design or debt — don't "fix" casually, but know them)

12. `InventarioMovimiento.cantidad_kg` is absolute for `compra|venta`, signed for `ajuste|conversion`.
13. `metodo_pago` uses singular `cheque`; `CuentaScrap360.tipo` uses plural `cheques` — validators map them explicitly.
14. Enum storage asymmetry (by value vs by name) — see DATA_MODEL.md.
15. `NotaPago` undo = zeroed rows kept for audit — always filter `monto > 0`.
16. `"Pago inicial"` comment prefix is a functional key for `adjust_initial_payment`.
17. `Inventario(sucursal, material)` has no unique constraint; get-or-create in service code only.
18. Excel exports are SpreadsheetML XML with `.xls` extension → Excel format warning on open. PDFs are latin-1 with `errors="ignore"` → non-latin-1 chars silently dropped.
19. ~~CSS: undefined vars / unclosed media scope in style.css; mojibake in two templates~~ — **RESOLVED 2026-08-06**: `style.css` was deleted (etapa 0 de la fase 2; las 10 clases vivas se portaron a `scrap360.css`), and the mojibake was already gone from both templates.
20. Stock is not enforced at normal venta approval — negative inventory is possible on purpose (approve first, fix stock later); only transfers hard-block.
