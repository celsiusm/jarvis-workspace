"""git corrido por el motor no puede abrir ventanas de login.

QUÉ PASÓ (2026-07-27)
=====================
Al abrir la app de Windows aparecía un diálogo «Connect to GitHub — Sign in».
No lo abría la app: lo abría **git**, llamado por el motor.

`fe_watch` hace `git push origin main` cada vez que detecta un commit
(AUTO_PUSH). El repo es privado por HTTPS, y en Windows el Git Credential
Manager, ante la falta de credenciales, ABRE UNA GUI y se queda esperando. Un
poller de fondo, sin ninguna interacción del usuario, plantando una ventana de
login en la cara.

Y no es solo molesto: `subprocess.run(..., timeout=120)` con un git que espera
input es un proceso colgado dos minutos por cada intento.

LA REGLA
========
Todo git que corra el motor va con el entorno no interactivo. Si faltan
credenciales, que FALLE y se loguee — nunca que pregunte. Quien tenga que
autenticarse lo hace en su terminal, a mano, una vez.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from plotspace.core import gitutil


def test_apaga_el_prompt_de_terminal():
    env = gitutil.entorno_no_interactivo({})
    assert env['GIT_TERMINAL_PROMPT'] == '0'


def test_apaga_la_gui_del_credential_manager():
    # Es la que abría la ventana «Connect to GitHub» en Windows.
    env = gitutil.entorno_no_interactivo({})
    assert env['GCM_INTERACTIVE'] == 'never'


def test_apaga_los_askpass():
    # Sin esto, git delega en un programa gráfico para pedir la contraseña —
    # otra ventana, el mismo problema.
    env = gitutil.entorno_no_interactivo({})
    assert env['GIT_ASKPASS'] == ''
    assert env['SSH_ASKPASS'] == ''


def test_no_pisa_el_resto_del_entorno():
    # El entorno del proceso lleva PATH, HOME y las credenciales que SÍ estén
    # configuradas. Reemplazarlo entero dejaría a git sin encontrarse a sí mismo.
    env = gitutil.entorno_no_interactivo({'PATH': '/usr/bin', 'HOME': '/home/x'})
    assert env['PATH'] == '/usr/bin'
    assert env['HOME'] == '/home/x'


def test_no_muta_el_entorno_que_le_pasan():
    # Devolver una copia: mutar os.environ le cambiaría el entorno a TODO el
    # proceso, incluidas las terminales de los agentes.
    base = {'PATH': '/usr/bin'}
    gitutil.entorno_no_interactivo(base)
    assert 'GIT_TERMINAL_PROMPT' not in base


def test_sin_argumento_usa_el_entorno_del_proceso():
    env = gitutil.entorno_no_interactivo()
    assert env['GIT_TERMINAL_PROMPT'] == '0'
    assert 'PATH' in env, 'perdió el PATH: git no se encontraría ni a sí mismo'
