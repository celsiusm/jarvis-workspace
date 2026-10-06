# plotspace/tests/test_brand_assets.py
"""Kit de marca (brand/): los assets commiteados tienen que ser válidos y cumplir las reglas
de cada plataforma. No prueba el diseño (eso se mira en la hoja de marca): prueba lo que un
build de verdad rompe — un SVG mal formado, un PNG del tamaño equivocado, un ícono de iOS con
alfa, un maskable con la marca fuera de la zona segura, un .ico/.icns corrupto."""
import json
import math
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

BRAND = Path(__file__).resolve().parents[2] / 'brand'
APP = BRAND / 'app'


def _tag(el):
    return el.tag.rsplit('}', 1)[-1]


def test_todos_los_svg_son_xml_valido_con_viewbox():
    svgs = sorted(BRAND.rglob('*.svg'))
    assert len(svgs) >= 20, 'el kit debería traer al menos 20 SVG'
    for f in svgs:
        root = ET.parse(f).getroot()
        assert _tag(root) == 'svg', f
        assert root.get('viewBox'), f'{f.name}: falta viewBox'


def test_svgs_del_kit_son_autocontenidos():
    """Sin texto, sin fuentes, sin imágenes ni recursos externos: el wordmark va en curvas."""
    for f in sorted((BRAND / 'svg').rglob('*.svg')) + sorted(APP.rglob('*.svg')):
        for el in ET.parse(f).getroot().iter():
            assert _tag(el) not in ('text', 'image', 'script', 'foreignObject'), f'{f.name}: <{_tag(el)}>'
            for k, v in el.attrib.items():
                assert k.rsplit('}', 1)[-1] != 'href', f'{f.name}: href externo'
                assert 'font' not in k, f'{f.name}: atributo de fuente {k}'
                assert 'http' not in v, f'{f.name}: URL en {k}'


def test_marca_mono_usa_currentcolor():
    s = (BRAND / 'svg' / 'jarvis-mark-mono.svg').read_text()
    assert 'currentColor' in s and '#' not in s


def test_android_adaptive_son_108dp():
    for n in ('background', 'foreground', 'monochrome'):
        root = ET.parse(APP / 'android' / f'ic_launcher_{n}.svg').getroot()
        assert root.get('viewBox') == '0 0 108 108', n


def test_manifest_coincide_con_los_archivos():
    PIL = pytest.importorskip('PIL.Image')
    m = json.loads((APP / 'web' / 'site.webmanifest').read_text())
    purposes = set()
    for ic in m['icons']:
        f = APP / 'web' / ic['src']
        assert f.exists(), ic['src']
        w, h = PIL.open(f).size
        assert ic['sizes'] == f'{w}x{h}', ic['src']
        purposes.add(ic['purpose'])
    assert {'any', 'maskable', 'monochrome'} <= purposes


@pytest.mark.parametrize('rel,size', [
    ('web/icon-192.png', 192), ('web/icon-512.png', 512), ('web/icon-maskable-512.png', 512),
    ('web/icon-monochrome-512.png', 512), ('web/apple-touch-icon.png', 180), ('ios/icon-1024.png', 1024),
    ('macos/icon-1024.png', 1024), ('android/play-store-512.png', 512), ('tray/jarvisTemplate.png', 16),
    ('tray/jarvisTemplate@2x.png', 32),
] + [(f'linux/hicolor/{s}x{s}/apps/jarvis-workspace.png', s) for s in (16, 22, 24, 32, 48, 64, 128, 256, 512)])
def test_png_con_el_tamano_exacto(rel, size):
    Image = pytest.importorskip('PIL.Image')
    assert Image.open(APP / rel).size == (size, size)


@pytest.mark.parametrize('rel', ['ios/icon-1024.png', 'web/apple-touch-icon.png', 'android/play-store-512.png',
                                 'web/icon-maskable-512.png'])
def test_iconos_a_sangre_son_opacos(rel):
    """Apple y Play piden cuadrado a sangre, sin alfa: el sistema pone la máscara."""
    Image = pytest.importorskip('PIL.Image')
    im = Image.open(APP / rel).convert('RGBA')
    assert im.getchannel('A').getextrema() == (255, 255), rel


def test_macos_clasico_lleva_margen_y_cuerpo_de_824():
    Image = pytest.importorskip('PIL.Image')
    im = Image.open(APP / 'macos' / 'icon-1024.png').convert('RGBA')
    assert im.getpixel((2, 2))[3] == 0 and im.getpixel((512, 512))[3] == 255
    opaco = im.getchannel('A').point(lambda a: 255 if a == 255 else 0).getbbox()
    ancho = opaco[2] - opaco[0]
    assert 816 <= ancho <= 832, f'cuerpo de {ancho}px (la plantilla de Apple pide 824)'


def test_maskable_deja_la_marca_dentro_del_80_por_ciento():
    """W3C: zona segura = círculo de radio 40 % del lado. Se miden los píxeles casi blancos (alas)
    y los dorados (nudo); el degradé y el brillo del fondo son más oscuros que cualquiera de los dos."""
    Image = pytest.importorskip('PIL.Image')
    im = Image.open(APP / 'web' / 'icon-maskable-512.png').convert('RGB')
    w = im.width
    lejos = 0.0
    for y in range(w):
        for x in range(w):
            r, g, b = im.getpixel((x, y))
            if (min(r, g, b) > 200) or (r > 200 and g > 140 and b < 140):
                lejos = max(lejos, math.hypot(x + .5 - w / 2, y + .5 - w / 2))
    assert 0 < lejos <= 0.40 * w + 1, f'la marca llega a {lejos:.1f}px del centro (máximo {0.40 * w:.1f})'


def _ico_frames(path):
    data = path.read_bytes()
    reserved, kind, n = struct.unpack('<HHH', data[:6])
    assert (reserved, kind) == (0, 1)
    sizes = []
    for i in range(n):
        w, _h, _c, _r, _p, _bpp, length, off = struct.unpack('<BBBBHHII', data[6 + 16 * i:22 + 16 * i])
        assert data[off:off + 8] == b'\x89PNG\r\n\x1a\n', 'cada frame del .ico es un PNG'
        assert off + length <= len(data)
        sizes.append(w or 256)
    return sizes


def test_ico_de_favicon_y_de_windows():
    assert _ico_frames(APP / 'web' / 'favicon.ico') == [16, 32, 48]
    assert _ico_frames(APP / 'windows' / 'jarvis.ico') == [16, 24, 32, 48, 64, 128, 256]


def test_icns_bien_formado():
    data = (APP / 'macos' / 'jarvis.icns').read_bytes()
    assert data[:4] == b'icns' and struct.unpack('>I', data[4:8])[0] == len(data)
    i, tipos = 8, []
    while i < len(data):
        tipo, largo = data[i:i + 4], struct.unpack('>I', data[i + 4:i + 8])[0]
        assert data[i + 8:i + 16] == b'\x89PNG\r\n\x1a\n', tipo
        tipos.append(tipo)
        i += largo
    assert i == len(data)
    assert {b'icp4', b'icp5', b'ic07', b'ic08', b'ic09', b'ic10'} <= set(tipos)
