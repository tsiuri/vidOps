# Refactor Plan: The Overlord System

**Objective:** To refactor the `vidops` toolkit from a collection of shell scripts and Python utilities into a robust, database-centric, and distributed system. This document outlines the proposed architecture, components, and a roadmap for this transition.

## 1. Core Principles

*   **Database as the Single Source of Truth:** All state, metadata, and job information will reside in the PostgreSQL database. Local files are considered temporary caches.
*   **Modularity and Decoupling:** Components will be independent, communicating through the database. This allows for individual components to be updated, scaled, or replaced without affecting the entire system.
*   **Portability:** Workers will be designed to run from any machine with access to the database and storage, without relying on a specific local file structure.
*   **Asynchronous and Scalable:** The system will be built around a job queue, allowing for multiple workers to process tasks in parallel.
*   **Clear Separation of Concerns:**
    *   **Orchestration:** Managing the what and when of job execution.
    *   **Execution:** Performing the actual work (transcription, clipping, etc.).
    *   **Data Management:** Handling the storage and retrieval of media files.
    *   **User Interaction:** Providing a clear and consistent command-line interface.

## 2. Proposed Architecture: "The Overlord System"

The refactored system will consist of four main types of components:

1.  **The Database:** The central nervous system. It will contain not just the transcription queue, but also queues for all other tasks, as well as all metadata about videos, clips, and other assets.
2.  **The Overlord (Supervisor):** A long-running process that monitors the state of the database and manages the job queues. It doesn't do any of the actual work, but it can decide to enqueue new jobs based on the state of the system (e.g., "this video was just transcribed, so now it needs to be diarized and analyzed").
3.  **Workers:** A suite of specialized, on-demand processes that perform the actual work. Each worker type will be responsible for a single task (e.g., transcription, diarization). They will pull jobs from the database, perform the task, and report back.
4.  **The CLI (`vo`):** A new, clean command-line interface for users to interact with the system. It will replace `workspace.sh` and will communicate with the system by adding jobs to the database.

![Overlord System Architecture Diagram](https://i.imgur.com/your-diagram-here.png)  <-- *Placeholder for a real diagram*

## 3. Component Breakdown

### 3.1. The Database

*   **Expanded Queues:** Create separate job queues for:
    *   `transcribe_jobs` (exists)
    *   `diarize_jobs`
    *   `analyze_jobs`
    *   `clipping_jobs`
    *   `stitch_jobs`
    *   `media_management_jobs` (for file transfers)
*   **Central Asset Table:** A single `assets` table to track all files (videos, transcripts, clips, etc.), their locations (local path, central storage URL), and their state.
*   **Standardized Job Tables:** All job tables will share a common schema: `job_id`, `status`, `priority`, `created_at`, `claimed_by`, `completed_at`, etc.
*   Include identities per computer in the work queueing system.  these will just be simple user-set aliases in a config.

### 3.2. The Overlord (Supervisor)

*   **Technology:** A Python service using a library like `apscheduler` to run tasks on a schedule.
*   **Responsibilities:**
    *   **Job Chaining:** Automatically enqueue follow-up jobs (e.g., when a transcription job finishes, create diarization and analysis jobs).
    *   **Housekeeping:** Periodically clean up old jobs, check for stalled workers, and manage the health of the system.
    *   **Prioritization:** Adjust job priorities based on system load or user requests.
    *   **Resource Aware (Future):** Could be made aware of which workers are running on which machines to better distribute tasks (e.g., send CUDA-heavy jobs to machines with available GPUs).

### 3.3. Workers

Each worker will be a Python script that follows a standard lifecycle:

1.  Start and register itself with the system.
2.  Loop:
    a. Claim a job from its specific queue in the database.
    b. If a job is claimed:
        i.   Download the necessary files from central storage to a local temporary directory.
        ii.  Perform the task.
        iii. Upload the resulting files back to central storage.
        iv.  Update the database with the new asset locations and mark the job as complete.
    c. If no job is claimed, sleep for a period.
3.  Send heartbeats to the database to show it's still alive.

*   **`TranscribeWorker`:** Based on the existing `transcribe_worker_*_db.py` scripts.
*   **`DiarizeWorker`:** For speaker diarization.
*   **`AnalysisWorker`:** For running AI analysis on transcripts.
*   **`ClippingWorker`:** Replaces `clips.sh`. Takes clipping parameters from a `clipping_jobs` table.
*   **`StitchWorker`:** Replaces `stitch_videos_*.sh`. Takes a list of clips to stitch from a `stitch_jobs` table.
*   **`MediaManagerWorker`:** Handles file transfers between local workspaces and central storage.

### 3.4. The Storage Manager

*   **Abstracted Storage:** A Python class that provides a simple API for `get_file(asset_id)` and `put_file(file_path)`.
*   **Backend Support:** It will initially use `rsync` or `scp` for network copies to a central NAS/server, but could be extended to support cloud storage (S3, GCS) in the future.
*   **Local Caching:** It will manage a local cache of files to avoid unnecessary downloads.

### 3.5. The CLI (`vo`)

*   **Technology:** A modern Python CLI using a library like `Typer` or `Click`.
*   **Commands:**
    *   `vo download <url>`: Enqueues a download job.
    *   `vo transcribe <video_id>`: Enqueues a transcription job.
    *   `vo hits <query>`: Queries the database for hits and returns the results.
    *   `vo clip <video_id> --start <ts> --end <ts>`: Enqueues a clipping job.
    *   `vo status`: Shows the status of all queues and workers.
    *   `vo worker start <worker_type>`: Starts a worker process.
*   **No More Shell Scripts:** The CLI will be pure Python, making it more portable and easier to maintain. It will interact with the system by manipulating the database.

## 4. Refactoring Roadmap

This is a large undertaking. We will approach it in phases.

### Phase 1: Solidify the Foundation (The "Databa-sexy" Phase)

1.  **Centralize Configuration:** Create a single `config.yaml` or `settings.toml` for database connections, storage paths, and worker settings.
2.  **Expand Database Schema:** Create the new job queues and the central `assets` table.
3.  **Create the Storage Manager:** Build the Python class for handling file transfers.
4.  **Refactor `download`:** Modify the download process to use the Storage Manager to move completed downloads to central storage and create an entry in the `assets` table.

### Phase 2: The "Worker Revolution"

1.  **Create the `vo` CLI:** Build the new CLI with the `download` and `status` commands.
2.  **Refactor `transcribe`:** Fully integrate the existing DB-aware transcription workers. They should get their source files via the Storage Manager.
3.  **Build a `ClippingWorker`:** Create a new Python-based worker to replace the `clips.sh` functionality.
4.  **Update `vo` CLI:** Add `transcribe` and `clip` commands.

### Phase 3: The "Rise of the Overlord"

1.  **Build the Overlord:** Create the supervisor service. Initially, it will just handle job chaining (transcribe -> analyze).
2.  **Build `AnalysisWorker` and `DiarizeWorker`:** Create these new workers.
3.  **Update `vo` CLI:** Add `analyze` and `diarize` commands.

### Phase 4: The "Final Assembly"

1.  **Build `StitchWorker`:** Create the final worker.
2.  **Flesh out the `vo` CLI:** Add all remaining commands and deprecate `workspace.sh`.
3.  **Documentation and Cleanup:** Write new documentation for the Overlord system and remove all the old scripts.

## 5. Key Design Decisions

*   **Configuration:** We will use a single, version-controllable configuration file (e.g., `config.yaml`) instead of scattered environment variables.
*   **API Contracts:** We will define clear, protobuf-like schemas for the data stored in the JSON columns of the job tables. This will ensure that workers and the CLI can communicate reliably.
*   **Python Everywhere:** We will migrate all shell script logic to Python. This will improve portability, testability, and maintainability.
*   **Dependency Management:** We will use `poetry` or a similar tool to manage Python dependencies for the entire project.

This outline provides a high-level roadmap for the refactoring effort. Each step will require detailed design and implementation, but this structure will help us build a more robust, scalable, and maintainable system.
