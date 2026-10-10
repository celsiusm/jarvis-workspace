"""La imagen de Docker: lo que un build/arranque real rompería y CI no ve sin
construirla (el build pesado corre en .github/workflows/docker.yml)."""
import os
import re
import xml.etree.ElementTree as ET

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _leer(*p):
    with open(os.path.join(RAIZ, *p), encoding='utf-8') as f:
        return f.read()


def _instrucciones(dockerfile):
    """Líneas de instrucciones (sin comentarios), con continuaciones unidas."""
    sin_coment = '\n'.join(l for l in dockerfile.splitlines() if not l.lstrip().startswith('#'))
    return sin_coment.replace('\\\n', ' ')


def test_sin_torch():
    # Nada importa torch (el dictado usa onnx-asr / faster-whisper / Groq) y eran varios GB.
    ins = _instrucciones(_leer('Dockerfile'))
    assert 'torch' not in ins
    for raiz, _dirs, archivos in os.walk(os.path.join(RAIZ, 'plotspace')):
        for a in archivos:
            if a.endswith('.py') and 'tests' not in raiz:
                src = _leer(raiz, a)
                assert not re.search(r'^\s*(import torch|from torch)', src, re.M), os.path.join(raiz, a)


def test_no_root_healthcheck_y_entrypoint():
    ins = _instrucciones(_leer('Dockerfile'))
    assert 'HEALTHCHECK' in ins and '/api/health' in ins
    assert 'ENTRYPOINT ["jarvis-entrypoint"]' in ins
    assert 'useradd' in ins and 'HOME=/home/jarvis' in ins
    assert 'git config --system' in ins          # vale para cualquier UID
    ep = _leer('docker', 'entrypoint.sh')
    assert 'setpriv' in ep and 'PUID' in ep and 'PGID' in ep


def test_compose_usa_la_imagen_publicada_y_el_home_nuevo():
    c = _leer('docker-compose.yml')
    assert 'image: ghcr.io/celsiusm/jarvis-workspace' in c
    assert 'jarvis_home:/home/jarvis' in c
    assert 'PUID' in c and 'PGID' in c


def test_plantilla_unraid_valida():
    raiz = ET.parse(os.path.join(RAIZ, 'packaging', 'unraid', 'jarvis-workspace.xml')).getroot()
    assert raiz.findtext('Repository').startswith('ghcr.io/celsiusm/jarvis-workspace')
    destinos = {c.get('Target'): c.get('Default') for c in raiz.findall('Config')}
    assert destinos['PUID'] == '99' and destinos['PGID'] == '100'
    for ruta in ('/app/data', '/home/jarvis', '/proyectos', '3000'):
        assert ruta in destinos, ruta
