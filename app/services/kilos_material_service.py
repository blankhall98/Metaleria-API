"""Kilos por material: agregaciones de solo lectura sobre las líneas de notas.

Solicitudes de la administradora (sep-2026, `docs/SOLICITUDES_SEP_2026.md`):

- Punto 1: total de kilos por material de un socio en un período
  (`kg_por_material`), para la tarjeta del expediente.
- Punto 2: ranking de proveedores de mayor a menor por kilos de un material
  (`ranking_por_material`), para el reporte del grupo Reportes.

Reglas compartidas: solo notas APROBADA; la base es `kg_neto` (lo que se paga
al socio); el rango se aplica sobre `Nota.created_at` como intervalo semiabierto
`[start_utc, end_utc)`; las devoluciones parciales ya viven reescritas en la
línea, así que no se resta nada aparte; las cancelaciones totales salen solas
del filtro de estado.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.models import Cliente, Material, Nota, NotaEstado, NotaMaterial, Proveedor, Sucursal, TipoOperacion

_KG = Decimal("0.001")
_MXN = Decimal("0.01")
_PCT = Decimal("0.1")
_INTERNAL_PREFIX = "Sucursal "


def _dec(value, quantum: Decimal) -> Decimal:
    return Decimal(str(value or 0)).quantize(quantum)


def _apply_note_filters(
    query,
    *,
    tipo_operacion: TipoOperacion,
    start_utc: datetime | None,
    end_utc: datetime | None,
    allowed_suc_ids: list[int] | None,
    sucursal_id: int | None = None,
):
    query = query.filter(
        Nota.estado == NotaEstado.aprobada,
        Nota.tipo_operacion == tipo_operacion,
    )
    if start_utc is not None:
        query = query.filter(Nota.created_at >= start_utc)
    if end_utc is not None:
        query = query.filter(Nota.created_at < end_utc)
    if sucursal_id:
        query = query.filter(Nota.sucursal_id == sucursal_id)
    elif allowed_suc_ids is not None:
        query = query.filter(Nota.sucursal_id.in_(allowed_suc_ids))
    return query


def kg_por_material(
    db: Session,
    *,
    tipo_operacion: TipoOperacion,
    proveedor_id: int | None = None,
    cliente_id: int | None = None,
    start_utc: datetime | None = None,
    end_utc: datetime | None = None,
    allowed_suc_ids: list[int] | None = None,
) -> list[dict]:
    """Kilos, notas e importe por material para un socio.

    Se pueden pasar las dos identidades de un par vinculado (proveedor y
    cliente) y las notas de ambas se suman. Devuelve filas ordenadas por kilos
    de mayor a menor: material_id, material_nombre, kg, notas, importe.
    """
    partner_filters = []
    if proveedor_id:
        partner_filters.append(Nota.proveedor_id == proveedor_id)
    if cliente_id:
        partner_filters.append(Nota.cliente_id == cliente_id)
    if not partner_filters:
        return []

    query = (
        db.query(
            NotaMaterial.material_id,
            Material.nombre,
            func.coalesce(func.sum(NotaMaterial.kg_neto), 0),
            func.count(func.distinct(Nota.id)),
            func.coalesce(func.sum(NotaMaterial.subtotal), 0),
        )
        .join(Nota, Nota.id == NotaMaterial.nota_id)
        .join(Material, Material.id == NotaMaterial.material_id)
        .filter(or_(*partner_filters))
    )
    query = _apply_note_filters(
        query,
        tipo_operacion=tipo_operacion,
        start_utc=start_utc,
        end_utc=end_utc,
        allowed_suc_ids=allowed_suc_ids,
    )
    query = query.group_by(NotaMaterial.material_id, Material.nombre)

    rows = [
        {
            "material_id": material_id,
            "material_nombre": nombre,
            "kg": _dec(kg, _KG),
            "notas": int(notas or 0),
            "importe": _dec(importe, _MXN),
        }
        for material_id, nombre, kg, notas, importe in query.all()
    ]
    rows.sort(key=lambda r: (-r["kg"], r["material_nombre"].lower()))
    return rows


def _internal_partner_names(db: Session) -> set[str]:
    return {name.strip() for (name,) in db.query(Sucursal.nombre).all() if name}


def _is_internal_partner(nombre: str | None, sucursal_names: set[str]) -> bool:
    if not nombre or not nombre.startswith(_INTERNAL_PREFIX):
        return False
    return nombre[len(_INTERNAL_PREFIX):].strip() in sucursal_names


def _ranking_rows(
    db: Session,
    *,
    partner_col,
    partner_model,
    partner_type: str,
    material_id: int,
    tipo_operacion: TipoOperacion,
    start_utc: datetime | None,
    end_utc: datetime | None,
    allowed_suc_ids: list[int] | None,
    sucursal_id: int | None,
    sucursal_names: set[str],
) -> list[dict]:
    query = (
        db.query(
            partner_col,
            partner_model.nombre_completo,
            func.coalesce(func.sum(NotaMaterial.kg_neto), 0),
            func.count(func.distinct(Nota.id)),
            func.coalesce(func.sum(NotaMaterial.subtotal), 0),
        )
        .join(Nota, Nota.id == NotaMaterial.nota_id)
        .join(partner_model, partner_model.id == partner_col)
        .filter(NotaMaterial.material_id == material_id)
    )
    query = _apply_note_filters(
        query,
        tipo_operacion=tipo_operacion,
        start_utc=start_utc,
        end_utc=end_utc,
        allowed_suc_ids=allowed_suc_ids,
        sucursal_id=sucursal_id,
    )
    query = query.group_by(partner_col, partner_model.nombre_completo)

    rows: list[dict] = []
    for partner_id, nombre, kg, notas, importe in query.all():
        if _is_internal_partner(nombre, sucursal_names):
            continue
        rows.append(
            {
                "partner_type": partner_type,
                "partner_id": partner_id,
                "nombre": nombre,
                "kg": _dec(kg, _KG),
                "notas": int(notas or 0),
                "importe": _dec(importe, _MXN),
            }
        )
    return rows


def _merge_sales_rows(db: Session, cliente_rows: list[dict], proveedor_rows: list[dict]) -> list[dict]:
    """Ventas: las directas a un proveedor vinculado se funden en su cliente
    (un solo socio comercial); un proveedor con ventas y sin vínculo queda
    como socio propio."""
    merged: dict[tuple[str, int], dict] = {("cliente", r["partner_id"]): r for r in cliente_rows}
    prov_ids = [r["partner_id"] for r in proveedor_rows]
    linked: dict[int, int] = {}
    if prov_ids:
        linked = {
            pid: cid
            for pid, cid in db.query(Proveedor.id, Proveedor.linked_cliente_id)
            .filter(Proveedor.id.in_(prov_ids), Proveedor.linked_cliente_id.isnot(None))
            .all()
        }
    missing = {cid for cid in linked.values() if ("cliente", cid) not in merged}
    cliente_names: dict[int, str] = {}
    if missing:
        cliente_names = {
            cid: nombre
            for cid, nombre in db.query(Cliente.id, Cliente.nombre_completo).filter(Cliente.id.in_(missing)).all()
        }
    for row in proveedor_rows:
        cliente_id = linked.get(row["partner_id"])
        key = ("cliente", cliente_id) if cliente_id else ("proveedor", row["partner_id"])
        if key in merged:
            target = merged[key]
            target["kg"] += row["kg"]
            target["notas"] += row["notas"]
            target["importe"] += row["importe"]
        elif cliente_id:
            merged[key] = {
                **row,
                "partner_type": "cliente",
                "partner_id": cliente_id,
                "nombre": cliente_names.get(cliente_id, row["nombre"]),
            }
        else:
            merged[key] = row
    return list(merged.values())


def ranking_por_material(
    db: Session,
    *,
    material_id: int,
    start_utc: datetime | None,
    end_utc: datetime | None,
    allowed_suc_ids: list[int] | None = None,
    sucursal_id: int | None = None,
    tipo_operacion: TipoOperacion = TipoOperacion.compra,
) -> dict:
    """Socios de mayor a menor por kilos de un material en un período.

    Con `compra` los socios son proveedores. Con `venta` son clientes, más las
    ventas directas a proveedores (`permite_ventas`): si el proveedor está
    vinculado a un cliente sus kilos se funden en él; si no, aparece como socio
    propio. Los socios internos "Sucursal X" (traspasos) se excluyen. Devuelve
    rows (partner_type, partner_id, nombre, kg, notas, importe, pct) más
    total_kg, total_importe y total_notas.
    """
    common = {
        "material_id": material_id,
        "tipo_operacion": tipo_operacion,
        "start_utc": start_utc,
        "end_utc": end_utc,
        "allowed_suc_ids": allowed_suc_ids,
        "sucursal_id": sucursal_id,
        "sucursal_names": _internal_partner_names(db),
    }
    proveedor_rows = _ranking_rows(
        db, partner_col=Nota.proveedor_id, partner_model=Proveedor, partner_type="proveedor", **common
    )
    if tipo_operacion == TipoOperacion.compra:
        rows = proveedor_rows
    else:
        cliente_rows = _ranking_rows(
            db, partner_col=Nota.cliente_id, partner_model=Cliente, partner_type="cliente", **common
        )
        rows = _merge_sales_rows(db, cliente_rows, proveedor_rows)

    total_kg = sum((r["kg"] for r in rows), Decimal("0"))
    total_importe = sum((r["importe"] for r in rows), Decimal("0"))
    total_notas = sum(r["notas"] for r in rows)
    for row in rows:
        row["pct"] = (
            (row["kg"] / total_kg * Decimal("100")).quantize(_PCT)
            if total_kg > 0
            else Decimal("0.0")
        )
    rows.sort(key=lambda r: (-r["kg"], r["nombre"].lower()))
    return {
        "rows": rows,
        "total_kg": total_kg,
        "total_importe": total_importe,
        "total_notas": total_notas,
    }


def lineas_por_nota(db: Session, notas: list[Nota]) -> dict[int, list[dict]]:
    """Líneas por material de cada nota, para el desglose del estado de cuenta
    (punto 3). Una consulta para todas las notas; orden de catálogo
    (`orden_display`) como en el PDF de la nota. Si la nota lleva IVA se agrega
    un renglón "IVA n %" para que los subtotales sumen el cargo de la nota.

    Cada línea: material, kg (kg_neto), precio (precio_unitario, puede ser
    None), subtotal. El renglón de IVA lleva kg y precio en None.
    """
    note_ids = [nota.id for nota in notas if nota.id]
    if not note_ids:
        return {}

    por_nota: dict[int, list[NotaMaterial]] = defaultdict(list)
    materiales = (
        db.query(NotaMaterial)
        .options(joinedload(NotaMaterial.material))
        .filter(NotaMaterial.nota_id.in_(note_ids))
        .all()
    )
    for nm in materiales:
        por_nota[nm.nota_id].append(nm)

    result: dict[int, list[dict]] = {}
    for nota in notas:
        items = sorted(por_nota.get(nota.id, []), key=lambda nm: (nm.display_order, nm.orden or 0, nm.id))
        lineas = [
            {
                "material": nm.material.nombre if nm.material else "Sin definir",
                "kg": _dec(nm.kg_neto, _KG),
                "precio": Decimal(str(nm.precio_unitario)) if nm.precio_unitario is not None else None,
                "subtotal": _dec(nm.subtotal, _MXN),
            }
            for nm in items
        ]
        iva_monto = Decimal(str(nota.iva_monto or 0))
        if nota.iva_incluido and iva_monto > 0:
            pct = Decimal(str(nota.iva_porcentaje or 0)).normalize()
            lineas.append(
                {
                    "material": f"IVA {pct:f} %",
                    "kg": None,
                    "precio": None,
                    "subtotal": _dec(iva_monto, _MXN),
                }
            )
        if lineas:
            result[nota.id] = lineas
    return result
