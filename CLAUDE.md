# synapse-db-query

Natural-language Postgres query app. Python MCP server + React/Vite widget,
distributed as an MCPB bundle. Built on Upjack (entity persistence) and
Synapse (host bridge / theme tokens).

## Architecture

```
synapse-db-query/
├── src/synapse_db_query/      Python MCP server (FastMCP + Upjack)
│   ├── server.py              get_schema, run_query, present_result, list_queries…
│   ├── instructions.py        Custom-instructions storage (workspace file)
│   └── ui.py                  Loads ui/dist/index.html into the ui:// resource
├── ui/                        React + Vite widget
│   ├── src/App.tsx            Result + History tabs, Vega chart rendering
│   └── dist/index.html        Built single-file bundle (vite-plugin-singlefile)
├── deps/                      Snapshot of third-party + (after `make bundle`)
│                              the synapse_db_query package itself
├── workspace/                 Runtime entity store (created on first run)
└── manifest.json              MCPB manifest (tools, UI placements, MTF perms)
```

**Statelessness contract:** the server holds no in-process memory across calls.
`present_result` writes a `query` Upjack entity; the widget reads `list_queries`
(newest-first) to render both the current answer and history. There is no
`get_last_result` tool and no module-level cache — that pattern previously
caused cross-conversation contamination in long-running runtimes. Do not
reintroduce process-scoped state. If a future feature needs continuity, the
caller passes the context (entity id, query reference) explicitly.

## Two run modes

| Mode | What runs | Source of truth | When to use |
|---|---|---|---|
| **Dev (HMR)** | `make dev` → Vite + Python server in stdio | `src/` (Python), `ui/src/` (HMR) | Active iteration on widget or server. |
| **Path install** | NimbleBrain (or mpak) spawns the bundle from this directory with `PYTHONPATH=deps:src`, iframe loads `ui/dist/index.html` | `deps/` if populated (else `src/`) for Python; `ui/dist/` for the widget | Verifying changes under the real platform. |

The two modes diverge in one important way: under path install, **`deps/` masks
`src/`** if the snapshot is present. After `make bundle`, edits to `src/` are
invisible to NimbleBrain until you re-run `make bundle`. Dev mode reads `src/`
directly and never has this problem.

## After you make a change

The following workflow keeps NimbleBrain (and any other path-installer) in sync.
Run it after **any** edit to `src/` or `ui/src/`:

```bash
make check     # ruff + pytest — fast quality gate, run first
make bundle    # builds ui/dist + refreshes deps/ from current src/
```

Then in the runtime:

1. **Restart NimbleBrain** so the bundle subprocess respawns and re-imports the
   refreshed `deps/synapse_db_query/`. Module bytes are frozen at process
   start; there is no hot-reload for a long-lived bundle subprocess.
2. **Hard-reload the iframe** (`Cmd+Shift+R`). `ui/dist/index.html` is read on
   iframe mount, not watched.

If you skip step 1, you'll see stale tool behavior (your Python edit didn't
land). If you skip step 2, you'll see stale UI (your widget edit didn't land).
Both failure modes are silent.

## Symptom-to-cause table

| What you see | Cause | Fix |
|---|---|---|
| Tool returns "method not found" you just added | Bundle subprocess emitted `tools/list` at startup | Restart runtime |
| Tool body unchanged after edit | `deps/synapse_db_query/` is masking `src/`, OR subprocess holds old code | `make bundle` + restart |
| Settings panel shows old sections | `_INLINE_SETTINGS_HTML` is a module-level constant, frozen at process start | Restart runtime |
| Widget UI looks old after `npm run build` | Iframe cached the previous `index.html` | Hard-reload iframe (Cmd+Shift+R) |
| Cross-conversation result contamination | Reintroduced module-level state | Don't. The bundle is stateless by design — see above. |

## Verification

`make check` runs `ruff check src/` and `pytest tests/`. Both must pass before
commit. Type-checking is available via `make typecheck` but not gated in
`check` because `ty` produces noisy false positives on `psycopg`'s
`row_factory=dict_row` overload resolution.

## See also

- `synapse-apps/CLAUDE.md` — cross-app conventions, MCP Apps spec status, host
  portability notes (NimbleBrain vs. ChatGPT).
- `products/upjack/code/CLAUDE.md` — entity model, auto-generated CRUD tools,
  graph traversal.
