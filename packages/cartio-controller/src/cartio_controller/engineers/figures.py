"""The report's figures: a Site map SVG drawn into the PDF and, as a PNG, into the DOCX.

The report's topology page (YAN-1745) prints the same SVG the Site map exports
(`SiteMap.svg()`), so a map looks the same on screen, in the export and in the report. That SVG
uses a small set of elements with presentation attributes only (no CSS, no references), which is
all this reads: `g` with a translate, `rect`, `line`, `circle`, `path` and `text`. Paths are
flattened to straight segments, which at the icons' size can't be seen.

ReportLab draws the PDF figure as vectors with its standard fonts. The DOCX gets a PNG drawn with
Pillow (a ReportLab dependency) using the IBM Plex fonts the engineer UI ships, drawn at twice
the size and scaled down so its lines are smooth.
"""

import io
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image, ImageDraw, ImageFont
from reportlab.graphics.shapes import Circle, Drawing, Group, Line, Polygon, PolyLine, Rect, String
from reportlab.lib import colors

_SVG = "{http://www.w3.org/2000/svg}"
_FONTS = Path(__file__).parent / "static" / "engineers" / "fonts"
_TRANSLATE = re.compile(r"translate\(\s*([-\d.]+)[\s,]+([-\d.]+)\s*\)")


@dataclass
class Style:
    fill: str = "#000000"
    stroke: str = "none"
    width: float = 1.0
    dash: tuple = ()
    opacity: float = 1.0
    fill_opacity: float = 1.0
    size: float = 12.0
    bold: bool = False
    mono: bool = False
    anchor: str = "start"


@dataclass
class Shape:
    """One thing to draw, in SVG units (y down)."""

    kind: str  # rect, line, circle, path, text
    style: Style
    # rect: x, y, w, h, rx; line: x1, y1, x2, y2; circle: cx, cy, r; text: x, y.
    at: tuple = ()
    # path: a list of polylines, each a list of points, and whether each one is closed.
    lines: list = field(default_factory=list)
    text: str = ""


def _number(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _styled(parent: Style, attrs) -> Style:
    style = Style(**vars(parent))
    style.opacity = parent.opacity * _number(attrs.get("opacity"), 1.0)
    if "fill" in attrs:
        style.fill = attrs["fill"]
    if "stroke" in attrs:
        style.stroke = attrs["stroke"]
    if "stroke-width" in attrs:
        style.width = _number(attrs["stroke-width"], 1.0)
    if "stroke-dasharray" in attrs:
        style.dash = tuple(_number(v) for v in attrs["stroke-dasharray"].replace(",", " ").split())
    if "fill-opacity" in attrs:
        style.fill_opacity = _number(attrs["fill-opacity"], 1.0)
    if "font-size" in attrs:
        style.size = _number(attrs["font-size"], 12.0)
    if "font-weight" in attrs:
        style.bold = attrs["font-weight"] in ("600", "700", "bold")
    if "font-family" in attrs:
        style.mono = "Mono" in attrs["font-family"] or "monospace" in attrs["font-family"]
    if "text-anchor" in attrs:
        style.anchor = attrs["text-anchor"]
    return style


def parse(svg: str) -> tuple[float, float, list]:
    """The SVG's size and its shapes, in drawing order."""
    root = ElementTree.fromstring(svg)  # noqa: S314 - our own SVG from SiteMap.svg().
    width, height = _number(root.get("width")), _number(root.get("height"))
    if not (width and height):
        box = [_number(v) for v in root.get("viewBox", "0 0 0 0").split()]
        width, height = box[2], box[3]
    shapes: list[Shape] = []
    _walk(root, Style(), 0.0, 0.0, shapes)
    return width, height, shapes


def _walk(element, parent: Style, dx: float, dy: float, out: list) -> None:
    for child in element:
        tag = child.tag.removeprefix(_SVG)
        if tag == "title":
            continue
        style = _styled(parent, child.attrib)
        get = child.get
        if tag in ("g", "a"):
            moved = _TRANSLATE.search(get("transform", ""))
            ox, oy = (float(moved[1]), float(moved[2])) if moved else (0.0, 0.0)
            _walk(child, style, dx + ox, dy + oy, out)
        elif tag == "rect":
            at = (
                _number(get("x")) + dx,
                _number(get("y")) + dy,
                _number(get("width")),
                _number(get("height")),
                _number(get("rx")),
            )
            out.append(Shape("rect", style, at))
        elif tag == "line":
            at = (
                _number(get("x1")) + dx,
                _number(get("y1")) + dy,
                _number(get("x2")) + dx,
                _number(get("y2")) + dy,
            )
            out.append(Shape("line", style, at))
        elif tag == "circle":
            at = (_number(get("cx")) + dx, _number(get("cy")) + dy, _number(get("r")))
            out.append(Shape("circle", style, at))
        elif tag == "path":
            lines = [
                ([(x + dx, y + dy) for x, y in points], closed)
                for points, closed in flatten(get("d", ""))
            ]
            out.append(Shape("path", style, lines=lines))
        elif tag == "text":
            at = (_number(get("x")) + dx, _number(get("y")) + dy)
            out.append(Shape("text", style, at, text="".join(child.itertext())))


# Path data: M L H V C A Z, absolute and relative, flattened to polylines.

_TOKEN = re.compile(r"[A-Za-z]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "A": 7, "Z": 0}


class _Scanner:
    def __init__(self, d: str):
        self.d, self.at = d, 0

    def _skip(self) -> None:
        while self.at < len(self.d) and self.d[self.at] in " ,\t\r\n":
            self.at += 1

    def command(self):
        self._skip()
        if self.at < len(self.d) and self.d[self.at].isalpha():
            self.at += 1
            return self.d[self.at - 1]
        return None

    def more(self) -> bool:
        self._skip()
        return self.at < len(self.d) and not self.d[self.at].isalpha()

    def number(self) -> float:
        self._skip()
        found = _TOKEN.match(self.d, self.at)
        if not found or found[0].isalpha():
            raise ValueError(f"Bad path data near {self.d[self.at : self.at + 10]!r}")
        self.at = found.end()
        return float(found[0])

    def flag(self) -> bool:
        # Arc flags are one character and may run into the next number ("0115").
        self._skip()
        value = self.d[self.at : self.at + 1]
        if value not in ("0", "1"):
            raise ValueError("Bad arc flag")
        self.at += 1
        return value == "1"


def flatten(d: str) -> list:
    """The path as (points, closed) polylines."""
    scan = _Scanner(d)
    out, points = [], []
    x = y = start_x = start_y = 0.0
    command = scan.command()
    while command:
        upper, relative = command.upper(), command.islower()
        if upper not in _ARGS:
            raise ValueError(f"Path command {command} isn't drawn")
        if upper == "Z":
            if points:
                out.append((points, True))
            points, x, y = [], start_x, start_y
            command = scan.command()
            continue
        first = True
        while first or scan.more():
            ox, oy = (x, y) if relative else (0.0, 0.0)
            if upper == "M":
                if points:
                    out.append((points, False))
                x, y = scan.number() + ox, scan.number() + oy
                start_x, start_y, points = x, y, [(x, y)]
                # Pairs after the first are lines.
                upper = "L"
            elif upper == "L":
                x, y = scan.number() + ox, scan.number() + oy
                points.append((x, y))
            elif upper == "H":
                x = scan.number() + ox
                points.append((x, y))
            elif upper == "V":
                y = scan.number() + oy
                points.append((x, y))
            elif upper == "C":
                c1 = (scan.number() + ox, scan.number() + oy)
                c2 = (scan.number() + ox, scan.number() + oy)
                end = (scan.number() + ox, scan.number() + oy)
                points += _bezier((x, y), c1, c2, end)
                x, y = end
            elif upper == "A":
                rx, ry, angle = scan.number(), scan.number(), scan.number()
                large, sweep = scan.flag(), scan.flag()
                end = (scan.number() + ox, scan.number() + oy)
                points += _arc((x, y), rx, ry, angle, large, sweep, end)
                x, y = end
            if not points:
                points = [(x, y)]
            first = False
        command = scan.command()
    if points:
        out.append((points, False))
    return out


def _bezier(p0, p1, p2, p3, steps=8) -> list:
    out = []
    for i in range(1, steps + 1):
        t = i / steps
        u = 1 - t
        out.append(
            tuple(
                u**3 * a + 3 * u * u * t * b + 3 * u * t * t * c + t**3 * e
                for a, b, c, e in zip(p0, p1, p2, p3, strict=True)
            )
        )
    return out


def _arc(start, rx, ry, angle, large, sweep, end, step=math.pi / 12) -> list:
    """An elliptical arc as points (SVG 1.1, appendix F.6.5)."""
    (x1, y1), (x2, y2) = start, end
    if not (rx and ry) or start == end:
        return [end]
    phi = math.radians(angle)
    cos, sin = math.cos(phi), math.sin(phi)
    hx, hy = (x1 - x2) / 2, (y1 - y2) / 2
    xp, yp = cos * hx + sin * hy, -sin * hx + cos * hy
    rx, ry = abs(rx), abs(ry)
    grow = xp * xp / (rx * rx) + yp * yp / (ry * ry)
    if grow > 1:
        rx, ry = rx * math.sqrt(grow), ry * math.sqrt(grow)
    top = rx * rx * ry * ry - rx * rx * yp * yp - ry * ry * xp * xp
    under = rx * rx * yp * yp + ry * ry * xp * xp
    k = math.sqrt(max(top, 0) / under) if under else 0.0
    if large == sweep:
        k = -k
    cxp, cyp = k * rx * yp / ry, -k * ry * xp / rx
    cx = cos * cxp - sin * cyp + (x1 + x2) / 2
    cy = sin * cxp + cos * cyp + (y1 + y2) / 2
    theta = math.atan2((yp - cyp) / ry, (xp - cxp) / rx)
    delta = math.atan2((-yp - cyp) / ry, (-xp - cxp) / rx) - theta
    if sweep and delta < 0:
        delta += 2 * math.pi
    elif not sweep and delta > 0:
        delta -= 2 * math.pi
    steps = max(2, math.ceil(abs(delta) / step))
    out = []
    for i in range(1, steps + 1):
        t = theta + delta * i / steps
        ex, ey = rx * math.cos(t), ry * math.sin(t)
        out.append((cos * ex - sin * ey + cx, sin * ex + cos * ey + cy))
    out[-1] = end
    return out


# PDF: a ReportLab drawing, scaled to fit.


def _colour(value: str, alpha: float = 1.0):
    if not value or value in ("none", "transparent"):
        return None
    colour = colors.HexColor(value)
    if alpha < 1:
        colour = colors.Color(colour.red, colour.green, colour.blue, alpha=alpha)
    return colour


def drawing(svg: str, max_width: float, max_height: float) -> Drawing:
    """The SVG as a ReportLab drawing no wider or taller than given, in points."""
    width, height, shapes = parse(svg)
    scale = min(max_width / width, max_height / height, 1.0)
    group = Group()
    flip = lambda x, y: (x, height - y)  # noqa: E731 - SVG's y runs down, PDF's up.
    for shape in shapes:
        group.add(_pdf_shape(shape, flip, height))
    group.scale(scale, scale)
    out = Drawing(width * scale, height * scale)
    out.add(group)
    return out


def _pdf_paint(shape, node) -> None:
    style = shape.style
    stroke = _colour(style.stroke, style.opacity)
    node.strokeColor = stroke
    node.strokeWidth = style.width if stroke else 0
    if style.dash:
        node.strokeDashArray = list(style.dash)
    if hasattr(node, "fillColor"):
        node.fillColor = _colour(style.fill, style.opacity * style.fill_opacity)


def _pdf_shape(shape, flip, height):
    style = shape.style
    if shape.kind == "rect":
        x, y, w, h, rx = shape.at
        node = Rect(x, height - y - h, w, h, rx=rx, ry=rx)
    elif shape.kind == "line":
        x1, y1, x2, y2 = shape.at
        node = Line(*flip(x1, y1), *flip(x2, y2))
    elif shape.kind == "circle":
        cx, cy, r = shape.at
        node = Circle(*flip(cx, cy), r)
    elif shape.kind == "text":
        font = "Courier" if style.mono else "Helvetica"
        if style.bold:
            font += "-Bold"
        # Plex is narrower than Courier, so mono labels print a little smaller to keep their width.
        size = style.size * (0.85 if style.mono else 1.0)
        anchor = {"middle": "middle", "end": "end"}.get(style.anchor, "start")
        x, y = flip(*shape.at)
        return String(
            x,
            y,
            shape.text,
            fontName=font,
            fontSize=size,
            fillColor=_colour(style.fill, style.opacity),
            textAnchor=anchor,
        )
    else:
        group = Group()
        for points, closed in shape.lines:
            flat = [v for point in points for v in flip(*point)]
            if len(points) == 1:
                flat += flat
            node = Polygon(flat) if closed else PolyLine(flat)
            _pdf_paint(shape, node)
            node.strokeLineCap = node.strokeLineJoin = 1
            if not closed and hasattr(node, "fillColor"):
                node.fillColor = None
            group.add(node)
        return group
    _pdf_paint(shape, node)
    return node


# DOCX: a PNG.


def _font(style: Style, size: float):
    name = "ibm-plex-mono-latin-500-normal" if style.mono else "ibm-plex-sans-latin-{}-normal"
    weight = "600" if style.bold else "400"
    try:
        return ImageFont.truetype(str(_FONTS / f"{name.format(weight)}.woff2"), round(size))
    except OSError:
        return ImageFont.load_default(round(size))


def _rgb(value: str, alpha: float, background=(250, 251, 252)):
    if not value or value in ("none", "transparent"):
        return None
    value = value.lstrip("#")
    rgb = tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))
    # Blended over the map's background, which is what opacity looks like on it.
    return tuple(round(c * alpha + b * (1 - alpha)) for c, b in zip(rgb, background, strict=True))


def _dashed(draw, points, colour, width, dash) -> None:
    on, off = (dash + dash)[:2]
    for (x1, y1), (x2, y2) in zip(points, points[1:], strict=False):
        length = math.hypot(x2 - x1, y2 - y1)
        at = 0.0
        while at < length:
            stop = min(at + on, length)
            draw.line(
                [
                    (x1 + (x2 - x1) * at / length, y1 + (y2 - y1) * at / length),
                    (x1 + (x2 - x1) * stop / length, y1 + (y2 - y1) * stop / length),
                ],
                fill=colour,
                width=width,
            )
            at = stop + off


def png(svg: str, width_px: int = 1600) -> tuple[bytes, int, int]:
    """The SVG as a PNG about `width_px` wide (never upscaled past 2x), and its size."""
    width, height, shapes = parse(svg)
    final = min(width_px / width, 2.0)
    scale = final * 2  # Drawn at twice the size, then scaled down.
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    image = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(image)
    for shape in shapes:
        _png_shape(draw, shape, scale)
    out_size = (max(1, round(width * final)), max(1, round(height * final)))
    image = image.resize(out_size, Image.Resampling.LANCZOS)
    out = io.BytesIO()
    image.save(out, "PNG", optimize=True)
    return out.getvalue(), *out_size


def _png_shape(draw, shape, scale) -> None:
    style = shape.style
    stroke = _rgb(style.stroke, style.opacity)
    fill = _rgb(style.fill, style.opacity * style.fill_opacity)
    width = max(1, round(style.width * scale))
    dash = tuple(v * scale for v in style.dash)
    if shape.kind == "rect":
        x, y, w, h, rx = (v * scale for v in shape.at)
        box = [x, y, x + w, y + h]
        draw.rounded_rectangle(box, rx, fill=fill, outline=None if dash else stroke, width=width)
        if dash and stroke:
            corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]
            _dashed(draw, corners, stroke, width, dash)
    elif shape.kind == "line":
        x1, y1, x2, y2 = (v * scale for v in shape.at)
        if stroke and dash:
            _dashed(draw, [(x1, y1), (x2, y2)], stroke, width, dash)
        elif stroke:
            draw.line([(x1, y1), (x2, y2)], fill=stroke, width=width)
    elif shape.kind == "circle":
        cx, cy, r = (v * scale for v in shape.at)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill, outline=stroke, width=width)
    elif shape.kind == "text":
        anchor = {"middle": "ms", "end": "rs"}.get(style.anchor, "ls")
        x, y = (v * scale for v in shape.at)
        font = _font(style, style.size * scale)
        draw.text((x, y), shape.text, fill=fill or (0, 0, 0), font=font, anchor=anchor)
    elif stroke:
        for points, closed in shape.lines:
            scaled = [(px * scale, py * scale) for px, py in points]
            if closed:
                scaled.append(scaled[0])
            if len(scaled) > 1:
                draw.line(scaled, fill=stroke, width=width, joint="curve")
            # Round caps, which also draw the icons' dots.
            r = width / 2
            for px, py in (scaled[0], scaled[-1]):
                draw.ellipse([px - r, py - r, px + r, py + r], fill=stroke)
