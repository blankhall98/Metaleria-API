# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**Scrap360 / Metalería MVP** — a server-rendered FastAPI + Jinja2 web app (Spanish, `es-MX`) that runs the daily operation of a multi-branch scrap-metal yard: weighing notes (notas de pesaje), inventory in kg, pricing tables, partner balances, cash-drawer reconciliation (corte de caja), internal treasury accounts, commission agents, and PDF/Excel reports. Domain language is Spanish everywhere (models, routes, UI) — keep it that way.

Detailed documentation lives in `docs/`:
- `docs/ARCHITECTURE.md` — stack, layers, deployment, migrations
- `docs/DATA_MODEL.md` — every table, enum, and constraint
- `docs/BUSINESS_RULES.md` — the nota lifecycle and all money/inventory invariants (**read before touching `note_service.py`**)
- `docs/DESIGN_SYSTEM.md` — tokens, components, copy rules (**read before touching any template or CSS**)
- `docs/UI_UX.md` — route map and per-role UX
- `docs/KNOWN_ISSUES.md` — confirmed bugs, security flags, tech debt

The original client plan is in `development_context/*.pdf` (roles, flows, MVP scope). Implementation has since diverged: Firebase Storage instead of S3, plus comisionarios, corte de caja, conversions, transfers, and Cuentas Scrap360.

## Commands

```bash
# Run locally (SQLite ./metalleria.db; .env must define SECRET_KEY)
uvicorn app.main:app --reload

# Migrations (alembic reads DATABASE_URL from .env via app config)
python -m alembic upgrade head
python -m alembic revision -m "description"   # hand-write; see migration notes below

# Seeds (idempotent, run from repo root)
python -m scripts.seed_super_admin_env          # super admin from SUPERADMIN_* env vars (UPSERTS — resets password)
python -m scripts.seed_initial_sucursales
python -m scripts.seed_initial_materials_and_prices

# Deploy — Heroku app "scrap360" (Postgres in prod); Procfile release phase runs alembic upgrade head
git push heroku main
heroku run "python -m alembic upgrade head" -a scrap360   # manual fallback
```

There is no linter config. Two test layers, both worth running after touching money logic:

```bash
python -m pytest tests -q                # pytest suite (neteo engine)
python -m scripts.test_neteo             # guard suites, one per fase-2 area:
                                         # test_neteo, test_neteo_mutuo, test_reversas,
                                         # test_capital, test_tratos, test_bitacora,
                                         # test_factura_pdf
```

Also verify by importing the app (`python -c "from app.main import app; print('OK')"`) and exercising the affected web routes.

## Architecture (big picture)

- `app/main.py` — app factory, session middleware, login/logout, navbar badge middleware.
- `app/web/` — **the real product**: server-rendered routes. `admin.py` (~16k lines) holds the entire admin/visor surface incl. report data assembly; `worker.py` the worker note wizard; `files.py` Firebase uploads.
- `app/services/` — domain logic. `note_service.py` (~2.5k lines) owns the Nota state machine and every inventory/accounting side effect. Services own transactions (`db.commit()` inside service functions); sessions are `autoflush=False`, so `db.flush()` explicitly when ids are needed.
- `app/api/` — small JSON API behind the same session cookie. **Vestigial**: no template calls it, it lags the web feature set, and it has no role checks. Don't extend it for new features; follow the web-route pattern.
- `app/models/` — SQLAlchemy models; `app/models/__init__.py` is the metadata registration point (`app/db/base.py` imports it so alembic autogenerate sees everything).
- Roles: `super_admin` (everything), `admin` (scoped to branches via `admin_sucursales` M2M), `visor` (read-only), `trabajador` (own-branch note capture only). Role guards and `_get_allowed_sucursal_ids()` live in `app/web/admin.py`.

## Critical conventions (violating these corrupts data or breaks prod)

- **Nota state machine**: BORRADOR → EN_REVISION → APROBADA → CANCELADA. Inventory, accounting, folio, and cash side effects happen **only at approval**, in one transaction, via `note_service.approve_note`. Never mutate an approved note directly — every post-approval correction is a compensating record (devoluciones, ajustes, reversos), never a delete/update. See `docs/BUSINESS_RULES.md`.
- **Enum storage asymmetry**: `NotaEstado`, `ComisionarioNotaEstado`, `CorteCajaEstado`, `CorteCajaMovimientoTipo` are stored by **value** (uppercase, e.g. `"APROBADA"`); `UserRole`, `UserStatus`, `SucursalStatus`, `TipoOperacion`, `TipoCliente` are stored by **name**. Raw SQL and migrations must respect this.
- **Datetimes**: store naive UTC (`datetime.utcnow()`), display via the `datetime_local`/`date_local` Jinja filters (`app/core/datetime_utils.py`, tz `America/Mexico_City`). Never write local time to the DB.
- **Migrations must run on both SQLite (dev) and Postgres (prod)**. `render_as_batch` is NOT configured — use `op.batch_alter_table(...)` manually for ALTERs, as existing migrations do. Single linear head; Heroku applies `upgrade head` automatically on release.
- **kg_neto vs kg_real**: `kg_neto` is what the partner is paid for (price × kg_neto); `kg_real` is what enters inventory. They can diverge (super-admin only).
- **Prices are append-only versions** (`pricing_service.create_price_version`); notes pin `version_precio_id`. Never update a `TablaPrecio` row in place.
- **Payments**: undo is soft-delete-by-zeroing (`monto = 0` + comment tag) — aggregations must filter `monto > 0`. The `"Pago inicial"` comment prefix is load-bearing for `adjust_initial_payment`.
- **String-typed business keys**: `MovimientoContable.tipo` (`compra|venta|pago|reverso|reverso_pago|restauracion|restauracion_pago|ajuste`), `metodo_pago` (`efectivo|transferencia|cheque` — singular) vs `CuentaScrap360.tipo` (`efectivo|transferencia|cheques` — plural). `metodo_pago == "efectivo"` is the trigger that routes money through the corte de caja.
- **The UI follows `docs/DESIGN_SYSTEM.md`.** Read it before touching a template or stylesheet. In short: import from `app/templates/_macros.html` (`page_header`, `stat`, `badge`, `money`, `kg`, `icon`, `empty`, `empty_row`); style with the tokens in `app/static/css/scrap360.css` and never a raw hex; one `.btn-primary` per screen; a card never nests in a card; emoji are never icons; a `$0.00` renders muted, never red. `app/static/css/style.css` is the **legacy** sheet being retired — never add rules to it.
- **Tables need no bespoke work.** Wrap in `.table-responsive`, mark the naming column `data-title-col` and the phone-critical columns `data-mobile-primary`; `app/static/js/app.js` labels the cells and restacks each row into a record card below 768px. Never add "arrastra con el mouse" instructions.
- **The UI is `es-MX` and accents are mandatory**, including on uppercase labels. `python -m scripts.fix_accents --check app/templates` fails if any template regresses; drop the `--check` to repair. It never rewrites Jinja expressions.
- Verify UI changes by rendering, not by reading: `bash scripts/check_pages.sh <path>…` logs in and reports any non-200 or template error. Check 390px and 1440px before calling a screen done.
- Uploads go through `app/services/firebase_storage.py` and are made **public** by URL; folder conventions are in `docs/ARCHITECTURE.md`. Local fallback credentials live in `secrets/` (never commit new secrets).
