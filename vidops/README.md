# VidOps Python Package - Overlord System

This directory (`vidops/`) contains the core Python implementation of the refactored VidOps "Overlord System". It replaces the monolithic `workspace.sh` with a modular, database-centric architecture.

## Overview

The Overlord System operates on a job queue model, where different types of workers claim and process jobs, all orchestrated through a PostgreSQL database.

**Key Components:**
-   **`vo_cli.py`**: The new command-line interface (CLI) for interacting with the system.
-   **`vidops/config.py`**: Centralized configuration management.
-   **`vidops/db/`**: Database connection pooling and utilities.
-   **`vidops/models/`**: Data models (dataclasses) representing entities like `Video`, `Job`, `Worker`, `Transcript`.
-   **`vidops/dal/`**: Data Access Layer, providing repository classes for database operations and a `FilesystemCache`.
-   **`vidops/services/`**: The service layer, containing business logic and orchestrating tasks (e.g., `TranscriptionService`, `ClippingService`, `OverlordService`).
-   **`vidops/workers/`**: Worker implementations (e.g., `TranscriptionWorker`, `ClippingWorker`) that claim and execute jobs.

## Quick Start (Using `vo_cli.py`)

1.  **Ensure Database Configuration**: Edit `config.yaml` in the project root with your PostgreSQL credentials.
2.  **Initialize Database Schema**: (Placeholder: Database migration scripts will go here in `vidops/db/migrations`).
3.  **Check Database Connection**:
    ```bash
    python3 vo_cli.py status db
    ```
4.  **Enqueue a Download Job**:
    ```bash
    python3 vo_cli.py download enqueue "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --priority 10
    ```
5.  **Enqueue a Transcription Job**:
    ```bash
    # Ensure the video metadata is in the 'videos' table first (e.g., by running a DownloadWorker)
    python3 vo_cli.py transcribe enqueue "dQw4w9WgXcQ" --model medium --lang en --priority 5
    python3 vo_cli.py transcribe enqueue-pending --model small --limit 5
    ```
6.  **Start a Worker**:
    ```bash
    python3 vo_cli.py worker start transcription
    python3 vo_cli.py worker start clipping
    python3 vo_cli.py worker start analysis
    python3 vo_cli.py worker start diarization
    python3 vo_cli.py worker start stitching
    python3 vo_cli.py worker start subtitle
    ```
7.  **Start the Overlord Service**:
    ```bash
    python3 vo_cli.py overlord start
    ```
8.  **Check Status**:
    ```bash
    python3 vo_cli.py status jobs --job-type transcribe_jobs
    python3 vo_cli.py status workers
    ```
9.  **Enqueue a Clipping Job**:
    ```bash
    python3 vo_cli.py clip enqueue "dQw4w9WgXcQ" --start 60 --end 90 --label "chorus"
    ```
10. **Enqueue an Analysis Job**:
    ```bash
    python3 vo_cli.py analyze enqueue "dQw4w9WgXcQ" --transcript-kind "words_whisper_medium" --model "llama3"
    ```
11. **Enqueue a Diarization Job**:
    ```bash
    python3 vo_cli.py diarize enqueue "dQw4w9WgXcQ" --transcript-kind "words_whisper_medium" --model "resemblyzer"
    ```
12. **Enqueue a Subtitle Download Job**:
    ```bash
    python3 vo_cli.py dl-subs enqueue "dQw4w9WgXcQ" --lang en --format vtt
    ```
13. **Enqueue a Stitching Job**:
    ```bash
    python3 vo_cli.py stitch enqueue "clip1.mp4" "clip2.mp4" --output "stitched_output.mp4"
    ```

## Development

**Dependencies**: Install required Python packages:
```bash
pip install -r requirements.txt
```

**Testing**: Run tests from the project root:
```bash
pytest
```

## Next Steps

-   **Database Migrations**: Implement proper database migration scripts using a tool like `Alembic` or similar.
-   **Error Handling**: Enhance error handling and logging across all components.
-   **Integration**: Replace placeholder logic in services and workers with actual calls to external tools (yt-dlp, faster-whisper, ffmpeg, etc.).
-   **Media Management**: Implement robust media management logic in `FilesystemCache` to sync with central storage.
-   **CLI Commands**: Add remaining CLI commands from the old `workspace.sh` or deprecate them.
-   **Deprecate `workspace.sh`**: Once the new system is mature, the old Bash scripts will be removed.
