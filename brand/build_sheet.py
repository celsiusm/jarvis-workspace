#!/usr/bin/env python3
"""Hoja de marca de Jarvis Workspace: una sola imagen con toda la identidad.

    python3 brand/build_sheet.py        # escribe brand/jarvis-brand-sheet.png

Usa los mismos generadores de build_brand.py (y los PNG ya construidos), así que corré primero
`python3 brand/build_brand.py`. Los textos de la hoja están en español.
"""
from __future__ import annotations

import base64
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_brand as bb  # noqa: E402

ROOT = Path(__file__).resolve().parent


def svg_uri(svg: str) -> str:
    return "data:image/svg+xml;utf8," + urllib.parse.quote(svg)


def png_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def gates_html(app: Path) -> str:
    """Pruebas de legibilidad: 16/24/32 px en color, grises y 1 bit (umbral 50 %), ampliadas ×6."""
    import io

    from PIL import Image
    sizes = (16, 24, 32)
    table = {"Color": [], "Escala de grises": [], "1 bit": []}
    for size in sizes:
        im = Image.open(app / "linux" / "hicolor" / f"{size}x{size}" / "apps" / "jarvis-workspace.png").convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        gray = bg.convert("L")
        for name, v in zip(table, (bg.convert("RGB"), gray.convert("RGB"),
                                   gray.point(lambda x: 255 if x > 127 else 0).convert("RGB"))):
            buf = io.BytesIO()
            v.save(buf, "PNG")
            uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
            table[name].append(f'<img src="{uri}" style="width:{size * 6}px;height:{size * 6}px">')
    head = '<div></div>' + "".join(f'<div class="gl">{z} px</div>' for z in sizes)
    rows = "".join(f'<div class="gn">{n}</div>' + "".join(f'<div class="gc">{c}</div>' for c in cells)
                   for n, cells in table.items())
    return f'<div class="gates">{head}{rows}</div>'


def construction_svg(mark: dict) -> str:
    """La marca con su caja y la zona de respeto (x = ancho del nudo) acotadas."""
    x0, y0, x1, y1 = mark["bounds"]
    x = bb.GEOM["kw"]
    pad = x + 36
    vx, vy, vw, vh = x0 - pad, y0 - pad, (x1 - x0) + 2 * pad, (y1 - y0) + 2 * pad
    defs, inner = bb.mark_parts(mark, "dark", bb.DEFAULT_ACCENT, "cn")
    guide = "#5E6B8A"
    cx = 256
    knot_w = bb.GEOM["kw"]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vx:.1f} {vy:.1f} {vw:.1f} {vh:.1f}">'
        f"<defs>{defs}</defs>"
        f'<rect x="{x0 - x:.1f}" y="{y0 - x:.1f}" width="{x1 - x0 + 2 * x:.1f}" height="{y1 - y0 + 2 * x:.1f}" '
        f'fill="none" stroke="{guide}" stroke-width="1.6" stroke-dasharray="6 6"/>'
        f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{x1 - x0:.1f}" height="{y1 - y0:.1f}" fill="none" stroke="{guide}" stroke-width="1.6"/>'
        f"{inner}"
        # cuadritos "x" en las cuatro puntas de la zona de respeto
        + "".join(
            f'<rect x="{px:.1f}" y="{py:.1f}" width="{x}" height="{x}" fill="{guide}" fill-opacity=".30" stroke="{guide}" stroke-width="1.4"/>'
            f'<text x="{px + x / 2:.1f}" y="{py + x / 2 + 7:.1f}" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" font-size="24" fill="#B7C2DB">x</text>'
            for px, py in ((x0 - x, y0 - x), (x1, y0 - x), (x0 - x, y1), (x1, y1))
        )
        + f'<rect x="{cx - knot_w / 2:.1f}" y="{y0 + (y1 - y0) / 2 + 80:.1f}" width="{knot_w}" height="0" />'
        + "</svg>"
    )


def build(out_png: Path) -> None:
    mark = bb.build_mark()
    wm = bb.Wordmark(ROOT / "fonts" / "Manrope-SemiBold.woff")
    wm_reg = bb.Wordmark(ROOT / "fonts" / "Manrope-Regular.woff")
    both = bb._Both(wm, wm_reg)

    acc = bb.DEFAULT_ACCENT
    app = ROOT / "app"
    hero = png_uri(app / "macos" / "icon-1024.png")
    full = png_uri(app / "ios" / "icon-1024.png")
    sq = lambda s, th="dark", a=acc, **k: svg_uri(bb.tile_svg(mark, 1024, "squircle", th, a, **k))  # noqa: E731
    mk = lambda th: svg_uri(bb.mark_svg(mark, th, acc, pad=6))  # noqa: E731
    lock = lambda th, **k: svg_uri(bb.lockup_svg(mark, wm if not k.get("workspace") else both, th, acc, **k))  # noqa: E731
    bg_android = svg_uri((app / "android" / "ic_launcher_background.svg").read_text())
    fg_android = svg_uri((app / "android" / "ic_launcher_foreground.svg").read_text())
    ladder = {s: png_uri(app / "linux" / "hicolor" / f"{s}x{s}" / "apps" / "jarvis-workspace.png")
              for s in (16, 24, 32, 48, 64, 128)}
    maskable = png_uri(app / "web" / "icon-maskable-512.png")
    construction = svg_uri(construction_svg(mark))
    f = ROOT / "fonts"

    def fontface(name, w):
        return (f'@font-face{{font-family:"Manrope";font-weight:{w};src:url("file://{f / name}") format("woff")}}')

    def swatch(name, hexes, grad=None, dark_text=False):
        bgc = grad or hexes[0]
        tc = "#0E1730" if dark_text else "#F4F7FB"
        chips = " · ".join(hexes)
        return (f'<div class="sw"><div class="chip" style="background:{bgc};color:{tc}"><b>{name}</b></div>'
                f'<div class="hex">{chips}</div></div>')

    knot = bb.ACCENTS[acc]
    alts = "".join(
        f'<div class="alt"><img src="{sq(0, a=a)}"><span>{a}</span></div>' for a in bb.ACCENTS if a != acc)

    donts = [
        ("Sin estirar", "transform:scaleX(1.55)", ""),
        ("Sin girar", "transform:rotate(-14deg)", ""),
        ("Sin cambiar colores", "filter:hue-rotate(150deg) saturate(1.6)", ""),
        ("Sin fondos que compitan", "", "background:repeating-linear-gradient(45deg,#E0457B 0 14px,#2E7BE0 14px 28px)"),
        ("Sin sombras ni brillos", "filter:drop-shadow(0 7px 5px #000) drop-shadow(0 0 14px #FFF176)", ""),
        ("Sin recortar", "transform:translateX(74px)", ""),
    ]
    dont_html = "".join(
        f'<div class="dont"><div class="dbox" style="{bgs}"><img src="{mk("dark")}" style="{tf}"></div><div class="dl"><i>✕</i> {n}</div></div>'
        for n, tf, bgs in donts)

    html = f"""<!doctype html><html><head><meta charset="utf-8"><style>
{fontface("Manrope-Regular.woff", 400)}
{fontface("Manrope-SemiBold.woff", 600)}
{fontface("Manrope-SemiBold.woff", 700)}
:root{{--bg:#0A0E18;--panel:#101626;--line:#1E2742;--mut:#8E9BB8;--tx:#E8EDF7}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--tx);font-family:Manrope,Arial,sans-serif;width:1600px}}
.wrap{{padding:56px 64px 64px}}
.top{{display:flex;align-items:center;gap:18px;margin-bottom:34px}}
.top img{{height:44px}}
h1{{font-size:30px;margin:0;font-weight:700;letter-spacing:-.01em}}
.sub{{color:var(--mut);font-size:15px;margin-top:3px}}
.sec{{margin-top:52px}}
.lab{{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--mut);margin-bottom:16px;font-weight:600}}
.row{{display:grid;gap:20px}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:22px;padding:26px;position:relative}}
.card.light{{background:#F2F5FA;border-color:#DCE3EF;color:#0E1730}}
.hero{{grid-template-columns:600px 1fr;gap:34px;align-items:stretch}}
.heroimg{{background:radial-gradient(circle at 50% 45%,#1B2644 0,#0F1526 70%);border-radius:28px;display:flex;align-items:center;justify-content:center;border:1px solid var(--line);padding:6px}}
.heroimg img{{width:560px;height:560px;display:block}}
h2{{font-size:34px;line-height:1.18;margin:0 0 14px;font-weight:700;letter-spacing:-.015em}}
h2 em{{font-style:normal;color:#FFC247}}
p{{margin:0 0 14px;color:#B9C4DC;font-size:16px;line-height:1.55}}
.three{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:6px 0 24px}}
.three div{{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:16px;font-size:14px;color:#B9C4DC;line-height:1.5}}
.three b{{display:block;color:#fff;font-size:15px;margin-bottom:4px}}
.pal{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}}
.sw .chip{{height:84px;border-radius:16px;padding:12px 14px;font-size:14px;display:flex;align-items:flex-end;border:1px solid rgba(255,255,255,.08)}}
.sw .hex{{font-size:12px;color:var(--mut);margin-top:8px;line-height:1.5;font-family:ui-monospace,Menlo,monospace}}
.lock img{{height:92px;display:block;margin:0 auto}}
.lock.tall img{{height:150px}}
.lock{{display:flex;align-items:center;justify-content:center;min-height:160px;padding:20px}}
.cap{{font-size:12px;color:var(--mut);margin-top:12px;text-align:center}}
.light .cap{{color:#5A6A8C}}
.ctx{{grid-template-columns:repeat(4,1fr)}}
.ctx .card{{display:flex;flex-direction:column;align-items:center;gap:14px;padding:22px}}
.tag{{font-size:13px;color:var(--mut)}}
.masks{{display:flex;gap:14px;align-items:center}}
.am{{width:104px;height:104px;position:relative;overflow:hidden;background:#000}}
.am img{{position:absolute;inset:0;width:100%;height:100%}}
.am.c{{border-radius:50%}}.am.s{{border-radius:34%}}.am.r{{border-radius:16%}}
.ios{{display:flex;gap:16px}}
.ios img{{width:104px;height:104px;border-radius:22.37%;display:block;box-shadow:0 6px 16px rgba(0,0,0,.4)}}
.ios .ph{{width:84px;height:84px;border-radius:22.37%;background:#1A2036;border:1px solid #2A3452}}
.tab{{background:#1A2036;border:1px solid #2A3452;border-radius:12px 12px 0 0;padding:9px 16px 9px 12px;display:flex;align-items:center;gap:9px;font-size:13px;color:#DDE4F2;font-family:Arial,sans-serif}}
.tab img{{width:16px;height:16px}}
.tabbar{{width:100%;background:#0E1322;border-radius:12px;padding:10px 10px 0;display:flex;align-items:flex-end;height:60px}}
.mskwrap{{position:relative;width:150px;height:150px}}
.mskwrap img{{width:150px;height:150px;border-radius:50%;display:block}}
.safe{{position:absolute;inset:15px;border:1.5px dashed #FF6B6B;border-radius:50%}}
.ladder{{display:flex;gap:26px;align-items:flex-end;justify-content:center;padding:6px 0}}
.ladder div{{display:flex;flex-direction:column;align-items:center;gap:8px;font-size:11px;color:var(--mut)}}
.light .ladder div{{color:#5A6A8C}}
.var{{grid-template-columns:repeat(4,1fr)}}
.var .card{{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;min-height:190px}}
.var img{{height:78px;display:block}}
.mono{{color:#fff}}
.alt{{display:flex;flex-direction:column;align-items:center;gap:8px;font-size:12px;color:var(--mut)}}
.alt img{{width:104px;height:104px;display:block}}
.alts{{display:flex;gap:22px;align-items:center;justify-content:center}}
.rules{{grid-template-columns:1.3fr 1fr}}
.cons img{{width:100%;display:block}}
.dont{{display:flex;flex-direction:column;gap:10px}}
.dbox{{height:118px;border-radius:14px;background:#0E1424;border:1px solid var(--line);display:flex;align-items:center;justify-content:center;overflow:hidden}}
.dbox img{{height:62px}}
.dl{{font-size:13px;color:#C7D0E6}}.dl i{{color:#FF6B6B;font-style:normal;margin-right:6px}}
.donts{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}}
.min{{display:flex;gap:30px;align-items:center;justify-content:center;font-size:12px;color:var(--mut)}}
.min div{{display:flex;flex-direction:column;align-items:center;gap:8px}}
.gates{{display:grid;grid-template-columns:150px repeat(3,230px);gap:14px 18px;justify-content:center;align-items:center}}
.gn{{font-size:12px;color:var(--mut)}}
.gc{{height:204px;display:flex;align-items:center;justify-content:center}}
.gc img{{display:block;image-rendering:pixelated;border-radius:2px}}
.gl{{font-size:12px;color:var(--mut);text-align:center}}
.foot{{margin-top:56px;color:#5F6C8A;font-size:12px;border-top:1px solid var(--line);padding-top:18px;display:flex;justify-content:space-between}}
</style></head><body><div class="wrap">

<div class="top"><img src="{mk('dark')}"><div><h1>Jarvis Workspace</h1><div class="sub">Identidad visual · la pajarita</div></div></div>

<div class="row hero">
  <div class="heroimg"><img src="{hero}"></div>
  <div>
    <h2>Jarvis es el mayordomo.<br><em>La pajarita lo dice sola.</em></h2>
    <p>Una sola forma, simétrica y a prueba de tamaño: se reconoce a 16 px en una pestaña y a 1024 px en el Dock. Sin letra, sin destellos, sin orbes: un símbolo propio.</p>
    <div class="three">
      <div><b>Las alas</b>Tus agentes, a cada lado, trabajando a la vez.</div>
      <div><b>El nudo</b>Quien los ata: el punto de coordinación.</div>
      <div><b>El cursor</b>El nudo es un cursor de bloque: se lee «terminal».</div>
    </div>
    <div class="lab">Paleta</div>
    <div class="pal">
      {swatch("Tinta", [bb.INK_TOP, bb.INK_BOTTOM], f"linear-gradient(180deg,{bb.INK_TOP},{bb.INK_BOTTOM})")}
      {swatch("Marfil", [bb.IVORY_TOP, bb.IVORY_BOTTOM], f"linear-gradient(180deg,{bb.IVORY_TOP},{bb.IVORY_BOTTOM})", True)}
      {swatch("Latón", [knot[1], knot[0], knot[2]], f"linear-gradient(180deg,{knot[0]},{knot[1]} 55%,{knot[2]})", True)}
      {swatch("Azul noche", [bb.NAVY], bb.NAVY)}
    </div>
  </div>
</div>

<div class="sec"><div class="lab">Logotipo · Manrope SemiBold en curvas</div>
<div class="row" style="grid-template-columns:1.25fr 1fr 0.8fr">
  <div class="card lock"><img src="{lock('dark')}"></div>
  <div class="card light lock"><img src="{lock('light')}"></div>
  <div class="card lock tall"><img src="{lock('dark', stacked=True)}"></div>
</div>
<div class="row" style="grid-template-columns:1.6fr 1fr;margin-top:20px">
  <div class="card lock"><img src="{lock('dark', workspace=True)}" style="height:78px"></div>
  <div class="card light lock"><img src="{lock('light', workspace=True)}" style="height:78px"></div>
</div></div>

<div class="sec"><div class="lab">Como ícono de app</div>
<div class="row ctx">
  <div class="card"><div class="ios"><img src="{full}" style="border-radius:0;box-shadow:none"><img src="{full}"></div><div class="tag">iOS / iPadOS · 1024 a sangre (izq.) y con la máscara del sistema (der.)</div></div>
  <div class="card"><div class="masks">
      <div class="am c"><img src="{bg_android}"><img src="{fg_android}"></div>
      <div class="am s"><img src="{bg_android}"><img src="{fg_android}"></div>
      <div class="am r"><img src="{bg_android}"><img src="{fg_android}"></div></div>
      <div class="tag">Android adaptive · fondo + frente + monocromo</div></div>
  <div class="card"><div class="mskwrap"><img src="{maskable}"><div class="safe"></div></div><div class="tag">PWA maskable · la marca vive dentro del 80 %</div></div>
  <div class="card"><div class="tabbar"><div class="tab"><img src="{ladder[16]}">Jarvis Workspace</div></div>
      <div class="ladder" style="margin-top:6px"><div><img src="{ladder[32]}" width="32"><span>32</span></div><div><img src="{ladder[48]}" width="48"><span>48</span></div></div>
      <div class="tag">Favicon · variantes ópticas por tamaño</div></div>
</div></div>

<div class="sec"><div class="lab">Variantes</div>
<div class="row var">
  <div class="card"><img src="{mk('dark')}"><div class="tag">Sobre oscuro</div></div>
  <div class="card light"><img src="{mk('light')}"><div class="cap">Sobre claro</div></div>
  <div class="card"><div class="mono"><img src="{svg_uri(bb.mark_svg(mark, 'white', acc, pad=6))}"></div><div class="tag">Monocromo · currentColor</div></div>
  <div class="card"><div class="alts">{alts}</div><div class="tag">Otros acentos (mismo ícono)</div></div>
</div></div>

<div class="sec"><div class="lab">Escala real</div>
<div class="row" style="grid-template-columns:1fr 1fr">
  <div class="card"><div class="ladder">{"".join(f'<div><img src="{ladder[s]}" width="{s}"><span>{s}</span></div>' for s in (16,24,32,48,64,128))}</div></div>
  <div class="card light"><div class="ladder">{"".join(f'<div><img src="{ladder[s]}" width="{s}"><span>{s}</span></div>' for s in (16,24,32,48,64,128))}</div></div>
</div></div>

<div class="sec"><div class="lab">Pruebas de legibilidad · a 16, 24 y 32 px</div>
<div class="card" style="padding:26px 40px">{gates_html(app)}<div class="cap" style="margin-top:14px">La pajarita se lee igual en color, en grises y en blanco/negro puro: no depende del acento.</div></div></div>

<div class="sec"><div class="lab">Reglas</div>
<div class="row rules">
  <div class="card cons"><img src="{construction}"><div class="cap" style="margin-top:6px">Zona de respeto = x (el ancho del nudo) por lado · mínimo: 16 px (ícono) · 24 px de ancho (marca sola)</div></div>
  <div class="card"><div class="donts" style="grid-template-columns:repeat(2,1fr)">{dont_html}</div>
    <div class="lab" style="margin:26px 0 14px">Tamaño mínimo</div>
    <div class="min"><div><img src="{ladder[16]}" width="16"><span>Ícono · 16 px</span></div>
      <div><img src="{mk('dark')}" width="24"><span>Marca sola · 24 px</span></div>
      <div><img src="{lock('dark')}" width="96"><span>Logotipo · 96 px</span></div></div></div>
</div></div>

<div class="foot"><span>Generado con brand/build_brand.py · fuente Manrope (SIL OFL) · sin referencias a marcas de terceros</span><span>v1</span></div>
</div></body></html>"""

    tmp = ROOT / ".sheet.html"
    tmp.write_text(html, encoding="utf-8")
    try:
        from playwright.sync_api import sync_playwright
        import os
        with sync_playwright() as pw:
            kw = dict(args=["--no-sandbox", "--allow-file-access-from-files"])
            try:
                b = pw.chromium.launch(**kw)
            except Exception:
                b = pw.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or "/opt/pw-browsers/chromium", **kw)
            pg = b.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=1.5)
            pg.goto(f"file://{tmp}")
            pg.wait_for_timeout(500)
            pg.screenshot(path=str(out_png), full_page=True)
            b.close()
    finally:
        tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    out = ROOT / "jarvis-brand-sheet.png"
    build(out)
    print(f"hoja de marca: {out}")
