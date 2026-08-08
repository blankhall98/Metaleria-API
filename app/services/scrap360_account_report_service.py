from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.core.datetime_utils import format_datetime_local
from app.services.contabilidad_report_service import (
    _PdfDocument,
    _PdfPage,
    _format_money,
    _safe_decimal,
    _safe_filename,
    _text_width,
    _truncate_text,
    _xml_escape,
)
from app.services import note_service


def _sheet_cell(value: str, cell_type: str = "String") -> str:
    return f"<Cell><Data ss:Type='{cell_type}'>{_xml_escape(value)}</Data></Cell>"


def _sheet_row(values: list[tuple[str, str]]) -> str:
    return "<Row>" + "".join([_sheet_cell(v, t) for v, t in values]) + "</Row>"


def _movement_breakdown(mov) -> tuple[Decimal, Decimal, str]:
    monto = _safe_decimal(getattr(mov, "monto", 0))
    tipo = (getattr(mov, "tipo", "") or "").strip().lower()
    if tipo == "ingreso":
        return monto.copy_abs(), Decimal("0"), "Entrada"
    if tipo == "egreso":
        return Decimal("0"), monto.copy_abs(), "Salida"
    if monto >= 0:
        return monto, Decimal("0"), "Ajuste a favor"
    return Decimal("0"), abs(monto), "Ajuste en contra"


def build_scrap360_account_statement(account, movimientos: list, *, generated_at: datetime | None = None) -> dict:
    generated = generated_at or datetime.utcnow()
    movimientos_asc = sorted(movimientos, key=lambda mov: (mov.created_at or generated, mov.id or 0))
    total_entradas = Decimal("0")
    total_salidas = Decimal("0")
    rows: list[dict] = []

    for mov in movimientos_asc:
        entrada, salida, tipo_label = _movement_breakdown(mov)
        total_entradas += entrada
        total_salidas += salida
        nota = getattr(mov, "nota", None)
        folio = None
        if nota:
            folio = note_service.format_folio(
                sucursal_id=nota.sucursal_id,
                tipo_operacion=nota.tipo_operacion,
                folio_seq=nota.folio_seq,
            ) or f"#{nota.id}"
        rows.append(
            {
                "fecha": mov.created_at,
                "tipo": tipo_label,
                "referencia": folio or "-",
                "monto_entrada": entrada,
                "monto_salida": salida,
                "saldo": _safe_decimal(getattr(mov, "saldo_resultante", 0)),
                "usuario": (
                    getattr(getattr(mov, "usuario", None), "nombre_completo", None)
                    or getattr(getattr(mov, "usuario", None), "username", None)
                    or "-"
                ),
                "comentario": getattr(mov, "comentario", None) or "-",
            }
        )

    return {
        "generated_at": generated,
        "account_name": account.nombre,
        "account_type": account.tipo,
        "account_id": account.id,
        "sucursales_label": ", ".join([s.nombre for s in getattr(account, "sucursales", [])]) or "-",
        "saldo_inicial": _safe_decimal(getattr(account, "saldo_inicial", 0)),
        "saldo_actual": _safe_decimal(getattr(account, "saldo_actual", 0)),
        "total_entradas": total_entradas,
        "total_salidas": total_salidas,
        "movimientos_total": len(movimientos_asc),
        "rows": rows,
    }


def build_scrap360_account_statement_excel(report: dict) -> tuple[bytes, str]:
    summary_rows = [
        _sheet_row([(f"Estado de cuenta Scrap360", "String")]),
        _sheet_row([(f"Cuenta: {report['account_name']}", "String")]),
        _sheet_row([(f"ID: {report['account_id']}", "String")]),
        _sheet_row([(f"Tipo: {report['account_type']}", "String")]),
        _sheet_row([(f"Sucursales: {report['sucursales_label']}", "String")]),
        _sheet_row([(f"Generado: {format_datetime_local(report['generated_at'])}", "String")]),
        _sheet_row([("", "String")]),
        _sheet_row([("Saldo de apertura", "String"), (str(_safe_decimal(report["saldo_inicial"])), "Number")]),
        _sheet_row([("Entradas", "String"), (str(_safe_decimal(report["total_entradas"])), "Number")]),
        _sheet_row([("Salidas", "String"), (str(_safe_decimal(report["total_salidas"])), "Number")]),
        _sheet_row([("Saldo actual", "String"), (str(_safe_decimal(report["saldo_actual"])), "Number")]),
        _sheet_row([("Movimientos", "String"), (str(report["movimientos_total"]), "Number")]),
    ]

    ledger_rows = [
        "<Row>"
        + "".join(
            [
                _sheet_cell("Fecha"),
                _sheet_cell("Tipo"),
                _sheet_cell("Referencia"),
                _sheet_cell("Entrada"),
                _sheet_cell("Salida"),
                _sheet_cell("Saldo"),
                _sheet_cell("Usuario"),
                _sheet_cell("Comentario"),
            ]
        )
        + "</Row>"
    ]
    for row in report["rows"]:
        ledger_rows.append(
            _sheet_row(
                [
                    (format_datetime_local(row["fecha"]) if row.get("fecha") else "-", "String"),
                    (row["tipo"], "String"),
                    (row["referencia"], "String"),
                    (str(_safe_decimal(row["monto_entrada"])), "Number"),
                    (str(_safe_decimal(row["monto_salida"])), "Number"),
                    (str(_safe_decimal(row["saldo"])), "Number"),
                    (row["usuario"], "String"),
                    (row["comentario"], "String"),
                ]
            )
        )

    workbook = f"""<?xml version="1.0"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
 xmlns:o="urn:schemas-microsoft-com:office:office"
 xmlns:x="urn:schemas-microsoft-com:office:excel"
 xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
 <Worksheet ss:Name="Resumen">
  <Table>
   {''.join(summary_rows)}
  </Table>
 </Worksheet>
 <Worksheet ss:Name="EstadoCuenta">
  <Table>
   {''.join(ledger_rows)}
  </Table>
 </Worksheet>
</Workbook>"""

    filename = f"estado_cuenta_scrap360_{_safe_filename(report['account_name'])}.xls"
    return workbook.encode("utf-8"), filename


def build_scrap360_account_statement_pdf(report: dict) -> tuple[bytes, str]:
    left = 42
    right = 570
    top = 760
    bottom = 58

    doc = _PdfDocument()

    def new_page(title_suffix: str | None = None) -> tuple[_PdfPage, float]:
        page = doc.new_page()
        y_pos = top
        title = "Estado de cuenta Scrap360"
        if title_suffix:
            title = f"{title} {title_suffix}"
        page.text(left, y_pos, title, size=16, font="F2")
        page.text(left, y_pos - 16, f"Cuenta: {report['account_name']}", size=9)
        page.text(left, y_pos - 28, f"Tipo: {report['account_type']}", size=9)
        page.text(left, y_pos - 40, f"Sucursales: {report['sucursales_label']}", size=9)
        page.text(right - 156, y_pos - 16, f"Generado: {format_datetime_local(report['generated_at'])}", size=9)
        page.line(left, y_pos - 48, right, y_pos - 48)
        return page, y_pos - 66

    page, y = new_page()
    page.rect(left, y - 18, right - left, 18, fill_gray=0.93, stroke_gray=0.85)
    page.text(left + 8, y - 6, "Resumen bancario", size=10, font="F2")
    y -= 28

    summary_items = [
        ("Saldo de apertura", _format_money(_safe_decimal(report["saldo_inicial"]))),
        ("Entradas", _format_money(_safe_decimal(report["total_entradas"]))),
        ("Salidas", _format_money(_safe_decimal(report["total_salidas"]))),
        ("Saldo actual", _format_money(_safe_decimal(report["saldo_actual"]))),
        ("Movimientos", str(report["movimientos_total"])),
    ]
    for label, value in summary_items:
        page.text(left + 8, y, label, size=9)
        page.text(right - _text_width(value, 9) - 8, y, value, size=9, font="F2")
        y -= 13
    y -= 10

    page.text(left, y, "Movimientos con saldo corrido", size=10, font="F2")
    y -= 16

    def draw_header(target: _PdfPage, y_pos: float) -> float:
        target.rect(left, y_pos - 16, right - left, 16, fill_gray=0.93, stroke_gray=0.85)
        columns = [
            ("Fecha", left, 78, "left"),
            ("Tipo", left + 78, 72, "left"),
            ("Referencia", left + 150, 74, "left"),
            ("Entrada", left + 224, 64, "right"),
            ("Salida", left + 288, 64, "right"),
            ("Saldo", left + 352, 72, "right"),
            ("Usuario", left + 424, 62, "left"),
        ]
        for title, x, width, align in columns:
            draw_x = x + 2
            if align == "right":
                draw_x = x + width - _text_width(title, 8) - 2
            target.text(draw_x, y_pos - 5, title, size=8, font="F2")
        return y_pos - 24

    def draw_row(target: _PdfPage, y_pos: float, row: dict) -> float:
        values = [
            (format_datetime_local(row["fecha"]) if row.get("fecha") else "-", left, 78, "left"),
            (row["tipo"], left + 78, 72, "left"),
            (row["referencia"], left + 150, 74, "left"),
            (_format_money(_safe_decimal(row["monto_entrada"])), left + 224, 64, "right"),
            (_format_money(_safe_decimal(row["monto_salida"])), left + 288, 64, "right"),
            (_format_money(_safe_decimal(row["saldo"])), left + 352, 72, "right"),
            (row["usuario"], left + 424, 62, "left"),
        ]
        for text, x, width, align in values:
            display = _truncate_text(text, width - 4, 8)
            draw_x = x + 2
            if align == "right":
                draw_x = x + width - _text_width(display, 8) - 2
            target.text(draw_x, y_pos, display, size=8)
        y_next = y_pos - 12
        comment = (row.get("comentario") or "").strip()
        if comment and comment != "-":
            target.text(left + 8, y_next, _truncate_text(f"Concepto: {comment}", right - left - 16, 7), size=7)
            y_next -= 10
        return y_next

    if report["rows"]:
        y = draw_header(page, y)
        for row in report["rows"]:
            needed = 22 if (row.get("comentario") or "").strip() not in ("", "-") else 12
            if y < bottom + needed:
                page, y = new_page("(continuacion)")
                y = draw_header(page, y)
            y = draw_row(page, y, row)
    else:
        page.text(left, y, "No hay movimientos registrados.", size=9)

    filename = f"estado_cuenta_scrap360_{_safe_filename(report['account_name'])}.pdf"
    return doc.render(), filename
