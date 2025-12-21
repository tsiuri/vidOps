# Analysis System Scripts

All new scripts for the analysis system should live under `scripts/analysis_system/`, with subdirectories per module:

- `ingestion/`
- `chunking/`
- `llm/`
- `validation/`
- `aggregation/`
- `persistence/`
- `search_ui/`
- `ops/`
- `quality/`

This keeps the hierarchy aligned with the module docs. Existing root-level scripts have not been moved yet; future additions should be placed here. Submodules can nest children under their parent directory.
