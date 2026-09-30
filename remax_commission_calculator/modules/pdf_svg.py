"""Draw the project's own SVG icons as PDF vectors. No network, no redraw."""

from __future__ import annotations

import math
import re
from xml.etree import ElementTree

from reportlab.lib import colors


_PATH_TOKEN = re.compile(
    r"[MmLlHhVvCcSsQqTtAaZz]|[+-]?(?:\d*\.\d+|\d+)(?:[eE][+-]?\d+)?"
)


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _number(value, default=0.0):
    if value is None or value == "":
        return default
    return float(value)


def _color(value, current):
    raw = (value or "").strip()
    if raw in {"", "none"}:
        return None
    if raw == "currentColor":
        return current
    if raw.lower() in {"#fff", "#ffffff", "white"}:
        return colors.white
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", raw):
        return colors.HexColor(raw)
    if re.fullmatch(r"#[0-9A-Fa-f]{3}", raw):
        return colors.HexColor("#" + "".join(ch * 2 for ch in raw[1:]))
    return current


def _view_box(root):
    raw = root.attrib.get("viewBox") or ""
    parts = [float(part) for part in raw.replace(",", " ").split() if part]
    if len(parts) == 4 and parts[2] > 0 and parts[3] > 0:
        return parts
    width = _number(root.attrib.get("width"), 24)
    height = _number(root.attrib.get("height"), 24)
    return 0.0, 0.0, width or 24.0, height or 24.0


def _tokens(path_data):
    return _PATH_TOKEN.findall(path_data or "")


def _angle(ux, uy, vx, vy):
    dot = ux * vx + uy * vy
    norm = math.hypot(ux, uy) * math.hypot(vx, vy)
    if norm == 0:
        return 0.0
    angle = math.acos(max(-1.0, min(1.0, dot / norm)))
    if ux * vy - uy * vx < 0:
        angle = -angle
    return angle


def _arc_center(x1, y1, rx, ry, phi_deg, large, sweep, x2, y2):
    if math.hypot(x2 - x1, y2 - y1) < 1e-8:
        return None
    rx = abs(rx)
    ry = abs(ry)
    if rx == 0 or ry == 0:
        return None
    phi = math.radians(phi_deg)
    cos_phi = math.cos(phi)
    sin_phi = math.sin(phi)
    dx = (x1 - x2) / 2.0
    dy = (y1 - y2) / 2.0
    x1p = cos_phi * dx + sin_phi * dy
    y1p = -sin_phi * dx + cos_phi * dy
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1:
        scale = math.sqrt(lam)
        rx *= scale
        ry *= scale
    sign = -1.0 if large == sweep else 1.0
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    coef = sign * math.sqrt(max(0.0, num / den)) if den else 0.0
    cxp = coef * rx * y1p / ry
    cyp = coef * -ry * x1p / rx
    cx = cos_phi * cxp - sin_phi * cyp + (x1 + x2) / 2.0
    cy = sin_phi * cxp + cos_phi * cyp + (y1 + y2) / 2.0
    theta1 = _angle(1.0, 0.0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dtheta = _angle(
        (x1p - cxp) / rx,
        (y1p - cyp) / ry,
        (-x1p - cxp) / rx,
        (-y1p - cyp) / ry,
    )
    if not sweep and dtheta > 0:
        dtheta -= 2 * math.pi
    elif sweep and dtheta < 0:
        dtheta += 2 * math.pi
    return cx, cy, rx, ry, phi, theta1, dtheta


def _map_ellipse(cx, cy, rx, ry, phi, px, py):
    return (
        cx + math.cos(phi) * rx * px - math.sin(phi) * ry * py,
        cy + math.sin(phi) * rx * px + math.cos(phi) * ry * py,
    )


def _arc_curves(x1, y1, rx, ry, phi_deg, large, sweep, x2, y2):
    center = _arc_center(x1, y1, rx, ry, phi_deg, large, sweep, x2, y2)
    if center is None:
        return []
    cx, cy, rx, ry, phi, theta1, dtheta = center
    pieces = max(1, int(math.ceil(abs(dtheta) / (math.pi / 2.0))))
    step = dtheta / pieces
    curves = []
    for index in range(pieces):
        start = theta1 + index * step
        delta = step
        kappa = (4.0 / 3.0) * math.tan(delta / 4.0)
        p1 = (math.cos(start), math.sin(start))
        p2 = (math.cos(start + delta), math.sin(start + delta))
        c1 = (p1[0] - kappa * math.sin(start), p1[1] + kappa * math.cos(start))
        c2 = (
            p2[0] + kappa * math.sin(start + delta),
            p2[1] - kappa * math.cos(start + delta),
        )
        curves.append(
            (
                *_map_ellipse(cx, cy, rx, ry, phi, *c1),
                *_map_ellipse(cx, cy, rx, ry, phi, *c2),
                *_map_ellipse(cx, cy, rx, ry, phi, *p2),
            )
        )
    return curves


def _apply_path(path, data, x, y):
    tokens = _tokens(data)
    index = 0
    command = ""
    start_x, start_y = x, y
    prev_cx, prev_cy = x, y
    prev_was_curve = False

    def take(count):
        nonlocal index
        values = [float(tokens[index + offset]) for offset in range(count)]
        index += count
        return values

    while index < len(tokens):
        token = tokens[index]
        if re.fullmatch(r"[MmLlHhVvCcSsQqTtAaZz]", token):
            command = token
            index += 1
        elif not command:
            break
        relative = command.islower()
        if command in {"M", "m"}:
            dx, dy = take(2)
            x = x + dx if relative else dx
            y = y + dy if relative else dy
            path.moveTo(x, y)
            start_x, start_y = x, y
            command = "l" if relative else "L"
            prev_was_curve = False
        elif command in {"L", "l"}:
            dx, dy = take(2)
            x = x + dx if relative else dx
            y = y + dy if relative else dy
            path.lineTo(x, y)
            prev_was_curve = False
        elif command in {"H", "h"}:
            (value,) = take(1)
            x = x + value if relative else value
            path.lineTo(x, y)
            prev_was_curve = False
        elif command in {"V", "v"}:
            (value,) = take(1)
            y = y + value if relative else value
            path.lineTo(x, y)
            prev_was_curve = False
        elif command in {"C", "c"}:
            x1, y1, x2, y2, nx, ny = take(6)
            if relative:
                x1 += x
                y1 += y
                x2 += x
                y2 += y
                nx += x
                ny += y
            path.curveTo(x1, y1, x2, y2, nx, ny)
            prev_cx, prev_cy = x2, y2
            x, y = nx, ny
            prev_was_curve = True
        elif command in {"S", "s"}:
            x2, y2, nx, ny = take(4)
            if relative:
                x2 += x
                y2 += y
                nx += x
                ny += y
            x1, y1 = (2 * x - prev_cx, 2 * y - prev_cy) if prev_was_curve else (x, y)
            path.curveTo(x1, y1, x2, y2, nx, ny)
            prev_cx, prev_cy = x2, y2
            x, y = nx, ny
            prev_was_curve = True
        elif command in {"Q", "q"}:
            x1, y1, nx, ny = take(4)
            if relative:
                x1 += x
                y1 += y
                nx += x
                ny += y
            path.curveTo(x1, y1, x1, y1, nx, ny)
            prev_cx, prev_cy = x1, y1
            x, y = nx, ny
            prev_was_curve = True
        elif command in {"A", "a"}:
            rx, ry, phi, large, sweep, nx, ny = take(7)
            if relative:
                nx += x
                ny += y
            for c1x, c1y, c2x, c2y, end_x, end_y in _arc_curves(
                x, y, rx, ry, phi, bool(large), bool(sweep), nx, ny
            ):
                path.curveTo(c1x, c1y, c2x, c2y, end_x, end_y)
            x, y = nx, ny
            prev_was_curve = False
        elif command in {"Z", "z"}:
            path.close()
            x, y = start_x, start_y
            prev_was_curve = False
        else:
            break
    return x, y


def _paint_shape(canvas, element, current):
    fill = _color(element.attrib.get("fill", "currentColor"), current)
    stroke = _color(element.attrib.get("stroke"), current)
    width = _number(element.attrib.get("stroke-width"), 1)
    tag = _local(element.tag)
    if tag == "path":
        path = canvas.beginPath()
        _apply_path(path, element.attrib.get("d"), 0, 0)
        canvas.setFillColor(fill or colors.Color(0, 0, 0, alpha=0))
        canvas.setStrokeColor(stroke or colors.Color(0, 0, 0, alpha=0))
        canvas.setLineWidth(width if stroke else 0)
        canvas.setLineCap(1)
        canvas.setLineJoin(1)
        canvas.drawPath(path, fill=1 if fill else 0, stroke=1 if stroke else 0)
        return
    if tag == "rect":
        x = _number(element.attrib.get("x"))
        y = _number(element.attrib.get("y"))
        w = _number(element.attrib.get("width"))
        h = _number(element.attrib.get("height"))
        radius = _number(element.attrib.get("rx"), _number(element.attrib.get("ry")))
        canvas.setFillColor(fill or colors.Color(0, 0, 0, alpha=0))
        canvas.setStrokeColor(stroke or colors.Color(0, 0, 0, alpha=0))
        canvas.setLineWidth(width if stroke else 0)
        canvas.setLineJoin(1)
        canvas.roundRect(x, y, w, h, radius, fill=1 if fill else 0, stroke=1 if stroke else 0)
        return
    if tag == "circle":
        canvas.setFillColor(fill or colors.Color(0, 0, 0, alpha=0))
        canvas.setStrokeColor(stroke or colors.Color(0, 0, 0, alpha=0))
        canvas.setLineWidth(width if stroke else 0)
        canvas.circle(
            _number(element.attrib.get("cx")),
            _number(element.attrib.get("cy")),
            _number(element.attrib.get("r")),
            fill=1 if fill else 0,
            stroke=1 if stroke else 0,
        )


def draw_svg(canvas, svg_text, x, y, box_width, box_height, *, current_color):
    """Place an SVG inside the box. The viewBox ratio is preserved."""
    root = ElementTree.fromstring(svg_text)
    min_x, min_y, view_w, view_h = _view_box(root)
    if view_w <= 0 or view_h <= 0 or box_width <= 0 or box_height <= 0:
        return 0.0, 0.0
    scale = min(box_width / view_w, box_height / view_h)
    draw_w = view_w * scale
    draw_h = view_h * scale
    offset_x = x + (box_width - draw_w) / 2.0
    offset_y = y + (box_height - draw_h) / 2.0
    canvas.saveState()
    canvas.translate(offset_x, offset_y + draw_h)
    canvas.scale(scale, -scale)
    canvas.translate(-min_x, -min_y)
    for element in list(root):
        _paint_shape(canvas, element, current_color)
    canvas.restoreState()
    return draw_w, draw_h
