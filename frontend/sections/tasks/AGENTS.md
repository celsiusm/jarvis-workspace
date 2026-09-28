# sections/tasks — «Tareas»: monitor en vivo de agentes

Pestaña `tasks` del dock. Ya NO es un kanban: muestra cada terminal activa del
proyecto (abierta por el usuario o lanzada por Jarvis) con su estado vivo.

- **Global:** `window.JarvisTasks = { init(pid), show(), onProjectChanged(pid), onAgentEvent(data), _pure }`
  (`onTasksUpdate`/`onWorkflowUpdate` son alias de `onAgentEvent`, por compatibilidad).
  `workspace.js` debe llamar `onAgentEvent` en los WS de agentes
  (`agente_termino/espera/trabajando`, `task_event`, `live_update`…): refresca con debounce.
- **Datos:** `GET /api/projects/{id}/agentes` (`plotspace/routers/tasks.py`) →
  `{agentes, orquestaciones, resumen}`. Un estado por agente, prioridad
  `caido > esperando > trabajando > arrancando > termino > quieto`.
- **Poll:** 2s SOLO con la pestaña visible (`JarvisDock.activeTab() === 'tasks'`);
  con la pestaña oculta solo los eventos refrescan, y un agente que ENTRA a
  «esperando» levanta `JarvisDock.notify('tasks', n)`.
- **Render por clave** (grupos y filas persistentes; el tiempo se parchea aparte):
  no re-crear el DOM en cada poll — reiniciaría las animaciones y perdería el foco.
- **Click / Enter** en una fila → `#terminal-card-<id>`: scrollIntoView + clase
  transitoria `tk-foco-card` + foco al textarea de xterm. ↑/↓ navegan filas.
- **Colores:** `--st` por `.tk-st-<estado>`. «esperando» = `--ob-work` (ámbar,
  late y brilla — es el estado que pide acción). En los temas de acento verde
  (`verde/esmeralda/salvia/aurora`, donde `--ob-run` es ámbar) «trabajando» usa el acento.
- **Lógica pura** en `_pure` (UMD) → `__tests__/tasks.test.js` (Node, assert nativo).
- **i18n:** los textos van en español en el HTML (conteos como plantillas
  `"{n} trabajando"`); nombres, tareas y motivos llevan `data-i18n-skip`; el
  título vivo pasa por `JarvisTitulosI18n.mostrar`.
