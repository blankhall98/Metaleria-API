# app/services/invoice_service.py
import io
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models import (
    Nota,
    Sucursal,
    Proveedor,
    Cliente,
    User,
    TipoOperacion,
)
from app.services import note_service
from app.services.firebase_storage import upload_file


def _format_decimal(value: object, places: int) -> str:
    try:
        return f"{Decimal(str(value or 0)):.{places}f}"
    except (InvalidOperation, ValueError):
        return f"{0:.{places}f}"


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


class _PdfBuilder:
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

    def render(self) -> bytes:
        stream_content = "\n".join(self.commands)
        stream_bytes = stream_content.encode("latin-1", errors="ignore")
        len_stream = len(stream_bytes)

        objects: list[tuple[int, bytes]] = []

        def obj(num: int, body: bytes) -> None:
            objects.append((num, body))

        obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
        obj(2, b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>")
        obj(
            3,
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> >>",
        )
        obj(4, f"<< /Length {len_stream} >>\nstream\n".encode() + stream_bytes + b"\nendstream")
        obj(5, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
        obj(6, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")

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


def _safe_filename(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value or "").strip("-")
    return slug or "factura"


def build_invoice_pdf(db: Session, nota: Nota, generated_at: datetime | None = None) -> tuple[bytes, str]:
    sucursal = db.get(Sucursal, nota.sucursal_id)
    proveedor = db.get(Proveedor, nota.proveedor_id) if nota.proveedor_id else None
    cliente = db.get(Cliente, nota.cliente_id) if nota.cliente_id else None
    trabajador = db.get(User, nota.trabajador_id) if nota.trabajador_id else None
    admin = db.get(User, nota.admin_id) if nota.admin_id else None

    folio = note_service.format_folio(
        sucursal_id=nota.sucursal_id,
        tipo_operacion=nota.tipo_operacion,
        folio_seq=nota.folio_seq,
    )
    generated_at = generated_at or datetime.utcnow()
    issue_date = generated_at.strftime("%Y-%m-%d %H:%M")

    partner_label = "Proveedor" if nota.tipo_operacion == TipoOperacion.compra else "Cliente"
    partner = proveedor if nota.tipo_operacion == TipoOperacion.compra else cliente
    partner_name = partner.nombre_completo if partner else "-"
    partner_phone = partner.telefono if partner else None
    partner_email = partner.correo_electronico if partner else None

    tipo_label = "Compra" if nota.tipo_operacion == TipoOperacion.compra else "Venta"
    sucursal_name = sucursal.nombre if sucursal else "-"
    sucursal_address = sucursal.direccion if sucursal and sucursal.direccion else "-"
    folio_label = folio or f"nota-{nota.id}"

    pdf = _PdfBuilder()

    left = 50
    right = 562
    top = 760

    pdf.text(left, top, "SCRAP360", size=18, font="F2")
    pdf.text(left, top - 18, f"Sucursal: {sucursal_name}", size=9)
    pdf.text(left, top - 30, f"Direccion: {sucursal_address}", size=9)
    pdf.text(left, top - 42, f"Operacion: {tipo_label}", size=9)

    pdf.text(380, top, "FACTURA", size=16, font="F2")
    pdf.text(380, top - 18, f"Folio: {folio_label}", size=9)
    pdf.text(380, top - 30, f"Fecha: {issue_date}", size=9)
    pdf.text(380, top - 42, f"Nota ID: {nota.id}", size=9)

    pdf.line(left, top - 52, right, top - 52)

    y = top - 70
    pdf.text(left, y, f"{partner_label}:", size=10, font="F2")
    pdf.text(left + 70, y, partner_name, size=10)
    y -= 14
    if partner_phone:
        pdf.text(left + 70, y, f"Tel: {partner_phone}", size=9)
        y -= 12
    if partner_email:
        pdf.text(left + 70, y, f"Email: {partner_email}", size=9)
        y -= 12
    if trabajador:
        pdf.text(left, y, f"Trabajador: {trabajador.nombre_completo}", size=9)
        y -= 12
    if admin:
        pdf.text(left, y, f"Admin: {admin.nombre_completo}", size=9)
        y -= 12

    metodo = (nota.metodo_pago or "").capitalize() or "-"
    cuenta = str(nota.cuenta_financiera_id or "-")
    caducidad = nota.fecha_caducidad_pago.strftime("%Y-%m-%d") if nota.fecha_caducidad_pago else "-"
    pdf.text(380, top - 70, f"Metodo de pago: {metodo}", size=9)
    pdf.text(380, top - 82, f"Cuenta: {cuenta}", size=9)
    pdf.text(380, top - 94, f"Vencimiento: {caducidad}", size=9)

    table_width = right - left
    header_height = 18
    row_height = 16
    table_top = y - 12
    header_bottom = table_top - header_height
    pdf.rect(left, header_bottom, table_width, header_height, fill_gray=0.93, stroke_gray=0.85)

    columns = [
        ("Material", left, 190, "left"),
        ("Kg bruto", left + 190, 60, "right"),
        ("Kg desc", left + 250, 60, "right"),
        ("Kg neto", left + 310, 60, "right"),
        ("Precio unit", left + 370, 70, "right"),
        ("Subtotal", left + 440, 72, "right"),
    ]

    header_text_y = header_bottom + 5
    for title, x, width, align in columns:
        draw_x = x + 2
        if align == "right":
            draw_x = x + width - _text_width(title, 9) - 2
        pdf.text(draw_x, header_text_y, title, size=9, font="F2")

    row_y = header_bottom - 12
    materiales = sorted(nota.materiales, key=lambda m: (m.orden or 0, m.id or 0))
    for nm in materiales:
        material_name = nm.material.nombre if nm.material else str(nm.material_id or "")
        material_name = _truncate_text(material_name, 180, 9)
        kg_bruto = _format_decimal(nm.kg_bruto, 2)
        kg_desc = _format_decimal(nm.kg_descuento, 2)
        kg_neto = _format_decimal(nm.kg_neto, 2)
        precio_unit = _format_decimal(nm.precio_unitario, 2)
        try:
            subtotal_val = (
                Decimal(str(nm.subtotal))
                if nm.subtotal is not None
                else Decimal(str(nm.kg_neto or 0)) * Decimal(str(nm.precio_unitario or 0))
            )
        except (InvalidOperation, ValueError):
            subtotal_val = Decimal("0")
        subtotal = _format_decimal(subtotal_val, 2)

        values = [
            material_name,
            kg_bruto,
            kg_desc,
            kg_neto,
            precio_unit,
            subtotal,
        ]
        for (title, x, width, align), value in zip(columns, values):
            draw_x = x + 2
            if align == "right":
                draw_x = x + width - _text_width(value, 9) - 2
            pdf.text(draw_x, row_y, value, size=9)
        row_y -= row_height

    summary_top = max(row_y - 8, 140)
    box_width = 220
    box_height = 70
    box_x = right - box_width
    box_y = summary_top - box_height
    pdf.rect(box_x, box_y, box_width, box_height, stroke_gray=0.8)
    pdf.text(box_x + 10, summary_top - 16, "Resumen", size=10, font="F2")

    total_kg = _format_decimal(nota.total_kg_neto, 2)
    total = _format_decimal(nota.total_monto, 2)
    pagado = _format_decimal(nota.monto_pagado, 2)
    try:
        saldo_val = Decimal(str(nota.total_monto or 0)) - Decimal(str(nota.monto_pagado or 0))
    except (InvalidOperation, ValueError):
        saldo_val = Decimal("0")
    saldo = _format_decimal(saldo_val, 2)

    summary_lines = [
        ("Total kg neto", f"{total_kg} kg"),
        ("Total", f"${total}"),
        ("Pagado", f"${pagado}"),
        ("Saldo", f"${saldo}"),
    ]
    summary_y = summary_top - 30
    for label, value in summary_lines:
        pdf.text(box_x + 10, summary_y, label, size=9)
        pdf.text(box_x + box_width - _text_width(value, 9) - 10, summary_y, value, size=9, font="F2")
        summary_y -= 12

    footer_y = box_y - 20
    pdf.line(left, footer_y, right, footer_y)
    pdf.text(left, footer_y - 14, "Documento generado por sistema. Este PDF es un comprobante interno.", size=8)

    pdf_bytes = pdf.render()
    filename = f"factura_{_safe_filename(folio_label)}.pdf"
    return pdf_bytes, filename


def upload_invoice_pdf(pdf_bytes: bytes, filename: str, nota_id: int) -> str:
    return upload_file(
        content=pdf_bytes,
        filename=filename,
        content_type="application/pdf",
        folder=f"facturas/nota_{nota_id}",
    )
