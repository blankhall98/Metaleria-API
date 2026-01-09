# app/services/contabilidad_report_service.py
from __future__ import annotations

import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models import (
    MovimientoContable,
    Nota,
    NotaEstado,
    Proveedor,
    Cliente,
    Sucursal,
    TipoOperacion,
)
from app.services import note_service


def _safe_decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _format_money(value: Decimal) -> str:
    try:
        sign = "-" if value < 0 else ""
        return f"{sign}${abs(value):,.2f}"
    except (ValueError, InvalidOperation):
        return "$0.00"


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _safe_filename(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value or "").strip("-")
    return slug or "reporte"


def build_report_data(
    db: Session,
    *,
    sucursal_id: int | None,
    date_from: date | None,
    date_to: date | None,
    allowed_suc_ids: list[int] | None,
) -> dict:
    sucursal = db.get(Sucursal, sucursal_id) if sucursal_id else None
    sucursal_label = sucursal.nombre if sucursal else "Todas"

    query = db.query(MovimientoContable)
    if allowed_suc_ids is not None:
        if sucursal_id:
            query = query.filter(MovimientoContable.sucursal_id == sucursal_id)
        else:
            query = query.filter(MovimientoContable.sucursal_id.in_(allowed_suc_ids))
    elif sucursal_id:
        query = query.filter(MovimientoContable.sucursal_id == sucursal_id)

    if date_from:
        dt_from = datetime.strptime(date_from.isoformat(), "%Y-%m-%d")
        query = query.filter(MovimientoContable.created_at >= dt_from)
    if date_to:
        dt_to = datetime.strptime(date_to.isoformat(), "%Y-%m-%d")
        query = query.filter(MovimientoContable.created_at <= dt_to)

    movimientos = query.order_by(MovimientoContable.created_at.desc()).all()
    nota_ids = {m.nota_id for m in movimientos if m.nota_id}
    notas_map = {}
    if nota_ids:
        notas_map = {n.id: n for n in db.query(Nota).filter(Nota.id.in_(nota_ids)).all()}

    sucursal_ids = {m.sucursal_id for m in movimientos if m.sucursal_id}
    sucursal_map = {}
    if sucursal_ids:
        sucursal_map = {
            s.id: s.nombre
            for s in db.query(Sucursal).filter(Sucursal.id.in_(sucursal_ids)).all()
        }

    total_ventas = Decimal("0")
    total_compras = Decimal("0")
    total_pagos_venta = Decimal("0")
    total_pagos_compra = Decimal("0")
    total_ajustes = Decimal("0")
    total_reversos = Decimal("0")

    movimientos_rows: list[dict] = []
    for mov in movimientos:
        nota = notas_map.get(mov.nota_id)
        tipo = (mov.tipo or "").lower()
        tipo_label = tipo or "-"
        if tipo == "pago" and nota and nota.tipo_operacion:
            tipo_label = f"pago {nota.tipo_operacion.value}"
        elif tipo == "reverso_pago":
            tipo_label = "reverso pago"

        monto = _safe_decimal(mov.monto)
        if tipo == "venta":
            total_ventas += monto
        elif tipo == "compra":
            total_compras += monto
        elif tipo in ("pago", "reverso_pago"):
            if nota and nota.tipo_operacion == TipoOperacion.compra:
                total_pagos_compra += monto
            else:
                total_pagos_venta += monto
        elif tipo == "ajuste":
            total_ajustes += monto
        elif tipo == "reverso":
            total_reversos += monto

        folio = None
        if nota:
            folio = note_service.format_folio(
                sucursal_id=nota.sucursal_id,
                tipo_operacion=nota.tipo_operacion,
                folio_seq=nota.folio_seq,
            )
        movimientos_rows.append(
            {
                "fecha": mov.created_at,
                "tipo": tipo_label.upper() if tipo_label else "-",
                "monto": monto,
                "nota_id": mov.nota_id,
                "folio": folio or (f"#{mov.nota_id}" if mov.nota_id else "-"),
                "sucursal": (
                    mov.sucursal.nombre if mov.sucursal else sucursal_map.get(mov.sucursal_id, "-")
                ),
                "metodo": mov.metodo_pago or "-",
                "cuenta": mov.cuenta_financiera or "-",
                "comentario": mov.comentario or "-",
            }
        )

    notas_query = db.query(Nota).filter(Nota.estado == NotaEstado.aprobada)
    if allowed_suc_ids is not None:
        if sucursal_id:
            notas_query = notas_query.filter(Nota.sucursal_id == sucursal_id)
        else:
            notas_query = notas_query.filter(Nota.sucursal_id.in_(allowed_suc_ids))
    elif sucursal_id:
        notas_query = notas_query.filter(Nota.sucursal_id == sucursal_id)

    if date_from:
        dt_from = datetime.strptime(date_from.isoformat(), "%Y-%m-%d")
        notas_query = notas_query.filter(Nota.created_at >= dt_from)
    if date_to:
        dt_to = datetime.strptime(date_to.isoformat(), "%Y-%m-%d")
        notas_query = notas_query.filter(Nota.created_at <= dt_to)

    notas = notas_query.order_by(Nota.created_at.desc()).all()
    notas_sucursal_ids = {n.sucursal_id for n in notas if n.sucursal_id}
    missing_suc_ids = notas_sucursal_ids.difference(sucursal_map.keys())
    if missing_suc_ids:
        for s in db.query(Sucursal).filter(Sucursal.id.in_(missing_suc_ids)).all():
            sucursal_map[s.id] = s.nombre
    prov_ids = {n.proveedor_id for n in notas if n.proveedor_id}
    cli_ids = {n.cliente_id for n in notas if n.cliente_id}
    prov_map = {}
    cli_map = {}
    if prov_ids:
        prov_map = {p.id: p for p in db.query(Proveedor).filter(Proveedor.id.in_(prov_ids)).all()}
    if cli_ids:
        cli_map = {c.id: c for c in db.query(Cliente).filter(Cliente.id.in_(cli_ids)).all()}

    total_facturado_ventas = Decimal("0")
    total_facturado_compras = Decimal("0")
    total_pagado_ventas = Decimal("0")
    total_pagado_compras = Decimal("0")
    notas_pendientes: list[dict] = []

    for nota in notas:
        total = _safe_decimal(nota.total_monto)
        pagado = _safe_decimal(nota.monto_pagado)
        saldo = total - pagado
        if nota.tipo_operacion == TipoOperacion.compra:
            total_facturado_compras += total
            total_pagado_compras += pagado
            partner = prov_map.get(nota.proveedor_id)
        else:
            total_facturado_ventas += total
            total_pagado_ventas += pagado
            partner = cli_map.get(nota.cliente_id)
        if saldo > Decimal("0"):
            folio = note_service.format_folio(
                sucursal_id=nota.sucursal_id,
                tipo_operacion=nota.tipo_operacion,
                folio_seq=nota.folio_seq,
            )
            notas_pendientes.append(
                {
                    "folio": folio or f"#{nota.id}",
                    "nota_id": nota.id,
                    "tipo": nota.tipo_operacion.value,
                    "partner": partner.nombre_completo if partner else "-",
                    "total": total,
                    "pagado": pagado,
                    "saldo": saldo,
                    "fecha": nota.created_at,
                    "sucursal": sucursal_map.get(nota.sucursal_id, "-"),
                }
            )

    total_ingresos = total_pagos_venta
    total_egresos = total_pagos_compra
    balance_neto = total_ingresos - total_egresos

    summary_items = [
        {"label": "Movimientos en periodo", "value": len(movimientos_rows), "type": "count"},
        {"label": "Ventas registradas", "value": total_ventas, "type": "money"},
        {"label": "Compras registradas", "value": total_compras, "type": "money"},
        {"label": "Pagos recibidos (venta)", "value": total_pagos_venta, "type": "money"},
        {"label": "Pagos realizados (compra)", "value": total_pagos_compra, "type": "money"},
        {"label": "Ingresos en caja", "value": total_ingresos, "type": "money"},
        {"label": "Egresos en caja", "value": total_egresos, "type": "money"},
        {"label": "Balance neto caja", "value": balance_neto, "type": "money"},
        {"label": "Ajustes", "value": total_ajustes, "type": "money"},
        {"label": "Reversos", "value": total_reversos, "type": "money"},
        {"label": "Notas aprobadas", "value": len(notas), "type": "count"},
        {"label": "Facturado ventas", "value": total_facturado_ventas, "type": "money"},
        {"label": "Pagado ventas", "value": total_pagado_ventas, "type": "money"},
        {"label": "Saldo pendiente ventas", "value": total_facturado_ventas - total_pagado_ventas, "type": "money"},
        {"label": "Facturado compras", "value": total_facturado_compras, "type": "money"},
        {"label": "Pagado compras", "value": total_pagado_compras, "type": "money"},
        {"label": "Saldo pendiente compras", "value": total_facturado_compras - total_pagado_compras, "type": "money"},
    ]

    return {
        "generated_at": datetime.utcnow(),
        "sucursal": sucursal_label,
        "date_from": date_from,
        "date_to": date_to,
        "summary_items": summary_items,
        "movimientos": movimientos_rows,
        "notas_pendientes": notas_pendientes,
    }


def build_report_excel(report: dict) -> tuple[bytes, str]:
    def cell(value: str, cell_type: str = "String") -> str:
        return f"<Cell><Data ss:Type='{cell_type}'>{_xml_escape(value)}</Data></Cell>"

    def row(values: list[tuple[str, str]]) -> str:
        return "<Row>" + "".join([cell(v, t) for v, t in values]) + "</Row>"

    title = "Reporte contable"
    sucursal = report["sucursal"]
    date_from = report["date_from"].isoformat() if report.get("date_from") else "---"
    date_to = report["date_to"].isoformat() if report.get("date_to") else "---"
    generated_at = report["generated_at"].strftime("%Y-%m-%d %H:%M")

    summary_rows = [
        row([(title, "String")]),
        row([(f"Sucursal: {sucursal}", "String")]),
        row([(f"Periodo: {date_from} a {date_to}", "String")]),
        row([(f"Generado: {generated_at}", "String")]),
        row([("", "String")]),
    ]
    for item in report["summary_items"]:
        label = item["label"]
        val = item["value"]
        if item["type"] == "count":
            summary_rows.append(row([(label, "String"), (str(int(val)), "Number")]))
        else:
            summary_rows.append(row([(label, "String"), (str(_safe_decimal(val)), "Number")]))

    summary_sheet = f"""
 <Worksheet ss:Name="Resumen">
  <Table>
   {''.join(summary_rows)}
  </Table>
 </Worksheet>"""

    mov_headers = [
        "Fecha",
        "Tipo",
        "Monto",
        "Folio",
        "Sucursal",
        "Metodo",
        "Cuenta",
        "Comentario",
    ]
    mov_rows = [
        "<Row>" + "".join([cell(h) for h in mov_headers]) + "</Row>"
    ]
    for mov in report["movimientos"]:
        mov_rows.append(
            row(
                [
                    (mov["fecha"].strftime("%Y-%m-%d %H:%M") if mov["fecha"] else "-", "String"),
                    (mov["tipo"], "String"),
                    (str(_safe_decimal(mov["monto"])), "Number"),
                    (mov["folio"], "String"),
                    (mov["sucursal"], "String"),
                    (mov["metodo"], "String"),
                    (mov["cuenta"], "String"),
                    (mov["comentario"], "String"),
                ]
            )
        )

    mov_sheet = f"""
 <Worksheet ss:Name="Movimientos">
  <Table>
   {''.join(mov_rows)}
  </Table>
 </Worksheet>"""

    pendientes_headers = [
        "Folio",
        "Operacion",
        "Partner",
        "Total",
        "Pagado",
        "Saldo",
        "Fecha",
        "Sucursal",
    ]
    pendientes_rows = [
        "<Row>" + "".join([cell(h) for h in pendientes_headers]) + "</Row>"
    ]
    for nota in report["notas_pendientes"]:
        pendientes_rows.append(
            row(
                [
                    (nota["folio"], "String"),
                    (nota["tipo"], "String"),
                    (nota["partner"], "String"),
                    (str(_safe_decimal(nota["total"])), "Number"),
                    (str(_safe_decimal(nota["pagado"])), "Number"),
                    (str(_safe_decimal(nota["saldo"])), "Number"),
                    (nota["fecha"].strftime("%Y-%m-%d") if nota["fecha"] else "-", "String"),
                    (nota["sucursal"], "String"),
                ]
            )
        )

    pendientes_sheet = f"""
 <Worksheet ss:Name="NotasPendientes">
  <Table>
   {''.join(pendientes_rows)}
  </Table>
 </Worksheet>"""

    workbook = f"""<?xml version="1.0"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
 xmlns:o="urn:schemas-microsoft-com:office:office"
 xmlns:x="urn:schemas-microsoft-com:office:excel"
 xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
{summary_sheet}
{mov_sheet}
{pendientes_sheet}
</Workbook>"""

    filename = f"reporte_contable_{_safe_filename(sucursal)}.xls"
    return workbook.encode("utf-8"), filename


def _escape_pdf(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _text_width(text: str, size: int) -> float:
    return len(text) * size * 0.5


def _truncate_text(text: str, max_width: float, size: int) -> str:
    if _text_width(text, size) <= max_width:
        return text
    ellipsis = "..."
    max_chars = max(1, int(max_width / (size * 0.5)) - len(ellipsis))
    return f"{text[:max_chars]}{ellipsis}"


class _PdfPage:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def text(self, x: float, y: float, text: str, size: int = 10, font: str = "F1") -> None:
        safe = _escape_pdf(text)
        self.commands.append(f"BT /{font} {size} Tf {x:.2f} {y:.2f} Td ({safe}) Tj ET")

    def line(self, x1: float, y1: float, x2: float, y2: float) -> None:
        self.commands.append(f"{x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S")

    def rect(self, x: float, y: float, w: float, h: float, fill_gray: float | None = None, stroke_gray: float | None = None) -> None:
        if fill_gray is not None:
            self.commands.append(f"{fill_gray:.2f} g")
        if stroke_gray is not None:
            self.commands.append(f"{stroke_gray:.2f} G")
        op = "f" if fill_gray is not None else "S"
        self.commands.append(f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} re {op}")
        if fill_gray is not None:
            self.commands.append("0 g")
        if stroke_gray is not None:
            self.commands.append("0 G")


class _PdfDocument:
    def __init__(self) -> None:
        self.pages: list[_PdfPage] = []

    def new_page(self) -> _PdfPage:
        page = _PdfPage()
        self.pages.append(page)
        return page

    def render(self) -> bytes:
        objects: list[tuple[int, bytes]] = []

        def obj(num: int, body: bytes) -> None:
            objects.append((num, body))

        page_count = len(self.pages)
        font_regular_id = 3 + (page_count * 2)
        font_bold_id = font_regular_id + 1

        obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")

        kids = []
        for idx in range(page_count):
            page_obj_id = 3 + idx * 2
            kids.append(f"{page_obj_id} 0 R")
        kids_str = " ".join(kids)
        obj(2, f"<< /Type /Pages /Count {page_count} /Kids [{kids_str}] >>".encode("latin-1"))

        for idx, page in enumerate(self.pages):
            page_obj_id = 3 + idx * 2
            content_obj_id = 4 + idx * 2
            stream_content = "\n".join(page.commands).encode("latin-1", errors="ignore")
            obj(
                page_obj_id,
                (
                    f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    f"/Contents {content_obj_id} 0 R /Resources << /Font << "
                    f"/F1 {font_regular_id} 0 R /F2 {font_bold_id} 0 R >> >> >>"
                ).encode("latin-1"),
            )
            obj(content_obj_id, f"<< /Length {len(stream_content)} >>\nstream\n".encode() + stream_content + b"\nendstream")

        obj(font_regular_id, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
        obj(font_bold_id, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")

        buffer = io.BytesIO()
        buffer.write(b"%PDF-1.4\n")
        offsets = [0]
        for num, body in objects:
            offsets.append(buffer.tell())
            buffer.write(f"{num} 0 obj\n".encode())
            buffer.write(body)
            buffer.write(b"\nendobj\n")
        xref_pos = buffer.tell()
        buffer.write(f"xref\n0 {len(offsets)}\n".encode())
        buffer.write(b"0000000000 65535 f \n")
        for off in offsets[1:]:
            buffer.write(f"{off:010} 00000 n \n".encode())
        buffer.write(b"trailer\n")
        buffer.write(f"<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode())
        buffer.seek(0)
        return buffer.read()


def build_report_pdf(report: dict) -> tuple[bytes, str]:
    doc = _PdfDocument()
    page = doc.new_page()

    left = 46
    right = 566
    top = 760
    y = top

    generated = report["generated_at"].strftime("%Y-%m-%d %H:%M")
    date_from = report["date_from"].isoformat() if report.get("date_from") else "---"
    date_to = report["date_to"].isoformat() if report.get("date_to") else "---"

    page.text(left, y, "Reporte contable", size=16, font="F2")
    page.text(left, y - 16, f"Sucursal: {report['sucursal']}", size=9)
    page.text(left, y - 28, f"Periodo: {date_from} a {date_to}", size=9)
    page.text(right - 140, y - 16, f"Generado: {generated}", size=9)
    page.line(left, y - 36, right, y - 36)

    y = y - 54
    page.rect(left, y - 18, right - left, 18, fill_gray=0.93, stroke_gray=0.85)
    page.text(left + 8, y - 6, "Resumen", size=10, font="F2")
    y = y - 26

    items = report["summary_items"]
    half = (len(items) + 1) // 2
    left_items = items[:half]
    right_items = items[half:]
    line_h = 12
    col_gap = 260
    value_x = left + 180
    value_x_right = left + col_gap + 180
    start_y = y
    for idx, item in enumerate(left_items):
        label = item["label"]
        value = item["value"]
        if item["type"] == "count":
            value_str = str(int(_safe_decimal(value)))
        else:
            value_str = _format_money(_safe_decimal(value))
        page.text(left + 8, start_y - idx * line_h, label, size=9)
        page.text(value_x, start_y - idx * line_h, value_str, size=9, font="F2")
    for idx, item in enumerate(right_items):
        label = item["label"]
        value = item["value"]
        if item["type"] == "count":
            value_str = str(int(_safe_decimal(value)))
        else:
            value_str = _format_money(_safe_decimal(value))
        page.text(left + col_gap, start_y - idx * line_h, label, size=9)
        page.text(value_x_right, start_y - idx * line_h, value_str, size=9, font="F2")

    y = start_y - max(len(left_items), len(right_items)) * line_h - 18

    def draw_mov_header(p: _PdfPage, y_pos: float) -> float:
        p.rect(left, y_pos - 16, right - left, 16, fill_gray=0.93, stroke_gray=0.85)
        cols = [
            ("Fecha", left, 70, "left"),
            ("Tipo", left + 70, 60, "left"),
            ("Monto", left + 130, 70, "right"),
            ("Folio", left + 200, 70, "left"),
            ("Sucursal", left + 270, 90, "left"),
            ("Metodo", left + 360, 60, "left"),
            ("Comentario", left + 420, 146, "left"),
        ]
        for title, x, width, align in cols:
            draw_x = x + 2
            if align == "right":
                draw_x = x + width - _text_width(title, 8) - 2
            p.text(draw_x, y_pos - 5, title, size=8, font="F2")
        return y_pos - 24

    def draw_mov_row(p: _PdfPage, y_pos: float, mov: dict) -> float:
        cols = [
            (mov["fecha"].strftime("%Y-%m-%d") if mov["fecha"] else "-", left, 70, "left"),
            (mov["tipo"], left + 70, 60, "left"),
            (_format_money(_safe_decimal(mov["monto"])), left + 130, 70, "right"),
            (mov["folio"], left + 200, 70, "left"),
            (mov["sucursal"], left + 270, 90, "left"),
            (mov["metodo"], left + 360, 60, "left"),
            (mov["comentario"], left + 420, 146, "left"),
        ]
        for text, x, width, align in cols:
            display = _truncate_text(str(text), width - 4, 8)
            draw_x = x + 2
            if align == "right":
                draw_x = x + width - _text_width(display, 8) - 2
            p.text(draw_x, y_pos, display, size=8)
        return y_pos - 12

    if report["movimientos"]:
        y = draw_mov_header(page, y)
        for mov in report["movimientos"]:
            if y < 80:
                page = doc.new_page()
                y = top
                page.text(left, y, "Reporte contable (continuacion)", size=12, font="F2")
                page.text(left, y - 14, f"Sucursal: {report['sucursal']}", size=9)
                page.text(left, y - 26, f"Periodo: {date_from} a {date_to}", size=9)
                y = y - 40
                y = draw_mov_header(page, y)
            y = draw_mov_row(page, y, mov)
    else:
        page.text(left, y, "Sin movimientos para el periodo seleccionado.", size=9)
        y -= 18

    notas = report["notas_pendientes"]
    if notas:
        if y < 120:
            page = doc.new_page()
            y = top
            page.text(left, y, "Notas con saldo pendiente", size=12, font="F2")
            y -= 20
        else:
            page.text(left, y, "Notas con saldo pendiente", size=11, font="F2")
            y -= 14

        def draw_pend_header(p: _PdfPage, y_pos: float) -> float:
            p.rect(left, y_pos - 16, right - left, 16, fill_gray=0.93, stroke_gray=0.85)
            cols = [
                ("Folio", left, 70, "left"),
                ("Operacion", left + 70, 60, "left"),
                ("Partner", left + 130, 140, "left"),
                ("Total", left + 270, 70, "right"),
                ("Pagado", left + 340, 70, "right"),
                ("Saldo", left + 410, 70, "right"),
                ("Fecha", left + 480, 86, "left"),
            ]
            for title, x, width, align in cols:
                draw_x = x + 2
                if align == "right":
                    draw_x = x + width - _text_width(title, 8) - 2
                p.text(draw_x, y_pos - 5, title, size=8, font="F2")
            return y_pos - 24

        def draw_pend_row(p: _PdfPage, y_pos: float, nota: dict) -> float:
            cols = [
                (nota["folio"], left, 70, "left"),
                (nota["tipo"], left + 70, 60, "left"),
                (nota["partner"], left + 130, 140, "left"),
                (_format_money(_safe_decimal(nota["total"])), left + 270, 70, "right"),
                (_format_money(_safe_decimal(nota["pagado"])), left + 340, 70, "right"),
                (_format_money(_safe_decimal(nota["saldo"])), left + 410, 70, "right"),
                (nota["fecha"].strftime("%Y-%m-%d") if nota["fecha"] else "-", left + 480, 86, "left"),
            ]
            for text, x, width, align in cols:
                display = _truncate_text(str(text), width - 4, 8)
                draw_x = x + 2
                if align == "right":
                    draw_x = x + width - _text_width(display, 8) - 2
                p.text(draw_x, y_pos, display, size=8)
            return y_pos - 12

        y = draw_pend_header(page, y)
        for nota in notas:
            if y < 80:
                page = doc.new_page()
                y = top
                page.text(left, y, "Notas con saldo pendiente (continuacion)", size=12, font="F2")
                y -= 20
                y = draw_pend_header(page, y)
            y = draw_pend_row(page, y, nota)

    pdf_bytes = doc.render()
    filename = f"reporte_contable_{_safe_filename(report['sucursal'])}.pdf"
    return pdf_bytes, filename
