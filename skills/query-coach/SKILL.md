# Query Coach

Guides how to answer data questions against the connected Postgres database. Writes SQL when asked, chooses when a chart helps, and renders results so the user can see what the numbers mean.

## When to Use

- The user asks a question that can only be answered by looking at the database (counts, trends, breakdowns, specific records, distributions).
- The user asks to "show", "graph", "chart", "plot", or "visualize" data.
- The user asks what's in the database or what tables exist.

Do not invoke this skill for questions the user is asking about the codebase, workflow, or anything that doesn't require a database lookup.

## Tools

| Tool | Purpose |
|------|---------|
| `get_schema` | Returns tables, columns, types, and foreign keys. Call once per session to learn what exists. |
| `run_query` | Executes a read-only SELECT. Optionally takes a Vega-Lite spec to render a chart. |
| `get_last_result` | Returns the most recent query result. Used by the UI for sync; rarely needed in the agent flow. |

## Process

### Step 1 — Learn the Schema

Call `get_schema()` the first time the user asks a data question in a session. Keep the returned structure in mind for the rest of the conversation — the schema does not change between turns.

If the schema turns out to be too large to reason about in one pass, focus on the tables most likely to be relevant based on the user's question and ask them to clarify if ambiguity remains.

### Step 2 — Write the Query

Write the smallest SELECT that answers the question. Prefer named CTEs (`WITH x AS (...)`) when the query has multiple steps — they are easier for the user to read in the response than nested subqueries.

Always add a `LIMIT` when the result set could be large and the user is exploring rather than asking for a full export. The server caps results at 10,000 rows regardless — going over that just truncates without warning.

Date values return as ISO strings (`"2025-04-20"`). Vega-Lite parses these natively as `temporal`, so no casting is needed on the client.

Quote identifiers with double quotes when they contain mixed case or reserved words (`"User"`, `"createdAt"`). Postgres folds unquoted identifiers to lowercase.

### Step 3 — Always Pass a Vega-Lite Spec When the Result Has Shape

Any result with more than one row and at least one numeric or temporal column should be accompanied by a chart. Default to visualizing. The user can see the table too — the chart is what makes the *shape* immediately legible, which is the entire reason they asked.

**Only skip the chart when:**

- The result is a single row or single scalar (just answer in prose).
- Every column is free-text (names, descriptions, IDs) with no numeric or date dimension.
- The user explicitly asked for a raw list ("list the records", "show me the rows").

**Match the result shape to the chart type:**

| Shape of result | Chart type | Mark |
|-----------------|-----------|------|
| One time column + one or more numeric columns | Line chart | `{"type": "line", "point": true}` |
| One time column + one categorical + one numeric | Multi-series line (color by category) | `{"type": "line", "point": true}` |
| One categorical (< ~30 unique) + one numeric | Bar chart | `"bar"` |
| Two categoricals + one numeric | Heatmap | `"rect"` |
| Two numeric columns (no temporal) | Scatter plot | `"point"` |
| One numeric column only | Histogram | `"bar"` with `"bin": true` on x |
| Stacked/grouped categorical over time | Area or stacked bar | `"area"` or `"bar"` |

### Step 4 — Call `run_query`

Pass the SQL as `sql` and the Vega-Lite spec as `vega_spec`. **Never include a `data` field in the spec** — the UI injects the returned rows automatically. Field names in the spec must exactly match column aliases in your SQL.

### Spec Library — Copy and Adapt

**Time series (line):**
```json
{
  "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
  "mark": {"type": "line", "point": true},
  "encoding": {
    "x": {"field": "month", "type": "temporal", "title": "Month"},
    "y": {"field": "n", "type": "quantitative", "title": "Count"}
  }
}
```

**Multi-series time series (line, color by category):**
```json
{
  "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
  "mark": {"type": "line", "point": true},
  "encoding": {
    "x": {"field": "month", "type": "temporal"},
    "y": {"field": "total", "type": "quantitative"},
    "color": {"field": "region", "type": "nominal"}
  }
}
```

**Categorical breakdown (bar):**
```json
{
  "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
  "mark": "bar",
  "encoding": {
    "x": {"field": "department", "type": "nominal", "sort": "-y"},
    "y": {"field": "total", "type": "quantitative"}
  }
}
```

**Two-dim heatmap:**
```json
{
  "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
  "mark": "rect",
  "encoding": {
    "x": {"field": "hour", "type": "ordinal"},
    "y": {"field": "day_of_week", "type": "ordinal"},
    "color": {"field": "count", "type": "quantitative"}
  }
}
```

**Scatter (correlation):**
```json
{
  "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
  "mark": "point",
  "encoding": {
    "x": {"field": "exposure_hours", "type": "quantitative"},
    "y": {"field": "incident_count", "type": "quantitative"}
  }
}
```

**Histogram (distribution of one numeric):**
```json
{
  "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
  "mark": "bar",
  "encoding": {
    "x": {"field": "duration_min", "type": "quantitative", "bin": true},
    "y": {"aggregate": "count", "type": "quantitative"}
  }
}
```

Adapt the field names to match your SQL aliases. Keep the shape and swap the specifics.

### Step 5 — Summarize in Plain Language

After calling the tool, tell the user what you found in one or two sentences. Point out the notable thing in the data — the peak, the outlier, the trend direction — not just "here are the results." The chart shows the shape; your job is to say what matters.

## Constraints

- **SELECT-only.** The server rejects INSERT, UPDATE, DELETE, DDL, and multi-statement queries. If the user asks to modify data, explain that this tool is read-only.
- **10-second timeout.** Queries that exceed it are killed. If a query might be slow (large aggregate over millions of rows), narrow the time window first or ask for a sampling strategy.
- **10,000 row cap.** Larger results are silently truncated. For anything intended as a full export, aggregate server-side instead of pulling raw rows.
- **Read-only role.** The DB connection has `SELECT` grants only. Any attempt to modify fails at the database level even if it slipped through validation.

## Rules

Never fabricate a query result. If a query fails or returns unexpected data, report what the database actually said rather than guessing.

Never invent column or table names. If the schema doesn't contain what the user is asking about, say so — don't write SQL that references columns you hope exist.

Prefer aggregation over raw row dumps. Users almost never want 10,000 rows in the UI; they want the pattern. Aggregate in SQL (`GROUP BY`, `COUNT`, `SUM`, `AVG`) rather than returning raw and expecting the chart to summarize.

When the user's question is ambiguous (which date column? which status values count as "active"?), ask before querying. A wrong answer is worse than a clarifying question.
