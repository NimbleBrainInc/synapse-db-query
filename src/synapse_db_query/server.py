"""Synapse DB Query — Postgres query app with persisted history via Upjack.

Tools split by concern:
  - `get_schema`      : introspect the DB (agent-internal)
  - `run_query`       : execute a SELECT, return rows (agent-internal)
  - `present_result`  : commit a row set as the user-visible answer (UI-facing;
                        also persists as a `query` Upjack entity — the entity
                        store IS the durable record, the bundle holds no
                        in-process view state)

Upjack auto-generates `list_queries`, `search_queries`, and `get_query` from
the entity manifest (create/update/delete suppressed — presented results are
immutable snapshots; `present_result` is the sole writer). The widget reads
`list_queries` to render the current view (newest entity = current answer)
and the full history.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row
from upjack.app import UpjackApp
from upjack.server import create_server

from .instructions import read_custom_instructions, write_custom_instructions
from .ui import load_ui

# Load .env from project root or src/ (whichever exists first).
_PKG_DIR = Path(__file__).resolve().parent
for _candidate in (_PKG_DIR.parent.parent / ".env", _PKG_DIR.parent / ".env"):
    if _candidate.exists():
        load_dotenv(_candidate)
        break

_PROJECT_ROOT = _PKG_DIR.parent.parent
_MANIFEST_PATH = _PROJECT_ROOT / "manifest.json"
_WORKSPACE_ROOT = (
    os.environ.get("UPJACK_ROOT") or os.environ.get("MPAK_WORKSPACE") or str(_PROJECT_ROOT / "workspace")
)

# Upjack auto-registers the query entity's CRUD tools (restricted to
# list/search/get via manifest.tools) plus its reverse-index tools.
mcp = create_server(_MANIFEST_PATH, root=_WORKSPACE_ROOT)
_app = UpjackApp.from_manifest(_MANIFEST_PATH, root=_WORKSPACE_ROOT)

# Append app-specific instructions to the ones create_server() set up.
mcp._mcp_server.instructions = (
    (mcp.instructions or "")
    + "\n\nDB Query workflow:\n"
    "  1. get_schema() — learn tables (call once per session).\n"
    "  2. run_query(sql) — probe / inspect / fetch the rows for the answer.\n"
    "     Results come back to you only; the user does not see them.\n"
    "  3. present_result(sql, columns, rows, vega_spec?, question?, summary?) —\n"
    "     the ONLY way to show something to the user. Call exactly once per\n"
    "     user question, with the final answer.\n"
    "  4. list_queries / search_queries / get_query — browse or reuse past\n"
    "     presented results. Useful when the user references an earlier chart.\n"
    "  5. set_custom_instructions(text) — save user conventions (default time\n"
    "     window, schema scope, soft-delete rules, naming quirks) when the user\n"
    "     explicitly asks. Empty clears. Picked up on every turn after saving."
    "\n\nThis bundle is stateless across calls. Every presented result is a "
    "fresh `query` entity; the widget reads `list_queries` to render. Do not "
    "expect the bundle to remember anything between tool calls."
)

# libpq-recognized connection parameters. Anything else (e.g. TablePlus's
# statusColor, enviroment, LabelColor) gets dropped silently so pasted URLs
# from GUI tools just work.
_LIBPQ_PARAMS = frozenset({
    "host", "hostaddr", "port", "dbname", "user", "password", "passfile",
    "channel_binding", "connect_timeout", "client_encoding", "options",
    "application_name", "fallback_application_name", "keepalives",
    "keepalives_idle", "keepalives_interval", "keepalives_count",
    "tcp_user_timeout", "replication", "gssencmode", "sslmode", "requiressl",
    "sslcompression", "sslcert", "sslkey", "sslpassword", "sslrootcert",
    "sslcrl", "sslcrldir", "sslsni", "requirepeer", "ssl_min_protocol_version",
    "ssl_max_protocol_version", "krbsrvname", "gsslib", "service",
    "target_session_attrs", "load_balance_hosts",
})


def _sanitize_database_url(url: str) -> str:
    """Keep only libpq-valid query params; drop GUI-client extras."""
    if not url:
        return url
    parts = urlsplit(url)
    if parts.scheme not in ("postgres", "postgresql"):
        return url
    pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k in _LIBPQ_PARAMS]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs), parts.fragment))


DATABASE_URL = _sanitize_database_url(os.environ.get("DATABASE_URL", ""))
QUERY_TIMEOUT_MS = int(os.environ.get("QUERY_TIMEOUT_MS", "10000"))
MAX_ROWS = int(os.environ.get("MAX_ROWS", "10000"))

# ---------- Helpers ----------

_SELECT_ONLY = re.compile(r"^\s*(with\b[\s\S]+?\bselect|select)\b", re.IGNORECASE)
# Reject statements that carry a second top-level statement. Naive but sufficient
# when paired with a read-only DB role — the role is the real defence.
_MULTI_STATEMENT = re.compile(r";\s*\S")


class QueryError(Exception):
    pass


def _ensure_readonly(sql: str) -> str:
    stripped = sql.strip().rstrip(";")
    if not _SELECT_ONLY.match(stripped):
        raise QueryError("Only SELECT (or WITH ... SELECT) statements are allowed.")
    if _MULTI_STATEMENT.search(stripped):
        raise QueryError("Multiple statements are not allowed.")
    return stripped


def _jsonable(value: Any) -> Any:
    """Convert Postgres/Python values to JSON-safe primitives."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)


def _connect() -> psycopg.Connection:
    if not DATABASE_URL:
        raise QueryError(
            "DATABASE_URL is not set. Provide a Postgres connection string for a "
            "read-only role."
        )
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


# ---------- Tools ----------


_IDENT_SAFE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _quote_ident(name: str) -> str:
    """Return a Postgres-safe identifier: bare if lowercase/underscore only,
    double-quoted otherwise (with embedded quotes escaped)."""
    if _IDENT_SAFE.match(name):
        return name
    return '"' + name.replace('"', '""') + '"'


@mcp.tool()
async def get_schema(schemas: list[str] | None = None) -> dict[str, Any]:
    """Return tables, columns, types, and foreign keys for the connected database.

    Each table and column includes a `sql` field with the Postgres-safe
    identifier (quoted if it contains uppercase characters). Paste these
    directly into queries — they're always safe to use. Do not retype the
    `name` field into SQL for mixed-case identifiers; Postgres will fold
    unquoted names to lowercase and the column won't exist.

    Args:
        schemas: Optional list of schema names to include. Defaults to ['public'].
    """
    target_schemas = schemas or ["public"]
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_schema, table_name, column_name, data_type, is_nullable,
                   column_default, ordinal_position
            FROM information_schema.columns
            WHERE table_schema = ANY(%s)
            ORDER BY table_schema, table_name, ordinal_position
            """,
            (target_schemas,),
        )
        columns = cur.fetchall()

        cur.execute(
            """
            SELECT tc.table_schema, tc.table_name, kcu.column_name,
                   ccu.table_schema AS foreign_schema,
                   ccu.table_name AS foreign_table,
                   ccu.column_name AS foreign_column,
                   tc.constraint_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name = tc.constraint_name
             AND ccu.table_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = ANY(%s)
            """,
            (target_schemas,),
        )
        foreign_keys = cur.fetchall()

    tables: dict[str, dict[str, Any]] = {}
    for col in columns:
        key = f"{col['table_schema']}.{col['table_name']}"
        tables.setdefault(
            key,
            {
                "schema": col["table_schema"],
                "name": col["table_name"],
                "sql": _quote_ident(col["table_name"]),
                "columns": [],
            },
        )
        tables[key]["columns"].append(
            {
                "name": col["column_name"],
                "sql": _quote_ident(col["column_name"]),
                "type": col["data_type"],
                "nullable": col["is_nullable"] == "YES",
                "default": col["column_default"],
            }
        )

    def _fk_ref(schema: str, table: str, column: str) -> str:
        return f"{schema}.{_quote_ident(table)}.{_quote_ident(column)}"

    fks = [
        {
            "from": _fk_ref(fk["table_schema"], fk["table_name"], fk["column_name"]),
            "to": _fk_ref(fk["foreign_schema"], fk["foreign_table"], fk["foreign_column"]),
        }
        for fk in foreign_keys
    ]

    return {"tables": list(tables.values()), "foreign_keys": fks}


@mcp.tool()
async def run_query(
    sql: str,
    row_limit: int | None = None,
) -> dict[str, Any]:
    """Execute a read-only SELECT query. Returns rows to you — NOT visible to the user.

    This is your internal data-access tool. Use it to probe, inspect, sample,
    and fetch the data you need to reason about. The user does not see anything
    that comes back from `run_query`. To display a result to the user, call
    `present_result` with the rows (and optionally a Vega-Lite spec).

    Typical workflow:
      1. get_schema()                       — learn tables
      2. run_query(sample sql)              — probe / inspect (may call multiple times)
      3. run_query(final sql)               — fetch the rows that answer the user
      4. present_result(sql, columns, rows, vega_spec=..., question=...)
                                            — show the user a single coherent answer

    IDENTIFIER QUOTING (Postgres gotcha — read before writing SQL):
      Postgres folds unquoted identifiers to lowercase. Any table or column
      name that contains uppercase characters MUST be double-quoted, or the
      query will fail with 'column "foo" does not exist'. Check get_schema
      output — every column there has a `sql` field with the correctly-quoted
      form ready to paste. Prefer those over retyping.

      Wrong:  SELECT DATE(createdAt) FROM "User"          -- folds to createdat
      Right:  SELECT DATE("createdAt") FROM "User"        -- preserves case

      Be consistent within a single query. If you quote one mixed-case
      identifier, quote all of them.

    DEFAULT TIME WINDOW (critical for time-series questions):
      If the user asks for a time-series without specifying a range, default to
      the **last 30 days**. Add a `WHERE <date_col> >= NOW() - INTERVAL '30 days'`
      (or equivalent) to any query that groups by time. Only query wider windows
      when the user explicitly asks: "all time," "since 2020," "last year,"
      "last 12 months," etc.

    Args:
        sql: A SELECT or WITH...SELECT statement. No DML, no multi-statement.
        row_limit: Max rows to return. Defaults to MAX_ROWS (10,000).

    Returns:
        {sql, columns, rows, row_count, truncated}. Feed these into
        `present_result` when you're ready to show the user.
    """
    limit = min(row_limit or MAX_ROWS, MAX_ROWS)
    stripped = _ensure_readonly(sql)

    with _connect() as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(f"SET LOCAL statement_timeout = {QUERY_TIMEOUT_MS}")
            cur.execute(stripped)
            raw_rows = cur.fetchmany(limit)
            truncated = cur.rowcount > limit if cur.rowcount is not None else False
            columns = [d.name for d in (cur.description or [])]

    rows = [{k: _jsonable(v) for k, v in r.items()} for r in raw_rows]

    return {
        "sql": stripped,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
    }


@mcp.tool()
async def present_result(
    sql: str,
    columns: list[str],
    rows: list[dict[str, Any]],
    vega_spec: dict[str, Any] | None = None,
    question: str | None = None,
    summary: str | None = None,
) -> dict[str, Any]:
    """Display a query result to the user. The ONLY way to update the UI.

    Call this exactly once per user question, with the final answer. The user
    does not see anything from `run_query` — they only see what `present_result`
    commits. Call it after you've done any probes and have the final rows in hand.

    DEFAULT TO VISUALIZING. If the rows have more than one row and at least one
    numeric or temporal column, pass a `vega_spec`. The chart is what makes the
    shape of the data legible — that's why the user asked. Only skip the chart
    for single-scalar results, pure-text lookups, or when the user explicitly
    asked for a raw list.

    Chart-type rules:
      - 1 temporal + 1+ numeric            → line  (mark: {"type": "line", "point": true})
      - 1 temporal + categorical + numeric → multi-series line (color by category)
      - 1 categorical (<~30 unique) + 1 numeric → bar
      - 2 categoricals + 1 numeric         → heatmap (mark: "rect" with color encoding)
      - 2 numerics, no temporal            → scatter (mark: "point")
      - 1 numeric only                     → histogram (mark: "bar" with "bin": true on x)

    Spec requirements:
      - Include `$schema`, `mark`, and `encoding` at minimum.
      - Field names in `encoding` MUST match keys in the `rows` dicts.
      - DO NOT include a `data` field — the UI injects the rows.

    Example for monthly time-series:
      {"$schema": "https://vega.github.io/schema/vega-lite/v5.json",
       "mark": {"type": "line", "point": true},
       "encoding": {"x": {"field": "month", "type": "temporal"},
                    "y": {"field": "n", "type": "quantitative"}}}

    Args:
        sql: The SQL that produced these rows. Shown in the UI's "query details"
            panel so the user can see exactly what ran. If synthesized from
            multiple queries, pass the most representative one.
        columns: Ordered column names, must match the keys in `rows`.
        rows: The rows to show, each a dict of {column_name: value}. These come
            from `run_query`'s response verbatim — don't reshape.
        vega_spec: A Vega-Lite v5 spec. Strongly preferred whenever the result
            has a visualizable shape. Omit only for scalar results or raw lists.
        question: The user's original natural-language question. ALWAYS pass
            this. Use their own words — don't paraphrase.
        summary: Optional one-line takeaway the agent wants surfaced in the UI
            (in addition to the fuller answer it writes in chat). Keep short.
    """
    payload = {
        "sql": sql,
        "question": question,
        "summary": summary,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": False,
        "vega_spec": vega_spec,
    }

    # The entity store is the durable record. The widget reads list_queries
    # (newest first) to render — there's no separate in-process cache.
    entity = _app.create_entity("query", payload)
    return {**payload, "id": entity.get("id"), "created_at": entity.get("created_at")}


# ---------- UI resource ----------


@mcp.resource("ui://db-query/main")
def query_ui() -> str:
    """The DB Query app UI — renders in the platform sidebar."""
    return load_ui()


# ---------- Custom Instructions (NimbleBrain platform contract) ----------


@mcp.resource("app://instructions", mime_type="text/markdown")
def db_query_custom_instructions() -> str:
    """Per-bundle custom instructions for the NimbleBrain platform.

    NimbleBrain reads `app://instructions` from every active bundle on each
    prompt assembly. A non-empty body lands inside `<app-custom-instructions>`
    containment in the system prompt; empty omits the block. Storage,
    validation, and the editor UI all live in this bundle.
    """
    return read_custom_instructions(_WORKSPACE_ROOT)


@mcp.tool()
async def set_custom_instructions(text: str) -> dict[str, str]:
    """Save custom instructions for this DB Query bundle.

    Use sparingly — only when the user explicitly asks to save a convention.
    Good fits: default time windows ("our reporting always uses last 90 days"),
    schema scoping ("only query the analytics schema, not public"), soft-delete
    rules ("treat deleted_at IS NOT NULL as removed"), naming quirks ("the
    customer table is actually `accounts`"). Empty text clears the instruction.
    Capped at 8 KiB.

    The saved text is surfaced to the agent on every conversation turn via
    NimbleBrain's `app://instructions` contract, so the agent picks up the
    convention without you having to repeat it.
    """
    try:
        return write_custom_instructions(_WORKSPACE_ROOT, text)
    except ValueError as err:
        # 8 KiB cap → return a structured tool error rather than letting the
        # exception cross the wire as a transport-level failure. The agent
        # sees `isError: true` and can react cleanly.
        return {"status": "error", "error": str(err)}


_SETTINGS_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>DB Query Settings</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: system-ui, -apple-system, sans-serif; padding: 0; color: #1a1a1a; background: transparent; font-size: 14px; line-height: 1.5; }
  h2 { font-size: 16px; font-weight: 600; margin-bottom: 4px; }
  p.lede { font-size: 13px; color: #555; margin-bottom: 12px; }
  .section { padding: 16px; border: 1px solid #e5e5e5; border-radius: 8px; background: #fff; }
  textarea { width: 100%; min-height: 180px; padding: 10px; border: 1px solid #ddd; border-radius: 6px; font-family: ui-monospace, SFMono-Regular, monospace; font-size: 13px; line-height: 1.5; resize: vertical; }
  /* flex-wrap so Save / Reset / count stack gracefully when the iframe is narrow
     instead of overflowing horizontally. */
  .row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 10px; }
  .count { font-size: 12px; color: #777; margin-left: auto; }
  .count.over { color: #b91c1c; font-weight: 500; }
  button { padding: 8px 14px; border: none; border-radius: 6px; font-size: 13px; font-weight: 500; cursor: pointer; background: #2563eb; color: #fff; }
  button:hover { background: #1d4ed8; }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-secondary { background: #f3f4f6; color: #374151; }
  .btn-secondary:hover { background: #e5e7eb; }
  .status { font-size: 12px; padding: 6px 10px; border-radius: 4px; }
  .status.ok { background: #f0fdf4; color: #166534; }
  .status.err { background: #fef2f2; color: #991b1b; }
  .loading { color: #999; font-style: italic; padding: 16px; }
</style>
</head>
<body>
<div id="root" class="loading">Loading…</div>
<script>
(function() {
  const MAX = 8 * 1024;
  let _reqId = 0;
  const _pending = {};

  function callTool(name, args) {
    return new Promise((resolve, reject) => {
      const id = ++_reqId;
      _pending[id] = { resolve, reject };
      window.parent.postMessage(
        { jsonrpc: "2.0", id, method: "tools/call", params: { name, arguments: args || {} } },
        "*",
      );
    });
  }
  function readResource(uri) {
    return new Promise((resolve, reject) => {
      const id = ++_reqId;
      _pending[id] = { resolve, reject };
      window.parent.postMessage(
        { jsonrpc: "2.0", id, method: "resources/read", params: { uri } },
        "*",
      );
    });
  }
  window.addEventListener("message", (e) => {
    const msg = e.data;
    if (!msg || !msg.jsonrpc) return;
    if (msg.id && _pending[msg.id]) {
      const { resolve, reject } = _pending[msg.id];
      delete _pending[msg.id];
      if (msg.error) reject(new Error(msg.error.message || "request failed"));
      else resolve(msg.result);
    }
  });

  // Character count for the UI; the backend enforces an 8 KiB UTF-8 byte
  // cap. For ASCII Markdown the two are equal; for emoji-heavy text the
  // server-side check may reject before the UI count reaches the limit and
  // the error surfaces in the status row.

  let lastSaved = "";

  async function init() {
    const root = document.getElementById("root");
    try {
      const result = await readResource("app://instructions");
      const body = (result && result.contents && result.contents[0] && result.contents[0].text) || "";
      lastSaved = body;
      root.classList.remove("loading");
      root.innerHTML = `
        <div class="section">
          <h2>Custom Instructions</h2>
          <p class="lede">
            Saved guidance the agent picks up on every turn — default time
            windows, schema scoping, soft-delete rules, table naming quirks,
            anything you'd otherwise repeat in chat. Empty clears.
          </p>
          <textarea id="ta" placeholder="e.g. Default time window is the last 90 days. Treat deleted_at IS NOT NULL as removed. The customers table is named 'accounts'.">${
            body.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
          }</textarea>
          <div class="row">
            <button id="save">Save</button>
            <button id="reset" class="btn-secondary">Reset</button>
            <span id="count" class="count">0 / ${MAX.toLocaleString()} characters</span>
          </div>
          <div class="row">
            <span id="status"></span>
          </div>
        </div>
      `;
      const ta = document.getElementById("ta");
      const count = document.getElementById("count");
      const status = document.getElementById("status");
      const saveBtn = document.getElementById("save");
      const resetBtn = document.getElementById("reset");

      function refreshCount() {
        const chars = ta.value.length;
        count.textContent = `${chars.toLocaleString()} / ${MAX.toLocaleString()} characters`;
        count.classList.toggle("over", chars > MAX);
        saveBtn.disabled = chars > MAX || ta.value === lastSaved;
        resetBtn.disabled = ta.value === lastSaved;
      }
      ta.addEventListener("input", refreshCount);
      refreshCount();

      saveBtn.addEventListener("click", async () => {
        status.className = "status";
        status.textContent = "Saving…";
        saveBtn.disabled = true;
        try {
          // The 8 KiB cap is enforced server-side on UTF-8 bytes, while the
          // UI count guard works in JS code units — non-ASCII (emoji, CJK)
          // can pass the local check and still get rejected. The tool returns
          // {status: "error", error: "..."} for those rather than throwing,
          // so we have to inspect structuredContent before calling it a save.
          const result = await callTool("set_custom_instructions", { text: ta.value });
          let payload = result && result.structuredContent;
          if (!payload) {
            try {
              payload = JSON.parse((result && result.content && result.content[0] && result.content[0].text) || "{}");
            } catch (_) { payload = {}; }
          }
          if (payload && payload.status === "error") {
            status.className = "status err";
            status.textContent = payload.error || "Save rejected";
            refreshCount();
            return;
          }
          lastSaved = ta.value;
          status.className = "status ok";
          status.textContent = payload && payload.status === "cleared" ? "Cleared" : "Saved";
          refreshCount();
          setTimeout(() => { status.textContent = ""; status.className = "status"; }, 1500);
        } catch (err) {
          status.className = "status err";
          status.textContent = String(err.message || err);
          refreshCount();
        }
      });
      resetBtn.addEventListener("click", () => {
        ta.value = lastSaved;
        refreshCount();
        status.textContent = "";
        status.className = "status";
      });
    } catch (err) {
      root.classList.remove("loading");
      root.innerHTML = `<div class="section"><h2>Custom Instructions</h2>
        <p class="lede" style="color:#b91c1c">Failed to load: ${String(err.message || err)}</p></div>`;
    }
  }
  init();
})();
</script>
</body>
</html>
"""


@mcp.resource("ui://db-query/settings")
def db_query_settings_ui() -> str:
    """Custom-instructions editor — rendered at /settings/apps/synapse-db-query.

    Inline HTML for now (no separate ui/settings.html build). Communicates
    with the bundle via the platform's iframe postMessage bridge.
    """
    return _SETTINGS_HTML


# ---------- Entrypoints ----------

# ASGI entrypoint for HTTP deployment
app = mcp.http_app()

# Stdio entrypoint for mpak / Claude Desktop
if __name__ == "__main__":
    print("DB Query MCP App starting in stdio mode…", file=sys.stderr)
    mcp.run()
