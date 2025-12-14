# Diarization References Registry

This documents the new reference registry and manifest flow used when building diarization references.

## What changed
- Reference builds now write a `manifest.json` alongside `reference.json` under `data/references/<name>/`.
- A new table `diarization_references` tracks reference metadata (name, path, model, transcript_kind, clip_count, manifest path, aggregate hash).
- References are registered automatically when built via `DiarizationService` (single or shared reference builders).
- Registry/manifest helpers live in `services/reference_registry.py`.

## Manifest format
- `manifest.json` lists every file under the reference directory (recursive), with `path` (relative), `size`, and `sha256`.
- It also includes an `aggregate_hash`: a SHA256 over sorted `path:sha256` lines, for quick integrity checks.

## Registry schema (db/migrations/008_diarization_references.sql)
- `name` (PK), `path`, `model`, `transcript_kind`, `clip_count`, `manifest_path`, `aggregate_hash`, `created_at`, `updated_at`.
- Indexes on `aggregate_hash` and `model`.

## Code entry points
- `services/reference_registry.py`: build manifest and upsert registry rows (`ReferenceRegistry.register`).
- `services/reference_builder.py`: builds reference clips and writes `reference.json`; diarization service calls the registry after building.
- `services/diarization.py`: when building references (single/shared), registers them with the registry.

## How to use / verify
1. Build a reference via diarization flows (e.g., shared reference build) — the registry entry is created automatically.
2. Check the table:
   ```sql
   SELECT name, model, transcript_kind, clip_count, aggregate_hash FROM diarization_references;
   ```
3. Inspect the manifest:
   ```bash
   jq '.' data/references/<name>/manifest.json
   ```
4. To validate integrity, recompute hashes from `manifest.json` and compare to `aggregate_hash`.

## Notes / Future work
- Speaker labels remain in `reference.json`; per-speaker metadata could be added to the registry if needed.
- If a reference is rebuilt, the registry row is updated with the new manifest/hash.
