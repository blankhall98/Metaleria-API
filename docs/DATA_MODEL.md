# Data Model

All models in `app/models/`. `app/models/__init__.py` re-exports everything and is imported by `app/db/base.py`, which is how alembic autogenerate sees the metadata.

## Entity map (by domain)

### Tenancy & users
- **`sucursales`** (`Sucursal`) — branch. `nombre` unique, `logo_url` (invoice branding), `estado` (`activa|inactiva`). The branch is the tenancy unit: nearly every table carries a `sucursal_id`.
- **`users`** (`User`) — `rol` (`super_admin | admin | visor | trabajador`), `estado` (`activo|inactivo`), `sucursal_id` (nullable in DB, **required for trabajador** by business rule), `super_admin_original` (protected bootstrap account; the app refuses to leave zero super_admins).
- **`admin_sucursales`** (association table) — which branches an `admin` manages. Admin scope comes from this M2M, **not** from `users.sucursal_id`.

### Catalog & pricing
- **`materiales`** (`Material`) — `nombre` unique, `unidad_medida` (informational, everything is kg), `activo`, `orden_display` (UI/PDF sort, default 999).
- **`tablas_precios`** (`TablaPrecio`) — versioned prices. Unique on `(material_id, tipo_operacion, tipo_cliente, version)`. `precio_por_unidad Numeric(10,5)`. Active row = `activo=True, vigente_hasta=None`. Append-only: new price ⇒ new version row (see BUSINESS_RULES).
- **`price_change_logs`** (`PriceChangeLog`) — audit of every price change (old/new price+version, user, source `web|api|seed`).

### Partners
- **`proveedores`** / **`clientes`** — branch-scoped (`sucursal_id` NOT NULL). `placas` globally unique; extra vehicles in **`proveedor_placas`** / **`cliente_placas`** (plate unique across the whole table). `proveedores.permite_ventas` allows a supplier to appear on sale notes ("ventas directas"). `linked_cliente_id` / `linked_proveedor_id` (unique, 1:1) link the same real person's two identities — reports net their balances ("neteo").
- **`ajustes_saldo_partner`** (`AjusteSaldoPartner`) — manual partner-level balance adjustment. Polymorphic by convention: `partner_type` (`"cliente"|"proveedor"`) + `partner_id` (no FK).

### Notas (the core aggregate) — `app/models/note.py`
- **`notas`** (`Nota`) — one truck/visit. Key fields:
  - `tipo_operacion` (`compra|venta`; enum also has `comision` but notas reject it)
  - `estado` (`NotaEstado`: `BORRADOR|EN_REVISION|APROBADA|CANCELADA`, stored uppercase, no DB default)
  - **two branch FKs**: `sucursal_id` (issuing branch: folio, permissions) and `inventario_sucursal_id` (branch whose stock moves; falls back to `sucursal_id`)
  - counterparty: `proveedor_id` XOR `cliente_id` (not DB-enforced)
  - kg totals `Numeric(12,3)`: `total_kg_bruto/descuento/neto/real`
  - money `Numeric(12,2)`: `total_monto`, `monto_pagado`; IVA: `iva_incluido` (means "IVA added on top"), `iva_porcentaje` (default 16.00), `iva_monto`
  - `folio_seq` — per (sucursal, tipo_operacion) sequence, assigned **at approval**, cleared on return-to-draft
  - payment header: `metodo_pago`, `numero_cheque`, `cuenta_financiera_id`→`cuentas`, `cuenta_scrap360_id`→`cuentas_scrap360`, `fecha_caducidad_pago` (Date)
  - `factura_url`/`factura_generada_at` (cached invoice; cleared on cancel), `nota_origen_id` (self-FK, links transfer pairs)
- **`nota_materiales`** (`NotaMaterial`) — line per material: `kg_bruto/descuento/neto/real`, `precio_unitario Numeric(12,5)` + `subtotal` (null until priced), `version_precio_id`→`tablas_precios` (pins the exact price version; cleared by manual overrides), `tipo_cliente` (price tier), `evidencia_url`, `orden`.
- **`subpesajes`** (`Subpesaje`) — individual weighings: `peso_kg`, `descuento_kg`, `foto_url`. Roll up into the line's kg.
- **`nota_originales`** (`NotaOriginal`) — one-per-note JSON snapshot of the worker's submission, taken at send-to-revision (anti-tampering record).
- **`nota_evidencias_extra`** — extra note-level photos.
- **`nota_pagos`** (`NotaPago`) — payments: `monto`, `metodo_pago`, `cuenta_id`, `cuenta_scrap360_id`, `caja_sucursal_id` (whose cash drawer). Undo = soft-delete-by-zeroing (`monto=0` + comment tag).
- **`nota_ajustes_saldo`** (`NotaAjusteSaldo`) — note balance deltas with before/after snapshots and one-shot reversal fields (`reversal_of_id`, `reverted_at`, `reverted_by_user_id`, `comentario_reversion`).
- **`nota_devoluciones_totales`** / **`nota_devoluciones_parciales`** (+`_lineas`, +`_aplicaciones`) — total/partial returns. Partial-return lines freeze the return price and allocate returned kg down to specific subpesajes (LIFO). Same reversal-trio pattern.

### Inventory & accounting — `app/models/inventory.py`
- **`inventarios`** (`Inventario`) — `stock_actual Numeric(12,3)` per (sucursal, material). **No unique constraint on the pair** — uniqueness only via the service's get-or-create.
- **`inventario_movimientos`** (`InventarioMovimiento`) — append-only ledger with `saldo_resultante`. `tipo` is a **plain string**: `compra|venta|ajuste|conversion`. ⚠️ `cantidad_kg` is **absolute** for `compra|venta` but **signed** for `ajuste|conversion` — consumers must branch on `tipo`.
- **`movimientos_contables`** (`MovimientoContable`) — money ledger. `tipo` string: `compra|venta|pago|reverso|reverso_pago|restauracion|restauracion_pago|ajuste`. Two branch FKs (`sucursal_id`, `caja_sucursal_id`). Sign conventions applied at report time (compra negative, venta positive; reversals flip).
- **`inventario_ajustes_manuales`** (`InventarioAjusteManual`) — audited manual stock corrections with before/after and one-shot reversal.
- **`inventario_valor_precios`** (`InventarioValorPrecio`) — per (sucursal, material) reference price for stock valuation reports (independent of buy/sell prices).

### Accounts (two deliberately separate concepts) — `app/models/account.py`
- **`cuentas`** (`Cuenta`) — *counterparty* bank details (CLABE etc.), owned by exactly one of `sucursal_id|cliente_id|proveedor_id|comisionario_id` (convention only, no constraint). Property `display_label` masks the number (`****1234`).
- **`cuentas_scrap360`** (`CuentaScrap360`) — the *company's own* treasury accounts with a denormalized running `saldo_actual`, `tipo` string `efectivo|transferencia|cheques` (**plural cheques**), shared across branches via `cuentas_scrap360_sucursales`. Ledger: **`cuentas_scrap360_movimientos`** (`tipo` `ingreso|egreso|ajuste`, `saldo_resultante` snapshot).

### Comisionarios — `app/models/comision.py` (parallel, lighter pipeline)
- **`comisionarios`** — commission agents, branch-scoped.
- **`comisionario_notas`** — settlement notes, **born `APROBADA`** (no draft/review), net-kg only (no subpesajes/evidence), **no inventory or contable impact**.
- **`comisionario_nota_materiales`** — `kg_neto × precio_por_kg = subtotal`.
- **`comisionario_pagos`** — payments; comisionario's own `Cuenta` required for bank methods; Scrap360 movements are egreso-only.

### Conversions — `app/models/conversion.py`
- **`conversiones_material`** (`ConversionMaterial`) — transform origin material kg → destination material kg at one branch. Quantities are independent (yield/loss allowed; mass not conserved by the system). Inventory-only (movements `tipo="conversion"`), no accounting.
- **`conversiones_material_reversiones`** — one-shot reversal implemented as a mirror conversion; both `conversion_id` and `reversal_conversion_id` unique.

### Corte de caja — `app/models/cash.py`
- **`cortes_caja`** (`CorteCaja`) — daily cash session, **unique per (sucursal, fecha)**. `estado` `ABIERTO|CERRADO`, `saldo_inicial/calculado/cierre`, `diferencia`, `motivo_diferencia`, `opened_at/closed_at`.
- **`corte_caja_gastos`** — petty-cash expenses (categorías `CAJA_CHICA|SERVICIOS|VIATICOS|MANTENIMIENTO|ADMINISTRATIVO|OTRO`).
- **`corte_caja_movimientos`** — manual drawer movements, `tipo` `INGRESO|EGRESO|RETIRO|DEPOSITO` (sign: INGRESO/DEPOSITO +, EGRESO/RETIRO −), categorías incl. `DOTACION_EFECTIVO`, `SOBRANTE_VIATICOS`, `SOBRANTE_GASTOS`.
- **`corte_caja_denominaciones`** — bill/coin count at close (`valor × cantidad`; valor ≥ 20 renders as BILLETES, else MONEDAS).

## Enum storage — the footgun

`values_callable` (stored by **VALUE**, uppercase): `NotaEstado`, `ComisionarioNotaEstado`, `CorteCajaEstado`, `CorteCajaMovimientoTipo`.
Stored by **NAME** (lowercase member names): `UserRole`, `UserStatus`, `SucursalStatus`, `TipoOperacion`, `TipoCliente`.
Raw SQL, migrations, and seed data must match the right convention per column.

## Declared constraints (the complete list)

- `uq_tabla_precio_material_tipo_version` (material, tipo_operacion, tipo_cliente, version)
- `uq_corte_caja_sucursal_fecha` (sucursal, fecha)
- Unique columns: `sucursales.nombre`, `materiales.nombre`, `users.username`, `proveedores.placas`, `clientes.placas`, `proveedor_placas.placa`, `cliente_placas.placa`, `proveedores.linked_cliente_id`, `clientes.linked_proveedor_id`, `nota_originales.nota_id`, `conversiones_material_reversiones.conversion_id` and `.reversal_conversion_id`.

Everything else (Inventario pair uniqueness, proveedor-XOR-cliente on notas, single-owner on cuentas, partner_type/partner_id integrity, all the string `tipo` vocabularies) is enforced **only in service code**.

## Numeric precision policy

Weights `Numeric(12,3)` (grams); money `Numeric(12,2)`; unit prices `Numeric(10,5)`/`Numeric(12,5)` (sub-centavo per kg); IVA pct `Numeric(5,2)`.

## Timestamps

`created_at = utcnow`, `updated_at = utcnow + onupdate` on mutable headers. All naive UTC.
