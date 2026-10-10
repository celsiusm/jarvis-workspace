import asyncio
import base64
import contextlib
import gc
import json
import os
import shutil
import tempfile
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from plotspace.core import stt_groq, stt_proc

router = APIRouter(prefix="/api/voice", tags=["voice"])

# Tope del audio que aceptamos transcribir. Una nota de voz para STT pesa <1 MB;
# 25 MB es holgado y evita que un body gigante agote memoria/disco (DoS).
MAX_AUDIO_BYTES = 25 * 1024 * 1024


async def _leer_audio_limitado(audio: UploadFile, max_bytes: int | None = None) -> bytes:
    """Lee el upload en chunks hasta `max_bytes`. Aborta con 413 si lo supera,
    en vez de cargar un body ilimitado entero en RAM."""
    if max_bytes is None:
        max_bytes = MAX_AUDIO_BYTES
    chunks = []
    total = 0
    while True:
        chunk = await audio.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Audio demasiado grande (>{max_bytes // (1024 * 1024)} MB)",
            )
        chunks.append(chunk)
    return b''.join(chunks)


def _nuevo_dir_audio() -> str:
    """Directorio temporal único por request (mkdtemp → 0700). Reemplaza las
    rutas fijas /tmp/jarvis_audio.* que dos requests concurrentes se pisaban
    (devolvían audio cruzado) y que eran un nombre predecible en /tmp (CWE-377)."""
    return tempfile.mkdtemp(prefix="jarvis_audio_")

# El modelo STT cargado del motor ACTIVO. El nombre es histórico: desde
# 2026-07-09 el motor default es parakeet-tdt-0.6b-v3 int8 (onnx) — medido acá
# 3× más rápido que whisper small fp32 con igual RAM (~1GB), mejor español y
# puntuación nativa (ver memoria deepwork-audio-transcripcion). STT_MOTOR=whisper
# es la vía de escape al motor viejo. Toda la maquinaria (executor, lock,
# prewarm, descarga por ocio) es COMPARTIDA entre motores.
_whisper_model = None
# time.monotonic() del último uso REAL del modelo (carga, prewarm o inferencia).
# Lo lee el vigilante de ocio para descargarlo cuando nadie dicta hace rato.
_whisper_ultimo_uso = None

_PARAKEET_MODELO = "nemo-parakeet-tdt-0.6b-v3"


def _stt_motor() -> str:
    """Motor STT activo según env (default parakeet; groq = remoto en los LPUs
    de Groq, cero CPU local; whisper = vía de escape al motor viejo)."""
    m = os.getenv("STT_MOTOR", "parakeet").strip().lower()
    return m if m in ("parakeet", "whisper", "groq") else "parakeet"


def _groq_habilitado() -> bool:
    """¿El dictado va primero a Groq? Exige STT_MOTOR=groq Y la key presente.
    Sin key, el motor cae al camino local sin intentar la red. OJO: aunque
    Groq esté activo, el motor LOCAL sigue siendo el fallback — _cargar_whisper
    con STT_MOTOR=groq resuelve a parakeet (todo != whisper cae ahí)."""
    return _stt_motor() == "groq" and stt_groq.api_key() is not None


def _plan_inferencia(motor: str, translate: bool) -> dict:
    """Cómo cumplir el pedido con el motor dado. PURA y testeable.
    Whisper trae task=translate nativo (el modelo ya devuelve inglés); parakeet
    y groq no — transcriben en español y el TEXTO se traduce después por el
    mismo camino rápido que usa el frontend (_traducir_es_en, ~0.5s).
    (El turbo de Groq NO soporta el endpoint /translations — texto siempre.)"""
    if motor == "whisper":
        return {"motor": "whisper", "traducir_texto": False}
    if motor == "groq":
        return {"motor": "groq", "traducir_texto": bool(translate)}
    return {"motor": "parakeet", "traducir_texto": bool(translate)}

# STT = faster-whisper (CTranslate2, int8): mismos pesos que openai-whisper pero
# 3-4× más rápido en CPU y sin torch (adiós al segfault de libtorch bajo Py3.14
# que obligó a serializar). El hilo dedicado + lock se CONSERVAN igual: ordenan
# la primera transcripción detrás del preload y garantizan una sola inferencia
# a la vez (una concurrente duplicaría hilos de CPU y pisaría el event loop).
_whisper_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")
_whisper_lock = asyncio.Lock()

# Hilos de CPU para la inferencia: todos menos uno (el que queda respira el
# event loop de uvicorn — sin esto una transcripción congela el eco de las
# terminales en WSL2). Medido en el Ryzen 3 3200G: fp32 con 3 hilos rinde igual
# que con 4. Techo en 8: CTranslate2 escala poco más allá y solo roba cores.
_WHISPER_CPU_THREADS = max(2, min(8, (os.cpu_count() or 4) - 1))


def _compute_type() -> str:
    """Precisión de los pesos en CPU. Default float32: en CPUs SIN VNNI (Intel
    pre-Ice Lake, AMD pre-Zen4 — este Ryzen 3 3200G incluido) los kernels int8
    de CTranslate2 son ~2× MÁS LENTOS que los fp32 (medido acá: small 17.8s int8
    vs 12.9s fp32; base 4.4s vs 2.0s). En CPUs modernas con VNNI conviene
    WHISPER_COMPUTE=int8 (más rápido y 3× menos RAM)."""
    return os.getenv("WHISPER_COMPUTE", "float32")


def _chunk_length_para(dur_s) -> int | None:
    """Ventana del encoder adaptada al clip. PURA y testeable.

    Whisper rellena TODO clip a una ventana de 30s: para un dictado de 5s eso
    es >2× de cómputo de encoder tirado a la basura (medido: 21.9s → 10.4s).
    Ventana = duración+2s, mínimo 8s. None (= default 30s) para clips largos
    o duración desconocida — cero cambio de comportamiento fuera del dictado corto."""
    if not dur_s or dur_s >= 28:
        return None
    return max(8, int(dur_s) + 2)


def _duracion_wav(path: str):
    """Duración en segundos del WAV que generó ffmpeg, o None si no se puede leer."""
    try:
        with contextlib.closing(wave.open(path)) as w:
            rate = w.getframerate()
            return (w.getnframes() / rate) if rate else None
    except Exception:
        return None

VOZ_PRINCIPAL = "es-PY-TaniaNeural"
VOZ_FALLBACK  = "es-AR-TomasNeural"
# Voces en inglés (cuando la UI está en EN) — registro femenino/elegante, coherente
# con la voz española. Se usan p.ej. en el saludo de bienvenida (/welcome?lang=en).
VOZ_EN          = "en-GB-SoniaNeural"
VOZ_EN_FALLBACK = "en-US-AriaNeural"


# ─── Whisper ──────────────────────────────────────────────────────────────────

def _warmup_whisper(modelo):
    """Inferencia dummy (1s de silencio) tras cargar: paga la inicialización de
    kernels/caches de CTranslate2 en el preload del startup, no en el primer
    dictado real del usuario. Nunca crítica."""
    try:
        import numpy as np
        segmentos, _info = modelo.transcribe(
            np.zeros(16000, dtype=np.float32), language="es", beam_size=1,
            chunk_length=8,   # ventana corta: warmup barato por el mismo camino
        )
        for _ in segmentos:   # el generador es lazy: consumirlo ejecuta la inferencia
            pass
    except Exception as e:
        print(f"[voice] Warmup de Whisper falló (no crítico): {e}")


def _warmup_parakeet(modelo):
    """Inferencia dummy (1s de silencio): paga la inicialización de kernels de
    onnxruntime en la carga (que dispara el prewarm del PTT), no en el primer
    dictado real. Nunca crítica."""
    try:
        import numpy as np
        modelo.recognize(np.zeros(16000, dtype=np.float32), sample_rate=16000, language="es")
    except Exception as e:
        print(f"[voice] Warmup de parakeet falló (no crítico): {e}")


def _cargar_motor_whisper():
    """Motor viejo (STT_MOTOR=whisper): faster-whisper, configurable por env
    WHISPER_MODEL=tiny|base|small|… y WHISPER_COMPUTE (ver _compute_type).
    OJO con turbo acá: fp32 son 3,2 GB de RSS y ~48s por inferencia en este
    Ryzen (medido) — inusable."""
    from faster_whisper import WhisperModel
    modelo = WhisperModel(
        os.getenv("WHISPER_MODEL", "small"),
        device="cpu",
        compute_type=_compute_type(),
        cpu_threads=_WHISPER_CPU_THREADS,
    )
    _warmup_whisper(modelo)
    return modelo


def _cargar_motor_parakeet():
    """Motor default: parakeet-tdt-0.6b-v3 int8 vía onnx-asr (~640MB en el
    cache de HF, RSS ~1GB). Acá int8 SÍ conviene: los kernels de onnxruntime
    no sufren la penalidad sin-VNNI de CTranslate2, y fp32 son 2.4GB de disco
    y el doble de RAM. Mismos threads que whisper para no pisar el event loop."""
    import onnx_asr
    import onnxruntime
    so = onnxruntime.SessionOptions()
    so.intra_op_num_threads = _WHISPER_CPU_THREADS
    modelo = onnx_asr.load_model(_PARAKEET_MODELO, quantization="int8", sess_options=so)
    _warmup_parakeet(modelo)
    return modelo


def _cargar_whisper():
    """Carga (si hace falta) el modelo del motor ACTIVO y sella el último uso.
    Nombre histórico — hoy despacha por STT_MOTOR (parakeet default). Carga
    ON-DEMAND (prewarm del PTT / primer dictado); ver la descarga por ocio.
    OJO: con STT_WORKER=on (default) esto corre DENTRO del proceso worker
    (core/stt_proc.py), nunca en el server: onnxruntime retiene el GIL ~5-20s
    al crear la sesión y congelaba el event loop entero (freeze post-update)."""
    global _whisper_model, _whisper_ultimo_uso
    if _whisper_model is None:
        _whisper_model = (_cargar_motor_whisper() if _stt_motor() == "whisper"
                          else _cargar_motor_parakeet())
    _whisper_ultimo_uso = time.monotonic()
    return _whisper_model


def _inferir_wav(modelo, tmp_wav: str, translate: bool):
    """(motor, texto) de un WAV con el modelo dado. BLOQUEANTE — corre en el
    hilo del executor (modo in-proc) o dentro del worker STT (default).
    Despacho por capacidad, no por env: si el modelo tiene .transcribe es
    faster-whisper (o su fake en tests); parakeet (onnx-asr) expone .recognize."""
    if hasattr(modelo, "transcribe"):
        kwargs = _whisper_kwargs(translate)
        chunk = _chunk_length_para(_duracion_wav(tmp_wav))
        if chunk:
            kwargs["chunk_length"] = chunk
        segmentos, _info = modelo.transcribe(tmp_wav, **kwargs)
        return "whisper", "".join(s.text for s in segmentos)
    return "parakeet", modelo.recognize(tmp_wav, language="es")


def _worker_habilitado() -> bool:
    """¿El STT corre en el proceso worker (default)? STT_WORKER=off es la vía
    de escape al modo in-proc viejo (modelo dentro del server — puede congelar
    el event loop mientras onnxruntime crea la sesión; ver core/stt_proc.py)."""
    return os.getenv("STT_WORKER", "on").strip().lower() not in ("off", "0", "no", "false")


# El manager del worker STT (spawn perezoso: no hay proceso hasta el primer
# prewarm/dictado; la descarga por ocio lo mata y la RAM vuelve al SO entera).
_worker_stt = stt_proc.WorkerSTT()


def matar_worker_stt_para_reexec():
    """Best-effort SYNC (corre en el hilo del re-exec del update): mata el
    worker STT y lo reapea ANTES del execv — el PID sobrevive al exec, así que
    un hijo vivo quedaría zombi colgando del server nuevo, que no lo conoce
    (mismo motivo que cerrar_clientes_para_reexec en control_mode). Un fallo
    acá JAMÁS frena el restart."""
    import signal
    try:
        proc = _worker_stt.proc_actual()
        if proc is None:
            return
        os.kill(proc.pid, signal.SIGTERM)
        for _ in range(20):               # ~1s: el worker muere al instante
            pid, _st = os.waitpid(proc.pid, os.WNOHANG)
            if pid:
                return
            time.sleep(0.05)
    except Exception:
        pass


# ─── Descarga por ocio (la RAM vuelve al SO) ─────────────────────────────────
# El modelo cargado es RSS anónimo puro del uvicorn (small fp32 ≈ 1 GB; turbo
# fp32 ≈ 3,2 GB) y el dictado usa Whisper solo como FALLBACK del SR del browser:
# dejarlo residente es pagar gigas por un camino que casi nunca corre. Tras
# WHISPER_IDLE_UNLOAD s sin uso se descarga; gc + malloc_trim devuelven la RAM
# al SO (verificado: 3.312 MB → 140 MB) y autoMemoryReclaim se la da a Windows.

def _umbral_ocio():
    """Segundos de ocio antes de descargar, o None si está desactivado
    (WHISPER_IDLE_UNLOAD=0|off)."""
    v = os.getenv("WHISPER_IDLE_UNLOAD", "600").strip().lower()
    if v in ("0", "off", "no", ""):
        return None
    try:
        return max(60, int(v))
    except ValueError:
        return 600


def _debe_descargar(ultimo_uso, ahora, umbral) -> bool:
    """¿Corresponde descargar el modelo? PURA y testeable."""
    if umbral is None or ultimo_uso is None:
        return False
    return (ahora - ultimo_uso) >= umbral


def _malloc_trim():
    """Pide a glibc devolver al SO los arenas que liberó el modelo. Sin esto el
    RSS del proceso no baja aunque Python ya haya soltado los 1-3 GB de pesos."""
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass


def _descargar_whisper() -> bool:
    """Descarga el modelo si sigue ocioso. Corre DENTRO del executor de whisper
    (1 hilo): jamás pisa una inferencia, y una inferencia encolada antes renueva
    el timestamp → el re-chequeo de acá se abstiene."""
    global _whisper_model
    if _whisper_model is None:
        return False
    if not _debe_descargar(_whisper_ultimo_uso, time.monotonic(), _umbral_ocio()):
        return False
    _whisper_model = None
    gc.collect()
    _malloc_trim()
    return True


async def _descargar_ociosos():
    """Una pasada del vigilante: mata el worker STT ocioso (la RAM vuelve al SO
    entera, sin gc ni malloc_trim) y/o descarga el modelo in-proc del modo viejo."""
    umbral = _umbral_ocio()
    ahora = time.monotonic()
    if _worker_stt.vivo and _debe_descargar(_whisper_ultimo_uso, ahora, umbral):
        if await _worker_stt.terminar():
            print("[voice] worker STT descargado por ocio — RAM devuelta al SO")
    if _whisper_model is not None and _debe_descargar(_whisper_ultimo_uso, ahora, umbral):
        loop = asyncio.get_event_loop()
        libero = await loop.run_in_executor(_whisper_executor, _descargar_whisper)
        if libero:
            print("[voice] Whisper descargado por ocio — RAM devuelta al SO")


async def vigilar_ocio_whisper():
    """Poller del lifespan: chequea cada 60s y descarga el modelo/worker ocioso."""
    while True:
        await asyncio.sleep(60)
        try:
            await _descargar_ociosos()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[voice] vigilar_ocio_whisper: {e}")


@router.post("/prewarm")
async def prewarm():
    """Dispara la carga del modelo STT SIN esperar. El front lo pega al apretar
    PTT y también por ACTIVIDAD (throttle 60s): la carga corre en paralelo a la
    grabación/uso, así /transcribe encuentra el modelo listo (o encola detrás
    de la carga). Default: la carga vive en el proceso WORKER (stt_proc) — el
    server no puede congelarse por ella (era el freeze post-update).
    Con Groq activo es NO-OP: no hay modelo local que calentar (ese es el
    punto — cero CPU/RAM); el fallback local paga carga fría solo si Groq
    falla, que es el caso raro."""
    global _whisper_ultimo_uso
    if _groq_habilitado():
        return {"estado": "listo", "motor": "groq"}
    # Renovar la ventana de ocio SIEMPRE: el prewarm por actividad significa
    # "el usuario sigue acá y va a dictar" — sin esto, el vigilante descargaba
    # el modelo a los 600s del último dictado aunque el usuario estuviera
    # activo, y el próximo dictado pagaba la carga fría entera (>10s).
    _whisper_ultimo_uso = time.monotonic()

    if _worker_habilitado():
        if _worker_stt.listo:
            return {"estado": "listo"}
        await _worker_stt.asegurar()   # spawn = ms; el modelo carga en el hijo
        return {"estado": "cargando"}

    # Modo in-proc viejo (STT_WORKER=off).
    if _whisper_model is not None:
        return {"estado": "listo"}

    def _cargar_seguro():
        try:
            _cargar_whisper()
        except Exception as e:
            print(f"[voice] Prewarm de Whisper falló: {e}")

    _whisper_executor.submit(_cargar_seguro)
    return {"estado": "cargando"}


# Sesga el decoder hacia la jerga del workspace (Whisper small en español suele
# errar los nombres técnicos). Solo para task=transcribe: el prompt es español
# y en task=translate (salida en inglés) contaminaría el idioma de salida.
# El frontend además post-corrige la jerga en el texto final (shared/stt-jerga.js),
# que cubre lo que este bias no alcanza — mantener ambas listas en sintonía.
_VOCABULARIO_DOMINIO = (
    "Jarvis, Claude Code, Codex, Gemini, qwen, opencode, antigravity, tmux, "
    "terminal, workflow, orquestador, kanban, Web Builder, commit, merge, push, "
    "branch, deploy, frontend, backend, dashboard, preview, review, localhost."
)


def _whisper_kwargs(translate: bool) -> dict:
    """Kwargs de decode para modelo.transcribe() de faster-whisper. PURA y testeable.

    - Decode RÁPIDO para dictado corto: greedy (beam_size=1) + temperature 0 FIJA
      (desactiva el fallback que re-corre la inferencia) + sin condicionar en texto
      previo (más rápido y sin loops de repetición en clips cortos).
    - task: 'translate' usa el modo de Whisper que devuelve SIEMPRE inglés (traduce
      desde el idioma hablado); 'transcribe' devuelve el idioma original (español).
    - OJO: nada de kwargs de openai-whisper acá (`fp16` etc.) — faster-whisper los
      rechaza con TypeError; la precisión se fija al cargar (compute_type)."""
    return {
        "language": "es",
        "beam_size": 1,
        "temperature": 0.0,
        "condition_on_previous_text": False,
        "task": "translate" if translate else "transcribe",
        "initial_prompt": None if translate else _VOCABULARIO_DOMINIO,
    }


# ─── TTS con edge-tts ─────────────────────────────────────────────────────────

async def _texto_a_base64(texto: str, voces: list[str] | None = None) -> str:
    """Genera audio con edge-tts y devuelve el contenido como base64.

    `voces` = lista de voces a probar en orden (la 1ra que funciona gana). Por
    defecto las españolas; el saludo en inglés pasa las voces `en-*`.
    """
    import edge_tts

    voces = voces or [VOZ_PRINCIPAL, VOZ_FALLBACK]

    # Archivo único por request (mkstemp → 0600): /speak y /welcome pueden correr
    # a la vez y un nombre fijo hacía que una pisara el mp3 de la otra.
    fd, tmp_path = tempfile.mkstemp(suffix=".mp3", prefix="jarvis_tts_")
    os.close(fd)

    try:
        ultimo_error = None
        for voz in voces:
            try:
                communicate = edge_tts.Communicate(texto, voz)
                await communicate.save(tmp_path)
                break
            except Exception as e:
                ultimo_error = e
                continue
        else:
            raise RuntimeError(f"edge-tts falló con todas las voces: {ultimo_error}")

        with open(tmp_path, "rb") as f:
            data = f.read()
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    return base64.b64encode(data).decode("utf-8")


# ─── Modelos ──────────────────────────────────────────────────────────────────

class SpeakRequest(BaseModel):
    text: str
    lang: str = "es"


class TranslateRequest(BaseModel):
    # `text` = un solo texto (dictado). `texts` = un LOTE (las novedades del
    # modal "Actualizar ahora", los títulos vivos de las cards); presente uno u
    # otro. `sl` = idioma origen: default 'es'; 'auto' para texto que puede venir
    # ya en inglés (títulos de pane de Codex/otras CLIs).
    text: str | None = None
    texts: list[str] | None = None
    sl: str | None = None


class DictadoLogRequest(BaseModel):
    texto: str = ""
    fuente: str = ""            # 'sr' | 'whisper' | 'preciso' — quién produjo el texto
    conf: float | None = None   # confianza reportada por el SR (null si no la dio)
    diag: str | None = None     # diagnóstico de captura ('bajo'/'saturado'/'bluetooth')
    ms: int | None = None       # latencia release→texto medida por el front (tuning real)


# Registro LOCAL de dictados: qué texto salió, por qué fuente y con qué
# confianza — la materia prima para afinar el corrector de jerga con errores
# REALES del usuario ("esto dicté, esto escribió"). Vive en data/ (gitignored),
# rotación simple por tamaño: histórico corto alcanza para diagnosticar.
_DICTADOS_LOG = os.path.join("data", "dictados.log")
_DICTADOS_MAX_BYTES = 2 * 1024 * 1024


def _escribir_dictado_log(linea: str, path: str | None = None) -> None:
    path = path or _DICTADOS_LOG
    try:
        if os.path.getsize(path) > _DICTADOS_MAX_BYTES:
            os.remove(path)
    except OSError:
        pass
    with open(path, "a", encoding="utf-8") as f:
        f.write(linea + "\n")


@router.post("/dictado-log")
async def dictado_log(req: DictadoLogRequest):
    """Anota un dictado en data/dictados.log (JSON-lines). Best-effort: jamás
    le devuelve un error al cliente — es telemetría local, no camino crítico."""
    try:
        _escribir_dictado_log(json.dumps({
            "ts": datetime.now().isoformat(timespec="seconds"),
            "fuente": (req.fuente or "")[:16],
            "conf": req.conf,
            "diag": (req.diag or None),
            "ms": req.ms,
            "texto": (req.texto or "")[:500],
        }, ensure_ascii=False))
    except Exception as e:
        print(f"[voice] dictado-log: {e}")
    return {"ok": True}


# ─── Traducción de TEXTO (rápida) ───────────────────────────────────────────────
# El dictado "Traducir a inglés" transcribe rápido en español (SpeechRecognition del
# browser) y traduce el TEXTO acá, en vez de re-correr Whisper en task=translate
# (que en CPU tardaba 15-20s). Una frase se traduce en ~400-700ms.

_MAX_TRAD_CHARS = 2000   # el GET de Google tiene tope de URL; un dictado nunca llega


def _parse_google_translate(data) -> str:
    """Extrae el texto traducido del array anidado de translate_a/single
    ([[[trad, orig, ...], ...], ...]) uniendo los segmentos. PURA y tolerante a basura."""
    try:
        segs = data[0]
        return ''.join(s[0] for s in segs if s and s[0]).strip()
    except (TypeError, IndexError, KeyError):
        return ''


async def _http_google_translate(texto: str, sl: str = "es", tl: str = "en") -> str:
    """Llama al endpoint gratuito translate_a/single de Google y devuelve la traducción.
    Aislada para poder mockearla en los tests."""
    import httpx
    async with httpx.AsyncClient(timeout=8) as client:
        r = await client.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": sl, "tl": tl, "dt": "t", "q": texto},
            headers={"User-Agent": "Mozilla/5.0"},
        )
        r.raise_for_status()
        return _parse_google_translate(r.json())


async def _traducir_es_en(texto: str, sl: str = "es") -> str:
    texto = (texto or "").strip()[:_MAX_TRAD_CHARS]
    if not texto:
        return ""
    return await _http_google_translate(texto, sl=sl)


async def _traducir_lote(textos: list, sl: str = "es") -> list:
    """Traduce una lista es→en EN PARALELO, preservando el orden. Degradación
    elegante ítem por ítem: uno vacío queda vacío (sin tocar la red) y uno cuya
    traducción falla vuelve como su ORIGINAL — así el modal "Actualizar ahora"
    muestra esa novedad en español y el resto en inglés, sin romper el lote.
    sl='auto' (títulos vivos de las cards): Google detecta el idioma origen, así
    un título que YA está en inglés vuelve intacto en vez de estropearse."""
    async def _uno(t: str) -> str:
        t = (t or "").strip()
        if not t:
            return t
        try:
            return (await _traducir_es_en(t, sl=sl)) or t
        except Exception:
            return t
    return list(await asyncio.gather(*[_uno(t) for t in (textos or [])]))


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/speak")
async def hablar(req: SpeakRequest):
    """Genera audio con edge-tts y lo devuelve como base64.

    `lang` (es|en): en 'en' TRADUCE el texto es→en (rápido, Google) y usa voz
    inglesa, así TODA la voz de Jarvis sigue el idioma de la UI (los avisos que
    llegan dinámicos del orquestador en español). La bienvenida va por /welcome (texto ya explícito).
    """
    texto = req.text.strip()
    if not texto:
        raise HTTPException(status_code=400, detail="Texto vacío")

    voces = None
    if str(req.lang).lower().startswith("en"):
        try:
            en = await _traducir_es_en(texto)
            if en:
                texto = en
        except Exception:
            pass  # si la traducción falla, hablamos el original (degradación elegante)
        voces = [VOZ_EN, VOZ_EN_FALLBACK]

    try:
        audio_b64 = await _texto_a_base64(texto, voces)
        return {"ok": True, "audio_base64": audio_b64}
    except Exception as e:
        print(f"[voice/speak] Error generando TTS: {e}")
        return {"ok": False, "audio_base64": None}


@router.post("/translate")
async def traducir_texto(req: TranslateRequest):
    """Traduce español → inglés (rápido, gratis vía Google). Acepta un texto
    (`text`, dictado 'Traducir a inglés') o un LOTE (`texts`, novedades del modal
    'Actualizar ahora' y títulos vivos de las cards — en paralelo, orden
    preservado). `sl='auto'` detecta el idioma origen (allowlist: es|auto)."""
    sl = req.sl if req.sl in ("es", "auto") else "es"
    if req.texts is not None:
        return {"texts": await _traducir_lote(req.texts, sl=sl)}
    texto = (req.text or "").strip()
    if not texto:
        return {"text": ""}
    try:
        en = await _traducir_es_en(texto, sl=sl)
        if not en:
            raise RuntimeError("traducción vacía")
        return {"text": en}
    except Exception as e:
        print(f"[voice/translate] Error traduciendo: {e}")
        raise HTTPException(status_code=502, detail="No se pudo traducir")


class GroqKeyIn(BaseModel):
    key: str = ""


@router.get("/setup")
def voice_setup():
    """¿Hay Groq listo para dictar? Lo usa el modal de primer arranque."""
    return {"groq": _groq_habilitado(), "motor": _stt_motor()}


@router.post("/groq-key")
def guardar_groq_key(body: GroqKeyIn):
    """Guarda GROQ_API_KEY + STT_MOTOR=groq en .env y en el proceso. Sin restart."""
    key = (body.key or "").strip()
    if not stt_groq.clave_parece_groq(key):
        raise HTTPException(status_code=400, detail="La clave de Groq empieza con gsk_")
    # Dos lugares: plotspace/.env (instalación desde el repo) y data/.env (el
    # volumen de datos: es lo ÚNICO que sobrevive a un update del contenedor, y en
    # Docker no-root el código es de solo lectura). Basta con que UNO funcione.
    from plotspace.core import datadir
    destinos = [stt_groq.ruta_env_local()]
    extra = datadir.ruta_data(".env")
    if os.path.abspath(extra) != os.path.abspath(destinos[0]):
        destinos.append(extra)
    escritos = 0
    for path in destinos:
        try:
            stt_groq.upsert_env(path, "GROQ_API_KEY", key)
            stt_groq.upsert_env(path, "STT_MOTOR", "groq")
            escritos += 1
        except OSError:
            pass
    if not escritos:
        raise HTTPException(status_code=500, detail="No se pudo guardar la clave (sin permiso de escritura)")
    os.environ["GROQ_API_KEY"] = key
    os.environ["STT_MOTOR"] = "groq"
    return {"groq": True, "motor": "groq"}


@router.get("/welcome")
async def bienvenida(lang: str = "es"):
    """Genera el audio de bienvenida y lo devuelve como base64.

    `lang` (es|en) elige el TEXTO y la VOZ, así el saludo HABLADO sigue el idioma
    de la UI (el texto coincide con el saludo mostrado en el hero del orquestador).
    """
    en = str(lang).lower().startswith("en")
    texto = "What should we do, sir?" if en else "¿Qué hacemos, señor?"
    voces = [VOZ_EN, VOZ_EN_FALLBACK] if en else [VOZ_PRINCIPAL, VOZ_FALLBACK]
    try:
        audio_b64 = await _texto_a_base64(texto, voces)
        return {"ok": True, "audio_base64": audio_b64}
    except Exception as e:
        print(f"[voice/welcome] Error generando bienvenida: {e}")
        return {"ok": True, "audio_base64": None}


# Recorta el silencio de PUNTA y FINAL antes de Whisper → menos audio = transcribe
# más corto (el usuario suele tardar en empezar a hablar tras apretar el PTT, y suelta
# un toque después de terminar). Recipe "trim ambos extremos": recorta el inicio,
# invierte, recorta el nuevo inicio (= el final original), re-invierte.
# start_silence=0.15 deja 150ms de aire en cada punta (60ms se comía el primer/último
# fonema con voz suave — pedido 2026-07-10); el umbral bajó -40 → -45 (mic del
# usuario crónicamente bajo, picos < -24dBFS) → -50dB (pedido 2026-07-17 "que me
# escuche más de lejos": hablando a distancia la voz entra pegada al piso y con
# -45 el trim la descartaba como silencio — verificado en test_voice_filtros con
# ráfagas a -46dBFS que salían con duración 0.0). Todo afloja el trim: mejor un
# pelo más de audio que perder palabras (el cortocircuito de <0.3s sigue matando
# los PTT sin voz, y speechnorm nivela después lo que sobrevive).
# Después del recorte, speechnorm NIVELA la voz: el modelo transcribe peor con señal
# débil. Medido acá: clip de -34dBFS → -6dBFS (el rango sano) con e=25. r=0.001 sube
# la ganancia gradual (sin pumping); el orden importa: silenceremove primero opera
# sobre los niveles ORIGINALES (su umbral quedaría corrido si se normaliza antes).
_RECORTE = (
    "silenceremove=start_periods=1:start_silence=0.15:start_threshold=-50dB,"
    "areverse,"
    "silenceremove=start_periods=1:start_silence=0.15:start_threshold=-50dB,"
    "areverse,"
    "speechnorm=e=25:r=0.001:l=1"
)


async def _transcribir_groq(tmp_wav: str) -> str:
    """Transcripción remota vía Groq con el sesgo de jerga del workspace.
    Módulo-level para que los tests puedan monkeypatchearla (mismo patrón que
    _http_google_translate). Levanta ante cualquier fallo — el endpoint decide
    el fallback al motor local."""
    return await stt_groq.transcribir(tmp_wav, prompt=_VOCABULARIO_DOMINIO)


async def _convertir_a_wav(tmp_webm: str, tmp_wav: str, filtro: str | None):
    """ffmpeg: webm → WAV 16 kHz mono (opcionalmente con filtro de recorte).
    Módulo-level para que los tests puedan monkeypatchearla. Devuelve (rc, stderr)."""
    args = ["ffmpeg", "-y", "-i", tmp_webm, "-ar", "16000", "-ac", "1"]
    if filtro:
        args += ["-af", filtro]
    args.append(tmp_wav)
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    return proc.returncode, stderr


@router.post("/transcribe")
async def transcribir(audio: UploadFile = File(...), translate: bool = Form(False)):
    """Recibe audio webm, convierte a WAV 16 kHz con ffmpeg y transcribe con Whisper.

    translate=True → Whisper devuelve el dictado traducido a inglés (task=translate)."""
    if not audio:
        raise HTTPException(status_code=400, detail="No se recibió archivo de audio")

    # Leer con tope (413 si se pasa) ANTES de tocar disco/ffmpeg.
    contenido = await _leer_audio_limitado(audio)

    tmp_dir  = _nuevo_dir_audio()
    tmp_webm = os.path.join(tmp_dir, "in.webm")
    tmp_wav  = os.path.join(tmp_dir, "out.wav")

    # Un solo try/finally envuelve TODO el trabajo: así el tmp_dir se limpia siempre,
    # también en los caminos de error (ffmpeg falla, CancelledError) que antes salían
    # con `raise` antes de llegar al finally de Whisper y dejaban el dir huérfano.
    try:
        try:
            with open(tmp_webm, "wb") as f:
                f.write(contenido)
        except Exception as e:
            # Log server-side, detail genérico (no filtrar errno/rutas al cliente).
            print(f'[voice] Error guardando audio temporal: {e}')
            raise HTTPException(status_code=500, detail="No se pudo procesar el audio")

        try:
            rc, stderr = await _convertir_a_wav(tmp_webm, tmp_wav, _RECORTE)
            if rc != 0:
                # Fallback graceful: si el filtro de recorte falla (ffmpeg sin el filtro,
                # audio raro), reconvertir SIN recorte. Nunca romper la transcripción.
                rc, stderr = await _convertir_a_wav(tmp_webm, tmp_wav, None)
            if rc != 0:
                raise RuntimeError(stderr.decode(errors="replace"))
        except asyncio.CancelledError:
            raise
        except HTTPException:
            raise
        except Exception as e:
            # Log server-side (incluye stderr de ffmpeg), detail genérico al cliente.
            print(f'[voice] Error convirtiendo audio con ffmpeg: {e}')
            raise HTTPException(status_code=500, detail="No se pudo procesar el audio")

        # Cortocircuito de silencio: si el recorte dejó el WAV en ~nada, no hay
        # voz que transcribir. Es el caso MÁS común de esta ruta (el fallback a
        # Whisper corre justo cuando el SR del browser no entendió nada — PTT
        # apretado sin querer, sin hablar) y se ahorra la inferencia entera.
        dur = _duracion_wav(tmp_wav)
        if dur is not None and dur < 0.3:
            return {"text": ""}

        # Motor remoto (STT_MOTOR=groq): la inferencia corre en los LPUs de
        # Groq — cero CPU/RAM local, sin lock (es solo HTTP; Groq paraleliza).
        # Ante CUALQUIER fallo (sin red, 429, 5xx, timeout) se cae al motor
        # local de abajo, que carga on-demand como siempre.
        motor_usado = texto = None
        if _groq_habilitado():
            try:
                texto = await _transcribir_groq(tmp_wav)
                motor_usado = "groq"
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[voice] Groq STT falló, fallback al motor local: {e}")

        try:
            global _whisper_ultimo_uso
            if motor_usado == "groq":
                pass
            elif _worker_habilitado():
                # Default: la inferencia (y la carga, si el worker está frío)
                # corre en el PROCESO worker — su GIL no puede congelar este
                # event loop (era el freeze post-update). Serializar igual:
                # una inferencia a la vez, mismo contrato que el executor.
                async with _whisper_lock:
                    motor_usado, texto = await _worker_stt.transcribir(tmp_wav, translate)
                _whisper_ultimo_uso = time.monotonic()
            else:
                # Modo in-proc viejo (STT_WORKER=off): todo dentro del hilo del
                # executor — la inferencia de ambos motores es bloqueante (y
                # transcribe() de faster-whisper es un generador LAZY que corre
                # al consumirlo); nada de esto va en el event loop.
                loop = asyncio.get_event_loop()

                def _inferir():
                    return _inferir_wav(_cargar_whisper(), tmp_wav, translate)

                # Serializar: una sola inferencia a la vez (ver _whisper_executor).
                async with _whisper_lock:
                    motor_usado, texto = await loop.run_in_executor(_whisper_executor, _inferir)
            texto = texto.strip()

            # parakeet no tiene task=translate: si el pedido era traducir, se
            # traduce el TEXTO (mismo camino rápido que el frontend, ~0.5s).
            if _plan_inferencia(motor_usado, translate)["traducir_texto"] and texto:
                try:
                    texto = (await _traducir_es_en(texto)) or texto
                except Exception:
                    pass  # degradación elegante: mejor devolver español que fallar
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Log server-side, detail genérico al cliente.
            print(f'[voice] Error en transcripción Whisper: {e}')
            raise HTTPException(status_code=500, detail="No se pudo transcribir el audio")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # `motor` es informativo (telemetría/debug del re-rank del front); el
    # contrato de siempre es `text` y los callers viejos lo ignoran.
    return {"text": texto, "motor": motor_usado}
