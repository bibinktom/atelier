"""Single-page poster/flyer PDF.  Designed for visual punch, not document layout."""
import os
from typing import Iterable

from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rlcanvas


def _hex(c: str | None, fallback: str) -> HexColor:
    try:
        return HexColor(c or fallback)
    except Exception:
        return HexColor(fallback)


def _wrap(text: str, font: str, size: float, max_w: float, c: rlcanvas.Canvas) -> list[str]:
    words = (text or "").split()
    lines: list[str] = []
    line = ""
    for w in words:
        cand = (line + " " + w).strip()
        if c.stringWidth(cand, font, size) <= max_w:
            line = cand
        else:
            if line:
                lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines or [""]


def build_flyer(
    path: str,
    *,
    title: str,
    subtitle: str = "",
    features: Iterable[str] = (),
    footer: str = "",
    cta_text: str = "",
    accent_color: str = "#E63946",
    background_color: str = "#FFFFFF",
    text_color: str = "#1A1A1A",
    hero_image_path: str | None = None,
) -> None:
    page_w, page_h = LETTER  # 612 x 792 pt
    c = rlcanvas.Canvas(path, pagesize=LETTER)

    bg = _hex(background_color, "#FFFFFF")
    accent = _hex(accent_color, "#E63946")
    ink = _hex(text_color, "#1A1A1A")

    # Page background
    c.setFillColor(bg)
    c.rect(0, 0, page_w, page_h, stroke=0, fill=1)

    margin = 0.6 * inch
    inner_w = page_w - 2 * margin

    # Top accent band with title
    band_h = 1.7 * inch
    c.setFillColor(accent)
    c.rect(0, page_h - band_h, page_w, band_h, stroke=0, fill=1)

    # Title (auto-sized to fit)
    c.setFillColor(white)
    title_size = 44.0
    while title_size > 18 and c.stringWidth(title or "", "Helvetica-Bold", title_size) > inner_w:
        title_size -= 2
    c.setFont("Helvetica-Bold", title_size)
    c.drawCentredString(page_w / 2, page_h - 0.95 * inch, (title or "")[:80])

    if subtitle:
        c.setFont("Helvetica", 16)
        c.drawCentredString(page_w / 2, page_h - 1.35 * inch, subtitle[:120])

    # Hero image
    cursor_y = page_h - band_h - 0.35 * inch
    if hero_image_path and os.path.isfile(hero_image_path):
        try:
            img = ImageReader(hero_image_path)
            iw, ih = img.getSize()
            target_w = inner_w
            target_h = target_w * (ih / iw)
            max_h = 3.6 * inch
            if target_h > max_h:
                target_h = max_h
                target_w = target_h * (iw / ih)
            img_x = (page_w - target_w) / 2
            img_y = cursor_y - target_h
            c.drawImage(img, img_x, img_y, width=target_w, height=target_h,
                        preserveAspectRatio=True, mask='auto')
            # Subtle frame
            c.setStrokeColor(_hex("#E5E7EB", "#E5E7EB"))
            c.setLineWidth(0.5)
            c.rect(img_x, img_y, target_w, target_h, stroke=1, fill=0)
            cursor_y = img_y - 0.35 * inch
        except Exception:
            pass

    # Features list (two-column if many)
    feat_list = [f for f in features if f]
    if feat_list:
        c.setFillColor(ink)
        cols = 2 if len(feat_list) >= 5 else 1
        col_w = (inner_w - 0.3 * inch) / cols
        c.setFont("Helvetica", 13)
        line_h = 18
        for i, f in enumerate(feat_list[: cols * 8]):
            col = i % cols
            row = i // cols
            x = margin + col * (col_w + 0.3 * inch)
            y = cursor_y - row * line_h
            # Accent bullet
            c.setFillColor(accent)
            c.circle(x + 4, y + 4, 3, stroke=0, fill=1)
            c.setFillColor(ink)
            wrapped = _wrap(f, "Helvetica", 13, col_w - 14, c)
            c.drawString(x + 14, y, wrapped[0])
            for j, extra in enumerate(wrapped[1:3], start=1):
                c.drawString(x + 14, y - j * line_h, extra)
        cursor_y -= ((len(feat_list[: cols * 8]) + cols - 1) // cols) * line_h
        cursor_y -= 0.3 * inch

    # CTA pill
    if cta_text:
        cta = cta_text[:40]
        c.setFont("Helvetica-Bold", 16)
        text_w = c.stringWidth(cta, "Helvetica-Bold", 16)
        pad_x, pad_y = 22, 12
        btn_w = text_w + pad_x * 2
        btn_h = 36
        btn_x = (page_w - btn_w) / 2
        btn_y = max(margin + 0.6 * inch, cursor_y - btn_h - 0.1 * inch)
        c.setFillColor(accent)
        c.roundRect(btn_x, btn_y, btn_w, btn_h, 8, stroke=0, fill=1)
        c.setFillColor(white)
        c.drawCentredString(page_w / 2, btn_y + pad_y, cta)
        cursor_y = btn_y - 0.25 * inch

    # Footer band
    if footer:
        footer_h = 0.5 * inch
        c.setFillColor(ink)
        c.rect(0, 0, page_w, footer_h, stroke=0, fill=1)
        c.setFillColor(white)
        c.setFont("Helvetica", 10)
        c.drawCentredString(page_w / 2, footer_h / 2 - 4, footer[:200])

    c.showPage()
    c.save()
