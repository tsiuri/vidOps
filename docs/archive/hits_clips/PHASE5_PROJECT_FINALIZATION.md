# Phase 5 — Project Finalization

This phase adds an **explicit, user-driven** way to mark a Hits & Clips project as **finalized** and record **exactly which hit/clip versions** are frozen.

## Design constraints respected

- **No automation**: finalization only happens via explicit API/UI calls.
- **Non-destructive**: no cleanup/GC, no row deletion, no asset deletion.
- **Append-only provenance**: the frozen set is recorded as immutable finalization rows.
- **Auditability**: you can fetch the latest finalization and its artifact list via API.

## Schema

Migration: `vidops/db/migrations/014_hc_project_finalization.sql`

New tables:

- `hc_project_finalizations`
  - One row per finalization event.
  - Stores `finalized_by`, `reason`, and a JSON `snapshot` (policy + counts).

- `hc_finalized_artifacts`
  - The frozen list (one row per artifact version).
  - `artifact_kind` is `hit` or `clip`.
  - Includes the concrete `artifact_id` plus logical id + version.

## Service

`services/hc_finalization.py`:

- `HCProjectFinalizationService.finalize_project(...)`
  - Selects artifacts using a policy (default is conservative):
    - Hits: **pinned only**, `active`, `valid`, latest version per logical id
    - Clips: `valid`, latest version per logical id
  - Creates finalization rows + artifact list
  - Sets `hc_projects.status = 'finalized'`

## Web UI

Project detail page: `/hc/projects/<project_id>`

- New **Finalize Project** form.
- When a project is `finalized`, the **Enqueue Run** action is blocked by default.
  - You can override explicitly by checking **Allow enqueue run anyway**.

## API

- `POST /api/hc/projects/<project_id>/finalize`
  - Body:
    - `actor` (optional)
    - `reason` (optional)
    - `policy` (optional)
    - `force` (optional; allows multiple finalizations)

- `GET /api/hc/projects/<project_id>/finalization`
  - Returns project, latest finalization row, and the frozen artifact list.
