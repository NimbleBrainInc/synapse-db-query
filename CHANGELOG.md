# Changelog

All notable changes to this bundle. Versions follow the mpak v0.x.y convention
(see `mcp-servers/VERSIONING.md`): minor bumps may include breaking changes
during the unstable channel.

## 0.4.3

### Fixed

- **`database_url` set via Configure now reaches the bundle.** The manifest
  was missing the `mcp_config.env` mapping that bridges the `user_config`
  field to the `DATABASE_URL` env var the Python code reads. Setting the
  URL via the UI was a silent no-op; only the host-env passthrough
  (`allowedEnv: ["DATABASE_URL"]` + a process-env `DATABASE_URL`) worked.
  Configure-UI is now the canonical path.

### Changed (breaking for misconfigured installs)

- **`database_url` is now `required: true` in `user_config`.** Existing
  workspaces with the field unset will fail `prepareServer` at next spawn
  with a clear "missing required configuration" error and the
  `nb config set` hint. Operators who relied solely on the host-env
  passthrough should now set the credential via Configure UI (or the
  CLI). Workspaces that already have the credential saved are unaffected.

## 0.4.0

### Removed (breaking)

- **`get_last_result` tool** — gone from the manifest. Was previously exposed
  for UI sync but inherently unsafe in a long-lived multi-conversation
  subprocess (see "Fixed"). The widget now reads `list_queries` instead, and
  any external client that pinned `get_last_result` should switch to
  `list_queries` (Upjack returns newest-first, so `entries[0]` is what
  `get_last_result` used to return).

### Fixed

- **Cross-conversation result contamination.** `present_result` previously
  cached the most-recent payload in a module-level `_last_result` global. In
  hosts that run one bundle subprocess across many conversations, that cache
  was visible to every caller — User A's chart could land in User B's widget
  via `get_last_result`. The bundle is now stateless across calls; every
  presented result is a fresh `query` entity in the workspace, and the widget
  renders from `list_queries`. Cross-call isolation is at workspace
  granularity, set by the host (`UPJACK_ROOT` / `MPAK_WORKSPACE`).
- **History selection no longer clobbered by new results.** When the user
  manually selected a historical query, the next `data-changed` event used
  to snap the Result tab back to the newest entry. The widget now only
  auto-advances when the user is on the previous head (or hasn't selected
  anything yet).
- **Entity-write failures surface to the agent.** `present_result` no longer
  swallows `_app.create_entity` errors. If the durable write fails, the tool
  returns an error so the agent can react instead of silently dropping the
  user's answer.

### Added

- **Mobile-responsive widget.** Fluid spacing via `clamp()` for page and chart
  padding; intrinsic fixes (`flex-wrap`, `min-width: 0`); single
  `@media (max-width: 640px)` block for touch targets and SQL/copy stack
  reflow on narrow viewports. Settings UI Save/Reset/count row gets
  `flex-wrap` too.
- **`make bundle` target** — refreshes `ui/dist/` and `deps/` in one step,
  matching the pattern in `synapse-astro-editor`. Run after any source change
  before retesting under NimbleBrain's path-install path.
- **`make check` target** — `ruff check` + `pytest` as a single quality gate.
- **`CLAUDE.md`** — bundle-level docs covering architecture, two run modes
  (HMR vs path install), the `deps/` snapshot trap, the after-change
  checklist, and a symptom-to-cause table for silent staleness failures.

## 0.3.0 and earlier

See git history.
