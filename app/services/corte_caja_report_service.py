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


def _format_summary_value(value: Decimal, value_type: str) -> str:
    value_type = (value_type or "money").lower()
    if value_type == "kg":
        try:
            return f"{value:,.3f} kg"
        except (ValueError, InvalidOperation):
            return "0.000 kg"
    return _format_money(value)


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


def _denom_section(valor: Decimal) -> str:
    return "BILLETES" if valor >= Decimal("20") else "MONEDAS"


def build_report(
    *,
    corte: CorteCaja,
    sucursal: Sucursal | None,
    cash_data: dict,
    manual_data: dict,
    gastos: list[CorteCajaGasto],
    denominaciones: list[CorteCajaDenominacion],
    saldo_calculado: Decimal,
    compras_rows: list[dict] | None = None,
    ventas_rows: list[dict] | None = None,
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
    compras_rows = compras_rows or []
    ventas_rows = ventas_rows or []
    dotaciones = list(manual_data.get("movimientos_by_categoria", {}).get("DOTACION_EFECTIVO", []))
    sobrantes_viaticos = list(manual_data.get("movimientos_by_categoria", {}).get("SOBRANTE_VIATICOS", []))
    sobrantes_gastos = list(manual_data.get("movimientos_by_categoria", {}).get("SOBRANTE_GASTOS", []))
    otros_ajustes = [
        mov
        for mov in manual_data.get("movimientos", [])
        if (mov.get("categoria") or "") not in {"DOTACION_EFECTIVO", "SOBRANTE_VIATICOS", "SOBRANTE_GASTOS"}
    ]

    denoms_data = []
    denoms_monedas = []
    denoms_billetes = []
    for denom in denominaciones:
        valor = _safe_decimal(denom.valor)
        cantidad = int(denom.cantidad or 0)
        entry = {
            "valor": valor,
            "cantidad": cantidad,
            "subtotal": valor * Decimal(str(cantidad)),
            "section": _denom_section(valor),
        }
        denoms_data.append(entry)
        if entry["section"] == "MONEDAS":
            denoms_monedas.append(entry)
        else:
            denoms_billetes.append(entry)
    denoms_monedas.sort(key=lambda row: row["valor"])
    denoms_billetes.sort(key=lambda row: row["valor"])

    saldo_contado_actual = saldo_cierre
    if corte.estado != corte.estado.cerrado:
        saldo_contado_actual = sum((_safe_decimal(row["subtotal"]) for row in denoms_data), Decimal("0"))
    diferencia_actual = diferencia if corte.estado == corte.estado.cerrado else saldo_contado_actual - saldo_calculado

    summary_items = [
        {"label": "Saldo inicial", "value": saldo_inicial, "type": "money"},
        {"label": "Dotacion efectivo", "value": _safe_decimal(manual_data.get("totals_by_categoria", {}).get("DOTACION_EFECTIVO", 0)), "type": "money"},
        {"label": "Ventas efectivo", "value": _safe_decimal(cash_data.get("ventas_efectivo_total")), "type": "money"},
        {"label": "Compras efectivo", "value": _safe_decimal(cash_data.get("compras_efectivo_total")), "type": "money"},
        {"label": "Kg comprados del dia", "value": sum((_safe_decimal(row.get("kg_neto")) for row in compras_rows), Decimal("0")), "type": "kg"},
        {"label": "Kg vendidos del dia", "value": sum((_safe_decimal(row.get("kg_neto")) for row in ventas_rows), Decimal("0")), "type": "kg"},
        {"label": "Sobrantes viaticos", "value": _safe_decimal(manual_data.get("totals_by_categoria", {}).get("SOBRANTE_VIATICOS", 0)), "type": "money"},
        {"label": "Sobrantes gastos", "value": _safe_decimal(manual_data.get("totals_by_categoria", {}).get("SOBRANTE_GASTOS", 0)), "type": "money"},
        {"label": "Otros ajustes", "value": sum((_safe_decimal(m.get("monto")) for m in otros_ajustes), Decimal("0")), "type": "money"},
        {"label": "Gastos caja chica", "value": gastos_total, "type": "money"},
        {"label": "Saldo esperado", "value": saldo_calculado, "type": "money"},
        {"label": "Saldo contado", "value": saldo_contado_actual, "type": "money"},
        {"label": "Diferencia", "value": diferencia_actual, "type": "money"},
    ]

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
        "saldo_cierre": saldo_contado_actual,
        "diferencia": diferencia_actual,
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
        "ventas_efectivo_rows": cash_data.get("ventas_efectivo_rows", []),
        "ventas_efectivo_total": _safe_decimal(cash_data.get("ventas_efectivo_total")),
        "compras_efectivo_rows": cash_data.get("compras_efectivo_rows", []),
        "compras_efectivo_total": _safe_decimal(cash_data.get("compras_efectivo_total")),
        "manual_movs": manual_data.get("movimientos", []),
        "dotaciones": dotaciones,
        "sobrantes_viaticos": sobrantes_viaticos,
        "sobrantes_gastos": sobrantes_gastos,
        "otros_ajustes": otros_ajustes,
        "gastos": gastos_data,
        "denominaciones": denoms_data,
        "denominaciones_monedas": denoms_monedas,
        "denominaciones_billetes": denoms_billetes,
        "denominaciones_total_monedas": sum((_safe_decimal(row["subtotal"]) for row in denoms_monedas), Decimal("0")),
        "denominaciones_total_billetes": sum((_safe_decimal(row["subtotal"]) for row in denoms_billetes), Decimal("0")),
        "denominaciones_total_general": sum((_safe_decimal(row["subtotal"]) for row in denoms_data), Decimal("0")),
        "compras_rows": compras_rows,
        "ventas_rows": ventas_rows,
        "compras_total": sum((_safe_decimal(row.get("subtotal")) for row in compras_rows), Decimal("0")),
        "ventas_total": sum((_safe_decimal(row.get("subtotal")) for row in ventas_rows), Decimal("0")),
        "compras_kg": sum((_safe_decimal(row.get("kg_neto")) for row in compras_rows), Decimal("0")),
        "ventas_kg": sum((_safe_decimal(row.get("kg_neto")) for row in ventas_rows), Decimal("0")),
    }
    return report


def build_report_excel(report: dict) -> tuple[bytes, str]:
    rows = []

    def add_row(values: list[str]) -> None:
        cells = "".join([f"<Cell><Data ss:Type='String'>{_xml_escape(str(v))}</Data></Cell>" for v in values])
        rows.append(f"<Row>{cells}</Row>")

    add_row([f"Corte de Caja Scrap360 - {report['sucursal']}"])
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
        add_row([item["label"], _format_summary_value(_safe_decimal(item["value"]), str(item.get("type") or "money"))])
    if report.get("motivo_diferencia"):
        add_row(["Motivo diferencia", report["motivo_diferencia"]])
    if report.get("comentarios_cierre"):
        add_row(["Comentarios cierre", report["comentarios_cierre"]])
    add_row([])

    add_row(["Arqueo por denominaciones"])
    add_row(["Monedas"])
    add_row(["Denominacion", "Cantidad", "Subtotal"])
    if report["denominaciones_monedas"]:
        for denom in report["denominaciones_monedas"]:
            add_row([
                f"${denom['valor']:.2f}",
                str(denom["cantidad"]),
                _format_money(_safe_decimal(denom["subtotal"])),
            ])
        add_row(["Total monedas", "", _format_money(_safe_decimal(report["denominaciones_total_monedas"]))])
    else:
        add_row(["Sin monedas registradas", "", ""])
    add_row(["Billetes"])
    add_row(["Denominacion", "Cantidad", "Subtotal"])
    if report["denominaciones_billetes"]:
        for denom in report["denominaciones_billetes"]:
            add_row([
                f"${denom['valor']:.2f}",
                str(denom["cantidad"]),
                _format_money(_safe_decimal(denom["subtotal"])),
            ])
        add_row(["Total billetes", "", _format_money(_safe_decimal(report["denominaciones_total_billetes"]))])
    else:
        add_row(["Sin billetes registrados", "", ""])
    add_row(["Total general contado", "", _format_money(_safe_decimal(report["denominaciones_total_general"]))])
    add_row([])

    add_row(["Relacion de compras del dia"])
    add_row(["Orden", "Proveedor", "Material", "Kg", "Precio", "Total linea", "Total orden", "Metodo", "Cuenta Scrap360", "No. Cheque"])
    if report["compras_rows"]:
        seen_compra = set()
        for row in report["compras_rows"]:
            is_first = row.get("nota_id") not in seen_compra
            if is_first:
                seen_compra.add(row.get("nota_id"))
            add_row([
                row.get("folio") or "-",
                row.get("partner") or "-",
                row.get("material") or "-",
                f"{_safe_decimal(row.get('kg_neto')):,.3f}",
                _format_money(_safe_decimal(row.get("precio_unitario"))),
                _format_money(_safe_decimal(row.get("subtotal"))),
                _format_money(_safe_decimal(row.get("total_nota"))),
                str(row.get("metodo_pago") or "-") if is_first else "",
                str(row.get("cuenta_scrap360_nombre") or "-") if is_first else "",
                str(row.get("numero_cheque") or "-") if is_first else "",
            ])
        add_row(["Totales compras", "", "", f"{_safe_decimal(report.get('compras_kg')):,.3f}", "", _format_money(_safe_decimal(report.get("compras_total"))), "", "", "", ""])
    else:
        add_row(["Sin compras registradas"])
    add_row([])

    add_row(["Relacion de ventas del dia"])
    add_row(["Orden", "Cliente", "Material", "Kg", "Precio", "Total linea", "Total orden", "Metodo", "Cuenta Scrap360", "No. Cheque"])
    if report["ventas_rows"]:
        seen_venta = set()
        for row in report["ventas_rows"]:
            is_first = row.get("nota_id") not in seen_venta
            if is_first:
                seen_venta.add(row.get("nota_id"))
            add_row([
                row.get("folio") or "-",
                row.get("partner") or "-",
                row.get("material") or "-",
                f"{_safe_decimal(row.get('kg_neto')):,.3f}",
                _format_money(_safe_decimal(row.get("precio_unitario"))),
                _format_money(_safe_decimal(row.get("subtotal"))),
                _format_money(_safe_decimal(row.get("total_nota"))),
                str(row.get("metodo_pago") or "-") if is_first else "",
                str(row.get("cuenta_scrap360_nombre") or "-") if is_first else "",
                str(row.get("numero_cheque") or "-") if is_first else "",
            ])
        add_row(["Totales ventas", "", "", f"{_safe_decimal(report.get('ventas_kg')):,.3f}", "", _format_money(_safe_decimal(report.get("ventas_total"))), "", "", "", ""])
    else:
        add_row(["Sin ventas registradas"])
    add_row([])

    add_row(["Dotacion de efectivo"])
    add_row(["Fecha", "Concepto", "Monto"])
    if report["dotaciones"]:
        for row in report["dotaciones"]:
            add_row([
                format_datetime_local(row["fecha"]) if row.get("fecha") else "-",
                row.get("descripcion") or "",
                _format_money(_safe_decimal(row.get("monto_abs"))),
            ])
    else:
        add_row(["Sin dotaciones"])
    add_row([])

    add_row(["Ventas de material en efectivo"])
    add_row(["Fecha", "Orden", "Cliente", "Caja", "Monto"])
    if report["ventas_efectivo_rows"]:
        for row in report["ventas_efectivo_rows"]:
            add_row([
                format_datetime_local(row["fecha"]) if row.get("fecha") else "-",
                row.get("folio") or "-",
                row.get("partner") or "-",
                row.get("caja_sucursal") or "-",
                _format_money(_safe_decimal(row.get("monto_abs"))),
            ])
    else:
        add_row(["Sin ventas en efectivo"])
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

    add_row(["Sobrantes de viaticos"])
    add_row(["Fecha", "Concepto", "Monto"])
    if report["sobrantes_viaticos"]:
        for row in report["sobrantes_viaticos"]:
            add_row([
                format_datetime_local(row["fecha"]) if row.get("fecha") else "-",
                row.get("descripcion") or "",
                _format_money(_safe_decimal(row.get("monto_abs"))),
            ])
    else:
        add_row(["Sin sobrantes de viaticos"])
    add_row([])

    add_row(["Sobrantes de gastos"])
    add_row(["Fecha", "Concepto", "Monto"])
    if report["sobrantes_gastos"]:
        for row in report["sobrantes_gastos"]:
            add_row([
                format_datetime_local(row["fecha"]) if row.get("fecha") else "-",
                row.get("descripcion") or "",
                _format_money(_safe_decimal(row.get("monto_abs"))),
            ])
    else:
        add_row(["Sin sobrantes de gastos"])
    add_row([])

    add_row(["Movimientos efectivo (notas)"])
    add_row(["Fecha", "Tipo", "Detalle", "Folio", "Partner", "Caja", "Monto", "Comentario"])
    if report["cash_movs"]:
        for mov in report["cash_movs"]:
            add_row([
                format_datetime_local(mov["fecha"]) if mov.get("fecha") else "-",
                mov.get("tipo") or "-",
                mov.get("detalle") or "-",
                mov.get("folio") or "-",
                mov.get("partner") or "-",
                mov.get("caja_sucursal") or "-",
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


# Helvetica proportional-width estimate per character.
# 0.5 underestimates uppercase-heavy text; 0.6 keeps wrapping/truncation
# conservative enough that long provider/client names don't overflow columns.
_CHAR_WIDTH_FACTOR = 0.6


def _text_width(text: str, size: int) -> float:
    return len(text) * size * _CHAR_WIDTH_FACTOR


def _truncate_text(text: str, max_width: float, size: int) -> str:
    if _text_width(text, size) <= max_width:
        return text
    ellipsis = "..."
    max_chars = max(1, int(max_width / (size * _CHAR_WIDTH_FACTOR)) - len(ellipsis))
    return f"{text[:max_chars]}{ellipsis}"


def _wrap_text(text: str, max_width: float, size: int) -> list[str]:
    value = str(text or "").strip()
    if not value:
        return [""]
    words = value.split()
    if not words:
        return [value]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if _text_width(candidate, size) <= max_width:
            current = candidate
            continue
        lines.append(current)
        current = word
    lines.append(current)
    return lines or [value]


class _PdfPage:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def text(
        self,
        x: float,
        y: float,
        text: str,
        size: int = 10,
        font: str = "F1",
        *,
        fill_gray: float | None = None,
        fill_rgb: tuple[float, float, float] | None = None,
    ) -> None:
        safe = _escape_pdf(text)
        if fill_rgb is not None:
            self.commands.append(f"{fill_rgb[0]:.3f} {fill_rgb[1]:.3f} {fill_rgb[2]:.3f} rg")
        elif fill_gray is not None:
            self.commands.append(f"{fill_gray:.2f} g")
        self.commands.append(f"BT /{font} {size} Tf {x:.2f} {y:.2f} Td ({safe}) Tj ET")
        if fill_rgb is not None or fill_gray is not None:
            self.commands.append("0 g")

    def line(self, x1: float, y1: float, x2: float, y2: float) -> None:
        self.commands.append(f"{x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S")

    def rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        fill_gray: float | None = None,
        stroke_gray: float | None = None,
        *,
        fill_rgb: tuple[float, float, float] | None = None,
        stroke_rgb: tuple[float, float, float] | None = None,
    ) -> None:
        if fill_rgb is not None:
            self.commands.append(f"{fill_rgb[0]:.3f} {fill_rgb[1]:.3f} {fill_rgb[2]:.3f} rg")
        elif fill_gray is not None:
            self.commands.append(f"{fill_gray:.2f} g")
        if stroke_rgb is not None:
            self.commands.append(f"{stroke_rgb[0]:.3f} {stroke_rgb[1]:.3f} {stroke_rgb[2]:.3f} RG")
        elif stroke_gray is not None:
            self.commands.append(f"{stroke_gray:.2f} G")
        op = "f" if fill_gray is not None else "S"
        if fill_rgb is not None:
            op = "f"
        elif (fill_gray is not None or fill_rgb is not None) and (stroke_gray is not None or stroke_rgb is not None):
            op = "B"
        elif stroke_gray is not None or stroke_rgb is not None:
            op = "S"
        self.commands.append(f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} re {op}")
        if fill_gray is not None or fill_rgb is not None:
            self.commands.append("0 g")
        if stroke_gray is not None or stroke_rgb is not None:
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
        font_italic_id = font_regular_id + 2

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
                    f"/F1 {font_regular_id} 0 R /F2 {font_bold_id} 0 R /F3 {font_italic_id} 0 R >> >> >>"
                ).encode("latin-1"),
            )
            obj(content_obj_id, f"<< /Length {len(stream_content)} >>\nstream\n".encode() + stream_content + b"\nendstream")

        # /WinAnsiEncoding: sin ella el visor aplica StandardEncoding, donde los
        # bytes latin-1 de los acentos no tienen glifo y se pintan como cajas.
        obj(font_regular_id, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        obj(font_bold_id, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        obj(font_italic_id, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Oblique /Encoding /WinAnsiEncoding >>")

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
    brand = (0.078, 0.133, 0.247)
    accent = (0.118, 0.227, 0.404)
    accent_soft = (0.898, 0.937, 0.988)
    line_soft = (0.812, 0.867, 0.933)
    green_soft = (0.906, 0.973, 0.933)
    amber_soft = (0.996, 0.953, 0.831)
    red_soft = (0.992, 0.914, 0.914)

    generated = format_datetime_local(report["generated_at"])
    fecha = report["fecha"].isoformat()

    page.rect(left, y - 74, right - left, 82, fill_rgb=brand)
    page.text(left + 12, y - 8, "Corte de Caja Scrap360", size=18, font="F2", fill_rgb=(1, 1, 1))
    page.text(left + 12, y - 24, report["sucursal"], size=12, font="F3", fill_rgb=(0.92, 0.95, 1))
    page.text(left + 12, y - 40, f"Fecha operativa: {fecha}", size=9, fill_rgb=(0.92, 0.95, 1))
    page.text(left + 12, y - 52, f"Estado: {report['estado']}", size=9, fill_rgb=(0.92, 0.95, 1))
    page.text(
        left + 220,
        y - 40,
        f"Abierto por: {report.get('abierto_por') or '-'} ({format_datetime_local(report['opened_at']) if report.get('opened_at') else '-'})",
        size=9,
        fill_rgb=(0.92, 0.95, 1),
    )
    page.text(
        left + 220,
        y - 52,
        f"Cerrado por: {report.get('cerrado_por') or '-'} ({format_datetime_local(report['closed_at']) if report.get('closed_at') else '-'})",
        size=9,
        fill_rgb=(0.92, 0.95, 1),
    )
    page.text(right - 145, y - 18, f"Generado: {generated}", size=9, fill_rgb=(0.92, 0.95, 1))

    y = y - 98
    page.text(left, y, "Resumen financiero y operativo", size=11, font="F2", fill_rgb=brand)
    page.text(left, y - 13, "Lectura rapida del corte, la caja esperada y la diferencia del dia.", size=9, font="F3", fill_gray=0.35)
    y = y - 30

    items = report["summary_items"]
    card_cols = 3
    card_gap = 10
    card_w = ((right - left) - (card_gap * (card_cols - 1))) / card_cols
    card_h = 36
    for idx, item in enumerate(items):
        row = idx // card_cols
        col = idx % card_cols
        x = left + col * (card_w + card_gap)
        card_y = y - row * (card_h + 8)
        label = item["label"]
        value = _safe_decimal(item["value"])
        value_str = _format_summary_value(value, str(item.get("type") or "money"))
        fill = accent_soft
        if "diferencia" in label.lower():
            fill = green_soft if value >= 0 else red_soft
        elif "gastos" in label.lower():
            fill = amber_soft
        page.rect(x, card_y - card_h, card_w, card_h, fill_rgb=fill, stroke_rgb=line_soft)
        page.text(x + 8, card_y - 12, label.upper(), size=7, font="F2", fill_gray=0.42)
        page.text(x + 8, card_y - 26, value_str, size=11, font="F2", fill_rgb=brand)

    y = y - (((len(items) + card_cols - 1) // card_cols) * (card_h + 8)) - 8

    if report.get("motivo_diferencia"):
        page.rect(left, y - 22, right - left, 18, fill_rgb=amber_soft, stroke_rgb=line_soft)
        page.text(left + 8, y - 10, f"Motivo diferencia: {report['motivo_diferencia']}", size=9, font="F2")
        y -= 26
    if report.get("comentarios_cierre"):
        wrapped_comments = _wrap_text(f"Comentarios cierre: {report['comentarios_cierre']}", right - left - 16, 9)
        box_h = max(24, 12 * len(wrapped_comments) + 8)
        page.rect(left, y - box_h, right - left, box_h, fill_rgb=(0.965, 0.973, 0.985), stroke_rgb=line_soft)
        line_y = y - 12
        for line in wrapped_comments:
            page.text(left + 8, line_y, line, size=9)
            line_y -= 11
        y -= box_h + 8

    def new_page_header(title: str) -> None:
        nonlocal page, y
        page = doc.new_page()
        y = top
        page.rect(left, y - 34, right - left, 42, fill_rgb=brand)
        page.text(left + 10, y - 10, title, size=13, font="F2", fill_rgb=(1, 1, 1))
        page.text(left + 10, y - 24, f"Sucursal: {report['sucursal']}  |  Fecha: {fecha}", size=9, fill_rgb=(0.92, 0.95, 1))
        y -= 48

    def ensure_space(min_y: float) -> None:
        nonlocal y
        if y < min_y:
            new_page_header("Corte de caja (continuacion)")

    def draw_table_header(title: str, cols: list[tuple[str, float, float, str]]) -> None:
        nonlocal y
        ensure_space(120)
        page.text(left, y, title, size=11, font="F2", fill_rgb=brand)
        y -= 12
        page.text(left, y - 1, "Detalle cronologico y subtotalizado del corte.", size=8, font="F3", fill_gray=0.45)
        y -= 12
        page.rect(left, y - 14, right - left, 14, fill_rgb=accent, stroke_rgb=accent)
        for col_title, x, width, align in cols:
            draw_x = x + 2
            if align == "right":
                draw_x = x + width - _text_width(col_title, 8) - 2
            page.text(draw_x, y - 5, col_title, size=8, font="F2", fill_rgb=(1, 1, 1))
        y -= 24

    _row_counter = [0]

    def draw_row(
        cols: list[tuple[str, float, float, str]],
        values: list[str],
        bg_rgb: tuple[float, float, float] | None = None,
        bold: bool = False,
    ) -> None:
        nonlocal y
        _row_counter[0] += 1
        font_body = "F2" if bold else "F1"
        cell_lines: list[list[str]] = []
        max_lines = 1
        for (_, _, width, align), text in zip(cols, values):
            if align == "right":
                lines = [_truncate_text(str(text), width - 4, 8)]
            else:
                lines = _wrap_text(str(text), width - 4, 8)
            max_lines = max(max_lines, len(lines))
            cell_lines.append(lines)
        row_h = max(13, max_lines * 10 + 4)
        ensure_space(80 + row_h)
        line_y_start = y
        row_x = cols[0][1]
        row_w = right - row_x
        if bg_rgb is not None:
            page.rect(row_x, line_y_start - row_h, row_w, row_h + 6, fill_rgb=bg_rgb)
        elif bold:
            page.rect(row_x, line_y_start - row_h, row_w, row_h + 6, fill_rgb=(0.937, 0.949, 0.969))
        elif _row_counter[0] % 2 == 0:
            page.rect(row_x, line_y_start - row_h, row_w, row_h + 6, fill_rgb=(0.973, 0.976, 0.98))
        for (col_title, x, width, align), lines in zip(cols, cell_lines):
            if align == "right":
                for idx, display in enumerate(lines):
                    draw_x = x + width - _text_width(display, 8) - 2
                    page.text(draw_x, line_y_start - idx * 10, display, size=8, font=font_body)
            else:
                for idx, display in enumerate(lines):
                    page.text(x + 2, line_y_start - idx * 10, display, size=8, font=font_body)
        y -= row_h

    # ── Payment-method helpers ────────────────────────────────────────────
    _pago_colors: dict[str, tuple[float, float, float]] = {
        "efectivo": green_soft,       # light green
        "transferencia": accent_soft,  # light blue
        "cheque": amber_soft,          # light amber
    }

    def _row_pago_color(row: dict) -> tuple[float, float, float] | None:
        return _pago_colors.get((row.get("metodo_pago") or "").strip().lower())

    def _row_folio_display(row: dict) -> str:
        """Return folio with a short inline payment-method tag.
        The tag stays on the SAME line as the folio so the Orden column
        never wraps to a visually empty second line in the other columns."""
        folio = row.get("folio") or "-"
        metodo = (row.get("metodo_pago") or "").strip().lower()
        if not metodo:
            return folio
        if metodo == "efectivo":
            abbr, ref = "ef", ""
        elif metodo == "cheque":
            abbr = "ch"
            num = (row.get("numero_cheque") or "").strip()
            cuenta360 = (row.get("cuenta_scrap360_nombre") or "").strip()
            ref = f"#{num}" if num else ""
            if cuenta360:
                ref = f"{ref} {cuenta360}".strip()
        else:
            abbr = "tr"
            ref = (row.get("cuenta_scrap360_nombre") or row.get("cuenta_financiera_label") or "").strip()
        # "02_C_116 ef" = 11 chars = 52.8 px < 56 px effective → fits one line.
        result = f"{folio} {abbr}"
        if ref:
            # Reference goes on a second line (only when a cheque/account ref exists).
            result += f" {_truncate_text(ref, 56.0, 8)}"
        return result

    def draw_pago_legend() -> None:
        """Draw a compact colour-key legend just below a table header."""
        nonlocal y
        legend_items = [
            ("Efectivo", green_soft),
            ("Transferencia", accent_soft),
            ("Cheque", amber_soft),
        ]
        lx = left
        for label, color in legend_items:
            page.rect(lx, y - 10, 8, 8, fill_rgb=color, stroke_rgb=line_soft)
            page.text(lx + 10, y - 9, label, size=7, font="F3", fill_gray=0.4)
            lx += 85
        y -= 14

    # Column widths designed so money cols (65+ pts each) never truncate
    # values up to $9,999,999.99 (13 chars × 8 × 0.6 = 62.4 pts + 4 margins).
    # Total: 62+100+108+52+64+67+67 = 520 = right - left
    compra_cols = [
        ("Orden",       left,           62, "left"),
        ("Proveedor",   left + 62,     100, "left"),
        ("Material",    left + 162,    108, "left"),
        ("Kg",          left + 270,     52, "right"),
        ("Precio",      left + 322,     64, "right"),
        ("Total linea", left + 386,     67, "right"),
        ("Total orden", left + 453,     67, "right"),
    ]
    _row_counter[0] = 0
    draw_table_header("Relacion de compras del dia", compra_cols)
    draw_pago_legend()
    if report["compras_rows"]:
        seen_nota: set = set()
        compras_kg_total = Decimal("0")
        compras_monto_total = Decimal("0")
        for row in report["compras_rows"]:
            nota_id = row.get("nota_id")
            is_first = nota_id not in seen_nota
            if is_first and nota_id:
                seen_nota.add(nota_id)
            compras_kg_total += _safe_decimal(row.get("kg_neto"))
            compras_monto_total += _safe_decimal(row.get("subtotal"))
            draw_row(
                compra_cols,
                [
                    _row_folio_display(row),
                    row.get("partner") or "-",
                    row.get("material") or "-",
                    f"{_safe_decimal(row.get('kg_neto')):,.3f}",
                    _format_money(_safe_decimal(row.get("precio_unitario"))),
                    _format_money(_safe_decimal(row.get("subtotal"))),
                    _format_money(_safe_decimal(row.get("total_nota"))) if is_first else "",
                ],
                bg_rgb=_row_pago_color(row),
            )
        draw_row(
            compra_cols,
            ["TOTALES", f"{len(seen_nota)} notas", "", f"{compras_kg_total:,.3f}", "", _format_money(compras_monto_total), ""],
            bold=True,
        )
    else:
        draw_row(compra_cols, ["Sin compras registradas", "", "", "", "", "", ""])

    venta_cols = [
        ("Orden",       left,           62, "left"),
        ("Cliente",     left + 62,     100, "left"),
        ("Material",    left + 162,    108, "left"),
        ("Kg",          left + 270,     52, "right"),
        ("Precio",      left + 322,     64, "right"),
        ("Total linea", left + 386,     67, "right"),
        ("Total orden", left + 453,     67, "right"),
    ]
    _row_counter[0] = 0
    draw_table_header("Relacion de ventas del dia", venta_cols)
    draw_pago_legend()
    if report["ventas_rows"]:
        seen_nota_v: set = set()
        ventas_kg_total = Decimal("0")
        ventas_monto_total = Decimal("0")
        for row in report["ventas_rows"]:
            nota_id = row.get("nota_id")
            is_first = nota_id not in seen_nota_v
            if is_first and nota_id:
                seen_nota_v.add(nota_id)
            ventas_kg_total += _safe_decimal(row.get("kg_neto"))
            ventas_monto_total += _safe_decimal(row.get("subtotal"))
            draw_row(
                venta_cols,
                [
                    _row_folio_display(row),
                    row.get("partner") or "-",
                    row.get("material") or "-",
                    f"{_safe_decimal(row.get('kg_neto')):,.3f}",
                    _format_money(_safe_decimal(row.get("precio_unitario"))),
                    _format_money(_safe_decimal(row.get("subtotal"))),
                    _format_money(_safe_decimal(row.get("total_nota"))) if is_first else "",
                ],
                bg_rgb=_row_pago_color(row),
            )
        draw_row(
            venta_cols,
            ["TOTALES", f"{len(seen_nota_v)} notas", "", f"{ventas_kg_total:,.3f}", "", _format_money(ventas_monto_total), ""],
            bold=True,
        )
    else:
        draw_row(venta_cols, ["Sin ventas registradas", "", "", "", "", "", ""])

    dot_cols = [
        ("Fecha", left, 90, "left"),
        ("Concepto", left + 90, 320, "left"),
        ("Monto", left + 410, 80, "right"),
    ]
    _row_counter[0] = 0
    draw_table_header("Dotacion de efectivo", dot_cols)
    if report["dotaciones"]:
        for row in report["dotaciones"]:
            draw_row(
                dot_cols,
                [
                    format_date_local(row["fecha"]) if row.get("fecha") else "-",
                    row.get("descripcion") or "",
                    _format_money(_safe_decimal(row.get("monto_abs"))),
                ],
            )
    else:
        draw_row(dot_cols, ["Sin dotaciones", "", ""])

    venta_ef_cols = [
        ("Fecha", left, 90, "left"),
        ("Orden", left + 90, 70, "left"),
        ("Cliente", left + 160, 180, "left"),
        ("Caja", left + 340, 90, "left"),
        ("Monto", left + 430, 80, "right"),
    ]
    _row_counter[0] = 0
    draw_table_header("Ventas de material en efectivo", venta_ef_cols)
    if report["ventas_efectivo_rows"]:
        for row in report["ventas_efectivo_rows"]:
            draw_row(
                venta_ef_cols,
                [
                    format_date_local(row["fecha"]) if row.get("fecha") else "-",
                    row.get("folio") or "-",
                    row.get("partner") or "-",
                    row.get("caja_sucursal") or "-",
                    _format_money(_safe_decimal(row.get("monto_abs"))),
                ],
            )
    else:
        draw_row(venta_ef_cols, ["Sin ventas en efectivo", "", "", "", ""])

    # Denominaciones
    denom_cols = [
        ("Denominacion", left, 160, "left"),
        ("Cantidad", left + 160, 80, "right"),
        ("Subtotal", left + 240, 80, "right"),
    ]
    _row_counter[0] = 0
    draw_table_header("Arqueo por denominaciones - Monedas", denom_cols)
    if report["denominaciones_monedas"]:
        for denom in report["denominaciones_monedas"]:
            draw_row(
                denom_cols,
                [
                    _format_money(_safe_decimal(denom["valor"])),
                    str(denom["cantidad"]),
                    _format_money(_safe_decimal(denom["subtotal"])),
                ],
            )
        draw_row(denom_cols, ["Total monedas", "", _format_money(_safe_decimal(report["denominaciones_total_monedas"]))])
    else:
        draw_row(denom_cols, ["Sin monedas registradas", "", ""])

    _row_counter[0] = 0
    draw_table_header("Arqueo por denominaciones - Billetes", denom_cols)
    if report["denominaciones_billetes"]:
        for denom in report["denominaciones_billetes"]:
            draw_row(
                denom_cols,
                [
                    _format_money(_safe_decimal(denom["valor"])),
                    str(denom["cantidad"]),
                    _format_money(_safe_decimal(denom["subtotal"])),
                ],
            )
        draw_row(denom_cols, ["Total billetes", "", _format_money(_safe_decimal(report["denominaciones_total_billetes"]))])
    else:
        draw_row(denom_cols, ["Sin billetes registrados", "", ""])
    draw_row(denom_cols, ["Total general contado", "", _format_money(_safe_decimal(report["denominaciones_total_general"]))])

    # Movimientos manuales
    manual_cols = [
        ("Fecha", left, 80, "left"),
        ("Tipo", left + 80, 60, "left"),
        ("Categoria", left + 140, 110, "left"),
        ("Descripcion", left + 250, 130, "left"),  # was 150 → fits page
        ("Usuario", left + 380, 80, "left"),
        ("Monto", left + 460, 60, "right"),
    ]
    _row_counter[0] = 0
    draw_table_header("Movimientos manuales", manual_cols)
    if report["manual_movs"]:
        for mov in report["manual_movs"]:
            draw_row(
                manual_cols,
                [
                    format_date_local(mov["fecha"]) if mov.get("fecha") else "-",
                    mov.get("tipo_label") or mov.get("tipo") or "-",
                    mov.get("categoria_label") or "-",
                    mov.get("descripcion") or "",
                    mov.get("usuario") or "-",
                    _format_money(_safe_decimal(mov.get("monto"))),
                ],
            )
    else:
        draw_row(manual_cols, ["Sin movimientos manuales", "", "", "", "", ""])

    sobrante_cols = [
        ("Fecha", left, 90, "left"),
        ("Concepto", left + 90, 320, "left"),
        ("Monto", left + 410, 80, "right"),
    ]
    _row_counter[0] = 0
    draw_table_header("Sobrantes de viaticos", sobrante_cols)
    if report["sobrantes_viaticos"]:
        for row in report["sobrantes_viaticos"]:
            draw_row(
                sobrante_cols,
                [
                    format_date_local(row["fecha"]) if row.get("fecha") else "-",
                    row.get("descripcion") or "",
                    _format_money(_safe_decimal(row.get("monto_abs"))),
                ],
            )
    else:
        draw_row(sobrante_cols, ["Sin sobrantes de viaticos", "", ""])

    _row_counter[0] = 0
    draw_table_header("Sobrantes de gastos", sobrante_cols)
    if report["sobrantes_gastos"]:
        for row in report["sobrantes_gastos"]:
            draw_row(
                sobrante_cols,
                [
                    format_date_local(row["fecha"]) if row.get("fecha") else "-",
                    row.get("descripcion") or "",
                    _format_money(_safe_decimal(row.get("monto_abs"))),
                ],
            )
    else:
        draw_row(sobrante_cols, ["Sin sobrantes de gastos", "", ""])

    # Movimientos efectivo
    cash_cols = [
        ("Fecha", left, 80, "left"),
        ("Tipo", left + 80, 60, "left"),
        ("Folio", left + 140, 60, "left"),
        ("Partner", left + 200, 150, "left"),
        ("Caja", left + 350, 90, "left"),
        ("Monto", left + 440, 70, "right"),
    ]
    _row_counter[0] = 0
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
                    mov.get("caja_sucursal") or "-",
                    _format_money(_safe_decimal(mov.get("monto"))),
                ],
            )
    else:
        draw_row(cash_cols, ["Sin movimientos en efectivo", "", "", "", "", ""])

    # Gastos
    gasto_cols = [
        ("Fecha", left, 80, "left"),
        ("Descripcion", left + 80, 180, "left"),   # was 200 → fits page
        ("Categoria", left + 260, 100, "left"),
        ("Usuario", left + 360, 90, "left"),
        ("Monto", left + 450, 70, "right"),
    ]
    _row_counter[0] = 0
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

    ensure_space(150)
    page.text(left, y, "Firmas y validacion", size=11, font="F2", fill_rgb=brand)
    page.text(left, y - 12, "Espacio para validar la elaboracion, revision y autorizacion del corte.", size=8, font="F3", fill_gray=0.45)
    y -= 28
    signature_rows = [
        ("Elaboro", report.get("abierto_por") or "-"),
        ("Reviso", report.get("cerrado_por") or "-"),
        ("Autorizo", ""),
        ("Responsable de caja", ""),
    ]
    sig_w = (right - left - 14) / 2
    sig_h = 34
    for idx, (label, name) in enumerate(signature_rows):
        col = idx % 2
        row = idx // 2
        x = left + col * (sig_w + 14)
        box_y = y - row * (sig_h + 16)
        page.rect(x, box_y - sig_h, sig_w, sig_h, fill_rgb=(0.988, 0.992, 0.998), stroke_rgb=line_soft)
        page.text(x + 8, box_y - 11, label.upper(), size=8, font="F2", fill_gray=0.42)
        page.text(x + 8, box_y - 23, name or "________________", size=9)
        page.line(x + 8, box_y - 30, x + sig_w - 8, box_y - 30)

    pdf_bytes = doc.render()
    filename = f"corte_caja_{_safe_filename(report['sucursal'])}_{report['fecha'].isoformat()}.pdf"
    return pdf_bytes, filename
