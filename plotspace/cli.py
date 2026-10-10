"""El comando `jarvis`: levanta el motor y abre la app.

    jarvis                  # levanta y abre el browser
    jarvis --puerto 3100    # otro puerto
    jarvis --sin-browser    # para correrlo en un servidor
"""
import argparse
import os
import sys
import threading
import time

from plotspace.core.consola import asegurar_salida_estandar

asegurar_salida_estandar()   # ver core/consola.py
import webbrowser


def _raiz() -> str:
    """La carpeta que contiene `plotspace/` y `frontend/`."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _abrir_cuando_responda(url: str, espera: float = 30.0):
    """Abre el browser recién cuando el motor contesta.

    Abrirlo antes muestra una pantalla de error y hace creer que la app está
    rota — el arranque en frío tarda unos segundos (DB, reconcile, pollers).
    """
    import urllib.error
    import urllib.request
    limite = time.time() + espera
    while time.time() < limite:
        try:
            urllib.request.urlopen(url + '/api/health', timeout=1)
            webbrowser.open(url)
            return
        except (urllib.error.URLError, OSError):
            time.sleep(0.4)
    print(f'[jarvis] el motor no respondió en {espera:.0f}s — abrilo a mano: {url}')


def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='jarvis',
        description='Jarvis Workspace — tu flota de agentes de código, en una app.')
    p.add_argument('--puerto', type=int, default=int(os.environ.get('JARVIS_PORT', 3000)))
    # 127.0.0.1 y NO 0.0.0.0: el default no puede exponer a toda la red local
    # una app que ejecuta comandos arbitrarios. Quien quiera entrar desde el
    # celular lo pide explícito.
    p.add_argument('--host', default=os.environ.get('JARVIS_HOST', '127.0.0.1'),
                   help='127.0.0.1 por defecto. Poné 0.0.0.0 para entrar desde la LAN.')
    p.add_argument('--sin-browser', action='store_true',
                   help='no abrir el navegador (para correrlo en un servidor)')
    p.add_argument('--datos', default=os.environ.get('JARVIS_DATA_DIR', ''),
                   help='dónde guardar la DB y el estado local')
    return p


def main(argv=None) -> int:
    args = construir_parser().parse_args(argv)

    if args.datos:
        os.environ['JARVIS_DATA_DIR'] = os.path.abspath(os.path.expanduser(args.datos))
    # El re-exec de "Update now" lee estos dos para volver con el MISMO host y
    # puerto (system._host_puerto_actuales): sin esto volvía en 0.0.0.0:3000.
    # (JARVIS_BIND_HOST y no JARVIS_HOST: ese lo lee el hook de los agentes para
    # saber a qué dirección CONECTARSE, y 0.0.0.0 no es una dirección de destino.)
    os.environ['JARVIS_PORT'] = str(args.puerto)
    os.environ['JARVIS_BIND_HOST'] = args.host

    raiz = _raiz()
    if raiz not in sys.path:
        sys.path.insert(0, raiz)
    os.chdir(raiz)

    if args.host not in ('127.0.0.1', 'localhost', '::1'):
        print(f'[jarvis] escuchando en {args.host}: la app queda accesible '
              'desde la red. Asegurate de que sea lo que querés.')

    url = f'http://{"127.0.0.1" if args.host == "0.0.0.0" else args.host}:{args.puerto}'
    if not args.sin_browser:
        threading.Thread(target=_abrir_cuando_responda, args=(url,),
                         daemon=True, name='jarvis-browser').start()

    import uvicorn
    print(f'[jarvis] {url}')
    uvicorn.run(
        'plotspace.main:app', host=args.host, port=args.puerto,
        # asyncio y NO uvloop: uvloop sufre un stall periódico del event loop
        # en WSL2 que se ve como cortes en el eco del tipeo de las terminales.
        loop='asyncio', log_level='info',
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
