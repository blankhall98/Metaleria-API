# app/services/note_service.py
from datetime import date, datetime
from decimal import Decimal
from typing import Iterable, Sequence

from sqlalchemy.orm import Session

from app.models import (
    Nota,
    NotaEstado,
    NotaMaterial,
    Subpesaje,
    Material,
    TipoOperacion,
    TablaPrecio,
    TipoCliente,
)


def _sum_decimal(values: Iterable[Decimal | float | int]) -> Decimal:
    total = Decimal("0")
    for v in values:
        total += Decimal(str(v or 0))
    return total


def _recalc_material(nm: NotaMaterial) -> None:
    """Recalcula kg_bruto/neto/desc a partir de subpesajes si existen."""
    if nm.subpesajes:
        neto_sum = _sum_decimal(sp.peso_kg for sp in nm.subpesajes)
        desc_sum = _sum_decimal(getattr(sp, "descuento_kg", 0) for sp in nm.subpesajes)
        nm.kg_neto = neto_sum
        nm.kg_descuento = desc_sum
        nm.kg_bruto = neto_sum + desc_sum
    else:
        kg_desc = Decimal(str(nm.kg_descuento or 0))
        nm.kg_neto = Decimal(str(nm.kg_bruto or 0)) - kg_desc


def _recalc_totals(nota: Nota) -> None:
    """
    Recalcula totales de kg y monto de la nota a partir de sus materiales.
    No persiste ni flushea; el caller decide el momento.
    """
    for m in nota.materiales:
        _recalc_material(m)

    total_bruto = _sum_decimal(m.kg_bruto for m in nota.materiales)
    total_desc = _sum_decimal(m.kg_descuento for m in nota.materiales)
    total_neto = _sum_decimal(m.kg_neto for m in nota.materiales)
    total_monto = _sum_decimal(m.subtotal for m in nota.materiales if m.subtotal is not None)

    nota.total_kg_bruto = total_bruto
    nota.total_kg_descuento = total_desc
    nota.total_kg_neto = total_neto
    nota.total_monto = total_monto


def apply_prices(
    db: Session,
    nota: Nota,
) -> None:
    """
    Asigna precio_unitario/subtotal a cada material según la tabla de precios activa
    para la combinación (material, tipo_operacion, tipo_cliente).
    """
    for nm in nota.materiales:
        tipo_cli = nm.tipo_cliente or TipoCliente.regular
        tp = (
            db.query(TablaPrecio)
            .filter(
                TablaPrecio.material_id == nm.material_id,
                TablaPrecio.tipo_operacion == nota.tipo_operacion,
                TablaPrecio.tipo_cliente == tipo_cli,
                TablaPrecio.activo.is_(True),
            )
            .order_by(TablaPrecio.version.desc())
            .first()
        )
        if tp:
            nm.precio_unitario = tp.precio_por_unidad
            nm.version_precio_id = tp.id
            nm.subtotal = Decimal(str(tp.precio_por_unidad)) * Decimal(str(nm.kg_neto or 0))
        else:
            nm.precio_unitario = None
            nm.version_precio_id = None
            nm.subtotal = None
        db.add(nm)
    _recalc_totals(nota)


def create_draft_note(
    db: Session,
    *,
    sucursal_id: int,
    trabajador_id: int,
    tipo_operacion: TipoOperacion,
    materiales_payload: Sequence[dict],
    comentarios_trabajador: str | None = None,
    proveedor_id: int | None = None,
    cliente_id: int | None = None,
) -> Nota:
    """
    Crea una nota en BORRADOR con materiales y subpesajes opcionales.
    materiales_payload: lista de dicts con material_id, kg_bruto, kg_descuento, subpesajes=[{peso_kg, foto_url}]
    """
    nota = Nota(
        sucursal_id=sucursal_id,
        trabajador_id=trabajador_id,
        tipo_operacion=tipo_operacion,
        estado=NotaEstado.borrador,
        comentarios_trabajador=(comentarios_trabajador or "").strip() or None,
    )
    # Asignar partner si viene
    if tipo_operacion == TipoOperacion.compra:
        nota.proveedor_id = proveedor_id
    else:
        nota.cliente_id = cliente_id
    db.add(nota)
    db.flush()

    for idx, mp in enumerate(materiales_payload):
        material = db.get(Material, mp["material_id"])
        if not material:
            raise ValueError(f"Material {mp['material_id']} no existe")

        sub_list_payload = mp.get("subpesajes", []) or []
        if sub_list_payload:
            neto_sum = _sum_decimal(sp.get("peso_kg", 0) for sp in sub_list_payload)
            desc_sum = _sum_decimal(sp.get("descuento_kg", 0) for sp in sub_list_payload)
            kg_neto = neto_sum
            kg_descuento = desc_sum
            kg_bruto = neto_sum + desc_sum
        else:
            kg_bruto = Decimal(str(mp.get("kg_bruto", 0)))
            kg_descuento = Decimal(str(mp.get("kg_descuento", 0)))
            kg_neto = kg_bruto - kg_descuento

        tipo_cli_raw = mp.get("tipo_cliente")
        tipo_cli = None
        if tipo_cli_raw:
            try:
                tipo_cli = TipoCliente(tipo_cli_raw)
            except Exception:
                tipo_cli = None

        nm = NotaMaterial(
            nota_id=nota.id,
            material_id=material.id,
            kg_bruto=kg_bruto,
            kg_descuento=kg_descuento,
            kg_neto=kg_neto,
            orden=idx,
            evidencia_url=mp.get("evidencia_url") or None,
            tipo_cliente=tipo_cli,
        )
        db.add(nm)
        db.flush()

        for sp in sub_list_payload:
            sub = Subpesaje(
                nota_material_id=nm.id,
                peso_kg=Decimal(str(sp.get("peso_kg", 0))),
                descuento_kg=Decimal(str(sp.get("descuento_kg", 0))),
                foto_url=(sp.get("foto_url") or None),
            )
            db.add(sub)

    _recalc_totals(nota)
    apply_prices(db, nota)
    db.commit()
    db.refresh(nota)
    return nota


def update_state(
    db: Session,
    nota: Nota,
    *,
    new_state: NotaEstado,
    admin_id: int | None = None,
    comentarios_admin: str | None = None,
    fecha_caducidad_pago: date | None = None,
) -> Nota:
    """
    Transición de estados con campos adicionales para admins.
    """
    nota.estado = new_state
    if admin_id is not None:
        nota.admin_id = admin_id
    if comentarios_admin is not None:
        nota.comentarios_admin = comentarios_admin.strip() or None
    if fecha_caducidad_pago is not None:
        nota.fecha_caducidad_pago = fecha_caducidad_pago
    nota.updated_at = datetime.utcnow()

    _recalc_totals(nota)
    db.add(nota)
    db.commit()
    db.refresh(nota)
    return nota


def send_to_revision(
    db: Session,
    nota: Nota,
) -> Nota:
    """
    Pasa una nota de BORRADOR a EN_REVISION. Solo válido desde BORRADOR.
    """
    if nota.estado != NotaEstado.borrador:
        raise ValueError("Solo notas en borrador pueden enviarse a revisión.")
    # aplicar precios (default regular si no se especificó tipo_cliente)
    for nm in nota.materiales:
        if nm.tipo_cliente is None:
            nm.tipo_cliente = TipoCliente.regular
    apply_prices(db, nota)
    nota.estado = NotaEstado.en_revision
    nota.updated_at = datetime.utcnow()
    _recalc_totals(nota)
    db.add(nota)
    db.commit()
    db.refresh(nota)
    return nota


def attach_partner(
    db: Session,
    nota: Nota,
    *,
    proveedor_id: int | None = None,
    cliente_id: int | None = None,
) -> Nota:
    """
    Asigna proveedor o cliente según tipo_operacion. Mantiene consistencia.
    """
    if nota.tipo_operacion == TipoOperacion.compra:
        nota.proveedor_id = proveedor_id
        nota.cliente_id = None
    else:
        nota.cliente_id = cliente_id
        nota.proveedor_id = None
    db.add(nota)
    db.commit()
    db.refresh(nota)
    return nota


def set_tipo_cliente_and_prices(
    db: Session,
    nota: Nota,
    tipo_cliente_map: dict[int, TipoCliente],
) -> Nota:
    """
    Actualiza el tipo_cliente por material y recalcula precios/subtotales.
    """
    for nm in nota.materiales:
        if nm.id in tipo_cliente_map:
            nm.tipo_cliente = tipo_cliente_map[nm.id]
            db.add(nm)
    apply_prices(db, nota)
    db.commit()
    db.refresh(nota)
    return nota
