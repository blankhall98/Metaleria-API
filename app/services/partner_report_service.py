from __future__ import annotations

from decimal import Decimal

from app.core.datetime_utils import format_date_local, format_datetime_local
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


def _sheet_cell(value: str, cell_type: str = "String") -> str:
    return f"<Cell><Data ss:Type='{cell_type}'>{_xml_escape(value)}</Data></Cell>"


def _sheet_row(values: list[tuple[str, str]]) -> str:
    return "<Row>" + "".join([_sheet_cell(v, t) for v, t in values]) + "</Row>"


def build_partner_statement_excel(report: dict) -> tuple[bytes, str]:
    partner_type = report["partner_label"]
    partner_name = report["partner_name"]
    generated_at = format_datetime_local(report["generated_at"])
    scope_label = report.get("scope_label") or "Historial completo"
    linked_summary = report.get("linked_summary") or "-"

    summary_rows = [
        _sheet_row([(f"Estado de cuenta {partner_type}", "String")]),
        _sheet_row([(f"Partner: {partner_name}", "String")]),
        _sheet_row([(f"ID: {report['partner_id']}", "String")]),
        _sheet_row([(f"Alcance: {scope_label}", "String")]),
        _sheet_row([(f"Relacion: {linked_summary}", "String")]),
        _sheet_row([(f"Generado: {generated_at}", "String")]),
        _sheet_row([("", "String")]),
    ]
    for item in report["summary_items"]:
        value = _safe_decimal(item["value"]) if item["type"] == "money" else int(_safe_decimal(item["value"]))
        summary_rows.append(
            _sheet_row(
                [
                    (item["label"], "String"),
                    (str(value), "Number" if item["type"] != "text" else "String"),
                ]
            )
        )

    ledger_headers = [
        "Fecha",
        "Movimiento",
        "Nota",
        "Metodo",
        "Cuenta",
        "Cargo",
        "Abono",
        "Saldo",
        "Comentario",
    ]
    ledger_rows = ["<Row>" + "".join([_sheet_cell(h) for h in ledger_headers]) + "</Row>"]
    for row in report["ledger_rows"]:
        ledger_rows.append(
            _sheet_row(
                [
                    (format_datetime_local(row["fecha"]) if row.get("fecha") else "-", "String"),
                    (str(row.get("tipo") or "-"), "String"),
                    (str(row.get("folio") or "-"), "String"),
                    (str(row.get("metodo") or "-"), "String"),
                    (str(row.get("cuenta") or "-"), "String"),
                    (str(_safe_decimal(row.get("cargo"))), "Number"),
                    (str(_safe_decimal(row.get("abono"))), "Number"),
                    (str(_safe_decimal(row.get("saldo"))), "Number"),
                    (str(row.get("comentario") or "-"), "String"),
                ]
            )
        )

    notes_headers = [
        "Folio",
        "Operacion",
        "Estado",
        "Fecha",
        "Sucursal",
        "Total",
        "Pagado",
        "Saldo pendiente",
        "Saldo a favor",
        "Ajuste aplicado",
        "Neteo aplicado",
        "Ajuste nota",
    ]
    notes_rows = ["<Row>" + "".join([_sheet_cell(h) for h in notes_headers]) + "</Row>"]
    for row in report["notes_rows"]:
        notes_rows.append(
            _sheet_row(
                [
                    (row["folio"], "String"),
                    (row["operacion"], "String"),
                    (row["estado"], "String"),
                    (format_datetime_local(row["fecha"]) if row.get("fecha") else "-", "String"),
                    (row["sucursal"], "String"),
                    (str(_safe_decimal(row["total"])), "Number"),
                    (str(_safe_decimal(row["pagado"])), "Number"),
                    (str(_safe_decimal(row["saldo_pendiente"])), "Number"),
                    (str(_safe_decimal(row["saldo_favor"])), "Number"),
                    (str(_safe_decimal(row["ajuste_aplicado"])), "Number"),
                    (str(_safe_decimal(row.get("neteo_aplicado"))), "Number"),
                    (str(_safe_decimal(row["ajuste_nota"])), "Number"),
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
 <Worksheet ss:Name="Notas">
  <Table>
   {''.join(notes_rows)}
  </Table>
 </Worksheet>
</Workbook>"""

    filename = (
        f"estado_cuenta_{_safe_filename(partner_type)}_"
        f"{_safe_filename(partner_name)}.xls"
    )
    return workbook.encode("utf-8"), filename


def build_partner_statement_pdf(report: dict) -> tuple[bytes, str]:
    left = 42
    right = 570
    top = 760
    bottom = 58

    # No crear página aquí: new_page() del encabezado crea la primera. La
    # versión anterior abría una página extra que salía en blanco al frente
    # del documento.
    doc = _PdfDocument()

    def new_page(title_suffix: str | None = None) -> tuple[_PdfPage, float]:
        p = doc.new_page()
        y_pos = top
        title = f"Estado de cuenta {report['partner_label']}"
        if title_suffix:
            title = f"{title} {title_suffix}"
        p.text(left, y_pos, title, size=16, font="F2")
        p.text(left, y_pos - 16, f"{report['partner_label']}: {report['partner_name']}", size=9)
        p.text(left, y_pos - 28, f"ID: {report['partner_id']}", size=9)
        p.text(left, y_pos - 40, f"Alcance: {report.get('scope_label') or 'Historial completo'}", size=9)
        p.text(right - 156, y_pos - 16, f"Generado: {format_datetime_local(report['generated_at'])}", size=9)
        p.line(left, y_pos - 48, right, y_pos - 48)
        return p, y_pos - 66

    page, y = new_page()
    linked_summary = report.get("linked_summary")
    if linked_summary:
        page.text(left, y, linked_summary, size=9)
        y -= 18

    # Resumen simple (petición fase 2): el bloque de conteos por estado y las
    # cifras accesorias hacían que el socio "se hiciera bolas". El documento
    # abre con las tres cifras que la administradora dicta por teléfono; el
    # saldo es ledger_final, el mismo que cierra el estado de cuenta en
    # pantalla. El detalle completo sigue disponible en el export a Excel.
    fecha_corte = format_date_local(report["generated_at"])
    resumen_simple = report.get("resumen_simple") or []
    if resumen_simple:
        alto = 18 + len(resumen_simple) * 16 + 8
        page.rect(left, y - alto, right - left, alto, fill_gray=0.93, stroke_gray=0.85)
        page.text(left + 8, y - 13, f"Resumen al {fecha_corte}", size=10, font="F2")
        fila_y = y - 13
        for item in resumen_simple:
            fila_y -= 16
            valor = _format_money(_safe_decimal(item["value"]))
            page.text(left + 8, fila_y, item["label"], size=10)
            page.text(right - 8 - _text_width(valor, 11), fila_y, valor, size=11, font="F2")
        y -= alto + 6
        notas_totales = report.get("notas_totales")
        if notas_totales is not None:
            page.text(left, y, f"Notas totales: {int(_safe_decimal(notas_totales))}", size=8)
            y -= 16

    page.text(left, y, f"{report['ledger_label']} al {fecha_corte}", size=10, font="F2")
    balance_label = _format_money(_safe_decimal(report["ledger_final"]))
    page.text(right - _text_width(balance_label, 10), y, balance_label, size=10, font="F2")
    y -= 12
    page.text(left, y, report["ledger_help"], size=8)
    y -= 16

    def draw_ledger_header(target: _PdfPage, y_pos: float) -> float:
        target.rect(left, y_pos - 16, right - left, 16, fill_gray=0.93, stroke_gray=0.85)
        columns = [
            ("Fecha", left, 64, "left"),
            ("Movimiento", left + 64, 96, "left"),
            ("Nota", left + 160, 60, "left"),
            ("Método", left + 220, 52, "left"),
            ("Cuenta", left + 272, 86, "left"),
            ("Cargo", left + 358, 54, "right"),
            ("Abono", left + 412, 54, "right"),
            ("Saldo", left + 466, 60, "right"),
        ]
        for title, x, width, align in columns:
            draw_x = x + 2
            if align == "right":
                draw_x = x + width - _text_width(title, 8) - 2
            target.text(draw_x, y_pos - 5, title, size=8, font="F2")
        return y_pos - 24

    def draw_ledger_row(target: _PdfPage, y_pos: float, row: dict) -> float:
        columns = [
            (format_date_local(row["fecha"]) if row.get("fecha") else "-", left, 64, "left"),
            (str(row.get("tipo") or "-"), left + 64, 96, "left"),
            (str(row.get("folio") or "-"), left + 160, 60, "left"),
            (str(row.get("metodo") or "-"), left + 220, 52, "left"),
            (str(row.get("cuenta") or "-"), left + 272, 86, "left"),
            (_format_money(_safe_decimal(row.get("cargo"))), left + 358, 54, "right"),
            (_format_money(_safe_decimal(row.get("abono"))), left + 412, 54, "right"),
            (_format_money(_safe_decimal(row.get("saldo"))), left + 466, 60, "right"),
        ]
        for text, x, width, align in columns:
            display = _truncate_text(text, width - 4, 8)
            draw_x = x + 2
            if align == "right":
                draw_x = x + width - _text_width(display, 8) - 2
            target.text(draw_x, y_pos, display, size=8)
        y_next = y_pos - 12
        comment = (row.get("comentario") or "").strip()
        if comment and comment != "-":
            comment_text = _truncate_text(f"Comentario: {comment}", right - left - 16, 7)
            target.text(left + 8, y_next, comment_text, size=7)
            y_next -= 10
        return y_next

    if report["ledger_rows"]:
        y = draw_ledger_header(page, y)
        for row in report["ledger_rows"]:
            needed = 22 if (row.get("comentario") or "").strip() else 12
            if y < bottom + needed:
                page, y = new_page("(continuación)")
                y = draw_ledger_header(page, y)
            y = draw_ledger_row(page, y, row)
    else:
        page.text(left, y, f"No hay movimientos para este {report['partner_label'].lower()}.", size=9)
        y -= 18

    # Solo notas reales (petición fase 2): borradores, en revisión y canceladas
    # no aparecen en el documento que se envía al socio. El filtro vive aquí y
    # no en el dict porque el Excel — herramienta interna — sí conserva todo.
    notes_rows_pdf = [
        row for row in report["notes_rows"]
        if row.get("estado") in ("Aprobada", "Pagada")
    ]
    if notes_rows_pdf:
        if y < 180:
            page, y = new_page("(resumen de notas)")
        else:
            y -= 8
        page.text(left, y, "Resumen de notas aprobadas", size=11, font="F2")
        y -= 16

        def draw_notes_header(target: _PdfPage, y_pos: float) -> float:
            target.rect(left, y_pos - 16, right - left, 16, fill_gray=0.93, stroke_gray=0.85)
            columns = [
                ("Folio", left, 70, "left"),
                ("Operación", left + 70, 72, "left"),
                ("Estado", left + 142, 66, "left"),
                ("Fecha", left + 208, 68, "left"),
                ("Total", left + 276, 64, "right"),
                ("Pagado", left + 340, 64, "right"),
                ("Pendiente", left + 404, 60, "right"),
                ("Favor", left + 464, 60, "right"),
            ]
            for title, x, width, align in columns:
                draw_x = x + 2
                if align == "right":
                    draw_x = x + width - _text_width(title, 8) - 2
                target.text(draw_x, y_pos - 5, title, size=8, font="F2")
            return y_pos - 24

        def draw_notes_row(target: _PdfPage, y_pos: float, row: dict) -> float:
            columns = [
                (row["folio"], left, 70, "left"),
                (row["operacion"], left + 70, 72, "left"),
                (row["estado"], left + 142, 66, "left"),
                (format_date_local(row["fecha"]) if row.get("fecha") else "-", left + 208, 68, "left"),
                (_format_money(_safe_decimal(row["total"])), left + 276, 64, "right"),
                (_format_money(_safe_decimal(row["pagado"])), left + 340, 64, "right"),
                (_format_money(_safe_decimal(row["saldo_pendiente"])), left + 404, 60, "right"),
                (_format_money(_safe_decimal(row["saldo_favor"])), left + 464, 60, "right"),
            ]
            for text, x, width, align in columns:
                display = _truncate_text(text, width - 4, 8)
                draw_x = x + 2
                if align == "right":
                    draw_x = x + width - _text_width(display, 8) - 2
                target.text(draw_x, y_pos, display, size=8)
            return y_pos - 12

        y = draw_notes_header(page, y)
        for row in notes_rows_pdf:
            if y < bottom + 12:
                page, y = new_page("(resumen de notas)")
                y = draw_notes_header(page, y)
            y = draw_notes_row(page, y, row)

    filename = (
        f"estado_cuenta_{_safe_filename(report['partner_label'])}_"
        f"{_safe_filename(report['partner_name'])}.pdf"
    )
    return doc.render(), filename


def build_provider_attendance_excel(report: dict) -> tuple[bytes, str]:
    generated_at = format_datetime_local(report["generated_at"])
    summary_rows = [
        _sheet_row([("Asistencias proveedor", "String")]),
        _sheet_row([(f"Proveedor: {report['partner_name']}", "String")]),
        _sheet_row([(f"ID: {report['partner_id']}", "String")]),
        _sheet_row([(f"Rango: {report['range_label']}", "String")]),
        _sheet_row([(f"Generado: {generated_at}", "String")]),
        _sheet_row([("", "String")]),
        _sheet_row([("Asistencias historicas", "String"), (str(report["attendance_total_historico"]), "Number")]),
        _sheet_row([("Asistencias en rango", "String"), (str(report["attendance_total_filtrado"]), "Number")]),
    ]

    day_headers = ["Fecha", "Sucursal(es)", "Notas del dia", "Folios considerados"]
    day_rows = ["<Row>" + "".join([_sheet_cell(h) for h in day_headers]) + "</Row>"]
    for row in report["attendance_rows"]:
        day_rows.append(
            _sheet_row(
                [
                    (row["fecha_label"], "String"),
                    (row["sucursales_label"], "String"),
                    (str(int(_safe_decimal(row["notes_count"]))), "Number"),
                    (", ".join(row.get("folios") or []), "String"),
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
 <Worksheet ss:Name="Asistencias">
  <Table>
   {''.join(day_rows)}
  </Table>
 </Worksheet>
</Workbook>"""
    filename = f"asistencias_proveedor_{_safe_filename(report['partner_name'])}.xls"
    return workbook.encode("utf-8"), filename


def build_provider_attendance_pdf(report: dict) -> tuple[bytes, str]:
    left = 42
    right = 570
    top = 760
    bottom = 58

    doc = _PdfDocument()

    def new_page(title_suffix: str | None = None) -> tuple[_PdfPage, float]:
        page = doc.new_page()
        y_pos = top
        title = "Asistencias proveedor"
        if title_suffix:
            title = f"{title} {title_suffix}"
        page.text(left, y_pos, title, size=16, font="F2")
        page.text(left, y_pos - 16, f"Proveedor: {report['partner_name']}", size=9)
        page.text(left, y_pos - 28, f"ID: {report['partner_id']}", size=9)
        page.text(left, y_pos - 40, f"Rango: {report['range_label']}", size=9)
        page.text(right - 156, y_pos - 16, f"Generado: {format_datetime_local(report['generated_at'])}", size=9)
        page.line(left, y_pos - 48, right, y_pos - 48)
        return page, y_pos - 66

    page, y = new_page()
    page.rect(left, y - 18, right - left, 18, fill_gray=0.93, stroke_gray=0.85)
    page.text(left + 8, y - 6, "Resumen", size=10, font="F2")
    y -= 28
    page.text(left + 8, y, "Asistencias historicas", size=9)
    page.text(left + 180, y, str(report["attendance_total_historico"]), size=9, font="F2")
    y -= 12
    page.text(left + 8, y, "Asistencias en rango", size=9)
    page.text(left + 180, y, str(report["attendance_total_filtrado"]), size=9, font="F2")
    y -= 22

    def draw_header(target: _PdfPage, y_pos: float) -> float:
        target.rect(left, y_pos - 16, right - left, 16, fill_gray=0.93, stroke_gray=0.85)
        cols = [
            ("Fecha", left, 90, "left"),
            ("Sucursal(es)", left + 90, 200, "left"),
            ("Notas", left + 290, 54, "right"),
            ("Folios", left + 344, 182, "left"),
        ]
        for title, x, width, align in cols:
            draw_x = x + 2
            if align == "right":
                draw_x = x + width - _text_width(title, 8) - 2
            target.text(draw_x, y_pos - 5, title, size=8, font="F2")
        return y_pos - 24

    def draw_row(target: _PdfPage, y_pos: float, row: dict) -> float:
        cols = [
            (row["fecha_label"], left, 90, "left"),
            (row["sucursales_label"], left + 90, 200, "left"),
            (str(row["notes_count"]), left + 290, 54, "right"),
            (", ".join(row.get("folios") or []) or "-", left + 344, 182, "left"),
        ]
        for text, x, width, align in cols:
            display = _truncate_text(str(text), width - 4, 8)
            draw_x = x + 2
            if align == "right":
                draw_x = x + width - _text_width(display, 8) - 2
            target.text(draw_x, y_pos, display, size=8)
        return y_pos - 12

    if report["attendance_rows"]:
        y = draw_header(page, y)
        for row in report["attendance_rows"]:
            if y < bottom + 12:
                page, y = new_page("(continuacion)")
                y = draw_header(page, y)
            y = draw_row(page, y, row)
    else:
        page.text(left, y, "No hay asistencias para el rango seleccionado.", size=9)

    filename = f"asistencias_proveedor_{_safe_filename(report['partner_name'])}.pdf"
    return doc.render(), filename
