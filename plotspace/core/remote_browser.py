"""JARVIS — Browser server-side (Chromium headless + screencast CDP).

POR QUÉ EXISTE
==============
El Web Preview viejo embebía cada sitio en un `<iframe>`. Eso sólo funciona con
lo que se deja embeber: X/Twitter, YouTube, Google, GitHub y casi todo lo demás
mandan `X-Frame-Options`/`CSP frame-ancestors` y el iframe queda en blanco. El
resultado era un "browser" de dos velocidades (uno que anda para localhost, otro
que no para afuera). Este módulo es el motor ÚNICO: un Chromium real del lado del
server, del que se transmite el video por screencast CDP y al que se le reenvía
el input. Adentro entra cualquier sitio, sin restricción de framing.

(Esto ya se había construido y borrado el 2026-07-26 — ver la memoria
`preview-browser-remoto`. Se reintroduce a pedido, con el alcance acotado:
un browser compartido, como mucho MAX_SESIONES contextos vivos, cierre prolijo.)

SEGURIDAD
=========
Todo lo que navega el usuario pasa por el guard anti-SSRF (`core/ssrf.py`): el
server no puede quedar apuntando a loopback/metadata. Ver `_guard`.

CONCURRENCIA
============
Playwright async corre sobre el MISMO event loop de uvicorn (que es asyncio
puro: el repo no usa uvloop). Un Chromium se comparte entre sesiones; cada WS
abre un contexto aislado (cookies/storage propios).
"""
import asyncio
import base64
from typing import Optional

from plotspace.core import ssrf

# Un Chromium por proceso; cada pestaña/panel es un contexto. El tope evita que
# N pestañas se coman la RAM (cada contexto ronda las decenas de MB).
MAX_SESIONES = 3
# Tamaño de un frame del screencast (JPEG). 70 es el balance calidad/peso.
CALIDAD_JPEG = 70
# Ancho/alto máximos del stream: no manda más píxeles de los que se muestran.
MAX_ANCHO = 1600
MAX_ALTO = 1000


def _args_chromium():
    """Flags de headless en contenedor/WSL: sin sandbox, sin GPU, sin /dev/shm.
    `--disable-blink-features=AutomationControlled` evita el fingerprint obvio
    que activa los muros anti-bot de X/Google."""
    return ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
            '--hide-scrollbars',
            '--disable-blink-features=AutomationControlled']


# UA de desktop real: el "HeadlessChrome" del default dispara muros anti-bot
# en X, Google, YouTube… El motor tiene que parecer un browser normal.
_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')


class BrowserError(Exception):
    """No se pudo abrir/navegar la sesión de browser."""


class _Pool:
    """Chromium compartido, arrancado perezosamente y protegido por lock."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._pw = None
        self._browser = None
        self.vivas = 0

    async def browser(self):
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                from playwright.async_api import async_playwright
                self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(
                    headless=True, args=_args_chromium())
            return self._browser

    async def cerrar_todo(self):
        async with self._lock:
            try:
                if self._browser:
                    await self._browser.close()
            except Exception:
                pass
            try:
                if self._pw:
                    await self._pw.stop()
            except Exception:
                pass
            self._browser = None
            self._pw = None


_pool = _Pool()


def _scheme_permitido(url: str) -> bool:
    s = url.split(':', 1)[0].lower() if ':' in url else ''
    return s in ('http', 'https', 'data', 'blob', 'about')


async def _guard(route):
    """Guard anti-SSRF por request. El documento (navegación top-level) se valida
    con DNS; los subrecursos se validan barato (IP literal interna / loopback),
    para no resolver DNS por cada imagen. `data:`/`blob:` pasan."""
    req = route.request
    url = req.url
    if not _scheme_permitido(url):
        await route.abort()
        return
    try:
        if req.resource_type == 'document':
            ok, _motivo = await asyncio.to_thread(ssrf.url_destino_segura, url)
            if not ok:
                await route.abort()
                return
        else:
            host = ''
            try:
                from urllib.parse import urlsplit
                host = (urlsplit(url).hostname or '').lower()
            except ValueError:
                pass
            if host in ('localhost', '127.0.0.1', '0.0.0.0', '::1', '[::1]'):
                await route.abort()
                return
    except Exception:
        pass
    try:
        await route.continue_()
    except Exception:
        pass


# ── Mapeo de teclas para Input.dispatchKeyEvent ──────────────────────────────
_VK = {
    'Enter': 13, 'Backspace': 8, 'Tab': 9, 'Escape': 27, ' ': 32,
    'ArrowLeft': 37, 'ArrowUp': 38, 'ArrowRight': 39, 'ArrowDown': 40,
    'Delete': 46, 'Home': 36, 'End': 35, 'PageUp': 33, 'PageDown': 34,
}


def _evento_tecla(tipo: str, key: str, code: Optional[str], mods: int = 0) -> dict:
    """Traduce una tecla del browser a un Input.dispatchKeyEvent de CDP."""
    ev = {'type': tipo, 'key': key or '', 'modifiers': mods}
    if code:
        ev['code'] = code
    vk = _VK.get(key)
    if vk is None and key and len(key) == 1:
        vk = ord(key.upper())
    if vk is not None:
        ev['windowsVirtualKeyCode'] = vk
        ev['nativeVirtualKeyCode'] = vk
    if tipo == 'keyDown' and key and len(key) == 1:
        ev['text'] = key
        ev['unmodifiedText'] = key
    return ev


class Sesion:
    """Una pestaña/panel del Browser: su propio contexto de Chromium, page y
    canal de screencast."""

    def __init__(self, contexto, page, cdp, ancho, alto, cola):
        self.ctx = contexto
        self.page = page
        self.cdp = cdp
        self.ancho = ancho
        self.alto = alto
        self.cola = cola                 # asyncio.Queue de mensajes al cliente
        self._cerrada = False
        self._screencast_on = False

    # ── Ciclo de vida ───────────────────────────────────────────────
    @classmethod
    async def crear(cls, ancho: int = 1280, alto: int = 800):
        if _pool.vivas >= MAX_SESIONES:
            raise BrowserError('demasiadas pestañas del browser abiertas')
        ancho = max(320, min(int(ancho or 1280), MAX_ANCHO))
        alto = max(240, min(int(alto or 800), MAX_ALTO))
        b = await _pool.browser()
        cola: asyncio.Queue = asyncio.Queue(maxsize=4)
        ctx = await b.new_context(viewport={'width': ancho, 'height': alto},
                                  device_scale_factor=1, user_agent=_UA,
                                  locale='es-AR')
        await ctx.route('**/*', _guard)
        page = await ctx.new_page()
        cdp = await ctx.new_cdp_session(page)
        s = cls(ctx, page, cdp, ancho, alto, cola)
        page.on('framenavigated', lambda fr: s._on_nav(fr))
        _pool.vivas += 1
        return s

    async def cerrar(self):
        if self._cerrada:
            return
        self._cerrada = True
        try:
            await self.cdp.send('Page.stopScreencast')
        except Exception:
            pass
        try:
            await self.ctx.close()
        except Exception:
            pass
        _pool.vivas = max(0, _pool.vivas - 1)

    # ── Navegación ──────────────────────────────────────────────────
    async def navegar(self, url: str):
        ok, motivo = await asyncio.to_thread(ssrf.url_destino_segura, url)
        if not ok:
            raise BrowserError(f'URL no permitida: {motivo}')
        await self.page.goto(url, wait_until='domcontentloaded', timeout=30000)
        await self._emitir_nav()

    async def atras(self):
        await self.page.go_back(wait_until='domcontentloaded', timeout=15000)

    async def adelante(self):
        await self.page.go_forward(wait_until='domcontentloaded', timeout=15000)

    async def recargar(self):
        await self.page.reload(wait_until='domcontentloaded', timeout=15000)

    def _on_nav(self, frame):
        # Sólo la navegación del frame principal; se emite async (handler sync).
        try:
            if frame == self.page.main_frame:
                asyncio.create_task(self._emitir_nav())
        except Exception:
            pass

    async def _emitir_nav(self):
        try:
            url = self.page.url
            titulo = await self.page.title()
        except Exception:
            return
        self._push({'t': 'nav', 'url': url, 'titulo': titulo})

    # ── Screencast ──────────────────────────────────────────────────
    async def iniciar_screencast(self):
        if self._screencast_on:
            return

        def on_frame(params):
            try:
                asyncio.create_task(self.cdp.send(
                    'Page.screencastFrameAck', {'sessionId': params['sessionId']}))
            except Exception:
                pass
            self._push({'t': 'frame', 'data': params.get('data', '')})

        self.cdp.on('Page.screencastFrame', on_frame)
        await self.cdp.send('Page.startScreencast', {
            'format': 'jpeg', 'quality': CALIDAD_JPEG,
            'maxWidth': self.ancho, 'maxHeight': self.alto,
        })
        self._screencast_on = True

    async def redimensionar(self, w: int, h: int):
        w = max(320, min(int(w or self.ancho), MAX_ANCHO))
        h = max(240, min(int(h or self.alto), MAX_ALTO))
        if (w, h) == (self.ancho, self.alto):
            return
        self.ancho, self.alto = w, h
        try:
            await self.cdp.send('Emulation.setDeviceMetricsOverride', {
                'width': w, 'height': h, 'deviceScaleFactor': 1, 'mobile': False})
            await self.cdp.send('Page.stopScreencast')
            self._screencast_on = False
            await self.iniciar_screencast()
        except Exception:
            pass

    # ── Input ───────────────────────────────────────────────────────
    async def input(self, msg: dict):
        t = msg.get('t')
        try:
            if t == 'nav':
                await self.navegar(msg['url'])
            elif t == 'back':
                await self.atras()
            elif t == 'fwd':
                await self.adelante()
            elif t == 'reload':
                await self.recargar()
            elif t == 'resize':
                await self.redimensionar(msg.get('w'), msg.get('h'))
            elif t == 'mouse':
                await self._mouse(msg)
            elif t == 'wheel':
                await self.cdp.send('Input.dispatchMouseEvent', {
                    'type': 'mouseWheel', 'x': msg.get('x', 0), 'y': msg.get('y', 0),
                    'deltaX': msg.get('dx', 0), 'deltaY': msg.get('dy', 0)})
            elif t == 'key':
                await self.cdp.send('Input.dispatchKeyEvent',
                                    _evento_tecla(msg.get('a') == 'up' and 'keyUp' or 'keyDown',
                                                  msg.get('key'), msg.get('code'),
                                                  int(msg.get('mods') or 0)))
        except Exception as e:  # una acción que falla no debe tumbar el WS
            self._push({'t': 'err', 'msg': str(e)[:200]})

    async def _mouse(self, msg: dict):
        a = msg.get('a')
        tipo = {'move': 'mouseMoved', 'down': 'mousePressed', 'up': 'mouseReleased'}.get(a)
        if not tipo:
            return
        ev = {'type': tipo, 'x': msg.get('x', 0), 'y': msg.get('y', 0),
              'button': msg.get('b') or 'left', 'clickCount': 1 if a == 'down' else 0}
        if a == 'move':
            ev.pop('button')
        await self.cdp.send('Input.dispatchMouseEvent', ev)

    # ── Cola hacia el cliente ───────────────────────────────────────
    def _push(self, item: dict):
        try:
            self.cola.put_nowait(item)
        except asyncio.QueueFull:
            # Frames viejos: descartar el más viejo para no acumular latencia.
            try:
                self.cola.get_nowait()
                self.cola.put_nowait(item)
            except Exception:
                pass
