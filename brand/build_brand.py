#!/usr/bin/env python3
"""Genera el kit de marca de Jarvis Workspace desde cero: geometría -> SVG -> PNG / ICO / ICNS.

    pip install -r brand/requirements.txt
    python3 brand/build_brand.py                 # todo el kit, dentro de brand/
    python3 brand/build_brand.py --accent cian   # el mismo kit con otro color de acento
    python3 brand/build_brand.py --svg-only      # solo vectores (no necesita Chromium)

El logo es una pajarita (Jarvis, el mayordomo): dos alas blancas y un nudo de color que es,
a la vez, el cursor de bloque de una terminal. Todo se construye con contornos exactos
(skia-pathops), sin trazos ni fuentes en los SVG: el wordmark sale de Manrope (SIL OFL)
convertido a curvas. Nada de esto lo necesita la app en runtime: son assets estáticos.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import shutil
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ───────────────────────────── paleta ─────────────────────────────
INK_TOP, INK_BOTTOM = "#1A2540", "#090D18"          # baldosa oscura
IVORY_TOP, IVORY_BOTTOM = "#FFFFFF", "#D7E1F0"      # alas sobre fondo oscuro
NAVY = "#0E1730"                                     # alas sobre fondo claro
PAPER_TOP, PAPER_BOTTOM = "#FFFFFF", "#E4EAF4"       # baldosa clara
TEXT_MUTED_DARK, TEXT_MUTED_LIGHT = "#9FB0CF", "#5A6A8C"
# (claro, base, profundo): el nudo lleva degradé vertical claro -> profundo
ACCENTS = {
    "oro": ("#FFE59A", "#FFC247", "#E39A10"),
    "cian": ("#9AF6FF", "#2EE6F6", "#10B4D4"),
    "menta": ("#A6FFE3", "#3DF2B0", "#12C58A"),
    "violeta": ("#D2C2FF", "#9B7BFF", "#6A4DE0"),
}
# Color del halo detrás del nudo (más saturado que el base: sobre azul noche un dorado puro se ensucia).
GLOW = {"oro": "#FF9F1C", "cian": "#19D3F5", "menta": "#1FE0A0", "violeta": "#8B6BFF"}
GLOW_OPACITY = 0.17
DEFAULT_ACCENT = "oro"

# ─────────────────────────── geometría ────────────────────────────
# Caja de 512 unidades, centrada en (256, 256). Alas con bordes superior/inferior cóncavos y
# borde exterior apenas convexo; el nudo (rect. redondeado) recorta un hueco `gap` en las alas
# para que la marca siga leyéndose en un solo color.
GEOM = dict(xo=100, xa=236, yt=150, yb=362, dip=0.60, bulge=12, r=13,
            kw=58, kh=124, krx=22, gap=12)


def _fmt(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _to_d(path) -> str:
    from fontTools.pens.basePen import decomposeQuadraticSegment
    out = []
    for verb, pts in path.segments:
        if verb == "moveTo":
            out.append(f"M{_fmt(pts[0][0])} {_fmt(pts[0][1])}")
        elif verb == "lineTo":
            out.append(f"L{_fmt(pts[0][0])} {_fmt(pts[0][1])}")
        elif verb == "qCurveTo":
            for c, e in decomposeQuadraticSegment(pts):
                out.append(f"Q{_fmt(c[0])} {_fmt(c[1])} {_fmt(e[0])} {_fmt(e[1])}")
        elif verb == "curveTo":
            out.append("C" + " ".join(f"{_fmt(x)} {_fmt(y)}" for x, y in pts))
        elif verb in ("closePath", "endPath"):
            out.append("Z")
    return "".join(out)


def _rounded(path, r):
    """Esquinas convexas con radio r: la forma + su propio contorno trazado con punta redonda."""
    from pathops import LineCap, LineJoin, Path, PathOp, op
    s = Path(path)
    s.stroke(2 * r, LineCap.ROUND_CAP, LineJoin.ROUND_JOIN, 4)
    s.convertConicsToQuads()
    u = op(path, s, PathOp.UNION)
    u.convertConicsToQuads()
    return u


def _rrect(x, y, w, h, rx):
    from pathops import Path
    p = Path()
    pen = p.getPen()
    k = rx * 0.5522847498
    pen.moveTo((x + rx, y))
    pen.lineTo((x + w - rx, y))
    pen.curveTo((x + w - rx + k, y), (x + w, y + rx - k), (x + w, y + rx))
    pen.lineTo((x + w, y + h - rx))
    pen.curveTo((x + w, y + h - rx + k), (x + w - rx + k, y + h), (x + w - rx, y + h))
    pen.lineTo((x + rx, y + h))
    pen.curveTo((x + rx - k, y + h), (x, y + h - rx + k), (x, y + h - rx))
    pen.lineTo((x, y + rx))
    pen.curveTo((x, y + rx - k), (x + rx - k, y), (x + rx, y))
    pen.closePath()
    return p


def _wing(xo, xa, yt, yb, dip, bulge, mirror=False, cx=256):
    from pathops import Path
    def X(x):
        return 2 * cx - x if mirror else x
    ym = (yt + yb) / 2
    xm = (xo + xa) / 2
    p = Path()
    pen = p.getPen()
    pen.moveTo((X(xo), yt))
    pen.qCurveTo((X(xm), yt + (ym - yt) * dip), (X(xa), ym))
    pen.qCurveTo((X(xm), yb - (yb - ym) * dip), (X(xo), yb))
    pen.qCurveTo((X(xo - bulge), ym), (X(xo), yt))
    pen.closePath()
    return p


def build_mark(g: dict = GEOM) -> dict:
    """Contornos de la marca ya centrados en (256, 256): alas, nudo, ambos y límites."""
    from pathops import PathOp, op
    left = _rounded(_wing(g["xo"], g["xa"], g["yt"], g["yb"], g["dip"], g["bulge"], False), g["r"])
    right = _rounded(_wing(g["xo"], g["xa"], g["yt"], g["yb"], g["dip"], g["bulge"], True), g["r"])
    wings = op(left, right, PathOp.UNION)
    cy = (g["yt"] + g["yb"]) / 2
    knot = _rrect(256 - g["kw"] / 2, cy - g["kh"] / 2, g["kw"], g["kh"], g["krx"])
    if g["gap"] > 0:
        wings = op(wings, _rounded(knot, g["gap"]), PathOp.DIFFERENCE)
    both = op(wings, knot, PathOp.UNION)
    x0, y0, x1, y1 = both.bounds
    tx, ty = 256 - (x0 + x1) / 2, 256 - (y0 + y1) / 2
    for p in (wings, knot, both):
        p.transform(1, 0, 0, 1, tx, ty)
    return dict(wings=_to_d(wings), knot=_to_d(knot), both=_to_d(both), bounds=both.bounds)


def squircle_d(x: float, y: float, w: float, h: float, n: float = 5.0, steps: int = 72) -> str:
    """Superelipse |x|^n+|y|^n=1 (n=5 ≈ la baldosa de iOS/macOS) como curvas cúbicas suaves."""
    cx, cy = x + w / 2, y + h / 2
    pts = []
    for i in range(steps):
        a = 2 * math.pi * i / steps
        c, s = math.cos(a), math.sin(a)
        r = (abs(c) ** n + abs(s) ** n) ** (-1 / n)
        pts.append((cx + (w / 2) * r * c, cy + (h / 2) * r * s))
    d = [f"M{_fmt(pts[0][0])} {_fmt(pts[0][1])}"]
    for i in range(steps):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[(i + 1) % steps], pts[(i + 2) % steps]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d.append(f"C{_fmt(c1[0])} {_fmt(c1[1])} {_fmt(c2[0])} {_fmt(c2[1])} {_fmt(p2[0])} {_fmt(p2[1])}")
    d.append("Z")
    return "".join(d)


# ───────────────────────────── wordmark ───────────────────────────
class Wordmark:
    """Texto -> contornos SVG con HarfBuzz (kerning real) + fontTools. Sin fuentes en el SVG."""

    def __init__(self, path: Path):
        import uharfbuzz as hb
        from fontTools.ttLib import TTFont
        self.tt = TTFont(str(path))
        self.upem = self.tt["head"].unitsPerEm
        self.cap = self.tt["OS/2"].sCapHeight / self.upem
        flat = TTFont(str(path))
        flat.flavor = None
        buf = io.BytesIO()
        flat.save(buf)
        self.hb = hb
        self.font = hb.Font(hb.Face(buf.getvalue()))
        self.glyphs = self.tt.getGlyphSet()
        self.order = self.tt.getGlyphOrder()

    def path(self, text: str, cap_height: float, tracking: float = 0.0):
        """(d, (x0, y0, x1, y1), baseline). El origen es el comienzo del texto sobre su línea base."""
        from fontTools.pens.boundsPen import BoundsPen
        from fontTools.pens.svgPathPen import SVGPathPen
        from fontTools.pens.transformPen import TransformPen
        size = cap_height / self.cap
        k = size / self.upem
        buf = self.hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        self.hb.shape(self.font, buf, {"kern": True, "liga": True})
        svg = SVGPathPen(self.glyphs, ntos=_fmt)
        bnd = BoundsPen(self.glyphs)
        x = 0.0
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
            name = self.order[info.codepoint]
            for pen in (svg, bnd):
                self.glyphs[name].draw(TransformPen(pen, (k, 0, 0, -k, x + pos.x_offset * k, -pos.y_offset * k)))
            x += pos.x_advance * k + tracking * size
        return svg.getCommands(), bnd.bounds, 0.0


# ───────────────────────────── SVG ────────────────────────────────
def _svg(w: float, h: float, body: str, defs: str = "", extra: str = "") -> str:
    d = f"<defs>{defs}</defs>" if defs else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_fmt(w)} {_fmt(h)}"{extra}>'
            f"{d}{body}</svg>\n")


def _grad(gid: str, y0: float, y1: float, stops, x0: float = 0, x1: float = 0) -> str:
    st = "".join(f'<stop offset="{o}" stop-color="{c}"/>' for o, c in stops)
    return (f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" x1="{_fmt(x0)}" y1="{_fmt(y0)}" '
            f'x2="{_fmt(x1)}" y2="{_fmt(y1)}">{st}</linearGradient>')


def mark_parts(mark: dict, theme: str, accent: str, uid: str):
    """(defs, svg) de la marca en su espacio de 512. theme: dark | light | mono | white."""
    x0, y0, x1, y1 = mark["bounds"]
    if theme == "mono":
        return "", f'<path d="{mark["both"]}" fill="currentColor"/>'
    if theme == "white":
        return "", f'<path d="{mark["both"]}" fill="#FFFFFF"/>'
    light, base, deep = ACCENTS[accent]
    defs = _grad(f"{uid}k", y0, y1, [(0, light), (0.55, base), (1, deep)])
    if theme == "dark":
        defs += _grad(f"{uid}w", y0, y1, [(0, IVORY_TOP), (1, IVORY_BOTTOM)])
        wing = f"url(#{uid}w)"
    else:
        wing = NAVY
    return defs, (f'<path d="{mark["wings"]}" fill="{wing}"/>'
                  f'<path d="{mark["knot"]}" fill="url(#{uid}k)"/>')


def place(mark: dict, inner: str, cx: float, cy: float, width: float) -> str:
    x0, _, x1, _ = mark["bounds"]
    s = width / (x1 - x0)
    return f'<g transform="translate({_fmt(cx)} {_fmt(cy)}) scale({s:.5f}) translate(-256 -256)">{inner}</g>'


def mark_svg(mark: dict, theme: str = "dark", accent: str = DEFAULT_ACCENT, pad: float = 0.0) -> str:
    """La marca sola (fondo transparente), recortada a su caja + `pad` unidades por lado."""
    defs, inner = mark_parts(mark, theme, accent, "jv")
    x0, y0, x1, y1 = mark["bounds"]
    vb = f'viewBox="{_fmt(x0 - pad)} {_fmt(y0 - pad)} {_fmt(x1 - x0 + 2 * pad)} {_fmt(y1 - y0 + 2 * pad)}"'
    d = f"<defs>{defs}</defs>" if defs else ""
    return f'<svg xmlns="http://www.w3.org/2000/svg" {vb}>{d}{inner}</svg>\n'


def flat_svg(mark: dict, size: int, width: float, color: str | None = None, part: str = "both",
             fills: tuple[str, str] | None = None) -> str:
    """La marca plana (sin degradés) centrada en un lienzo size×size, de fondo transparente.
    part: both | wings | knot. color=None usa `fills` = (alas, nudo); si no, un solo color."""
    path = {"both": mark["both"], "wings": mark["wings"], "knot": mark["knot"]}
    inner = ""
    if part in ("both", "wings"):
        inner += f'<path d="{mark["wings"]}" fill="{color or (fills or ("#FFFFFF",))[0]}"/>'
    if part in ("both", "knot"):
        inner += f'<path d="{mark["knot"]}" fill="{color or (fills or ("", "#FFC247"))[1]}"/>'
    return _svg(size, size, place(mark, inner, size / 2, size / 2, width))


def tile_svg(mark: dict, size: int = 1024, shape: str = "squircle", theme: str = "dark",
             accent: str = DEFAULT_ACCENT, ratio: float = 0.68, glow: bool = True, rim: bool = True,
             uid: str = "jv", inset: float = 0.0, svg_attrs: str = "") -> str:
    """Ícono de app. shape: squircle (esquinas transparentes) | full (cuadrado a sangre) |
    macos (squircle con margen del 9.8 %, como pide la plantilla de Apple)."""
    S = float(size)
    if shape == "macos":
        inset = S * 100 / 1024
    bx, by, bw = inset, inset, S - 2 * inset
    body = (squircle_d(bx, by, bw, bw) if shape in ("squircle", "macos")
            else f"M0 0H{_fmt(S)}V{_fmt(S)}H0Z")
    cx = cy = S / 2
    light = theme == "light"
    top, bot = (PAPER_TOP, PAPER_BOTTOM) if light else (INK_TOP, INK_BOTTOM)
    defs = _grad(f"{uid}b", by, by + bw, [(0, top), (1, bot)])
    mdefs, minner = mark_parts(mark, "light" if light else "dark", accent, uid)
    defs += mdefs
    out = f'<path d="{body}" fill="url(#{uid}b)"/>'
    if glow and not light:
        gc = GLOW[accent]
        defs += (f'<radialGradient id="{uid}g" cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="{_fmt(bw * 0.38)}" '
                 f'gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="{gc}" stop-opacity="{GLOW_OPACITY}"/>'
                 f'<stop offset="1" stop-color="{gc}" stop-opacity="0"/></radialGradient>')
        out += f'<circle cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="{_fmt(bw * 0.38)}" fill="url(#{uid}g)"/>'
    if rim:
        edge = "#0E1730" if light else "#FFFFFF"
        defs += f'<clipPath id="{uid}c"><path d="{body}"/></clipPath>'
        out += (f'<path d="{body}" fill="none" stroke="{edge}" stroke-opacity="{.10 if light else .12}" '
                f'stroke-width="{_fmt(bw * 0.0055)}" clip-path="url(#{uid}c)"/>')
    out += place(mark, minner, cx, cy, bw * ratio)
    return _svg(S, S, out, defs, svg_attrs)


def lockup_svg(mark: dict, wm: Wordmark, theme: str = "dark", accent: str = DEFAULT_ACCENT,
               workspace: bool = False, stacked: bool = False, pad: float = 0.14) -> str:
    """Marca + 'Jarvis' (+ 'Workspace'). Todo en curvas. theme: dark | light | mono."""
    wmark_dark = theme == "dark"
    c_main = {"dark": "#FFFFFF", "light": NAVY, "mono": "currentColor"}[theme]
    c_soft = {"dark": TEXT_MUTED_DARK, "light": TEXT_MUTED_LIGHT, "mono": "currentColor"}[theme]
    soft_attr = ' fill-opacity=".62"' if theme == "mono" else ""
    x0, y0, x1, y1 = mark["bounds"]
    mw, mh = x1 - x0, y1 - y0
    defs, minner = mark_parts(mark, "mono" if theme == "mono" else ("dark" if wmark_dark else "light"), accent, "jv")
    jar_d, jb, _ = wm.path("Jarvis", mh * (0.50 if stacked else 0.62), tracking=-0.012)
    jw = jb[2] - jb[0]
    parts = []
    if stacked:
        gap = mh * 0.42
        total_w = max(mw, jw)
        cx = total_w / 2
        parts.append(f'<g transform="translate({_fmt(cx - 256)} {_fmt(-y0)})">{minner}</g>')
        # el texto queda con su tope de tinta a `gap` por debajo de la marca
        parts.append(f'<path transform="translate({_fmt(cx - jw / 2 - jb[0])} {_fmt(mh + gap - jb[1])})" '
                     f'd="{jar_d}" fill="{c_main}"/>')
        W, H = total_w, mh + gap + (jb[3] - jb[1])
    else:
        gap = mw * 0.20
        capc = jb[1] + (jb[3] - jb[1]) / 2        # centro vertical del texto (y hacia abajo, base en 0)
        ty = mh / 2 - capc
        parts.append(f'<g transform="translate({_fmt(-x0)} {_fmt(-y0)})">{minner}</g>')
        tx = mw + gap - jb[0]
        parts.append(f'<path transform="translate({_fmt(tx)} {_fmt(ty)})" d="{jar_d}" fill="{c_main}"/>')
        W = mw + gap + jw
        if workspace:
            ws_d, wb, _ = wm.path("Workspace", mh * 0.62, tracking=0.0)
            sp = mh * 0.62 * 0.30
            wx = mw + gap + jw + sp - wb[0]
            parts.append(f'<path transform="translate({_fmt(wx)} {_fmt(ty)})" d="{ws_d}" fill="{c_soft}"{soft_attr}/>')
            W = mw + gap + jw + sp + (wb[2] - wb[0])
        H = mh
    p = mh * pad
    body = f'<g transform="translate({_fmt(p)} {_fmt(p)})">{"".join(parts)}</g>'
    return _svg(W + 2 * p, H + 2 * p, body, defs)


# ───────────────────────────── raster ─────────────────────────────
class Raster:
    """Chromium headless: rasteriza SVG a PNG exacto (y mide, para los chequeos)."""

    def __enter__(self):
        import os
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        kw = dict(args=["--no-sandbox"])
        try:
            self._b = self._pw.chromium.launch(**kw)
        except Exception:
            exe = os.environ.get("CHROMIUM_PATH") or "/opt/pw-browsers/chromium"
            self._b = self._pw.chromium.launch(executable_path=exe, **kw)
        self._page = self._b.new_page(device_scale_factor=1)
        return self

    def __exit__(self, *a):
        self._b.close()
        self._pw.stop()

    def png(self, svg: str, w: int, h: int | None = None, background: str = "transparent",
            shadow: tuple | None = None) -> bytes:
        h = h or w
        self._page.set_viewport_size({"width": w, "height": h})
        fx = ""
        if shadow:
            dy, blur, alpha = shadow
            fx = f"filter:drop-shadow(0 {dy * w / 1024:.2f}px {blur * w / 1024:.2f}px rgba(0,0,0,{alpha}));"
        self._page.set_content(
            f'<!doctype html><meta charset="utf-8"><style>html,body{{margin:0;background:{background}}}'
            f'svg{{display:block;width:{w}px;height:{h}px;{fx}}}</style>{svg}')
        data = self._page.screenshot(clip={"x": 0, "y": 0, "width": w, "height": h}, omit_background=True)
        try:                                   # recompresión sin pérdida (mismos píxeles, menos bytes)
            from PIL import Image
            buf = io.BytesIO()
            Image.open(io.BytesIO(data)).save(buf, "PNG", optimize=True)
            return buf.getvalue() if len(buf.getvalue()) < len(data) else data
        except Exception:
            return data


# ─────────────────────── contenedores ICO / ICNS ───────────────────
def write_ico(path: Path, frames: list[tuple[int, bytes]]) -> None:
    """ICO con PNGs adentro (Vista+). Cada frame se rasteriza a su tamaño, no se reescala."""
    n = len(frames)
    head = struct.pack("<HHH", 0, 1, n)
    off = 6 + 16 * n
    entries, blobs = b"", b""
    for size, png in frames:
        wh = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", wh, wh, 0, 0, 1, 32, len(png), off + len(blobs))
        blobs += png
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(head + entries + blobs)


ICNS_TYPES = {16: b"icp4", 32: b"icp5", 64: b"icp6", 128: b"ic07", 256: b"ic08", 512: b"ic09", 1024: b"ic10"}
ICNS_RETINA = {32: b"ic11", 64: b"ic12", 256: b"ic13", 512: b"ic14"}   # 16@2x, 32@2x, 128@2x, 256@2x


def write_icns(path: Path, pngs: dict[int, bytes]) -> None:
    chunks = b""
    for size, png in sorted(pngs.items()):
        for table in (ICNS_TYPES, ICNS_RETINA):
            if size in table:
                chunks += table[size] + struct.pack(">I", 8 + len(png)) + png
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"icns" + struct.pack(">I", 8 + len(chunks)) + chunks)


# ───────────────────────────── el kit ─────────────────────────────
def optical(size: int) -> dict:
    """Variante óptica por tamaño: a 16-32 px la marca crece, se saca el brillo y el borde."""
    if size <= 20:
        return dict(ratio=0.86, glow=False, rim=False)
    if size <= 32:
        return dict(ratio=0.80, glow=False, rim=False)
    if size <= 64:
        return dict(ratio=0.72, glow=False, rim=size >= 48)
    if size <= 128:
        return dict(ratio=0.67, glow=True, rim=True)
    return dict(ratio=0.68, glow=True, rim=True)


def build(out: Path, accent: str, svg_only: bool) -> None:
    mark = build_mark()
    wm = Wordmark(ROOT / "fonts" / "Manrope-SemiBold.woff")
    wm_regular = Wordmark(ROOT / "fonts" / "Manrope-Regular.woff")

    def write(rel: str, data) -> Path:
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            p.write_text(data, encoding="utf-8")
        else:
            p.write_bytes(data)
        return p

    # ── vectores de la marca ──
    write("svg/jarvis-mark.svg", mark_svg(mark, "dark", accent, pad=8))
    write("svg/jarvis-mark-onlight.svg", mark_svg(mark, "light", accent, pad=8))
    write("svg/jarvis-mark-mono.svg", mark_svg(mark, "mono", pad=8))
    for theme, suffix in (("dark", ""), ("light", "-onlight"), ("mono", "-mono")):
        write(f"svg/jarvis-lockup{suffix}.svg", lockup_svg(mark, wm, theme, accent))
        write(f"svg/jarvis-workspace-lockup{suffix}.svg", lockup_svg(mark, _Both(wm, wm_regular), theme, accent, workspace=True))
        write(f"svg/jarvis-lockup-stacked{suffix}.svg", lockup_svg(mark, wm, theme, accent, stacked=True))
    write("svg/jarvis-icon.svg", tile_svg(mark, 1024, "squircle", "dark", accent))
    write("svg/jarvis-icon-onlight.svg", tile_svg(mark, 1024, "squircle", "light", accent))
    write("svg/jarvis-icon-fullbleed.svg", tile_svg(mark, 1024, "full", "dark", accent, rim=False))

    # ── Android adaptive (108 dp): fondo / frente / monocromo ──
    bg = _grad("jvb", 0, 108, [(0, INK_TOP), (1, INK_BOTTOM)])
    gc = GLOW[accent]
    bg += (f'<radialGradient id="jvg" cx="54" cy="54" r="40" gradientUnits="userSpaceOnUse"><stop offset="0" '
           f'stop-color="{gc}" stop-opacity="{GLOW_OPACITY}"/><stop offset="1" stop-color="{gc}" stop-opacity="0"/></radialGradient>')
    write("app/android/ic_launcher_background.svg", _svg(108, 108,
          '<rect width="108" height="108" fill="url(#jvb)"/><circle cx="54" cy="54" r="40" fill="url(#jvg)"/>', bg))
    fdefs, finner = mark_parts(mark, "dark", accent, "jv")
    write("app/android/ic_launcher_foreground.svg", _svg(108, 108, place(mark, finner, 54, 54, 52), fdefs))
    _, minner = mark_parts(mark, "white", accent, "jv")
    write("app/android/ic_launcher_monochrome.svg", _svg(108, 108, place(mark, minner, 54, 54, 52).replace("#FFFFFF", "#000000")))

    # ── pajarita en otros colores (solo el ícono) ──
    for alt in ACCENTS:
        if alt != accent:
            write(f"alternativas/jarvis-icon-{alt}.svg", tile_svg(mark, 1024, "squircle", "dark", alt))

    # ── Apple: capas planas para Icon Composer (macOS/iOS 26+) + fondo como referencia ──
    light, base, deep = ACCENTS[accent]
    write("app/apple/icon-composer/wings.svg", flat_svg(mark, 1024, 1024 * 0.66, part="wings", fills=("#FFFFFF", base)))
    write("app/apple/icon-composer/knot.svg", flat_svg(mark, 1024, 1024 * 0.66, part="knot", fills=("#FFFFFF", base)))
    write("app/apple/icon-composer/background.svg", _svg(1024, 1024, '<rect width="1024" height="1024" fill="url(#jvb)"/>',
          _grad("jvb", 0, 1024, [(0, INK_TOP), (1, INK_BOTTOM)])))
    # ── glifo monocromo (solo alfa): PWA `monochrome`, bandeja del sistema ──
    write("app/web/icon-monochrome.svg", flat_svg(mark, 512, 512 * 0.56, color="#000000"))
    write("app/web/favicon.svg", tile_svg(mark, 64, "squircle", "dark", accent, **optical(32)))
    write("app/linux/hicolor/scalable/apps/jarvis-workspace.svg", tile_svg(mark, 1024, "squircle", "dark", accent))

    if svg_only:
        return

    # ── PNG / ICO / ICNS ──
    def any_tile(size):          # esquinas transparentes
        o = optical(size)
        return tile_svg(mark, 1024 if size > 64 else 256, "squircle", "dark", accent, **o)

    with Raster() as r:
        def png_any(size):
            return r.png(any_tile(size), size)

        def png_full(size):
            return r.png(tile_svg(mark, 1024, "full", "dark", accent, ratio=0.66, rim=False), size)

        def png_mac(size):
            return r.png(tile_svg(mark, 1024, "macos", "dark", accent), size, shadow=(10, 14, 0.32))

        def png_apple(size):     # sin brillo horneado: Apple pone el suyo (HIG)
            return r.png(tile_svg(mark, 1024, "full", "dark", accent, ratio=0.66, rim=False, glow=False), size)

        # web / PWA
        write("app/web/apple-touch-icon.png", png_apple(180))
        write("app/web/icon-192.png", png_any(192))
        write("app/web/icon-512.png", png_any(512))
        write("app/web/icon-maskable-512.png", r.png(tile_svg(mark, 1024, "full", "dark", accent, ratio=0.66, rim=False), 512))
        (out / "app/web").mkdir(parents=True, exist_ok=True)
        write_ico(out / "app/web/favicon.ico", [(s, png_any(s)) for s in (16, 32, 48)])
        write("app/web/site.webmanifest", json.dumps({
            "name": "Jarvis Workspace", "short_name": "Jarvis", "start_url": "/", "display": "standalone",
            "background_color": INK_BOTTOM, "theme_color": INK_BOTTOM,
            "icons": [
                {"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
                {"src": "icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
                {"src": "icon-monochrome-512.png", "sizes": "512x512", "type": "image/png", "purpose": "monochrome"},
            ]}, indent=2) + "\n")
        # iOS / iPadOS: 1024 a sangre, sin transparencia (el sistema pone la máscara)
        write("app/ios/icon-1024.png", png_apple(1024))
        # Android: icono de la ficha de Google Play (cuadrado, sin esquinas ni sombra)
        write("app/android/play-store-512.png", png_apple(512))
        # glifo monocromo (negro + alfa) para `purpose: monochrome` y la bandeja (macOS: *Template.png)
        mono_svg = flat_svg(mark, 512, 512 * 0.56, color="#000000")
        write("app/web/icon-monochrome-512.png", r.png(mono_svg, 512))
        tray = flat_svg(mark, 64, 64 * 0.94, color="#000000")
        write("app/tray/jarvisTemplate.png", r.png(tray, 16))
        write("app/tray/jarvisTemplate@2x.png", r.png(tray, 32))
        write("app/tray/jarvis-tray-64.png", r.png(tray, 64))
        # macOS
        mac = {s: png_mac(s) for s in (16, 32, 64, 128, 256, 512, 1024)}
        write("app/macos/icon-1024.png", mac[1024])
        write_icns(out / "app/macos/jarvis.icns", mac)
        # Windows
        write_ico(out / "app/windows/jarvis.ico", [(s, png_any(s)) for s in (16, 24, 32, 48, 64, 128, 256)])
        # Linux (hicolor)
        for s in (16, 22, 24, 32, 48, 64, 128, 256, 512):
            write(f"app/linux/hicolor/{s}x{s}/apps/jarvis-workspace.png", png_any(s))
        # Redes: vista previa de GitHub / OG
        social = _social(mark, wm, accent)
        write("social/social-preview-1280x640.png", r.png(social, 1280, 640))
        write("social/social-preview.svg", social)


class _Both:
    """Adaptador: 'Jarvis' en semibold y 'Workspace' en regular dentro de un mismo lockup."""
    def __init__(self, bold: Wordmark, regular: Wordmark):
        self.bold, self.regular = bold, regular
        self._n = 0

    def path(self, text, cap_height, tracking=0.0):
        return (self.regular if text == "Workspace" else self.bold).path(text, cap_height, tracking)


def _social(mark: dict, wm: Wordmark, accent: str) -> str:
    """1280×640: lockup apilado centrado sobre el degradé oscuro con el brillo del nudo."""
    W, H = 1280, 640
    lock = lockup_svg(mark, wm, "dark", accent, stacked=True, pad=0)
    inner = lock[lock.index(">") + 1: lock.rindex("</svg>")]
    vb = lock.split('viewBox="')[1].split('"')[0].split()
    lw, lh = float(vb[2]), float(vb[3])
    s = 300 / lh
    defs_start = inner.index("<defs>")
    defs_end = inner.index("</defs>") + 7
    defs = inner[defs_start + 6: defs_end - 7]
    body = inner[defs_end:]
    gc = GLOW[accent]
    d = _grad("jvs", 0, H, [(0, INK_TOP), (1, INK_BOTTOM)]) + defs
    d += (f'<radialGradient id="jvq" cx="640" cy="300" r="380" gradientUnits="userSpaceOnUse"><stop offset="0" '
          f'stop-color="{gc}" stop-opacity=".14"/><stop offset="1" stop-color="{gc}" stop-opacity="0"/></radialGradient>')
    out = (f'<rect width="{W}" height="{H}" fill="url(#jvs)"/><rect width="{W}" height="{H}" fill="url(#jvq)"/>'
           f'<g transform="translate({_fmt(W / 2 - lw * s / 2)} {_fmt(H / 2 - lh * s / 2)}) scale({s:.5f})">{body}</g>')
    return _svg(W, H, out, d)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--accent", default=DEFAULT_ACCENT, choices=sorted(ACCENTS))
    ap.add_argument("--out", default=str(ROOT), help="carpeta de salida (por defecto, brand/)")
    ap.add_argument("--svg-only", action="store_true", help="solo vectores (no usa Chromium)")
    a = ap.parse_args(argv)
    build(Path(a.out), a.accent, a.svg_only)
    print(f"kit de marca listo en {a.out} (acento: {a.accent})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
