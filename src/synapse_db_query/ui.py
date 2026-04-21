"""UI resource loader for Synapse DB Query.

In development: run `cd ui && npm run dev` for HMR via Vite.
In production: `cd ui && npm run build` produces a single-file HTML bundle
at ui/dist/index.html, which the server reads and serves as a ui:// resource.

Fallback: if no built UI exists (e.g., running from a raw mpak install),
serves a minimal inline HTML that works without Synapse.
"""

from pathlib import Path

_UI_DIR = Path(__file__).resolve().parent.parent.parent / "ui" / "dist"


def load_ui() -> str:
    """Load the built single-file HTML, or fall back to inline HTML."""
    built = _UI_DIR / "index.html"
    if built.exists():
        return built.read_text()
    return FALLBACK_HTML


FALLBACK_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: var(--font-sans, -apple-system, BlinkMacSystemFont, sans-serif);
    background: var(--color-background-primary, #fff);
    color: var(--color-text-primary, #1a1a1a);
    padding: 1.5rem;
  }
  .empty { color: var(--color-text-secondary, #6b7280); font-size: 0.9rem; }
</style>
</head>
<body>
  <div class="empty">
    DB Query UI not built. Run <code>cd ui &amp;&amp; npm install &amp;&amp; npm run build</code>.
  </div>
</body>
</html>
"""
