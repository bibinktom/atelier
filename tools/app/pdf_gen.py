"""Render markdown to PDF via reportlab platypus. Supports headings, paragraphs, lists, code, tables."""
import re
from markdown_it import MarkdownIt
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, ListFlowable, ListItem,
    Table, TableStyle, Preformatted, PageBreak,
)
from reportlab.lib import colors


def _styles():
    base = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=base["Heading1"], fontSize=22, spaceAfter=14, leading=26)
    h2 = ParagraphStyle("h2", parent=base["Heading2"], fontSize=16, spaceAfter=10, leading=20)
    h3 = ParagraphStyle("h3", parent=base["Heading3"], fontSize=13, spaceAfter=8, leading=17)
    body = ParagraphStyle("body", parent=base["BodyText"], fontSize=11, leading=15, spaceAfter=8)
    code = ParagraphStyle("code", parent=base["Code"], fontSize=9, leading=12)
    return {"h1": h1, "h2": h2, "h3": h3, "body": body, "code": code}


def _inline(tokens) -> str:
    """Tokens from markdown-it inline -> reportlab-friendly markup."""
    out = []
    stack = []
    for t in tokens:
        if t.type == "text":
            out.append(_escape(t.content))
        elif t.type == "softbreak" or t.type == "hardbreak":
            out.append("<br/>")
        elif t.type == "code_inline":
            out.append(f'<font face="Courier">{_escape(t.content)}</font>')
        elif t.type == "strong_open":
            out.append("<b>"); stack.append("</b>")
        elif t.type == "strong_close":
            out.append(stack.pop() if stack else "</b>")
        elif t.type == "em_open":
            out.append("<i>"); stack.append("</i>")
        elif t.type == "em_close":
            out.append(stack.pop() if stack else "</i>")
        elif t.type == "link_open":
            href = next((a[1] for a in (t.attrs or []) if a[0] == "href"), "") if isinstance(t.attrs, list) else (t.attrs or {}).get("href", "")
            out.append(f'<link href="{_escape(href)}" color="#2563eb">')
            stack.append("</link>")
        elif t.type == "link_close":
            out.append(stack.pop() if stack else "</link>")
    return "".join(out)


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_LEADING_H1 = re.compile(r"\A\s*#\s+[^\n]+\n+")


def build_pdf(path: str, title: str, body_md: str) -> None:
    # Drop a leading H1 from the body — `title` is already rendered as the
    # document heading, and models often duplicate it at the top of body_md.
    body_md = _LEADING_H1.sub("", body_md or "", count=1)
    md = MarkdownIt("commonmark", {"html": False}).enable("table")
    tokens = md.parse(body_md or "")
    s = _styles()
    flow = [Paragraph(_escape(title), s["h1"]), Spacer(1, 0.1 * inch)]

    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.type == "heading_open":
            level = int(t.tag[1])
            inline = tokens[i + 1].children if i + 1 < len(tokens) and tokens[i + 1].type == "inline" else []
            text = _inline(inline)
            style = s.get({1: "h1", 2: "h2", 3: "h3"}.get(level, "h3"))
            flow.append(Paragraph(text, style))
            i += 3
            continue
        if t.type == "paragraph_open":
            inline = tokens[i + 1].children if i + 1 < len(tokens) and tokens[i + 1].type == "inline" else []
            flow.append(Paragraph(_inline(inline), s["body"]))
            i += 3
            continue
        if t.type in ("bullet_list_open", "ordered_list_open"):
            items, j = [], i + 1
            depth = 1
            while j < len(tokens) and depth:
                tj = tokens[j]
                if tj.type in ("bullet_list_open", "ordered_list_open"):
                    depth += 1
                elif tj.type in ("bullet_list_close", "ordered_list_close"):
                    depth -= 1
                    if depth == 0:
                        break
                elif tj.type == "inline" and depth == 1:
                    items.append(Paragraph(_inline(tj.children or []), s["body"]))
                j += 1
            bt = "bullet" if t.type == "bullet_list_open" else "1"
            flow.append(ListFlowable([ListItem(p) for p in items], bulletType=bt, leftIndent=18))
            flow.append(Spacer(1, 0.05 * inch))
            i = j + 1
            continue
        if t.type == "fence" or t.type == "code_block":
            flow.append(Preformatted(t.content.rstrip(), s["code"]))
            flow.append(Spacer(1, 0.05 * inch))
            i += 1
            continue
        if t.type == "table_open":
            rows, j = [], i + 1
            current_row = None
            while j < len(tokens) and tokens[j].type != "table_close":
                tj = tokens[j]
                if tj.type == "tr_open":
                    current_row = []
                elif tj.type == "tr_close":
                    if current_row is not None:
                        rows.append(current_row); current_row = None
                elif tj.type == "inline" and current_row is not None:
                    current_row.append(Paragraph(_inline(tj.children or []), s["body"]))
                j += 1
            if rows:
                tbl = Table(rows, hAlign="LEFT")
                tbl.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d1d5db")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e5e7eb")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]))
                flow.append(tbl); flow.append(Spacer(1, 0.1 * inch))
            i = j + 1
            continue
        if t.type == "hr":
            flow.append(Spacer(1, 0.1 * inch))
            i += 1
            continue
        i += 1

    doc = SimpleDocTemplate(
        path, pagesize=LETTER,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
        title=title,
    )
    doc.build(flow)
