# Gemini Starter — Please Read First

Context: Use `SOURCE_OF_TRUTH.md` as the authority. Old plans are in `archived/` and are historical only. Claude owns queue/schema work; do not modify the job system.

Your scope (stay clear of the job schema):
1) Real transcription: replace the mocked section in `vidops/services/transcription.py` with faster-whisper. Produce VTT + words, write transcripts/words to DB with model-specific kinds (e.g., `words_whisper_medium`), and register output assets if applicable. Assume media is available locally; if storage manager hooks land, use them.
2) Smoke test flow: add a simple, reproducible download → transcribe → verify script/test (CLI-based is fine) against a test DB, and note required env/config. Keep it non-destructive and self-contained.

Outputs:
- Log your work in `logs/changelog/2025-11-29_gemini_transcription_test.txt` (summary, decisions, commands run).
- If you need doc changes, update `SOURCE_OF_TRUTH.md` only after completing work, and note the edits clearly.

Rules:
- Do not touch queue/schema or `JobRepository` (Claude’s area). If you hit blockers there, leave a note in your log.
- Coordinate with the storage manager work only by calling any interface Claude lands; do not build a competing one.
