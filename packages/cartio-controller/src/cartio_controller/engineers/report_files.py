"""The report's PDF and DOCX files, written from `Report.blocks()`.

Topology maps print as vectors in the PDF and as PNGs in the DOCX (see `figures`).

The DOCX is written by hand, like `xlsx.py`, with real Word styles (Title, Heading 1–3, Code)
so engineers can edit it before sending. The PDF uses ReportLab's standard Helvetica, so no
font file has to ship with it; it covers Latin text only, so other scripts in names or notes
print correctly only in the DOCX.
"""

import io
import re
import textwrap
import zipfile
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from . import figures

BRAND = "IT Cloud Network"

# DESIGN.md's tokens, without the "#".
TEXT = "101828"
TEXT_2 = "475467"
ACCENT = "0E7490"
BORDER = "E4E8EC"
SEVERITY_COLOURS = {"critical": "B42318", "high": "E8590C", "medium": "B7791F", "low": "667085"}
SEVERITY_WORDS = {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low"}

# Columns that hold a count, which get a narrow column.
NUMERIC = {"Devices", "Findings", "Pass", "Fail", "Count"}

# Characters XML 1.0 can't hold.
_NOT_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def _clean(text) -> str:
    return _NOT_XML.sub("", str(text))


# DOCX

_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
_DRAWING = (
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"'
)
_R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
_XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'

_CONTENT_TYPES = (
    _XML + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Default Extension="png" ContentType="image/png"/>'
    '<Override PartName="/word/document.xml" ContentType="application/'
    'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/'
    'vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    '<Override PartName="/word/header1.xml" ContentType="application/'
    'vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>'
    '<Override PartName="/word/footer1.xml" ContentType="application/'
    'vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
    '<Override PartName="/docProps/core.xml" '
    'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
    "</Types>"
)
_ROOT_RELS = (
    _XML + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/>'
    '<Relationship Id="rId2" '
    'Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" '
    'Target="docProps/core.xml"/>'
    "</Relationships>"
)
_DOCUMENT_RELS = (
    _XML + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
    'Target="styles.xml"/>'
    '<Relationship Id="rId2" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" '
    'Target="header1.xml"/>'
    '<Relationship Id="rId3" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" '
    'Target="footer1.xml"/>'
    "{images}</Relationships>"
)
_IMAGE_REL = (
    '<Relationship Id="{rid}" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
    'Target="media/{name}"/>'
)
# A figure fits the text width and leaves room for its heading and caption, in EMU.
_EMU_PER_TWIP = 635
_FIGURE_WIDTH = 9640 * _EMU_PER_TWIP
_FIGURE_HEIGHT = 7 * 914400


def _style(style_id, name, run, paragraph="", based="Normal") -> str:
    base = f'<w:basedOn w:val="{based}"/>' if based else ""
    default = ' w:default="1"' if style_id == "Normal" else ""
    return (
        f'<w:style w:type="paragraph"{default} w:styleId="{style_id}">'
        f'<w:name w:val="{name}"/>{base}'
        f"<w:qFormat/><w:pPr>{paragraph}</w:pPr><w:rPr>{run}</w:rPr></w:style>"
    )


_STYLES = (
    _XML + f"<w:styles {_W}>"
    "<w:docDefaults><w:rPrDefault><w:rPr>"
    '<w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/>'
    f'<w:color w:val="{TEXT}"/><w:sz w:val="20"/></w:rPr></w:rPrDefault>'
    '<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/>'
    "</w:pPr></w:pPrDefault></w:docDefaults>"
    + _style("Normal", "Normal", "", based="")
    + _style(
        "Title",
        "Title",
        f'<w:b/><w:color w:val="{TEXT}"/><w:sz w:val="56"/>',
        '<w:spacing w:before="2400" w:after="240"/>',
    )
    + _style("Subtitle", "Subtitle", f'<w:color w:val="{TEXT_2}"/><w:sz w:val="28"/>')
    + _style(
        "Heading1",
        "heading 1",
        f'<w:b/><w:color w:val="{ACCENT}"/><w:sz w:val="32"/>',
        '<w:keepNext/><w:spacing w:before="360" w:after="160"/><w:outlineLvl w:val="0"/>',
    )
    + _style(
        "Heading2",
        "heading 2",
        '<w:b/><w:sz w:val="26"/>',
        f'<w:keepNext/><w:pBdr><w:top w:val="single" w:sz="4" w:space="8" w:color="{BORDER}"/>'
        '</w:pBdr><w:spacing w:before="320" w:after="120"/><w:outlineLvl w:val="1"/>',
    )
    + _style(
        "Heading3",
        "heading 3",
        f'<w:b/><w:color w:val="{TEXT_2}"/><w:sz w:val="20"/>',
        '<w:keepNext/><w:spacing w:before="160" w:after="60"/><w:outlineLvl w:val="2"/>',
    )
    + _style(
        "Code",
        "Code",
        '<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/><w:sz w:val="17"/>',
        '<w:shd w:val="clear" w:color="auto" w:fill="F3F5F7"/>'
        '<w:spacing w:after="160" w:line="240" w:lineRule="auto"/><w:ind w:left="120"/>',
    )
    + _style("Caption", "caption", f'<w:color w:val="{TEXT_2}"/><w:sz w:val="17"/>')
    + _style("ListBullet", "List Bullet", "", '<w:ind w:left="360" w:hanging="240"/>')
    + '<w:style w:type="table" w:styleId="ReportTable"><w:name w:val="Report Table"/>'
    "<w:tblPr><w:tblBorders>"
    + "".join(
        f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="{BORDER}"/>'
        for side in ("top", "left", "bottom", "right", "insideH", "insideV")
    )
    + "</w:tblBorders>"
    '<w:tblCellMar><w:top w:w="40" w:type="dxa"/><w:left w:w="80" w:type="dxa"/>'
    '<w:bottom w:w="40" w:type="dxa"/><w:right w:w="80" w:type="dxa"/></w:tblCellMar>'
    "</w:tblPr></w:style>"
    "</w:styles>"
)


def _run(text, props="") -> str:
    rpr = f"<w:rPr>{props}</w:rPr>" if props else ""
    lines = _clean(text).split("\n")
    body = "<w:br/>".join(f'<w:t xml:space="preserve">{escape(line)}</w:t>' for line in lines)
    return f"<w:r>{rpr}{body}</w:r>"


def _para(text="", style="", runs=None, props="") -> str:
    ppr = f'<w:pStyle w:val="{style}"/>' if style else ""
    content = runs if runs is not None else (_run(text) if text else "")
    return f"<w:p><w:pPr>{ppr}{props}</w:pPr>{content}</w:p>"


def _table(header, rows) -> str:
    def cell(text, bold=False):
        props = "<w:b/>" if bold else ""
        shade = '<w:shd w:val="clear" w:color="auto" w:fill="F3F5F7"/>' if bold else ""
        return (
            f"<w:tc><w:tcPr>{shade}</w:tcPr>"
            f'<w:p><w:pPr><w:spacing w:after="0"/></w:pPr>{_run(text, props)}</w:p></w:tc>'
        )

    # Text width (12240 less the margins) split evenly; Word widens columns to fit.
    grid = f'<w:gridCol w:w="{9640 // len(header)}"/>' * len(header)
    head = "<w:tr><w:trPr><w:tblHeader/></w:trPr>" + "".join(cell(h, True) for h in header)
    body = "".join("<w:tr>" + "".join(cell(v) for v in row) + "</w:tr>" for row in rows)
    return (
        '<w:tbl><w:tblPr><w:tblStyle w:val="ReportTable"/><w:tblW w:w="5000" w:type="pct"/>'
        f"</w:tblPr><w:tblGrid>{grid}</w:tblGrid>{head}</w:tr>{body}</w:tbl>"
        + _para(props='<w:spacing w:after="0"/>')
    )


def _figure(number, image, width, height, alt) -> str:
    scale = min(_FIGURE_WIDTH / width, _FIGURE_HEIGHT / height)
    cx, cy = round(width * scale), round(height * scale)
    alt = escape(_clean(alt), {'"': "&quot;"})
    return (
        '<w:p><w:pPr><w:keepNext/><w:jc w:val="center"/></w:pPr><w:r><w:drawing>'
        f'<wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{cx}" cy="{cy}"/>'
        f'<wp:docPr id="{number}" name="Figure {number}" descr="{alt}"/>'
        '<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f'<pic:pic><pic:nvPicPr><pic:cNvPr id="{number}" name="{image}"/><pic:cNvPicPr/>'
        f'</pic:nvPicPr><pic:blipFill><a:blip r:embed="rIdFigure{number}"/>'
        "<a:stretch><a:fillRect/></a:stretch></pic:blipFill>"
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
        "</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>"
    )


def _docx_body(blocks, media) -> str:
    """The document's body. Figures are added to `media` as (file name, PNG bytes)."""
    out = []
    for block in blocks:
        if block.kind == "cover":
            brand = _run(BRAND, f'<w:color w:val="{ACCENT}"/>')
            out.append(_para(runs=brand, props='<w:spacing w:after="0"/>'))
            out.append(_para(block.text, "Title"))
            out += [_para(line, "Subtitle") for line in block.items]
        elif block.kind == "break":
            out.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
        elif block.kind == "h1":
            out.append(_para(block.text, "Heading1"))
        elif block.kind == "h2":
            out.append(_para(block.text, "Heading3"))
        elif block.kind == "finding":
            colour = SEVERITY_COLOURS[block.severity]
            word = SEVERITY_WORDS[block.severity].upper()
            runs = _run(f"{word}  ", f'<w:color w:val="{colour}"/>') + _run(block.text)
            out.append(_para(style="Heading2", runs=runs))
        elif block.kind == "p":
            out.append(_para(block.text))
        elif block.kind == "bullets":
            out += [
                _para(style="ListBullet", runs=f"{_run('•')}<w:r><w:tab/></w:r>{_run(item)}")
                for item in block.items
            ]
        elif block.kind == "code":
            out.append(_para(block.text, "Code"))
        elif block.kind == "table":
            out.append(_table(block.header, block.items))
        elif block.kind == "map":
            number = len(media) + 1
            content, width, height = figures.png(block.svg)
            name = f"figure{number}.png"
            media.append((name, content))
            out.append(_figure(number, name, width, height, block.text))
            out.append(_para(block.text, "Caption"))
    return "".join(out)


def docx(report) -> bytes:
    footer_text = f"Confidential · prepared for {report.customer.name}"
    media = []
    body = _docx_body(report.blocks(), media)
    document = (
        _XML + f"<w:document {_W} {_R} {_DRAWING}><w:body>{body}"
        '<w:sectPr><w:headerReference w:type="default" r:id="rId2"/>'
        '<w:footerReference w:type="default" r:id="rId3"/>'
        '<w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1300" w:right="1300" w:bottom="1300" w:left="1300" '
        'w:header="600" w:footer="600" w:gutter="0"/><w:titlePg/></w:sectPr>'
        "</w:body></w:document>"
    )
    small = f'<w:color w:val="{TEXT_2}"/><w:sz w:val="16"/>'
    header = (
        _XML
        + f"<w:hdr {_W}>"
        + _para(
            runs=_run(f"{BRAND}  ·  {report.customer.name}  ·  {report.engagement.name}", small)
        )
        + "</w:hdr>"
    )
    page = (
        f'<w:r><w:rPr>{small}</w:rPr><w:fldChar w:fldCharType="begin"/></w:r>'
        f'<w:r><w:rPr>{small}</w:rPr><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
        f'<w:r><w:rPr>{small}</w:rPr><w:fldChar w:fldCharType="end"/></w:r>'
    )
    footer = (
        _XML
        + f"<w:ftr {_W}>"
        + _para(
            runs=_run(f"{footer_text}  ·  Page ", small) + page,
            props='<w:tabs><w:tab w:val="right" w:pos="9640"/></w:tabs>',
        )
        + "</w:ftr>"
    )
    core = (
        _XML + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
        'metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>{escape(_clean(_title(report)))}</dc:title>"
        f"<dc:creator>{escape(BRAND)}</dc:creator></cp:coreProperties>"
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", _CONTENT_TYPES)
        package.writestr("_rels/.rels", _ROOT_RELS)
        package.writestr("docProps/core.xml", core)
        package.writestr("word/document.xml", document)
        package.writestr("word/styles.xml", _STYLES)
        package.writestr("word/header1.xml", header)
        package.writestr("word/footer1.xml", footer)
        images = "".join(
            _IMAGE_REL.format(rid=f"rIdFigure{n}", name=name)
            for n, (name, _) in enumerate(media, 1)
        )
        package.writestr("word/_rels/document.xml.rels", _DOCUMENT_RELS.format(images=images))
        for name, content in media:
            package.writestr(f"word/media/{name}", content)
    return out.getvalue()


def _title(report) -> str:
    return f"Network assessment · {report.customer.name} · {report.engagement.name}"


# PDF


def _hex(value: str):
    return colors.HexColor(f"#{value}")


_BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=10, leading=14, spaceAfter=6)
# Headings stay on the page with what follows them.
_PDF_STYLES = {
    "title": ParagraphStyle("title", _BODY, fontName="Helvetica-Bold", fontSize=28, leading=34),
    "subtitle": ParagraphStyle("subtitle", _BODY, fontSize=14, leading=20, textColor=_hex(TEXT_2)),
    "h1": ParagraphStyle(
        "h1",
        _BODY,
        keepWithNext=1,
        fontName="Helvetica-Bold",
        fontSize=16,
        leading=20,
        textColor=_hex(ACCENT),
        spaceBefore=14,
        spaceAfter=8,
    ),
    "finding": ParagraphStyle(
        "finding",
        _BODY,
        keepWithNext=1,
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=17,
        spaceBefore=14,
    ),
    "h2": ParagraphStyle(
        "h2",
        _BODY,
        keepWithNext=1,
        fontName="Helvetica-Bold",
        fontSize=10,
        textColor=_hex(TEXT_2),
        spaceBefore=6,
        spaceAfter=2,
    ),
    "caption": ParagraphStyle(
        "caption", _BODY, fontSize=8.5, leading=11, textColor=_hex(TEXT_2), spaceBefore=4
    ),
    "bullet": ParagraphStyle("bullet", _BODY, leftIndent=14, bulletIndent=2),
    "cell": ParagraphStyle("cell", _BODY, fontSize=9, leading=11, spaceAfter=0),
    "head": ParagraphStyle("head", _BODY, fontName="Helvetica-Bold", fontSize=9, leading=11),
    "code": ParagraphStyle(
        "code",
        fontName="Courier",
        fontSize=8,
        leading=10,
        backColor=_hex("F3F5F7"),
        borderPadding=6,
        spaceBefore=4,
        spaceAfter=10,
    ),
}
# Courier at 8pt fits about this many characters across the text width.
_CODE_WIDTH = 92


def _text(value) -> str:
    """Text for a Paragraph, which reads markup: escape it, keep line breaks."""
    return escape(_clean(value)).replace("\n", "<br/>")


def _code(value) -> str:
    lines = []
    for line in _clean(value).splitlines() or [""]:
        lines += textwrap.wrap(line, _CODE_WIDTH, subsequent_indent="  ") or [""]
    return "\n".join(lines)


def _pdf_table(header, rows, width):
    weights = [0.45 if h in NUMERIC else 1.0 for h in header]
    widths = [width * w / sum(weights) for w in weights]
    data = [[Paragraph(_text(h), _PDF_STYLES["head"]) for h in header]]
    data += [[Paragraph(_text(v), _PDF_STYLES["cell"]) for v in row] for row in rows]
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _hex("F3F5F7")),
                ("GRID", (0, 0), (-1, -1), 0.5, _hex(BORDER)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return [table, Spacer(1, 10)]


def _map(block, width, height) -> list:
    drawing = figures.drawing(block.svg, width, height)
    drawing.hAlign = "CENTER"
    return [drawing, Paragraph(_text(block.text), _PDF_STYLES["caption"]), Spacer(1, 8)]


def _flowables(blocks, width, height) -> list:
    out = []
    for block in blocks:
        if block.kind == "cover":
            out.append(Spacer(1, 2 * inch))
            out.append(Paragraph(_text(block.text), _PDF_STYLES["title"]))
            out.append(Spacer(1, 12))
            out += [Paragraph(_text(line), _PDF_STYLES["subtitle"]) for line in block.items]
        elif block.kind == "break":
            out.append(PageBreak())
        elif block.kind == "finding":
            colour = SEVERITY_COLOURS[block.severity]
            word = SEVERITY_WORDS[block.severity].upper()
            text = f'<font color="#{colour}">{word}</font>&nbsp;&nbsp;{_text(block.text)}'
            out.append(Paragraph(text, _PDF_STYLES["finding"]))
        elif block.kind in ("h1", "h2"):
            out.append(Paragraph(_text(block.text), _PDF_STYLES[block.kind]))
        elif block.kind == "p":
            out.append(Paragraph(_text(block.text), _BODY))
        elif block.kind == "bullets":
            out += [
                Paragraph(_text(item), _PDF_STYLES["bullet"], bulletText="•")
                for item in block.items
            ]
        elif block.kind == "code":
            out.append(Preformatted(_code(block.text), _PDF_STYLES["code"]))
        elif block.kind == "table":
            out += _pdf_table(block.header, block.items, width)
        elif block.kind == "map":
            # Room on the page for the site's heading above and the caption below.
            out += _map(block, width, height - 72)
    return out


def pdf(report) -> bytes:
    out = io.BytesIO()
    margin = 0.9 * inch
    document = SimpleDocTemplate(
        out,
        pagesize=letter,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=margin,
        title=_clean(_title(report)),
        author=BRAND,
        creator="Cartio",
    )
    page_width, page_height = letter
    customer, engagement = report.customer.name, report.engagement.name

    def cover(canvas, _doc):
        canvas.saveState()
        canvas.setFillColor(_hex("0E1A24"))
        canvas.rect(0, page_height - 0.5 * inch, page_width, 0.5 * inch, stroke=0, fill=1)
        canvas.setFillColor(_hex("5EC4D6"))
        canvas.setFont("Helvetica-Bold", 11)
        canvas.drawString(margin, page_height - 0.32 * inch, BRAND)
        canvas.restoreState()

    def page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(_hex(TEXT_2))
        canvas.drawString(margin, page_height - 0.55 * inch, _clean(BRAND))
        canvas.drawRightString(
            page_width - margin, page_height - 0.55 * inch, _clean(f"{customer} · {engagement}")
        )
        canvas.drawString(margin, 0.5 * inch, _clean(f"Confidential · prepared for {customer}"))
        canvas.drawRightString(page_width - margin, 0.5 * inch, f"Page {doc.page}")
        canvas.restoreState()

    document.build(
        _flowables(report.blocks(), document.width, document.height),
        onFirstPage=cover,
        onLaterPages=page,
    )
    return out.getvalue()
