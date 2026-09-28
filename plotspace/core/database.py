import sqlite3
import os
from datetime import datetime

# Ruta de la base de datos (en el data dir activo — ver core/datadir.py)
from plotspace.core.datadir import ruta_data

DB_PATH = ruta_data('jarvis.db')


def get_db():
    """Obtiene una conexión a SQLite con row_factory activado.

    WAL + busy_timeout son críticos acá: 5 pollers en loop + monitores de
    keywords + el REST escriben concurrentemente. Sin WAL, un writer bloquea
    a los lectores y dos writers chocan al instante → `database is locked` y
    TASK_DONE/updates perdidos en silencio (el síntoma "el agente terminó pero
    nadie se enteró"). WAL permite lectores concurrentes con un writer;
    busy_timeout hace que un writer espere hasta 5s en vez de fallar."""
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db():
    """Crea las tablas si no existen"""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS projects (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre          TEXT    NOT NULL,
            ruta            TEXT    NOT NULL,
            fecha_creacion  TEXT    NOT NULL,
            ultimo_acceso   TEXT    NOT NULL,
            seccion         TEXT    NOT NULL DEFAULT 'active',
            orden           INTEGER NOT NULL DEFAULT 0
        )
    ''')

    # Migración: si la DB ya existía sin seccion/orden, agregarlas
    cursor.execute("PRAGMA table_info(projects)")
    cols = {row['name'] for row in cursor.fetchall()}
    if 'seccion' not in cols:
        cursor.execute("ALTER TABLE projects ADD COLUMN seccion TEXT NOT NULL DEFAULT 'active'")
    if 'orden' not in cols:
        cursor.execute("ALTER TABLE projects ADD COLUMN orden INTEGER NOT NULL DEFAULT 0")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS terminals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id      INTEGER NOT NULL,
            nombre          TEXT    NOT NULL,
            tipo_ia         TEXT    NOT NULL DEFAULT 'manual',
            puerto          INTEGER,
            activa          INTEGER NOT NULL DEFAULT 1,
            fecha_creacion  TEXT    NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
    ''')

    # Migración: session_uuid — el uuid de la sesión de claude (`claude
    # --session-id <uuid>`), que ES el basename del `<uuid>.jsonl` en
    # ~/.claude/projects/. Habilita el mapeo DETERMINISTA terminal→transcript
    # del overlay de selección (ver plotspace/core/transcript.py). Nullable: sólo
    # las terminales claude lo usan; NULL cae al fallback por cwd+mtime.
    cursor.execute("PRAGMA table_info(terminals)")
    cols_t = {row['name'] for row in cursor.fetchall()}
    if 'session_uuid' not in cols_t:
        cursor.execute("ALTER TABLE terminals ADD COLUMN session_uuid TEXT")
    # Migración: ORIGEN de la terminal — Jarvis la spawneó con una tarea (o le
    # mandó un prompt con enviar_prompt). `origen` = 'jarvis' (NULL = la abrió
    # el usuario), `tarea` = el texto que se le entregó, `orquestacion` = el
    # objetivo corto (la orden del chat recortada) y `orquestacion_ts` (ISO).
    # Los lee el monitor en vivo de Tasks.
    for col in ('origen', 'tarea', 'orquestacion', 'orquestacion_ts'):
        if col not in cols_t:
            cursor.execute(f"ALTER TABLE terminals ADD COLUMN {col} TEXT")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS task_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            terminal_id INTEGER NOT NULL,
            project_id  INTEGER NOT NULL,
            event       TEXT    NOT NULL,
            timestamp   TEXT    NOT NULL,
            workflow_id TEXT,
            motivo      TEXT
        )
    ''')

    # Migración: motivo del TASK_BLOCKED/TASK_ERROR — la materia prima de las
    # lecciones del enjambre (antes el "por qué" de cada fallo se descartaba).
    cursor.execute("PRAGMA table_info(task_events)")
    cols_te = {row['name'] for row in cursor.fetchall()}
    if 'motivo' not in cols_te:
        cursor.execute("ALTER TABLE task_events ADD COLUMN motivo TEXT")

    # (Las tablas `tasks` —kanban manual— y `workflows` —motor de workflows
    #  del orquestador— se eliminaron 2026-09-28 junto con sus features. Una DB
    #  vieja las conserva huérfanas y sin uso — no las dropeamos.)

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS project_skills (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id  INTEGER NOT NULL,
            nombre      TEXT    NOT NULL,
            descripcion TEXT    NOT NULL DEFAULT '',
            contenido   TEXT    NOT NULL DEFAULT '',
            activa      INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT    NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
    ''')

    # Dedupe filas con mismo (project_id, nombre) antes de crear el índice único
    # (la primera ejecución contra una DB legacy podría tener duplicados).
    # Conservamos la fila con id más alto = la más reciente.
    cursor.execute('''
        DELETE FROM project_skills
        WHERE id NOT IN (
            SELECT MAX(id) FROM project_skills
            GROUP BY project_id, nombre
        )
    ''')

    # UNIQUE constraint sobre (project_id, nombre) — habilita UPSERT atómico
    # vía ON CONFLICT en plugins.py
    cursor.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_project_skills_unique
        ON project_skills(project_id, nombre)
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orquestador_historial (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id  INTEGER NOT NULL,
            thread_id   TEXT    NOT NULL,
            mensajes    TEXT    NOT NULL DEFAULT '[]',
            created_at  TEXT    NOT NULL,
            updated_at  TEXT    NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
    ''')

    # (El Web Builder v1 —tablas web_pages / wb_chats / wb_chat_mensajes— se
    #  eliminó 2026-07-11 para reconstruirlo desde cero. La nueva versión
    #  definirá su propio esquema. Una DB vieja conserva esas tablas huérfanas
    #  sin uso — no las dropeamos. Ver [[web-builder-v1-eliminado]].)

    # Uso acumulado del orquestador por proyecto (tokens in/out + llamadas).
    # Antes response.usage se tiraba — esto lo persiste para mostrar costo.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orquestador_uso (
            project_id    INTEGER PRIMARY KEY,
            input_tokens  INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            llamadas      INTEGER NOT NULL DEFAULT 0,
            updated_at    TEXT    NOT NULL DEFAULT ''
        )
    ''')

    # (Las tablas jai_* del viejo "Jarvis AI" se quitaron junto con la feature;
    #  si una DB vieja todavía las tiene, quedan sin uso — no las dropeamos.)

    # Cuentas de CLIs (claude/codex/qwen/opencode): SOLO metadata. El
    # secreto (snapshot de los archivos de credencial) vive en disco bajo
    # data/cli-accounts/<id>/ (gitignored), NUNCA en esta tabla. Tabla global
    # (no cuelga de un proyecto). Ver plotspace/core/cli_accounts.py.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cli_accounts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo        TEXT    NOT NULL,
            label       TEXT    NOT NULL DEFAULT '',
            email       TEXT,
            activa      INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT    NOT NULL,
            updated_at  TEXT    NOT NULL
        )
    ''')

    # Telemetría de la memoria compartida: qué memorias leyó cada agente y CÓMO
    # terminó su paso (done/blocked/error). Antes vivía en el audit trail
    # rotativo (data/jarvis.log, horizonte ~500 eventos) — acá persiste y se
    # puede cruzar lectura↔resultado: la señal de salience del recall.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS memoria_uso (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id  INTEGER NOT NULL,
            terminal_id INTEGER,
            slug        TEXT    NOT NULL,
            resultado   TEXT    NOT NULL,
            timestamp   TEXT    NOT NULL
        )
    ''')

    # Mailbox v2: cada mensaje del MAILBOX.md con ESTADO (pendiente →
    # entregado → archivado). El .md sigue siendo la API de escritura de los
    # agentes; esta tabla es la que permite entrega garantizada (en idle o al
    # despachar tarea), digest y janitor — antes un mensaje no leído moría.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mailbox_msgs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id  INTEGER NOT NULL,
            de          TEXT    NOT NULL,
            para        TEXT    NOT NULL,
            terminal_id INTEGER,
            msg         TEXT    NOT NULL,
            estado      TEXT    NOT NULL DEFAULT 'pendiente',
            timestamp   TEXT    NOT NULL,
            entregado_ts TEXT,
            clase       TEXT    DEFAULT 'normal'
        )
    ''')

    # Migración: CLASE del mensaje (ask/handoff/normal) — decide si amerita
    # DESPERTAR al destinatario (tipearle el digest) o si puede esperar en su
    # inbox. Medido en este proyecto: 52 de 134 mensajes (38%) fueron a un agente
    # que YA había cerrado su tarea, y 47 de esos eran charla; cada entrega le
    # quema un turno COMPLETO de inferencia. Las filas viejas quedan en NULL →
    # se tratan como 'normal' (no despiertan).
    cursor.execute("PRAGMA table_info(mailbox_msgs)")
    cols_mb = {row['name'] for row in cursor.fetchall()}
    if 'clase' not in cols_mb:
        cursor.execute("ALTER TABLE mailbox_msgs ADD COLUMN clase TEXT")

    # Estado del Web Builder por proyecto: un snapshot JSON (sesiones + hilos de
    # chat + páginas web generadas). Una fila por proyecto (UPSERT). Los modelos
    # de pizarra/flyer NO se persisten (viven en closures del motor) — ver
    # [[builder-porteo-plan]].
    # (Acá vivían wb_state / wb_estado / wb_anclas / wb_cli_sesiones, del Web
    # Builder. La sección se eliminó por completo el 2026-07-25 y las tablas ya
    # no se crean; una DB vieja las conserva huérfanas, sin uso —dropearlas no
    # aporta y sí puede llevarse datos si alguien reintroduce el nombre—.
    # Ver [[web-builder-eliminado]].)

    # Notas del proyecto (Mobile Studio): papeles pegados al lienzo con el saber
    # operativo del proyecto — credenciales de Expo/EAS, comandos, pendientes.
    # Viven SOLO acá (data/jarvis.db es local y gitignoreado): nunca en el repo.
    # `secreta` = la nota nace tapada (se destapa a mano) — no es cifrado.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS project_notes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id  INTEGER NOT NULL,
            titulo      TEXT    NOT NULL DEFAULT '',
            cuerpo      TEXT    NOT NULL DEFAULT '',
            secreta     INTEGER NOT NULL DEFAULT 0,
            color       TEXT    NOT NULL DEFAULT 'papel',
            x           REAL    NOT NULL DEFAULT 0,
            y           REAL    NOT NULL DEFAULT 0,
            w           REAL    NOT NULL DEFAULT 320,
            h           REAL    NOT NULL DEFAULT 300,
            creado      TEXT    NOT NULL,
            actualizado TEXT    NOT NULL,
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_project_notes_proj ON project_notes(project_id)')

    # ── lápidas del Web Builder v1 ───────────────────────────────────────────
    # `web_pages`, `wb_chats` y `wb_chat_mensajes` murieron con el v1 (2026-07-11)
    # pero nadie hizo el DROP: quedaron en la DB de todos los que ya la tenían,
    # sin una sola referencia en el código. Se van SOLO si están vacías — si
    # alguna tuviera filas es que no eran lo que creemos, y mejor no tocarla.
    for _muerta in ('wb_chat_mensajes', 'wb_chats', 'web_pages'):
        try:
            if cursor.execute(f'SELECT COUNT(*) FROM {_muerta}').fetchone()[0] == 0:
                cursor.execute(f'DROP TABLE {_muerta}')
        except sqlite3.Error:
            pass   # no existe (DB nueva) o está en uso: nada que limpiar

    conn.commit()
    conn.close()


def registrar_uso_orquestador(project_id: int, input_tokens: int, output_tokens: int):
    """Acumula el uso de una llamada al orquestador en orquestador_uso (UPSERT).
    Defensivo: nunca rompe el flujo del chat si falla."""
    from datetime import datetime
    conn = get_db()
    try:
        conn.execute(
            '''INSERT INTO orquestador_uso (project_id, input_tokens, output_tokens, llamadas, updated_at)
               VALUES (?, ?, ?, 1, ?)
               ON CONFLICT(project_id) DO UPDATE SET
                 input_tokens  = input_tokens  + excluded.input_tokens,
                 output_tokens = output_tokens + excluded.output_tokens,
                 llamadas      = llamadas      + 1,
                 updated_at    = excluded.updated_at''',
            (project_id, int(input_tokens or 0), int(output_tokens or 0), datetime.now().isoformat())
        )
        conn.commit()
    finally:
        conn.close()


def obtener_uso_orquestador(project_id: int) -> dict:
    """Uso acumulado del orquestador para un proyecto (ceros si no hay)."""
    conn = get_db()
    try:
        row = conn.execute(
            'SELECT input_tokens, output_tokens, llamadas FROM orquestador_uso WHERE project_id = ?',
            (project_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {'input_tokens': 0, 'output_tokens': 0, 'llamadas': 0}
    return {'input_tokens': row['input_tokens'], 'output_tokens': row['output_tokens'],
            'llamadas': row['llamadas']}


def purgar_task_events(keep: int = 5000):
    """Acota task_events a las últimas `keep` filas. Retiene 5000 (antes 1000):
    los BLOCKED/ERROR con motivo son el corpus del destilador de lecciones del
    enjambre — purgar corto tiraba el material de aprendizaje. De paso acota
    memoria_uso (misma familia de telemetría, mismo momento de purga)."""
    conn = get_db()
    try:
        conn.execute(
            'DELETE FROM task_events WHERE id NOT IN '
            '(SELECT id FROM task_events ORDER BY id DESC LIMIT ?)',
            (keep,)
        )
        conn.commit()
    finally:
        conn.close()
    _purgar_memoria_uso()


def _purgar_memoria_uso(keep: int = 20000):
    """Techo de memoria_uso (reteniendo lo más nuevo). Techo alto a propósito:
    el historial de uso es la señal de salience del recall. De paso acota
    mailbox_msgs (misma familia)."""
    conn = get_db()
    try:
        conn.execute(
            'DELETE FROM memoria_uso WHERE id NOT IN '
            '(SELECT id FROM memoria_uso ORDER BY id DESC LIMIT ?)',
            (keep,)
        )
        conn.execute(
            'DELETE FROM mailbox_msgs WHERE id NOT IN '
            '(SELECT id FROM mailbox_msgs ORDER BY id DESC LIMIT 5000)'
        )
        conn.commit()
    finally:
        conn.close()


def registrar_uso_memorias(project_id: int, terminal_id, slugs: list, resultado: str):
    """Persiste qué memorias leyó un agente al cerrar su paso y con qué
    resultado (done/blocked/error). Defensivo: jamás rompe el flujo del
    sentinel; slugs vacíos = no-op."""
    slugs = [s for s in (slugs or []) if isinstance(s, str) and s.strip()][:20]
    if not slugs:
        return
    conn = get_db()
    try:
        ahora = datetime.now().isoformat()
        conn.executemany(
            'INSERT INTO memoria_uso (project_id, terminal_id, slug, resultado, timestamp) '
            'VALUES (?, ?, ?, ?, ?)',
            [(project_id, terminal_id, s.strip(), str(resultado or ''), ahora) for s in slugs]
        )
        conn.commit()
    finally:
        conn.close()


def registrar_mensaje_mailbox(project_id: int, de: str, para: str, msg: str,
                              terminal_id=None, clase: str = 'normal') -> int:
    """Persiste un mensaje del mailbox con estado. terminal_id = destino
    resuelto (None si ambiguo/broadcast → queda 'sin_destino', nadie lo
    entrega pero el historial existe). `clase` (ask/handoff/normal) decide si
    amerita despertar al destinatario — ver mailbox.clase_de_mensaje."""
    estado = 'pendiente' if terminal_id is not None else 'sin_destino'
    conn = get_db()
    try:
        cur = conn.execute(
            'INSERT INTO mailbox_msgs (project_id, de, para, terminal_id, msg, estado, timestamp, clase) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (project_id, de, para, terminal_id, msg, estado,
             datetime.now().isoformat(), clase or 'normal'))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def mensaje_ya_registrado(project_id: int, de: str, para: str, msg: str) -> bool:
    """¿Esta línea EXACTA del mailbox ya entró alguna vez (en cualquier estado)?

    Es el candado contra la re-entrega del historial. El watcher se guiaba solo
    por un offset en bytes, y el 2026-07-25 ese offset quedó desfasado tras un
    reinicio: re-registró ~108 líneas viejas como mensajes nuevos y las resolvió
    contra las terminales de HOY, así que mensajes de sagas del 21-22 de julio
    aterrizaron en el inbox de un agente que reusaba el nombre de julio.

    Con esto el offset pasa a ser una OPTIMIZACIÓN (no releer lo mismo) y la
    corrección la da la identidad del mensaje."""
    conn = get_db()
    try:
        fila = conn.execute(
            'SELECT 1 FROM mailbox_msgs WHERE project_id = ? AND de = ? '
            'AND para = ? AND msg = ? LIMIT 1',
            (project_id, de, para, msg)).fetchone()
        return fila is not None
    finally:
        conn.close()


def mensajes_pendientes_mailbox(project_id: int, terminal_id: int = None) -> list:
    """Mensajes con estado pendiente (opcionalmente de UNA terminal destino)."""
    conn = get_db()
    try:
        q = ("SELECT id, de, para, terminal_id, msg, timestamp, clase FROM mailbox_msgs "
             "WHERE project_id = ? AND estado = 'pendiente'")
        args = [project_id]
        if terminal_id is not None:
            q += ' AND terminal_id = ?'
            args.append(terminal_id)
        return [dict(f) for f in conn.execute(q + ' ORDER BY id', args).fetchall()]
    finally:
        conn.close()


def marcar_mensajes_entregados(ids: list):
    if not ids:
        return
    conn = get_db()
    try:
        ahora = datetime.now().isoformat()
        conn.executemany(
            "UPDATE mailbox_msgs SET estado = 'entregado', entregado_ts = ? WHERE id = ?",
            [(ahora, i) for i in ids])
        conn.commit()
    finally:
        conn.close()


def conteo_uso_memorias(resultado: str = None, n: int = 2000,
                        excluir: str = None) -> dict:
    """{slug → veces} sobre las últimas `n` filas de memoria_uso.
    `resultado` filtra a uno solo ('done' = lecturas de pasos exitosos);
    `excluir` descarta uno (p.ej. 'inyectada': inyectar ≠ leer)."""
    conn = get_db()
    try:
        filas = conn.execute(
            'SELECT slug, resultado FROM memoria_uso ORDER BY id DESC LIMIT ?',
            (n,)).fetchall()
    finally:
        conn.close()
    conteo = {}
    for f in filas:
        if resultado and f['resultado'] != resultado:
            continue
        if excluir and f['resultado'] == excluir:
            continue
        conteo[f['slug']] = conteo.get(f['slug'], 0) + 1
    return conteo


def metricas_memoria_uso(dias: int = 7) -> dict:
    """El Altímetro de la memoria: ¿el recall rinde? Sobre los últimos `dias`:
    cuántas memorias se INYECTARON a prompts, cuántas se LEYERON de verdad
    (según el cierre de los agentes) y cuántas aparecieron en pasos done.
    tasa_lectura = de las inyectadas, qué fracción alguien leyó."""
    from datetime import timedelta
    corte = (datetime.now() - timedelta(days=dias)).isoformat()
    conn = get_db()
    try:
        filas = conn.execute(
            'SELECT slug, resultado FROM memoria_uso WHERE timestamp >= ?',
            (corte,)).fetchall()
    finally:
        conn.close()
    iny, leidas, en_done = set(), set(), 0
    n_iny = n_leidas = 0
    for f in filas:
        if f['resultado'] == 'inyectada':
            iny.add(f['slug'])
            n_iny += 1
        else:
            leidas.add(f['slug'])
            n_leidas += 1
            if f['resultado'] == 'done':
                en_done += 1
    return {
        'dias': dias,
        'inyecciones': n_iny,
        'lecturas': n_leidas,
        'lecturas_en_done': en_done,
        'tasa_lectura': (round(len(iny & leidas) / len(iny), 2) if iny else None),
    }
