# Hits & Clips Projects (HC) — Quickstart

This repo now contains an initial implementation of the **Hits & Clips Project** system.

What’s implemented in this pass:

- Database schema (migration `009_hits_clips_projects.sql`)
- A minimal executable DAG runner (worker job type `hc_project_run`)
  - `keyword_hit_generator` node
  - `clip_projection` node
- Basic WebUI pages for creating projects, editing nodes, enqueuing runs, and viewing node execution results
- Simple JSON APIs for hits/clips listing and hit state changes

## Apply the migration

Run the SQL migration against your VidOps Postgres DB:

```bash
psql -h <host> -U <user> -d <db> -f vidops/db/migrations/009_hits_clips_projects.sql
```

## WebUI

Start the web server (as you already do for VidOps):

```bash
python3 -m vidops.web.web_app
```

Then visit:

- `http://127.0.0.1:5000/hc/projects`

## Create a project and run it

1. Create a project in the UI.
2. Add a `keyword_hit_generator` node.
3. Add a `clip_projection` node.
4. Click **Enqueue Run**.
5. Make sure a worker is running that can claim `jobs.job_type='hc_project_run'`:

```bash
python3 -m vidops.workers.general
```

## Node config examples

### keyword_hit_generator

```json
{
  "phrases": ["hasan", "destiny", "drama"],
  "words_source": "whisper-turbo",
  "exact": false,
  "limit": 2000,
  "padding_pre": 0.15,
  "padding_post": 0.15,
  "merge_within_sec": 0.5,
  "label": "keywords:drama"
}
```

### clip_projection

```json
{
  "profile_name": "short",
  "profile_json": {"padding_pre": 2.0, "padding_post": 2.0},
  "hit_limit": 1000,
  "quickclip": false
}
```

## APIs

- List hits: `/api/hc/projects/<project_id>/hits`
- Pin/unpin: `/api/hc/hits/<hit_id>/pin`
- Update status: `/api/hc/hits/<hit_id>/status`
- List clips: `/api/hc/projects/<project_id>/clips`

## What’s not implemented yet

This is a foundation pass. The following parts of the spec are intentionally left for the next phase:

- Analysis-derived suggestions → hits (LLM integration + provenance)
- Hybrid/boolean hit logic, overlap/merge policies beyond basic span merge
- Full retention/finalize/cleanup flows
- Export pipelines and on-demand rendering (cut-net integration) from `hc_clips`
- DAG editor UX (graphical) — only basic node upsert is present
