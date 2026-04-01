# app/services/corte_caja_report_service.py
from __future__ import annotations

import io
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.core.datetime_utils import format_date_local, format_datetime_local
from app.models import CorteCaja, Sucursal, CorteCajaGasto, CorteCajaDenominacion


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
    return slug or "corte-caja"


def build_report(
    *,
    corte: CorteCaja,
    sucursal: Sucursal | None,
    cash_data: dict,
    manual_data: dict,
    gastos: list[CorteCajaGasto],
    denominaciones: list[CorteCajaDenominacion],
    saldo_calculado: Decimal,
    generated_at: datetime | None = None,
) -> dict:
    generated_at = generated_at or datetime.utcnow()
    sucursal_name = sucursal.nombre if sucursal else "-"
    abierto_por = corte.abierto_por.nombre_completo if corte.abierto_por else "-"
    cerrado_por = corte.cerrado_por.nombre_completo if corte.cerrado_por else "-"

    saldo_inicial = _safe_decimal(corte.saldo_inicial)
    saldo_cierre = _safe_decimal(corte.saldo_cierre)
    diferencia = _safe_decimal(corte.diferencia)
    cash_ingresos = _safe_decimal(cash_data.get("ingresos"))
    cash_egresos = _safe_decimal(cash_data.get("egresos"))
    cash_neto = _safe_decimal(cash_data.get("neto"))
    manual_ingresos = _safe_decimal(manual_data.get("ingresos"))
    manual_egresos = _safe_decimal(manual_data.get("egresos"))
    manual_neto = _safe_decimal(manual_data.get("neto"))
    gastos_total = sum((_safe_decimal(g.monto) for g in gastos), Decimal("0"))

    summary_items = [
        {"label": "Saldo inicial", "value": saldo_inicial, "type": "money"},
        {"label": "Ingresos efectivo", "value": cash_ingresos, "type": "money"},
        {"label": "Egresos efectivo", "value": cash_egresos, "type": "money"},
        {"label": "Ingresos manuales", "value": manual_ingresos, "type": "money"},
        {"label": "Egresos manuales", "value": manual_egresos, "type": "money"},
        {"label": "Gastos caja chica", "value": gastos_total, "type": "money"},
        {"label": "Saldo esperado", "value": saldo_calculado, "type": "money"},
        {"label": "Saldo contado", "value": saldo_cierre, "type": "money"},
        {"label": "Diferencia", "value": diferencia, "type": "money"},
    ]

    denoms_data = []
    for denom in denominaciones:
        valor = _safe_decimal(denom.valor)
        cantidad = int(denom.cantidad or 0)
        denoms_data.append(
            {
                "valor": valor,
                "cantidad": cantidad,
                "subtotal": valor * Decimal(str(cantidad)),
            }
        )

    gastos_data = []
    for gasto in gastos:
        gastos_data.append(
            {
                "fecha": gasto.created_at,
                "descripcion": gasto.descripcion,
                "categoria": gasto.categoria or "",
                "usuario": gasto.usuario.nombre_completo if gasto.usuario else "-",
                "monto": _safe_decimal(gasto.monto),
            }
        )

    report = {
        "generated_at": generated_at,
        "sucursal": sucursal_name,
        "fecha": corte.fecha,
        "estado": corte.estado.value if corte.estado else "-",
        "abierto_por": abierto_por,
        "cerrado_por": cerrado_por,
        "opened_at": corte.opened_at,
        "closed_at": corte.closed_at,
        "saldo_inicial": saldo_inicial,
        "saldo_calculado": saldo_calculado,
        "saldo_cierre": saldo_cierre,
        "diferencia": diferencia,
        "motivo_diferencia": corte.motivo_diferencia or "",
        "comentarios_cierre": corte.comentarios_cierre or "",
        "cash_ingresos": cash_ingresos,
        "cash_egresos": cash_egresos,
        "cash_neto": cash_neto,
        "manual_ingresos": manual_ingresos,
        "manual_egresos": manual_egresos,
        "manual_neto": manual_neto,
        "gastos_total": gastos_total,
        "summary_items": summary_items,
        "cash_movs": cash_data.get("movimientos", []),
        "manual_movs": manual_data.get("movimientos", []),
        "gastos": gastos_data,
        "denominaciones": denoms_data,
    }
    return report


def build_report_excel(report: dict) -> tuple[bytes, str]:
    rows = []

    def add_row(values: list[str]) -> None:
        cells = "".join([f"<Cell><Data ss:Type='String'>{_xml_escape(str(v))}</Data></Cell>" for v in values])
        rows.append(f"<Row>{cells}</Row>")

    add_row(["Corte de caja"])
    add_row(["Sucursal", report["sucursal"], "Fecha", report["fecha"].isoformat(), "Estado", report["estado"]])
    add_row(["Generado", format_datetime_local(report["generated_at"])])
    add_row([
        "Abierto por",
        report.get("abierto_por") or "-",
        "Hora apertura",
        format_datetime_local(report["opened_at"]) if report.get("opened_at") else "-",
    ])
    add_row([
        "Cerrado por",
        report.get("cerrado_por") or "-",
        "Hora cierre",
        format_datetime_local(report["closed_at"]) if report.get("closed_at") else "-",
    ])
    add_row([])

    add_row(["Resumen"])
    for item in report["summary_items"]:
        add_row([item["label"], _format_money(_safe_decimal(item["value"]))])
    if report.get("motivo_diferencia"):
        add_row(["Motivo diferencia", report["motivo_diferencia"]])
    if report.get("comentarios_cierre"):
        add_row(["Comentarios cierre", report["comentarios_cierre"]])
    add_row([])

    add_row(["Arqueo por denominaciones"])
    add_row(["Denominacion", "Cantidad", "Subtotal"])
    if report["denominaciones"]:
        for denom in report["denominaciones"]:
            add_row([
                f"${denom['valor']:.2f}",
                str(denom["cantidad"]),
                _format_money(_safe_decimal(denom["subtotal"])),
            ])
    else:
        add_row(["Sin arqueo registrado"])
    add_row([])

    add_row(["Movimientos manuales"])
    add_row(["Fecha", "Tipo", "Descripcion", "Usuario", "Monto"])
    if report["manual_movs"]:
        for mov in report["manual_movs"]:
            add_row([
                format_datetime_local(mov["fecha"]) if mov.get("fecha") else "-",
                mov.get("tipo_label") or mov.get("tipo") or "-",
                mov.get("descripcion") or "",
                mov.get("usuario") or "-",
                _format_money(_safe_decimal(mov.get("monto"))),
            ])
    else:
        add_row(["Sin movimientos manuales"])
    add_row([])

    add_row(["Movimientos efectivo (notas)"])
    add_row(["Fecha", "Tipo", "Detalle", "Folio", "Partner", "Monto", "Comentario"])
    if report["cash_movs"]:
        for mov in report["cash_movs"]:
            add_row([
                format_datetime_local(mov["fecha"]) if mov.get("fecha") else "-",
                mov.get("tipo") or "-",
                mov.get("detalle") or "-",
                mov.get("folio") or "-",
                mov.get("partner") or "-",
                _format_money(_safe_decimal(mov.get("monto"))),
                mov.get("comentario") or "",
            ])
    else:
        add_row(["Sin movimientos en efectivo"])
    add_row([])

    add_row(["Gastos caja chica"])
    add_row(["Fecha", "Descripcion", "Categoria", "Usuario", "Monto"])
    if report["gastos"]:
        for gasto in report["gastos"]:
            add_row([
                format_datetime_local(gasto["fecha"]) if gasto.get("fecha") else "-",
                gasto.get("descripcion") or "",
                gasto.get("categoria") or "",
                gasto.get("usuario") or "-",
                _format_money(_safe_decimal(gasto.get("monto"))),
            ])
    else:
        add_row(["Sin gastos registrados"])
    add_row([])

    add_row(["Firmas"])
    add_row(["Elaboro", report.get("abierto_por") or "-", "Firma", "________________"])
    add_row(["Reviso", report.get("cerrado_por") or "-", "Firma", "________________"])
    add_row(["Autorizo", "", "Firma", "________________"])
    add_row(["Responsable de caja", "", "Firma", "________________"])

    workbook = f"""<?xml version="1.0"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
 xmlns:o="urn:schemas-microsoft-com:office:office"
 xmlns:x="urn:schemas-microsoft-com:office:excel"
 xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
 <Worksheet ss:Name="CorteCaja">
  <Table>
   {''.join(rows)}
 </Table>
 </Worksheet>
</Workbook>"""

    filename = f"corte_caja_{_safe_filename(report['sucursal'])}_{report['fecha'].isoformat()}.xls"
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

    generated = format_datetime_local(report["generated_at"])
    fecha = report["fecha"].isoformat()

    page.text(left, y, "Corte de caja", size=16, font="F2")
    page.text(left, y - 16, f"Sucursal: {report['sucursal']}", size=9)
    page.text(left, y - 28, f"Fecha: {fecha}", size=9)
    page.text(left, y - 40, f"Estado: {report['estado']}", size=9)
    page.text(
        left,
        y - 52,
        f"Abierto por: {report.get('abierto_por') or '-'} ({format_datetime_local(report['opened_at']) if report.get('opened_at') else '-'})",
        size=9,
    )
    page.text(
        left,
        y - 64,
        f"Cerrado por: {report.get('cerrado_por') or '-'} ({format_datetime_local(report['closed_at']) if report.get('closed_at') else '-'})",
        size=9,
    )
    page.text(right - 150, y - 16, f"Generado: {generated}", size=9)
    page.line(left, y - 72, right, y - 72)

    y = y - 88
    page.rect(left, y - 18, right - left, 18, fill_gray=0.93, stroke_gray=0.85)
    page.text(left + 8, y - 6, "Resumen", size=10, font="F2")
    y = y - 26

    items = report["summary_items"]
    half = (len(items) + 1) // 2
    left_items = items[:half]
    right_items = items[half:]
    line_h = 12
    col_gap = 260
    value_x = left + 170
    value_x_right = left + col_gap + 170
    start_y = y
    for idx, item in enumerate(left_items):
        label = item["label"]
        value_str = _format_money(_safe_decimal(item["value"]))
        page.text(left + 8, start_y - idx * line_h, label, size=9)
        page.text(value_x, start_y - idx * line_h, value_str, size=9, font="F2")
    for idx, item in enumerate(right_items):
        label = item["label"]
        value_str = _format_money(_safe_decimal(item["value"]))
        page.text(left + col_gap, start_y - idx * line_h, label, size=9)
        page.text(value_x_right, start_y - idx * line_h, value_str, size=9, font="F2")

    y = start_y - max(len(left_items), len(right_items)) * line_h - 16

    if report.get("motivo_diferencia"):
        page.text(left, y, f"Motivo diferencia: {report['motivo_diferencia']}", size=9)
        y -= 14
    if report.get("comentarios_cierre"):
        page.text(left, y, f"Comentarios cierre: {report['comentarios_cierre']}", size=9)
        y -= 14

    def new_page_header(title: str) -> None:
        nonlocal page, y
        page = doc.new_page()
        y = top
        page.text(left, y, title, size=12, font="F2")
        page.text(left, y - 14, f"Sucursal: {report['sucursal']}", size=9)
        page.text(left, y - 26, f"Fecha: {fecha}", size=9)
        y -= 44

    def ensure_space(min_y: float) -> None:
        nonlocal y
        if y < min_y:
            new_page_header("Corte de caja (continuacion)")

    def draw_table_header(title: str, cols: list[tuple[str, float, float, str]]) -> None:
        nonlocal y
        ensure_space(120)
        page.text(left, y, title, size=11, font="F2")
        y -= 12
        page.rect(left, y - 12, right - left, 12, fill_gray=0.93, stroke_gray=0.85)
        for col_title, x, width, align in cols:
            draw_x = x + 2
            if align == "right":
                draw_x = x + width - _text_width(col_title, 8) - 2
            page.text(draw_x, y - 4, col_title, size=8, font="F2")
        y -= 22

    def draw_row(cols: list[tuple[str, float, float, str]], values: list[str]) -> None:
        nonlocal y
        ensure_space(80)
        for (col_title, x, width, align), text in zip(cols, values):
            display = _truncate_text(str(text), width - 4, 8)
            draw_x = x + 2
            if align == "right":
                draw_x = x + width - _text_width(display, 8) - 2
            page.text(draw_x, y, display, size=8)
        y -= 12

    # Denominaciones
    denom_cols = [
        ("Denominacion", left, 160, "left"),
        ("Cantidad", left + 160, 80, "right"),
        ("Subtotal", left + 240, 80, "right"),
    ]
    draw_table_header("Arqueo por denominaciones", denom_cols)
    if report["denominaciones"]:
        for denom in report["denominaciones"]:
            draw_row(
                denom_cols,
                [
                    _format_money(_safe_decimal(denom["valor"])),
                    str(denom["cantidad"]),
                    _format_money(_safe_decimal(denom["subtotal"])),
                ],
            )
    else:
        draw_row(denom_cols, ["Sin arqueo registrado", "", ""])

    # Movimientos manuales
    manual_cols = [
        ("Fecha", left, 80, "left"),
        ("Tipo", left + 80, 60, "left"),
        ("Descripcion", left + 140, 200, "left"),
        ("Usuario", left + 340, 120, "left"),
        ("Monto", left + 460, 70, "right"),
    ]
    draw_table_header("Movimientos manuales", manual_cols)
    if report["manual_movs"]:
        for mov in report["manual_movs"]:
            draw_row(
                manual_cols,
                [
                    format_date_local(mov["fecha"]) if mov.get("fecha") else "-",
                    mov.get("tipo_label") or mov.get("tipo") or "-",
                    mov.get("descripcion") or "",
                    mov.get("usuario") or "-",
                    _format_money(_safe_decimal(mov.get("monto"))),
                ],
            )
    else:
        draw_row(manual_cols, ["Sin movimientos manuales", "", "", "", ""])

    # Movimientos efectivo
    cash_cols = [
        ("Fecha", left, 80, "left"),
        ("Tipo", left + 80, 60, "left"),
        ("Folio", left + 140, 60, "left"),
        ("Partner", left + 200, 210, "left"),
        ("Monto", left + 410, 70, "right"),
    ]
    draw_table_header("Movimientos efectivo (notas)", cash_cols)
    if report["cash_movs"]:
        for mov in report["cash_movs"]:
            draw_row(
                cash_cols,
                [
                    format_date_local(mov["fecha"]) if mov.get("fecha") else "-",
                    mov.get("tipo") or "-",
                    mov.get("folio") or "-",
                    mov.get("partner") or "-",
                    _format_money(_safe_decimal(mov.get("monto"))),
                ],
            )
    else:
        draw_row(cash_cols, ["Sin movimientos en efectivo", "", "", "", ""])

    # Gastos
    gasto_cols = [
        ("Fecha", left, 80, "left"),
        ("Descripcion", left + 80, 200, "left"),
        ("Categoria", left + 280, 100, "left"),
        ("Usuario", left + 380, 100, "left"),
        ("Monto", left + 480, 70, "right"),
    ]
    draw_table_header("Gastos caja chica", gasto_cols)
    if report["gastos"]:
        for gasto in report["gastos"]:
            draw_row(
                gasto_cols,
                [
                    format_date_local(gasto["fecha"]) if gasto.get("fecha") else "-",
                    gasto.get("descripcion") or "",
                    gasto.get("categoria") or "",
                    gasto.get("usuario") or "-",
                    _format_money(_safe_decimal(gasto.get("monto"))),
                ],
            )
    else:
        draw_row(gasto_cols, ["Sin gastos registrados", "", "", "", ""])

    ensure_space(110)
    page.text(left, y, "Firmas", size=11, font="F2")
    y -= 16
    page.text(left, y, f"Elaboro: {report.get('abierto_por') or '-'}", size=9)
    page.line(left + 160, y - 2, left + 320, y - 2)
    y -= 16
    page.text(left, y, f"Reviso: {report.get('cerrado_por') or '-'}", size=9)
    page.line(left + 160, y - 2, left + 320, y - 2)
    y -= 16
    page.text(left, y, "Autorizo:", size=9)
    page.line(left + 160, y - 2, left + 320, y - 2)
    y -= 16
    page.text(left, y, "Responsable de caja:", size=9)
    page.line(left + 160, y - 2, left + 320, y - 2)

    pdf_bytes = doc.render()
    filename = f"corte_caja_{_safe_filename(report['sucursal'])}_{report['fecha'].isoformat()}.pdf"
    return pdf_bytes, filename
