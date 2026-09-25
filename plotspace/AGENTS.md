# plotspace/ — map for agents

FastAPI + uvicorn. Organized by section in `routers/`.

```
plotspace/
├── main.py          # entrypoint: app, startup, mount /static, HTML routes. Command:
│                    #   python3 -m uvicorn plotspace.main:app --host 0.0.0.0 --port 3000
├── core/         # database, events, auth, ssrf, mantenimiento,
│                 # agent_live, agent_watch, dev_detect, fe_watch, mailbox,
│                 # puertos, web_search (Radio's YouTube), stt_proc (STT worker in its own
│                 # process — NEVER load ML models in-proc: the GIL freezes the loop)
│   ├── orq_contexto.py # what the orchestrator sees each turn (terminals + pane, git, guide, workflows…)
│   ├── orq_cli.py   # claude -p headless transport (prompt via stdin, stream-json parser)
│   ├── database.py  # SQLite WAL (DB_PATH → data/jarvis.db), get_db(), init_db(), purgar_task_events()
│   ├── events.py    # EventBroadcaster (WebSocket per project_id), singleton `broadcaster`
│   └── auth.py      # token-gate + host_permitido/origen_permitido (anti rebinding/CSWSH)
├── routers/         # one router per section (15): orchestrator, terminals,
│   │                # projects, projects_files, plugins, voice, workspace,
│   │                # mobile_preview, memory, tasks,
│   │                # review, live, system, cuentas, fs
│   ├── orchestrator.py   # /api/orchestrator/* + workflows (the biggest)
│   ├── terminals.py      # /api/terminals/* + keyword monitor + tmux/PTY
│   ├── projects.py       # /api/projects/*
│   ├── projects_files.py # files tab (read/write/search/upload, with safe-join)
│   └── ...
└── tests/           # pytest (config in pytest.ini) — each test also runs
                     # as a standalone script (__main__ block). `python -m pytest`
                     # or `python3 plotspace/tests/test_<x>.py` (isolated DB in tempfile).
```

## Critical rules (see CLAUDE.md for the detail)
- **tmux/git control:** use `subprocess.run` (synchronous), NOT asyncio (hangs).
  Exception: `_capture_tmux_output()` in terminals.py.
- **Circular import** orchestrator ↔ terminals: resolve with lazy import inside the function.
- **core imports:** `from plotspace.core.database import get_db`, `from plotspace.core.events import broadcaster`.
- **Paths:** always `os.path.join`. The `ANTHROPIC_API_KEY` is excluded from the PTY environment.

## Verification
`python3 -c "import plotspace.main"` + run the suite: `python -m pytest`
(or `for t in plotspace/tests/test_*.py; do python3 "$t"; done`).
