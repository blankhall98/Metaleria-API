# Business Rules

The domain invariants. Almost all of this lives in `app/services/note_service.py` (~2.5k lines); read this file before changing anything there. Money and stock are only ever corrected with **compensating records** — nothing is deleted or silently updated.

## 1. Nota lifecycle (`NotaEstado`)

```
BORRADOR ──send_to_revision──▶ EN_REVISION ──approve_note──▶ APROBADA ──cancel_approved_note──▶ CANCELADA
   ▲                                │                            │                                  │
   └────────"devolver a borrador"───┘                            └──────reverse_total_return────────┘
```

### Creation (`create_draft_note`)
- `tipo_operacion` must be `compra` or `venta`.
- Workers are **forced** to their own `sucursal_id`; passing another raises.
- Counterparty rules (`_validate_partner_for_nota_sucursal`):
  - **compra**: cliente forbidden; proveedor optional but must be activo and same-sucursal.
  - **venta**: exactly one counterparty (cliente XOR proveedor); a proveedor only if `permite_ventas=True`.
- kg from subpesajes: `bruto=Σpeso`, `descuento=Σdesc`, `neto=bruto−desc` (floored at 0); flat entry requires `descuento ≤ bruto`. `kg_real` defaults to `kg_neto`.
- Draft has **no** folio, inventory, or accounting impact.

### Send to revision (`send_to_revision`)
Only from BORRADOR. Defaults `tipo_cliente` to `regular`, applies prices, recalcs totals, and stores the immutable **`NotaOriginal` JSON snapshot** of the worker's submission (audit/anti-tampering).

### Approval (`approve_note`) — the central transaction (single commit)
1. From EN_REVISION or BORRADOR only; requires compra/venta.
2. Admin may set: per-line `tipo_cliente`, IVA (`iva_incluido`, pct 0–100, default 16), price overrides, kg_real overrides, payment method + cuentas, initial payment, and (compra only) a different `inventario_sucursal_id` (venta is forced to the note's own branch).
3. **Stock is NOT enforced for a normal venta** (`allow_negative=True`) — sales can drive stock negative. Only transfers hard-block.
4. **Folio assigned here**: `max(folio_seq)+1` per `(sucursal, tipo_operacion)` → folio order = approval order. Format: `"{sucursal_id:02d}_{C|V}_{seq}"`. Returning to draft clears folio (it will be reused).
5. Side effects: one `InventarioMovimiento` per line (compra +kg_real, venta −kg_real, on the **inventario** branch), one base `MovimientoContable` (`tipo="compra"|"venta"`, guarded against double-posting on re-approval), optional initial payment.

### Payments (`add_payment`)
- Only on APROBADA. `0 < monto ≤ saldo_pendiente` (effective saldo — see §3).
- Bank methods (`transferencia|cheque`) require a `Cuenta` linked to the note's sucursal or partner. Cash requires `caja_sucursal_id` (defaults to the note's branch) — **`metodo_pago == "efectivo"` is what routes the money through the corte de caja**.
- Optional `CuentaScrap360` must match branch and method↔type: `efectivo`⇔`efectivo`, `transferencia|cheque`⇔`transferencia|cheques` (plural!). Scrap360 direction: **compra → egreso, venta → ingreso**.
- Effects: `NotaPago` row, `monto_pagado += monto`, `MovimientoContable(tipo="pago")`, Scrap360 movement.

### Undo payment (`undo_payment`)
Soft-delete-by-zeroing: `pago.monto = 0` + comment tag `"DESHECHO {fecha} | monto original {x}"`; posts `tipo="reverso_pago"` and mirrors Scrap360. **Any aggregation over pagos must filter `monto > 0`.**

### Initial-payment adjustment (`adjust_initial_payment`)
Targets pagos whose comment starts with **`"Pago inicial"`** (string convention, load-bearing). Increase → `add_payment("Pago inicial (ajuste)")`; decrease → walks initial payments newest-first with reversos.

### Cancellation = total return (`cancel_approved_note`)
Only from APROBADA. Reverses inventory (compra −, venta +; **blocks if stock would go negative** — a compra whose material was already sold can't be cancelled), posts `reverso` + `reverso_pago` movements, mirrors Scrap360, clears `factura_url`. `monto_pagado` survives (needed for un-cancel). One-shot un-cancel: `reverse_total_return` (`restauracion`/`restauracion_pago`).

### Partial return (`partial_return_approved_note`)
- Per material: `kg_devolucion ≤ kg_neto`, `monto ≤ subtotal`; kg_real prorated; consumed **LIFO over subpesajes by increasing their `descuento_kg`** (traced in `..._aplicaciones`).
- Line's `precio_unitario` re-derived as `subtotal/kg_neto` and **`version_precio_id` cleared** (line decoupled from price table).
- Inventory compra − / venta + (negative stock blocked); one `MovimientoContable(tipo="ajuste")` (sign flipped for compra).
- **No automatic refund** — overpayment stays as saldo a favor. Per-line one-shot reversal validates the descuentos haven't been edited since.

### Super-admin edit (`edit_note_by_superadmin`)
Any state, but inventory/contable deltas post only when APROBADA. Re-prices a line only if `tipo_cliente` changed or price was null. kg_real heuristic: explicit override wins; else tracks kg_neto only if it previously equalled it.

### Transfers (`create_transfer_notes`)
Origin ≠ destination. Creates **two already-APROBADA notes** in one transaction (venta at origin + compra at destination, cross-referenced via comments and `nota_origen_id`), each with its own folio. Prices from payload (no price table). **Stock hard-blocked** (`allow_negative=False`) — the only such path. Inventory movements yes; **no contable movements** (a transfer is not revenue/cost).

## 2. Pricing

- Resolution (`apply_prices`): active `TablaPrecio` for `(material, tipo_operacion, tipo_cliente or regular)`, highest version. Miss → price/subtotal/version all `None` (line contributes 0 and shows blank).
- Price is computed on **`kg_neto`** (payment basis); `kg_real` is inventory-only.
- Versioning (`pricing_service.create_price_version`): append-only — deactivate old rows (`activo=False`, `vigente_hasta=now`), insert `version+1`, write `PriceChangeLog`. Notes pin `version_precio_id`, so history is immune to re-pricing.
- Manual overrides (unit price or subtotal) clear `version_precio_id`. Negative prices rejected.

## 3. Balances (the canonical formula)

```
saldo            = total_monto − monto_pagado + Σ NotaAjusteSaldo.monto_delta
saldo_pendiente  = max(saldo, 0)        # owed
saldo_favor      = max(−saldo, 0)       # credit in partner's favor
total_efectivo   = total_monto + ajuste_delta
```
(`note_service._effective_note_balance_snapshot`; the per-note-list bulk version is `note_service._get_note_balance_adjustment_totals_map`, aliased in `app/web/admin.py` for its call sites.)

Partner-level grouping (contabilidad/capital/saldos reports): linked proveedor+cliente pairs collapse into one group; per approved note **compra adds saldo (we owe them), venta subtracts (they owe us)**; `AjusteSaldoPartner` same signs; then classified into *por pagar a proveedores* / *por cobrar a clientes* / *saldo a favor*. `neto_por_pagar` also includes `comisiones_pendientes`.

⚠️ `invoice_service` computes saldo as `total − pagado` only (ignores NotaAjusteSaldo) — invoice saldo can differ from UI saldo. See KNOWN_ISSUES #4.

## 4. IVA

`iva_incluido=True` means IVA is **added on top**: `total = base + base×pct/100` (naming trap — it does not mean "price includes IVA"). Default pct 16.00 (Mexico). Applied only when incluido && pct>0 && base>0.

## 5. Inventory

- `kg_real` (fallback `kg_neto`) is what moves stock. Direction: compra +, venta −, on `inventario_sucursal_id` (fallback `sucursal_id`).
- Movement ledger `InventarioMovimiento` carries `saldo_resultante`. ⚠️ `cantidad_kg` is **absolute** for `compra|venta`, **signed** for `ajuste|conversion`.
- Stock-negative blocking summary: blocked in transfers, conversions, cancellations, partial returns/reversals, and super-admin edits of compras — **not blocked at normal venta approval**.
- Manual adjustments (`ajustar_stock`): inventory movement + `InventarioAjusteManual` audit (before/after) + a **zero-amount** `MovimientoContable(tipo="ajuste", monto=0)` so it appears in the money ledger without moving money. One-shot reversal.
- Conversions: origin stock must cover `cantidad_origen`; destination quantity independent (yield loss OK); two movements (`… | Salida` negative, `… | Entrada` positive); reversal = mirror conversion, one-shot. No accounting.

## 6. Comisionarios

- `ComisionarioNota` born **APROBADA** (no draft/review), net kg × price only, **no inventory, no MovimientoContable** — they surface in reports only as "comisiones pendientes" (`Σ max(total − pagado, 0)`).
- Payments: bank methods require a `Cuenta` **owned by that comisionario** (stricter than notes); Scrap360 movements are **egreso-only** and have no reversal path.

## 7. Corte de caja (daily cash reconciliation)

- One per (sucursal, fecha). Open (admin+): `saldo_inicial` pre-filled from the **previous closed corte's `saldo_cierre`** (cash-chain continuity). Close: **super_admin only**.
- Window: `opened_at` → `closed_at`/now. Day boundaries via `_corte_local_day_bounds` (the one tz-correct range in the app).
- Automatic cash lines: `MovimientoContable` with `tipo ∈ {pago, reverso_pago, restauracion_pago}` AND `metodo_pago == "efectivo"`, matched by `caja_sucursal_id` (fallback `sucursal_id`). Signs: venta pago +, compra pago −, reversals flip.
- Manual movements: INGRESO/DEPOSITO +, EGRESO/RETIRO −.
- Formula:
  ```
  saldo_calculado = saldo_inicial + cash_neto + manual_neto − gastos_total
  diferencia      = Σ(denominación.valor × cantidad) − saldo_calculado
  ```
- Closing requires `motivo_diferencia` when `diferencia ≠ 0`, persists everything, and **auto-opens the next day's corte** with `saldo_inicial = saldo_cierre`.

## 8. Reports (who builds what)

| Report | Data assembled in | Rendered by |
|---|---|---|
| Contabilidad (movimientos, saldos, netos) | `contabilidad_report_service.build_report_data` | same service (xls/pdf) |
| Estado de cuenta partner / asistencias | `app/web/admin.py::_build_partner_statement_report` etc. | `partner_report_service` |
| Estado de cuenta Scrap360 | `scrap360_account_report_service` | same |
| Corte de caja | `app/web/admin.py` (window math) | `corte_caja_report_service` |
| Factura (orden de compra/venta) | `invoice_service.build_invoice_pdf` | same; cached in Firebase for super_admin, cache invalidated by `updated_at`/cancel |

Movement sign convention at report time (`_movimiento_monto_firmado`): **compra negative, venta positive**; `pago` follows the note's operation; `reverso*` flips; `restauracion*` restores.

All PDFs are hand-rolled (latin-1, `errors="ignore"` — chars outside latin-1 silently dropped); Excel exports are SpreadsheetML 2003 XML with a `.xls` extension (Excel opens with a format warning).
