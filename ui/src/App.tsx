import { useCallback, useEffect, useMemo, useState } from "react";
import {
  SynapseProvider,
  useCallTool,
  useDataSync,
  useTheme,
} from "@nimblebrain/synapse/react";
import { VegaEmbed } from "react-vega";
import type { TopLevelSpec, Config as VegaLiteConfig } from "vega-lite";
// NimbleBrain iframes run under a strict CSP (no unsafe-eval). Vega's default
// expression compiler uses new Function(), which the CSP blocks. The
// interpreter runs expressions without eval — slower but CSP-safe.
import { expressionInterpreter } from "vega-interpreter";

type Row = Record<string, unknown>;

type QueryResult = {
  sql: string | null;
  question: string | null;
  summary: string | null;
  columns: string[];
  rows: Row[];
  row_count: number;
  truncated: boolean;
  vega_spec: TopLevelSpec | null;
};

const EMPTY_RESULT: QueryResult = {
  sql: null,
  question: null,
  summary: null,
  columns: [],
  rows: [],
  row_count: 0,
  truncated: false,
  vega_spec: null,
};

// Styles consume the full Synapse design-token set injected by the NimbleBrain
// host (same set synapse-collateral uses). Fallbacks are purely for running
// outside a host (Claude Desktop, raw mpak). Anything not covered by Synapse
// tokens (spacing, transitions, max-widths) is defined locally.
const DBQ_CSS = `
.dbq-root {
  max-width: 1100px;
  margin: 0 auto;
  padding: 1.5rem;
  font-family: var(--font-sans, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif);
  font-size: var(--font-text-base-size, 1rem);
  line-height: var(--font-text-base-line-height, 1.5rem);
  background: var(--color-background-primary, #ffffff);
  color: var(--color-text-primary, #1a1a1a);
}
.dbq-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 1rem;
}
.dbq-heading {
  font-family: var(--nb-font-heading, var(--font-sans, sans-serif));
  font-size: var(--font-heading-sm-size, 1.25rem);
  line-height: var(--font-heading-sm-line-height, 1.75rem);
  font-weight: var(--font-weight-semibold, 600);
}
.dbq-meta {
  font-size: var(--font-text-sm-size, 0.875rem);
  line-height: var(--font-text-sm-line-height, 1.25rem);
  color: var(--color-text-secondary, #6b7280);
}

/* Tabs — switch between Result and History views. */
.dbq-tabs {
  display: flex;
  gap: 0.25rem;
  border-bottom: 1px solid var(--color-border-primary, #e5e7eb);
  margin-bottom: 1rem;
}
.dbq-tab {
  padding: 0.5rem 0.9rem;
  background: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  color: var(--color-text-secondary, #6b7280);
  font-family: inherit;
  font-size: var(--font-text-sm-size, 0.875rem);
  line-height: var(--font-text-sm-line-height, 1.25rem);
  font-weight: var(--font-weight-medium, 500);
  cursor: pointer;
}
.dbq-tab:hover { color: var(--color-text-primary, #1a1a1a); }
.dbq-tab.active {
  color: var(--color-text-accent, #6366f1);
  border-bottom-color: var(--color-text-accent, #6366f1);
}
.dbq-tab:focus-visible {
  outline: 2px solid var(--color-ring-primary, #6366f1);
  outline-offset: 2px;
}

/* History search + pager. */
.dbq-history-search {
  width: 100%;
  padding: 0.55rem 0.75rem;
  margin-bottom: 0.75rem;
  border: 1px solid var(--color-border-primary, #e5e7eb);
  border-radius: var(--border-radius-sm, 0.5rem);
  background: var(--color-background-secondary, #f9fafb);
  color: var(--color-text-primary, #1a1a1a);
  font-family: inherit;
  font-size: var(--font-text-sm-size, 0.875rem);
  line-height: var(--font-text-sm-line-height, 1.25rem);
  outline: none;
}
.dbq-history-search:focus {
  border-color: var(--color-text-accent, #6366f1);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--color-ring-primary, #6366f1) 20%, transparent);
}
.dbq-pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.75rem;
  margin-top: 0.75rem;
  padding: 0.5rem 0;
}
.dbq-pager-btn {
  padding: 0.4rem 0.9rem;
  border: 1px solid var(--color-border-primary, #e5e7eb);
  border-radius: var(--border-radius-sm, 0.5rem);
  background: var(--color-background-secondary, #f9fafb);
  color: var(--color-text-primary, #1a1a1a);
  font-family: inherit;
  font-size: var(--font-text-sm-size, 0.875rem);
  line-height: var(--font-text-sm-line-height, 1.25rem);
  font-weight: var(--font-weight-medium, 500);
  cursor: pointer;
}
.dbq-pager-btn:hover:not(:disabled) {
  color: var(--color-text-accent, #6366f1);
  border-color: var(--color-text-accent, #6366f1);
}
.dbq-pager-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.dbq-pager-btn:focus-visible {
  outline: 2px solid var(--color-ring-primary, #6366f1);
  outline-offset: 2px;
}
.dbq-pager-status {
  font-size: var(--font-text-sm-size, 0.875rem);
  color: var(--color-text-secondary, #6b7280);
}

/* History list — flat rows, thin dividers. No outer card. */
.dbq-history-list {
  list-style: none;
  margin: 0;
  padding: 0;
  border-top: 1px solid var(--color-border-primary, #e5e7eb);
}
.dbq-history-item {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  width: 100%;
  padding: 0.75rem 0.25rem;
  background: transparent;
  border: none;
  border-bottom: 1px solid var(--color-border-primary, #e5e7eb);
  color: var(--color-text-primary, #1a1a1a);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
}
.dbq-history-item:focus-visible {
  outline: 2px solid var(--color-ring-primary, #6366f1);
  outline-offset: 2px;
  border-radius: var(--border-radius-xs, 0.25rem);
}
.dbq-history-question {
  font-size: var(--font-text-sm-size, 0.875rem);
  line-height: var(--font-text-sm-line-height, 1.25rem);
  font-weight: var(--font-weight-medium, 500);
  color: var(--color-text-primary, #1a1a1a);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: color 120ms ease;
}
.dbq-history-item:hover .dbq-history-question {
  color: var(--color-text-accent, #6366f1);
}
.dbq-history-meta {
  font-size: var(--font-text-xs-size, 0.75rem);
  line-height: var(--font-text-xs-line-height, 1rem);
  color: var(--color-text-secondary, #6b7280);
}

/* Query details panel — collapsible, surfaces the user's question + SQL. */
.dbq-query-panel {
  border: 1px solid var(--color-border-primary, #e5e7eb);
  border-radius: var(--border-radius-sm, 0.5rem);
  background: var(--color-background-secondary, #f9fafb);
  box-shadow: var(--shadow-sm, 0 1px 2px rgba(0,0,0,0.05));
  margin-bottom: 1rem;
  overflow: hidden;
}
.dbq-query-summary {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  width: 100%;
  padding: 0.6rem 0.9rem;
  background: transparent;
  border: none;
  color: var(--color-text-primary, #1a1a1a);
  font-family: inherit;
  font-size: var(--font-text-sm-size, 0.875rem);
  line-height: var(--font-text-sm-line-height, 1.25rem);
  font-weight: var(--font-weight-medium, 500);
  text-align: left;
  cursor: pointer;
}
.dbq-query-summary:focus-visible {
  outline: 2px solid var(--color-ring-primary, #6366f1);
  outline-offset: -2px;
}
.dbq-caret {
  display: inline-block;
  width: 0.7rem;
  color: var(--color-text-secondary, #6b7280);
  transition: transform 120ms ease;
}
.dbq-caret.open { transform: rotate(90deg); }
.dbq-question-preview {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-text-secondary, #6b7280);
  font-weight: var(--font-weight-normal, 400);
}
.dbq-query-body {
  padding: 0 0.9rem 0.9rem;
  border-top: 1px solid var(--color-border-primary, #e5e7eb);
}
.dbq-question-full {
  padding: 0.75rem 0;
  font-size: var(--font-text-base-size, 1rem);
  line-height: var(--font-text-base-line-height, 1.5rem);
  color: var(--color-text-primary, #1a1a1a);
}
.dbq-sql-wrap {
  position: relative;
  margin-top: 0.25rem;
  border: 1px solid var(--color-border-primary, #e5e7eb);
  border-radius: var(--border-radius-xs, 0.25rem);
  background: var(--color-background-tertiary, var(--color-background-primary, #ffffff));
  overflow: hidden;
}
.dbq-sql-block {
  margin: 0;
  padding: 0.75rem 0.9rem;
  padding-right: 3.5rem;
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: var(--font-text-xs-size, 0.75rem);
  line-height: var(--font-text-xs-line-height, 1rem);
  color: var(--color-text-primary, #1a1a1a);
  white-space: pre-wrap;
  word-break: break-word;
}
.dbq-copy-btn {
  position: absolute;
  top: 0.4rem;
  right: 0.4rem;
  padding: 0.25rem 0.6rem;
  border: 1px solid var(--color-border-primary, #e5e7eb);
  border-radius: var(--border-radius-xs, 0.25rem);
  background: var(--color-background-secondary, #f9fafb);
  color: var(--color-text-secondary, #6b7280);
  font-family: inherit;
  font-size: var(--font-text-xs-size, 0.75rem);
  line-height: var(--font-text-xs-line-height, 1rem);
  font-weight: var(--font-weight-medium, 500);
  cursor: pointer;
}
.dbq-copy-btn:hover {
  color: var(--color-text-accent, #6366f1);
  border-color: var(--color-text-accent, #6366f1);
}
.dbq-copy-btn:focus-visible {
  outline: 2px solid var(--color-ring-primary, #6366f1);
  outline-offset: 2px;
}

.dbq-summary {
  margin-bottom: 1rem;
  padding: 0.6rem 0.9rem;
  border-left: 3px solid var(--color-text-accent, #6366f1);
  background: var(--color-background-secondary, #f9fafb);
  border-radius: var(--border-radius-xs, 0.25rem);
  font-size: var(--font-text-sm-size, 0.875rem);
  line-height: var(--font-text-sm-line-height, 1.25rem);
  color: var(--color-text-primary, #1a1a1a);
}
.dbq-chart {
  padding: 1rem;
  border-radius: var(--border-radius-sm, 0.5rem);
  border: 1px solid var(--color-border-primary, #e5e7eb);
  background: var(--color-background-secondary, #f9fafb);
  box-shadow: var(--shadow-sm, 0 1px 2px rgba(0,0,0,0.05));
  margin-bottom: 1rem;
  overflow: hidden;
  width: 100%;
}
/* react-vega 8 renders into a nested div; make sure it fills our chart box
   so the width:"container" spec setting has something to measure against.
   Do NOT override the canvas/svg sizing — Vega manages its own intrinsic
   size, and fighting it via CSS causes a layout feedback loop on mouse move
   (Vega writes width/height → our !important overrides → Vega's resize
   listener refits → flicker). */
.dbq-chart > div,
.dbq-chart .vega-embed {
  width: 100%;
}
.dbq-table-wrap {
  border-radius: var(--border-radius-sm, 0.5rem);
  border: 1px solid var(--color-border-primary, #e5e7eb);
  box-shadow: var(--shadow-sm, 0 1px 2px rgba(0,0,0,0.05));
  overflow: auto;
  max-height: 60vh;
}
.dbq-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--font-text-sm-size, 0.875rem);
  line-height: var(--font-text-sm-line-height, 1.25rem);
}
.dbq-table th {
  text-align: left;
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid var(--color-border-primary, #e5e7eb);
  background: var(--color-background-secondary, #f9fafb);
  color: var(--color-text-primary, #1a1a1a);
  font-weight: var(--font-weight-semibold, 600);
  position: sticky;
  top: 0;
}
.dbq-table td {
  padding: 0.4rem 0.75rem;
  border-bottom: 1px solid var(--color-border-primary, #e5e7eb);
  color: var(--color-text-primary, #1a1a1a);
  vertical-align: top;
}
.dbq-empty {
  padding: 2rem;
  text-align: center;
  font-size: var(--font-text-sm-size, 0.875rem);
  line-height: var(--font-text-sm-line-height, 1.25rem);
  color: var(--color-text-secondary, #6b7280);
}
`;

let _cssInjected = false;
function injectCss() {
  if (_cssInjected || typeof document === "undefined") return;
  const el = document.createElement("style");
  el.setAttribute("data-synapse-app", "db-query");
  el.textContent = DBQ_CSS;
  document.head.appendChild(el);
  _cssInjected = true;
}

/** Read a CSS variable from `:root` with a fallback. */
function readVar(name: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/** Build a Vega-Lite config that tracks the host theme. Recomputed on mode change. */
function useVegaConfig(): VegaLiteConfig {
  const theme = useTheme();
  return useMemo<VegaLiteConfig>(() => {
    const bg = readVar("--color-background-secondary", "#f9fafb");
    const fg = readVar("--color-text-primary", "#1a1a1a");
    const muted = readVar("--color-text-secondary", "#6b7280");
    const border = readVar("--color-border-primary", "#e5e7eb");
    const accent = readVar("--color-text-accent", "#2563eb");
    return {
      background: bg,
      axis: {
        domainColor: border,
        gridColor: border,
        tickColor: border,
        labelColor: muted,
        titleColor: fg,
      },
      legend: { labelColor: muted, titleColor: fg },
      title: { color: fg },
      mark: { color: accent },
      view: { stroke: "transparent" },
    };
    // theme.mode triggers recompute; readVar reads the live CSS var values.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme.mode]);
}

type HistoryEntry = QueryResult & {
  id: string;
  created_at: string;
};

type View = "result" | "history";
const HISTORY_FETCH_LIMIT = 500;
const HISTORY_PAGE_SIZE = 20;

function DBQueryApp() {
  injectCss();
  const [view, setView] = useState<View>("result");
  const [result, setResult] = useState<QueryResult>(EMPTY_RESULT);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [historySearch, setHistorySearch] = useState("");
  const [historyPage, setHistoryPage] = useState(0);

  const getLastResult = useCallTool<QueryResult>("get_last_result");
  const listQueries = useCallTool<{ entities?: HistoryEntry[] } | HistoryEntry[]>(
    "list_queries",
  );
  const vegaConfig = useVegaConfig();

  const refresh = useCallback(async () => {
    try {
      const res = await getLastResult.call({});
      if (!res.isError && isQueryResult(res.data)) setResult(res.data);
    } catch {
      // non-critical — keep previous result on transport failure
    }
  }, [getLastResult]);

  const refreshHistory = useCallback(async () => {
    try {
      const res = await listQueries.call({ limit: HISTORY_FETCH_LIMIT });
      if (res.isError || !res.data) return;
      const entities = Array.isArray(res.data) ? res.data : (res.data.entities ?? []);
      setHistory(entities.filter(isQueryResult) as HistoryEntry[]);
    } catch {
      // non-critical — history just stays empty
    }
  }, [listQueries]);

  useEffect(() => {
    refresh();
    refreshHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useDataSync(() => {
    refresh();
    refreshHistory();
  });

  // Reset pagination when the search term changes.
  useEffect(() => {
    setHistoryPage(0);
  }, [historySearch]);

  const filteredHistory = useMemo(() => {
    const q = historySearch.trim().toLowerCase();
    if (!q) return history;
    return history.filter((entry) => {
      const haystack = [
        entry.question,
        entry.summary,
        entry.sql,
      ].filter(Boolean).join(" ").toLowerCase();
      return haystack.includes(q);
    });
  }, [history, historySearch]);

  const totalPages = Math.max(1, Math.ceil(filteredHistory.length / HISTORY_PAGE_SIZE));
  const pageItems = filteredHistory.slice(
    historyPage * HISTORY_PAGE_SIZE,
    (historyPage + 1) * HISTORY_PAGE_SIZE,
  );

  const hasResult = result.row_count > 0 || result.sql !== null;
  const hasChart = Boolean(result.vega_spec) && result.rows.length > 0;
  // Inline rows directly into the spec via data.values — robust regardless of
  // whether the agent's spec references `{ name: "table" }` or no data source.
  // Avoids react-vega's named-dataset injection which has edge cases that can
  // surface as Vega's "Infinite extent" warning when the dataset lookup misses.
  const chartSpec = useMemo<TopLevelSpec | null>(() => {
    if (!hasChart || !result.vega_spec) return null;
    return {
      ...result.vega_spec,
      data: { values: result.rows },
      // Fill the container width; keep a sensible default height unless the
      // spec overrides it. `autosize: fit` respects the container while still
      // honoring axis labels and legends.
      width: "container",
      height: (result.vega_spec as { height?: unknown }).height ?? 320,
      autosize: { type: "fit", contains: "padding", resize: true },
      config: { ...vegaConfig, ...(result.vega_spec.config || {}) },
    };
  }, [hasChart, result.vega_spec, result.rows, vegaConfig]);

  function replayFromHistory(entry: HistoryEntry) {
    setResult(entry);
    setDetailsOpen(false);
    setView("result");
  }

  async function copySql() {
    if (!result.sql) return;
    try {
      await navigator.clipboard.writeText(result.sql);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard API blocked — skip silently */
    }
  }

  return (
    <div className="dbq-root">
      <div className="dbq-header">
        <h1 className="dbq-heading">DB Query</h1>
        {view === "result" && result.sql && (
          <div className="dbq-meta">
            {result.row_count} row{result.row_count === 1 ? "" : "s"}
            {result.truncated && " (truncated)"}
          </div>
        )}
        {view === "history" && (
          <div className="dbq-meta">
            {filteredHistory.length} of {history.length}
          </div>
        )}
      </div>

      <nav className="dbq-tabs" aria-label="Views">
        <button
          className={`dbq-tab${view === "result" ? " active" : ""}`}
          onClick={() => setView("result")}
        >
          Result
        </button>
        <button
          className={`dbq-tab${view === "history" ? " active" : ""}`}
          onClick={() => setView("history")}
        >
          History{history.length > 0 ? ` (${history.length})` : ""}
        </button>
      </nav>

      {view === "result" && (
        <>
          {result.sql && (
            <div className="dbq-query-panel">
              <button
                className="dbq-query-summary"
                onClick={() => setDetailsOpen((v) => !v)}
                aria-expanded={detailsOpen}
              >
                <span className={`dbq-caret${detailsOpen ? " open" : ""}`}>▸</span>
                <span className="dbq-question-preview">
                  {result.question ?? "Query details"}
                </span>
              </button>
              {detailsOpen && (
                <div className="dbq-query-body">
                  {result.question && <div className="dbq-question-full">{result.question}</div>}
                  <div className="dbq-sql-wrap">
                    <pre className="dbq-sql-block">{result.sql}</pre>
                    <button className="dbq-copy-btn" onClick={copySql}>
                      {copied ? "Copied" : "Copy"}
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}

          {result.summary && <div className="dbq-summary">{result.summary}</div>}

          {chartSpec && (
            <div className="dbq-chart">
              <VegaEmbed
                spec={chartSpec}
                options={{
                  actions: false,
                  // ast + expr: parse the spec into an AST and evaluate it via the
                  // interpreter instead of Function()-based codegen. Required under
                  // the iframe CSP (no unsafe-eval). Vega-embed only threads `expr`
                  // to the View when `ast: true` is set.
                  ast: true,
                  expr: expressionInterpreter,
                }}
              />
            </div>
          )}

          {hasResult && result.columns.length > 0 && (
            <div className="dbq-table-wrap">
              <table className="dbq-table">
                <thead>
                  <tr>
                    {result.columns.map((col) => (
                      <th key={col}>{humanizeColumn(col)}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.rows.map((row, i) => (
                    <tr key={i}>
                      {result.columns.map((col) => (
                        <td key={col}>{formatCell(row[col])}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {!hasResult && (
            <div className="dbq-empty">
              Ask the agent a question about the database — results appear here.
            </div>
          )}
        </>
      )}

      {view === "history" && (
        <>
          <input
            type="search"
            className="dbq-history-search"
            placeholder="Search history by question, summary, or SQL…"
            value={historySearch}
            onChange={(e) => setHistorySearch(e.target.value)}
          />

          {pageItems.length > 0 ? (
            <ul className="dbq-history-list">
              {pageItems.map((entry) => (
                <li key={entry.id}>
                  <button
                    className="dbq-history-item"
                    onClick={() => replayFromHistory(entry)}
                    title={entry.sql ?? undefined}
                  >
                    <span className="dbq-history-question">
                      {entry.question ?? entry.sql ?? "(untitled)"}
                    </span>
                    <span className="dbq-history-meta">
                      {formatTimestamp(entry.created_at)} · {entry.row_count} row
                      {entry.row_count === 1 ? "" : "s"}
                      {entry.vega_spec ? " · chart" : ""}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <div className="dbq-empty">
              {historySearch
                ? `No queries match "${historySearch}".`
                : "No history yet. Ask the agent a question to get started."}
            </div>
          )}

          {filteredHistory.length > HISTORY_PAGE_SIZE && (
            <div className="dbq-pager">
              <button
                className="dbq-pager-btn"
                onClick={() => setHistoryPage((p) => Math.max(0, p - 1))}
                disabled={historyPage === 0}
              >
                ← Prev
              </button>
              <span className="dbq-pager-status">
                Page {historyPage + 1} of {totalPages}
              </span>
              <button
                className="dbq-pager-btn"
                onClick={() =>
                  setHistoryPage((p) => Math.min(totalPages - 1, p + 1))
                }
                disabled={historyPage >= totalPages - 1}
              >
                Next →
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

/** Short relative-ish timestamp: "5m ago", "2h ago", "Apr 20". */
function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return "";
  const ms = Date.now() - then;
  const mins = Math.floor(ms / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

// Common initialisms worth upper-casing instead of Title-casing.
const INITIALISMS = new Set([
  "id", "ids", "url", "uri", "api", "uuid", "http", "https",
  "json", "jsonb", "xml", "csv", "sql", "db", "ip", "html",
  "utc", "pdf",
]);

/** Turn SQL column aliases into human-readable header text.
 *  `hazard_type` → "Hazard Type", `createdAt` → "Created At",
 *  `user_id` → "User ID", `exposure_count` → "Exposure Count". */
function humanizeColumn(name: string): string {
  return name
    .replace(/([a-z])([A-Z])/g, "$1 $2") // split camelCase
    .replace(/[_\-]+/g, " ") // split snake_case / kebab-case
    .split(/\s+/)
    .filter(Boolean)
    .map((word) => {
      const lower = word.toLowerCase();
      if (INITIALISMS.has(lower)) return lower.toUpperCase();
      return lower.charAt(0).toUpperCase() + lower.slice(1);
    })
    .join(" ");
}

/** Narrow `data` to the shape our UI expects before stuffing it into state. */
function isQueryResult(value: unknown): value is QueryResult {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return Array.isArray(v.columns) && Array.isArray(v.rows);
}

export function App() {
  return (
    <SynapseProvider name="db-query" version="0.1.0">
      <DBQueryApp />
    </SynapseProvider>
  );
}
