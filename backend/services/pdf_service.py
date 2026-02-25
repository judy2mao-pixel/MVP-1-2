"""
PDF generation service for Servex Holdings backend.
Handles invoice PDF generation using ReportLab.
"""
from io import BytesIO
from datetime import datetime
from pathlib import Path
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

from database import db


def format_weight(weight, decimals=4):
    """Format weight with specified decimal places"""
    if weight is None:
        return "-"
    return f"{float(weight):.{decimals}f}"


def format_dimension(dim, decimals=3):
    """Format dimension with specified decimal places"""
    if dim is None:
        return "-"
    return f"{float(dim):.{decimals}f}"


def format_dimensions(length, width, height, decimals=3):
    """Format L×W×H dimensions"""
    if not length and not width and not height:
        return "-"
    len_str = format_dimension(length, decimals) if length else "0"
    wid_str = format_dimension(width, decimals) if width else "0"
    hei_str = format_dimension(height, decimals) if height else "0"
    return f"{len_str} × {wid_str} × {hei_str}"


def format_currency(amount, currency="ZAR"):
    """Format currency amount"""
    if amount is None:
        return "-"
    symbols = {"ZAR": "R", "KES": "KES", "USD": "$", "EUR": "€", "GBP": "£"}
    symbol = symbols.get(currency, currency)
    return f"{symbol} {float(amount):,.2f}"


def get_payment_terms_display(payment_terms, payment_terms_custom, total):
    """Get payment terms display text with calculated amounts"""
    if not payment_terms:
        return None
    
    terms_map = {
        "full_on_receipt": "Full payment due on receipt",
        "net_30": "Net 30 days",
        "custom": payment_terms_custom or "Custom terms"
    }
    
    if payment_terms == "50_50":
        half = total / 2
        return f"50% upfront, 50% on delivery\n• Due on receipt: {format_currency(half)}\n• Due on delivery: {format_currency(half)}"
    elif payment_terms == "30_70":
        upfront = total * 0.3
        delivery = total * 0.7
        return f"30% upfront, 70% on delivery\n• Due on receipt: {format_currency(upfront)}\n• Due on delivery: {format_currency(delivery)}"
    
    return terms_map.get(payment_terms, payment_terms)


async def generate_invoice_pdf(
    invoice_id: str,
    tenant_id: str
):
    """Generate TYPE 2 invoice PDF - Servex Holdings branded layout."""
    # --- Fetch data ---
    invoice = await db.invoices.find_one({"id": invoice_id, "tenant_id": tenant_id}, {"_id": 0})
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    client = await db.clients.find_one({"id": invoice.get("client_id")}, {"_id": 0})
    client_name = invoice.get("client_name_snapshot") or (client.get("name") if client else "Unknown")
    client_phone = invoice.get("client_phone_snapshot") or (client.get("phone") if client else "")
    client_address = invoice.get("client_address_snapshot") or (client.get("billing_address") or client.get("physical_address") if client else "")

    line_items = await db.invoice_line_items.find({"invoice_id": invoice_id}, {"_id": 0}).to_list(200)
    shipment_ids = [li.get("shipment_id") for li in line_items if li.get("shipment_id")]
    shipments = {}
    if shipment_ids:
        for s in await db.shipments.find({"id": {"$in": shipment_ids}}, {"_id": 0}).to_list(200):
            shipments[s["id"]] = s

    payments = await db.payments.find({"invoice_id": invoice_id}, {"_id": 0}).to_list(100)
    paid_amount = sum(p.get("amount", 0) for p in payments)

    # KES rate
    settings = await db.settings.find_one({"tenant_id": tenant_id})
    kes_rate = 6.67
    if settings and settings.get("currencies"):
        for cur in settings["currencies"]:
            if cur.get("code") == "KES":
                kes_rate = cur.get("exchange_rate", 6.67)

    currency = invoice.get("currency", "ZAR")
    total = invoice.get("total", 0)
    subtotal = invoice.get("subtotal", total)
    adj_total = invoice.get("adjustments", 0)
    outstanding = total - paid_amount

    # --- Colors / Styles ---
    dark_red = colors.HexColor('#8B1A1A')
    dark_bg = colors.HexColor('#1C2B1E')
    white = colors.white
    light_gray = colors.HexColor('#F5F5F5')
    black = colors.black

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=15*mm, rightMargin=15*mm, topMargin=15*mm, bottomMargin=20*mm)
    styles = getSampleStyleSheet()

    def S(name, **kw): return ParagraphStyle(name=name, **kw)
    p_small = S('p_small', fontSize=8, fontName='Helvetica', leading=10)
    p_bold = S('p_bold', fontSize=9, fontName='Helvetica-Bold', leading=11)
    p_red_bold = S('p_red', fontSize=9, fontName='Helvetica-Bold', textColor=dark_red, alignment=TA_CENTER)
    p_center = S('p_center', fontSize=8, fontName='Helvetica', leading=10, alignment=TA_CENTER)
    p_right = S('p_right', fontSize=9, fontName='Helvetica', leading=11, alignment=TA_RIGHT)
    p_disclaimer = S('p_disc', fontSize=7, fontName='Helvetica', leading=9, alignment=TA_CENTER, textColor=colors.gray)

    elements = []

    # --- 1. Header row: Logo | Tagline ---
    logo_path = Path(__file__).parent.parent / 'frontend' / 'public' / 'servex-logo.png'
    if logo_path.exists():
        try:
            from PIL import Image as PILImage
            pil = PILImage.open(logo_path)
            asp = pil.height / pil.width
            logo_img = Image(str(logo_path), width=40*mm, height=40*mm*asp)
        except Exception:
            logo_img = Paragraph("<b>SERVEX</b>", p_bold)
    else:
        logo_img = Paragraph("<b>SERVEX</b>", p_bold)

    tagline = Paragraph("Logistics Services to Kenya / and South Africa", S('tag', fontSize=11, fontName='Helvetica-Bold', textColor=dark_red, alignment=TA_RIGHT))
    h1 = Table([[logo_img, tagline]], colWidths=[60*mm, 120*mm])
    h1.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('ALIGN', (1, 0), (1, 0), 'RIGHT')]))
    elements.append(h1)

    # --- 2. Company | Invoice No ---
    issue_date_str = invoice.get('issue_date') or str(invoice.get('created_at', ''))[:10]
    try:
        issue_dt = datetime.fromisoformat(issue_date_str.replace('Z', '+00:00'))
        issue_date_fmt = issue_dt.strftime('%d %B %Y')
    except Exception:
        issue_date_fmt = issue_date_str

    h2 = Table([
        [Paragraph("<b>Servex Holdings (PTY) Ltd</b>", p_bold),
         Paragraph(f"<b>INVOICE NO: {invoice.get('invoice_number','')}</b>", S('inv_no', fontSize=12, fontName='Helvetica-Bold', alignment=TA_RIGHT))]
    ], colWidths=[90*mm, 90*mm])
    h2.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE')]))
    elements.append(h2)
    elements.append(Spacer(1, 2*mm))

    # --- 3. Bordered header grid ---
    # Row 1: Date (full width)
    # Row 2: Sender | Contact
    # Row 3: Contact | Destination
    first_li = line_items[0] if line_items else {}
    first_ship = shipments.get(first_li.get("shipment_id", ""), {}) if first_li else {}
    sender_name = client_name
    sender_contact = client_phone
    recipient = first_li.get("recipient_name") or first_ship.get("recipient", "")
    destination = first_ship.get("destination") or invoice.get("recipient", "")

    grid_data = [
        [Paragraph(f"<b>Date:</b> {issue_date_fmt}", p_small), ""],
        [Paragraph(f"<b>Sent by:</b> {sender_name}", p_small), Paragraph(f"<b>Contact:</b> {sender_contact}", p_small)],
        [Paragraph(f"<b>Contact:</b> {client_address or '—'}", p_small), Paragraph(f"<b>Destination:</b> {destination or '—'}", p_small)],
    ]
    grid_t = Table(grid_data, colWidths=[90*mm, 90*mm])
    grid_t.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, black),
        ('INNERGRID', (0, 0), (-1, -1), 0.3, colors.lightgrey),
        ('SPAN', (0, 0), (1, 0)),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(grid_t)
    elements.append(Spacer(1, 3*mm))

    # --- 4. Payment Terms line ---
    elements.append(Paragraph(
        "<b>Full payment due upon receipt. Queries must be raised within 7 days of invoice date.</b>",
        p_red_bold
    ))
    elements.append(Spacer(1, 3*mm))

    # --- 5. Line items table ---
    tbl_headers = ['#', 'Recipient', 'Description', 'QTY', 'KG', 'L', 'W', 'H', 'V', 'Ship Wt', 'Amount']
    tbl_data = [tbl_headers]
    total_qty = 0
    total_kg = 0.0
    total_ship = 0.0

    for idx, li in enumerate(line_items, 1):
        ship = shipments.get(li.get("shipment_id", ""), {})
        l = float(li.get("length_cm") or 0)
        w = float(li.get("width_cm") or 0)
        h = float(li.get("height_cm") or 0)
        qty = int(li.get("quantity") or 1)
        kg = float(li.get("weight_kg") or li.get("weight") or 0)
        vol = round(l * w * h / 1000, 4) if (l and w and h) else 0
        ship_wt = max(kg, l * w * h / 5000) if (l and w and h) else kg
        amount = float(li.get("amount") or 0)
        recip = li.get("recipient_name") or ship.get("recipient") or "—"
        desc = li.get("description") or "—"
        total_qty += qty
        total_kg += kg
        total_ship += ship_wt
        tbl_data.append([
            str(idx), recip[:20], desc[:30],
            str(qty),
            f"{kg:.2f}" if kg else "—",
            f"{l:.0f}" if l else "—",
            f"{w:.0f}" if w else "—",
            f"{h:.0f}" if h else "—",
            f"{vol:.4f}" if vol else "—",
            f"{ship_wt:.2f} Kg" if ship_wt else "—",
            format_currency(amount, currency)
        ])

    # Totals row with asterisk
    tbl_data.append([
        "*", "", "TOTALS",
        str(total_qty),
        f"{total_kg:.2f}",
        "", "", "", "",
        f"{total_ship:.2f} Kg",
        format_currency(total, currency)
    ])

    col_w = [7*mm, 28*mm, 35*mm, 8*mm, 12*mm, 8*mm, 8*mm, 8*mm, 14*mm, 18*mm, 24*mm]
    items_t = Table(tbl_data, colWidths=col_w, repeatRows=1)
    ts = TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), dark_bg),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 7),
        ('ALIGN', (3, 0), (-1, -1), 'RIGHT'),
        ('ALIGN', (0, 0), (2, -1), 'LEFT'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('GRID', (0, 0), (-1, -1), 0.3, colors.lightgrey),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('BACKGROUND', (0, -1), (-1, -1), light_gray),
    ])
    for i in range(1, len(tbl_data) - 1):
        if i % 2 == 0:
            ts.add('BACKGROUND', (0, i), (-1, i), light_gray)
    items_t.setStyle(ts)
    elements.append(items_t)
    elements.append(Spacer(1, 4*mm))

    # --- 6. Two-column footer: Payment Info | Subtotal/Total ---
    payment_info = (
        "Payment to FNB (First National Bank)<br/>"
        "<b>Account name:</b> Servex Holdings Pty Ltd<br/>"
        "<b>Account number:</b> 63112859666<br/>"
        "<b>Branch:</b> Bryanston<br/>"
        "<b>Swift:</b> FIRNZAJJ<br/>"
        f"<b>Reference:</b> {invoice.get('invoice_number','')}"
    )
    totals_lines = [
        [Paragraph("Subtotal:", p_small), Paragraph(format_currency(subtotal, currency), p_right)],
    ]
    if adj_total != 0:
        totals_lines.append([Paragraph("Adjustments:", p_small), Paragraph(format_currency(adj_total, currency), p_right)])
    totals_lines.append([Paragraph("<b>TOTAL:</b>", p_bold), Paragraph(f"<b>{format_currency(total, currency)}</b>", S('tr', fontSize=10, fontName='Helvetica-Bold', alignment=TA_RIGHT))])
    if currency == "ZAR":
        totals_lines.append([Paragraph("KES equivalent:", p_small), Paragraph(f"KES {total * kes_rate:,.0f}", p_right)])
    if paid_amount > 0:
        totals_lines.append([Paragraph("Paid:", p_small), Paragraph(format_currency(paid_amount, currency), p_right)])
        totals_lines.append([Paragraph("<b>Outstanding:</b>", p_bold), Paragraph(f"<b>{format_currency(outstanding, currency)}</b>", p_right)])

    totals_t = Table(totals_lines, colWidths=[35*mm, 25*mm])
    totals_t.setStyle(TableStyle([('ALIGN', (1, 0), (1, -1), 'RIGHT'), ('BOTTOMPADDING', (0, 0), (-1, -1), 2)]))

    footer_t = Table([[Paragraph(payment_info, p_small), totals_t]], colWidths=[100*mm, 80*mm])
    footer_t.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(footer_t)
    elements.append(Spacer(1, 4*mm))

    # --- 7-8. Collection Locations ---
    elements.append(Paragraph("<b>Collection Location:</b>", S('cl_hd', fontSize=9, fontName='Helvetica-Bold', alignment=TA_CENTER)))
    elements.append(Spacer(1, 2*mm))
    loc_t = Table([[
        Paragraph("<b>JHB:</b><br/>Unit 19 Eastborough Business Park<br/>15 Olympia Street, Eastgate, JHB 2090", p_center),
        Paragraph("<b>Nairobi:</b><br/>Godown 3, Libra House<br/>Mombasa Road, Nairobi Kenya", p_center)
    ]], colWidths=[90*mm, 90*mm])
    loc_t.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ('INNERGRID', (0, 0), (-1, -1), 0.3, colors.lightgrey),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(loc_t)
    elements.append(Spacer(1, 2*mm))

    # --- 9. Operating hours ---
    elements.append(Paragraph("Operating hours: Weekdays 9 - 5pm", p_center))
    elements.append(Spacer(1, 3*mm))

    # --- 10. Disclaimer ---
    elements.append(Paragraph(
        "Servex Holdings (Pty) Ltd accepts no liability for goods lost, damaged or delayed in transit. "
        "All claims must be submitted in writing within 7 days. "
        "Rates quoted are exclusive of VAT unless otherwise stated. "
        f"KES rate used: 1 ZAR = {kes_rate} KES.",
        p_disclaimer
    ))

    # --- Red bar at bottom (drawn via canvas) ---
    def add_red_bar(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(dark_red)
        canvas.rect(doc.leftMargin, 8*mm, doc.width, 3*mm, fill=1, stroke=0)
        canvas.restoreState()

    doc.build(elements, onFirstPage=add_red_bar, onLaterPages=add_red_bar)
    buffer.seek(0)

    filename = f"Invoice-{invoice.get('invoice_number', invoice_id)}.pdf"
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
    
    # Fetch invoice with all related data
    invoice = await db.invoices.find_one({"id": invoice_id, "tenant_id": tenant_id}, {"_id": 0})
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    
    # Get client (use snapshot if available, fallback to current)
    client = await db.clients.find_one({"id": invoice.get("client_id")}, {"_id": 0})
    
    # Prefer snapshot data for historical accuracy
    client_name = invoice.get("client_name_snapshot") or (client.get("name") if client else "Unknown")
    client_address = invoice.get("client_address_snapshot") or (client.get("billing_address") or client.get("physical_address") if client else "")
    client_vat = invoice.get("client_vat_snapshot") or (client.get("vat_number") if client else "")
    client_phone = invoice.get("client_phone_snapshot") or (client.get("phone") if client else "")
    client_email = invoice.get("client_email_snapshot") or (client.get("email") if client else "")
    
    # Get line items with enriched data
    line_items = await db.invoice_line_items.find({"invoice_id": invoice_id}, {"_id": 0}).to_list(100)
    
    # Get shipments for recipient details
    shipment_ids = [li.get("shipment_id") for li in line_items if li.get("shipment_id")]
    shipments = {}
    if shipment_ids:
        shipment_docs = await db.shipments.find(
            {"id": {"$in": shipment_ids}},
            {"_id": 0}
        ).to_list(100)
        shipments = {s["id"]: s for s in shipment_docs}
    
    # Get adjustments
    adjustments = await db.invoice_adjustments.find({"invoice_id": invoice_id}, {"_id": 0}).to_list(100)
    
    # Get payments
    payments = await db.payments.find({"invoice_id": invoice_id}, {"_id": 0}).to_list(100)
    paid_amount = sum(p.get("amount", 0) for p in payments)
    
    # Define colors
    olive = colors.HexColor('#6B633C')
    dark_gray = colors.HexColor('#3C3F42')
    light_gray = colors.HexColor('#F5F5F5')
    
    # Currency
    currency = invoice.get("currency", "ZAR")
    
    # Create PDF buffer
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=15*mm, rightMargin=15*mm, topMargin=15*mm, bottomMargin=15*mm)
    
    # Styles
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='CompanyName', fontSize=14, fontName='Helvetica-Bold', textColor=dark_gray))
    styles.add(ParagraphStyle(name='CompanyInfo', fontSize=9, fontName='Helvetica', textColor=dark_gray, leading=12))
    styles.add(ParagraphStyle(name='InvoiceTitle', fontSize=22, fontName='Helvetica-Bold', textColor=olive, alignment=TA_RIGHT))
    styles.add(ParagraphStyle(name='InvoiceInfo', fontSize=10, fontName='Helvetica', textColor=dark_gray, alignment=TA_RIGHT))
    styles.add(ParagraphStyle(name='SectionTitle', fontSize=11, fontName='Helvetica-Bold', textColor=dark_gray))
    styles.add(ParagraphStyle(name='ClientInfo', fontSize=10, fontName='Helvetica', textColor=dark_gray, leading=14))
    styles.add(ParagraphStyle(name='TotalLabel', fontSize=12, fontName='Helvetica-Bold', textColor=dark_gray, alignment=TA_RIGHT))
    styles.add(ParagraphStyle(name='TotalAmount', fontSize=16, fontName='Helvetica-Bold', textColor=olive, alignment=TA_RIGHT))
    styles.add(ParagraphStyle(name='FooterStyle', fontSize=8, fontName='Helvetica', textColor=colors.gray, alignment=TA_CENTER))
    styles.add(ParagraphStyle(name='PaymentTerms', fontSize=10, fontName='Helvetica', textColor=dark_gray, leading=14))
    styles.add(ParagraphStyle(name='SmallText', fontSize=8, fontName='Helvetica', textColor=dark_gray))
    
    elements = []
    
    # Header section
    logo_path = Path(__file__).parent.parent / 'frontend' / 'public' / 'servex-logo.png'
    
    left_content = []
    if logo_path.exists():
        try:
            from PIL import Image as PILImage
            img = PILImage.open(logo_path)
            aspect = img.height / img.width
            logo = Image(str(logo_path), width=100, height=100*aspect)
            left_content.append(logo)
        except Exception:
            left_content.append(Paragraph("SERVEX HOLDINGS", styles['CompanyName']))
    else:
        left_content.append(Paragraph("SERVEX HOLDINGS", styles['CompanyName']))
    
    left_content.append(Spacer(1, 3*mm))
    left_content.append(Paragraph("<b>SERVEX HOLDINGS (PTY) LTD</b>", styles['CompanyInfo']))
    left_content.append(Paragraph("Unit 19 Eastborough Business Park", styles['CompanyInfo']))
    left_content.append(Paragraph("15 Olympia Street, Eastgate", styles['CompanyInfo']))
    left_content.append(Paragraph("Johannesburg 2090", styles['CompanyInfo']))
    left_content.append(Paragraph("info@servexholdings.com | +27 79 645 6281", styles['CompanyInfo']))
    
    # Invoice info (right side)
    issue_date = invoice.get('issue_date') or invoice.get('created_at', '')[:10]
    due_date = invoice.get('due_date', '')
    
    right_content = []
    right_content.append(Paragraph("INVOICE", styles['InvoiceTitle']))
    right_content.append(Spacer(1, 2*mm))
    right_content.append(Paragraph(f"<b>Invoice #:</b> {invoice.get('invoice_number', 'N/A')}", styles['InvoiceInfo']))
    right_content.append(Paragraph(f"<b>Date:</b> {issue_date}", styles['InvoiceInfo']))
    right_content.append(Paragraph(f"<b>Due:</b> {due_date}", styles['InvoiceInfo']))
    right_content.append(Paragraph(f"<b>Status:</b> {invoice.get('status', 'draft').upper()}", styles['InvoiceInfo']))
    
    header_table = Table([[left_content, right_content]], colWidths=[100*mm, 80*mm])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 8*mm))
    
    # Bill To section with full client details
    elements.append(Paragraph("Bill To:", styles['SectionTitle']))
    elements.append(Spacer(1, 2*mm))
    
    bill_to_text = f"<b>{client_name}</b><br/>"
    if client_address:
        bill_to_text += f"{client_address}<br/>"
    if client_vat:
        bill_to_text += f"VAT: {client_vat}<br/>"
    if client_phone:
        bill_to_text += f"Tel: {client_phone}<br/>"
    if client_email:
        bill_to_text += f"Email: {client_email}"
    
    elements.append(Paragraph(bill_to_text, styles['ClientInfo']))
    elements.append(Spacer(1, 6*mm))
    
    # Collect unique recipients from line items
    recipients = {}
    for li in line_items:
        shipment_id = li.get("shipment_id")
        if shipment_id and shipment_id in shipments:
            s = shipments[shipment_id]
            recipient_name = li.get("recipient_name") or s.get("recipient")
            if recipient_name and recipient_name not in recipients:
                recipients[recipient_name] = {
                    "name": recipient_name,
                    "phone": s.get("recipient_phone"),
                    "vat": s.get("recipient_vat"),
                    "address": s.get("shipping_address")
                }
    
    # Ship To section (if recipient differs from client)
    if recipients:
        elements.append(Paragraph("Ship To:", styles['SectionTitle']))
        elements.append(Spacer(1, 2*mm))
        
        for r_name, r_info in recipients.items():
            ship_to_text = f"<b>{r_info['name']}</b><br/>"
            if r_info.get("address"):
                ship_to_text += f"{r_info['address']}<br/>"
            if r_info.get("vat"):
                ship_to_text += f"VAT: {r_info['vat']}<br/>"
            if r_info.get("phone"):
                ship_to_text += f"Tel: {r_info['phone']}"
            elements.append(Paragraph(ship_to_text, styles['ClientInfo']))
            elements.append(Spacer(1, 2*mm))
        
        elements.append(Spacer(1, 4*mm))
    
    # Line Items Table with enhanced columns
    table_data = [['#', 'Description', 'Dimensions', 'Qty', 'Weight', 'Rate', 'Amount']]
    
    total_qty = 0
    for idx, item in enumerate(line_items, 1):
        shipment = shipments.get(item.get("shipment_id"), {})
        
        # Get parcel label
        parcel_label = item.get("parcel_label", "")
        if not parcel_label and shipment:
            seq = shipment.get("parcel_sequence")
            total = shipment.get("total_in_sequence")
            if seq and total:
                parcel_label = f"{seq}/{total}"
        
        # Get dimensions from line item or shipment
        length = item.get("length_cm") or shipment.get("length_cm")
        width = item.get("width_cm") or shipment.get("width_cm")
        height = item.get("height_cm") or shipment.get("height_cm")
        dimensions = format_dimensions(length, width, height)
        
        qty = item.get('quantity', 1)
        total_qty += qty
        weight = item.get('weight') or item.get('quantity', 0)
        rate = item.get('rate', 0)
        amount = item.get('amount') or (weight * rate)
        
        # Build description with recipient if different
        desc = item.get('description', '')[:40]
        recipient = item.get('recipient_name') or shipment.get('recipient')
        if recipient:
            desc = f"{desc}\n({recipient})"
        
        table_data.append([
            f"{idx}. {parcel_label}" if parcel_label else str(idx),
            Paragraph(desc, styles['SmallText']),
            dimensions,
            str(int(qty)),
            format_weight(weight),
            format_currency(rate, currency),
            format_currency(amount, currency)
        ])
    
    if not line_items:
        table_data.append(['', 'No line items', '', '', '', '', ''])
    
    # Create table with proper column widths
    col_widths = [18*mm, 50*mm, 32*mm, 12*mm, 22*mm, 22*mm, 26*mm]
    items_table = Table(table_data, colWidths=col_widths)
    
    # Table styling
    table_style = [
        ('BACKGROUND', (0, 0), (-1, 0), olive),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('ALIGN', (2, 0), (-1, -1), 'RIGHT'),
        ('ALIGN', (0, 0), (1, -1), 'LEFT'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]
    
    # Alternating row colors
    for i in range(1, len(table_data)):
        if i % 2 == 0:
            table_style.append(('BACKGROUND', (0, i), (-1, i), light_gray))
    
    items_table.setStyle(TableStyle(table_style))
    elements.append(items_table)
    elements.append(Spacer(1, 2*mm))
    
    # Item count
    elements.append(Paragraph(f"<i>{len(line_items)} line items | Total Qty: {int(total_qty)} pieces</i>", styles['SmallText']))
    elements.append(Spacer(1, 6*mm))
    
    # Adjustments section
    if adjustments:
        elements.append(Paragraph("Adjustments:", styles['SectionTitle']))
        elements.append(Spacer(1, 2*mm))
        
        adj_data = [['Description', 'Amount']]
        for adj in adjustments:
            sign = "+" if adj.get("is_addition", True) else "-"
            amt = adj.get("amount", 0)
            adj_data.append([
                adj.get("description", "Adjustment"),
                f"{sign} {format_currency(amt, currency)}"
            ])
        
        adj_table = Table(adj_data, colWidths=[130*mm, 30*mm])
        adj_table.setStyle(TableStyle([
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        elements.append(adj_table)
        elements.append(Spacer(1, 4*mm))
    
    # Totals section
    subtotal = invoice.get('subtotal', 0)
    adj_total = invoice.get('adjustments', 0)
    total = invoice.get('total', 0)
    outstanding = total - paid_amount
    
    totals_data = [
        ['Subtotal:', format_currency(subtotal, currency)],
    ]
    
    if adj_total != 0:
        sign = "+" if adj_total >= 0 else ""
        totals_data.append(['Adjustments:', f"{sign}{format_currency(adj_total, currency)}"])
    
    totals_data.append(['TOTAL:', format_currency(total, currency)])
    
    if paid_amount > 0:
        totals_data.append(['Paid:', format_currency(paid_amount, currency)])
        totals_data.append(['Outstanding:', format_currency(outstanding, currency)])
    
    totals_table = Table(totals_data, colWidths=[130*mm, 40*mm])
    totals_style = [
        ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]
    
    # Bold the total row
    total_row_idx = 2 if adj_total != 0 else 1
    totals_style.append(('FONTNAME', (0, total_row_idx), (-1, total_row_idx), 'Helvetica-Bold'))
    totals_style.append(('FONTSIZE', (0, total_row_idx), (-1, total_row_idx), 12))
    totals_style.append(('TEXTCOLOR', (1, total_row_idx), (1, total_row_idx), olive))
    
    totals_table.setStyle(TableStyle(totals_style))
    elements.append(totals_table)
    elements.append(Spacer(1, 6*mm))
    
    # Payment terms
    payment_terms = invoice.get("payment_terms")
    if payment_terms:
        terms_display = get_payment_terms_display(
            payment_terms,
            invoice.get("payment_terms_custom"),
            total
        )
        if terms_display:
            elements.append(Paragraph("<b>Payment Terms:</b>", styles['SectionTitle']))
            elements.append(Spacer(1, 2*mm))
            elements.append(Paragraph(terms_display.replace("\n", "<br/>"), styles['PaymentTerms']))
            elements.append(Spacer(1, 4*mm))
    
    # Banking details
    elements.append(Paragraph("<b>Banking Details:</b>", styles['SectionTitle']))
    elements.append(Spacer(1, 2*mm))
    banking = """
    Bank: First National Bank (FNB)<br/>
    Account Name: Servex Holdings (Pty) Ltd<br/>
    Account Number: 62842877857<br/>
    Branch Code: 250655<br/>
    Reference: {invoice_number}
    """.format(invoice_number=invoice.get('invoice_number', ''))
    elements.append(Paragraph(banking, styles['ClientInfo']))
    elements.append(Spacer(1, 8*mm))
    
    # Footer
    elements.append(Paragraph(
        f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M')} | Servex Holdings (Pty) Ltd | Thank you for your business",
        styles['FooterStyle']
    ))
    
    # Build PDF
    doc.build(elements)
    buffer.seek(0)
    
    filename = f"Invoice-{invoice.get('invoice_number', invoice_id)}.pdf"
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
