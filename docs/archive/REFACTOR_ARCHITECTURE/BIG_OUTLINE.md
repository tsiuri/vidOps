# The Grand Unification: A Refactoring Proposal for the Overlord System

**Objective:** To refactor the `vidops` toolkit from a collection of shell scripts and Python utilities into a robust, database-centric, and distributed system named the "Overlord System". This document syncretizes previous proposals into a single, comprehensive master plan.

---

## 1. Executive Summary & Core Problems

The current VidOps system is a powerful but complex collection of over 65 scripts and a 1100+ line `workspace.sh` entrypoint. Its organic growth has led to significant challenges:

*   **Dual-Mode Complexity:** Parallel file-based and database queue modes create redundant, branching logic, making maintenance and testing difficult.
*   **Inconsistent Data Authority:** There is no single source of truth. The database, local `.info.json` files, and various TSV files often diverge, requiring manual, multi-step sync scripts to resolve.
*   **Portability Issues:** Workers and scripts often rely on hardcoded or assumed local file paths, hindering distributed operation and scalability.
*   **Configuration Sprawl:** Settings are scattered across environment variables, multiple config files, and command-line flags, leading to confusion and difficult troubleshooting.
*   **Fragmented Tooling:** The monolithic `workspace.sh` is becoming unwieldy and hard to extend.

**This proposal** outlines a **database-first architecture** that addresses these issues by establishing PostgreSQL as the **single source of truth**, introducing a formal service-oriented architecture, and replacing the current script-based system with a modern, modular Python application.

---

## 2. Core Principles

*   **Database as the Single Source of Truth:** All state, metadata, and job information will reside in the PostgreSQL database. Local files are treated as a temporary cache.
*   **Layered Service Architecture:** The system will be composed of distinct layers (CLI, Service, Data Access, Data), each with a specific responsibility.
*   **API-First Design:** Communication between layers will occur through well-defined, type-safe Python APIs.
*   **Stateless, Portable Workers:** Workers will be designed to run from any machine with network access to the database and central storage, claiming jobs from a central queue.
*   **Asynchronous and Scalable:** The system will be built around job queues, allowing for multiple, specialized workers to process tasks in parallel.
*   **Idempotent Operations:** All database write operations will use an "upsert" pattern (INSERT ON CONFLICT DO UPDATE), making them safe to retry.

---

## 3. Proposed Architecture: "The Overlord System"

The refactored system will consist of several communicating layers:

```
┌─────────────────────────────────────────────────────────────┐
│                  CLI LAYER (vo.py / workspace.py)            │
│       Thin command dispatcher - no business logic             │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│              SERVICE LAYER (vidops/services)                 │
│  ┌──────────────┬──────────────┬──────────────┬──────────┐  │
│  │DownloadSvc   │TranscribeSvc │ ClipsSvc     │ Overlord │  │
│  │- enqueue()   │- enqueue()   │- search()    │- orchestrate()│
│  │- sync_meta() │- claim_job() │- extract()   │- cleanup()  │
│  └──────────────┴──────────────┴──────────────┴──────────┘  │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│             DATA ACCESS LAYER (vidops/dal)                   │
│  ┌──────────────┬──────────────┬──────────────┬──────────┐  │
│  │VideoRepo     │TranscriptRepo│ JobRepo      │ AssetRepo│  │
│  │- get()       │- get()       │- claim()     │- register() │
│  │- upsert()    │- upsert()    │- update()    │- get_path() │
│  └──────────────┴──────────────┴──────────────┴──────────┘  │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────┐
│                      DATA LAYER                              │
│  ┌────────────────────────┬──────────────────────────────┐  │
│  │   PostgreSQL           │    Central Storage (NAS/S3)  │  │
│  │   (Source of Truth)    │    (Canonical Media Files)   │  │
│  ├────────────────────────┴──────────────────────────────┤  │
│  │                 Local Filesystem Cache                 │  │
│  │              (Temporary/Cached Media)                  │  │
│  └────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### 3.1. Component Breakdown

#### The Database (Data Layer)
The central nervous system.
*   **Expanded Queues:** Create separate job queues for: `transcribe_jobs` (exists), `diarize_jobs`, `analyze_jobs`, `clipping_jobs`, `stitch_jobs`, and `media_management_jobs`.
*   **Central Asset Table:** A single `assets` table to track all files (videos, transcripts, clips), their canonical location (e.g., `nas://path/to/file`), and metadata.
*   **Standardized Job Tables:** All job tables will share a common schema: `job_id`, `status`, `priority`, `created_at`, `claimed_by`, `completed_at`, etc.
*   **Worker Identity:** A `workers` table to track registered workers, their machine aliases, capabilities, and heartbeats.

#### The Overlord (Service Layer)
A long-running process that acts as the system's brain.
*   **Technology:** A Python service using a library like `apscheduler`.
*   **Responsibilities:**
    *   **Job Chaining:** Automatically enqueues follow-up jobs (e.g., a finished transcription triggers diarization and analysis jobs).
    *   **Housekeeping:** Periodically cleans up old jobs, purges stale worker registrations, and manages system health.
    *   **Prioritization:** Can dynamically adjust job priorities.

#### Workers (Execution Layer)
A suite of specialized, on-demand Python processes.
*   **Lifecycle:**
    1.  Start and register itself with the database.
    2.  Loop: claim a job, download necessary assets from central storage, perform the task, upload resulting assets, update the database, and send heartbeats.
*   **Worker Types:** `TranscribeWorker`, `DiarizeWorker`, `AnalysisWorker`, `ClippingWorker`, `StitchWorker`, `MediaManagerWorker`.

#### Data Access Layer (DAL)
A set of Python classes (Repositories) that abstract all database interactions.
*   **Pattern:** Each major table (`videos`, `jobs`, etc.) gets its own Repository class (e.g., `VideoRepository`).
*   **Responsibility:** Contains all SQL queries and business logic for fetching and modifying data. Services will *only* interact with the database through these repositories.

#### The CLI (`vo`) (CLI Layer)
A new, clean Python-based command-line interface.
*   **Technology:** A modern CLI library like `Typer` or `Click`.
*   **Responsibility:** To translate user commands into service-layer calls. It will contain no business logic itself.
*   **Commands:** `vo download <url>`, `vo transcribe <video_id>`, `vo hits <query>`, `vo status`, `vo worker start <type>`.

---

## 4. Refactoring Roadmap

### Phase 1: Solidify the Foundation
1.  **New Module Structure:** Create the new `vidops/` Python module directory (`vidops/services`, `vidops/dal`, `vidops/cli`).
2.  **Centralize Configuration:** Create a single `config.py` to manage all settings, replacing scattered env vars and files.
3.  **Expand Database Schema:** Create the new job queues, the central `assets` table, and the `workers` table.
4.  **Implement the DAL:** Build the core Repository classes for `videos`, `jobs`, `assets`, and `workers`. Write comprehensive tests for the DAL.
5.  **Create the Storage Manager:** Build a Python class (as part of the DAL or a `storage` service) for abstracting file transfers between local caches and central storage.

### Phase 2: The Worker & CLI Revolution
1.  **Create the `vo` CLI:** Build the new CLI with initial `status` and `worker` commands.
2.  **Unify Workers:** Refactor the existing `transcribe_worker_*_db.py` scripts into a single `TranscriptionWorker` that uses the new DAL and Storage Manager. Eliminate all file-based queue logic.
3.  **Refactor `download`:** Create a `DownloadWorker` and a `vo download` command that correctly registers the downloaded file in the `assets` table and moves it to central storage.
4.  **Update `vo` CLI:** Add the `download` and `transcribe` commands.

### Phase 3: The Rise of the Overlord
1.  **Build the Overlord Service:** Create the supervisor service. Initially, it will only handle basic job chaining (e.g., `transcribe` -> `analyze`).
2.  **Build New Workers:** Create the `AnalysisWorker` and `DiarizeWorker` as new, clean-slate Python workers.
3.  **Update `vo` CLI:** Add `analyze` and `diarize` commands.

### Phase 4: The Final Assembly & Deprecation
1.  **Build Final Workers:** Create the `ClippingWorker` and `StitchWorker`, fully replacing the `clips.sh` and `stitch_videos_*.sh` logic in Python.
2.  **Complete `vo` CLI:** Flesh out all remaining commands (`hits`, `clip`, `stitch`).
3.  **Deprecate `workspace.sh`:** Add a warning to `workspace.sh` pointing users to the new `vo` CLI.
4.  **Documentation:** Write new, comprehensive documentation for the Overlord system.
5.  **Cleanup:** After a transition period, remove all old scripts and the `workspace.sh` file.

---

## 5. Key Design Decisions & Open Questions

### Configuration Management
We will use a single, unified `config.py` module that loads settings with a clear precedence:
1.  Default values in code.
2.  Values from a central `config.yaml` file.
3.  Values from environment variables.
4.  Command-line arguments.

### API Design
All communication between layers will happen through typed Python APIs. This makes the system self-documenting and easier to test. For example:
```python
# In the ClipsService
def search_words(self, query: str) -> List[Hit]:
    # uses WordRepository.search()
    ...

# In the vo CLI
@app.command()
def hits(query: str):
    hits = clips_service.search_words(query)
    # display hits
```

### Testing Strategy
*   **Unit Tests:** Each Repository and Service class will have comprehensive unit tests with mocked dependencies.
*   **Integration Tests:** We will have a suite of tests that run against a temporary, dedicated test database to verify interactions between services and the DAL.
*   **End-to-End Tests:** The `vo` CLI will be tested using a CLI testing library to simulate user workflows from command to completion.

### Open Questions for User Input
1.  **Central Media Storage:** What is the canonical path for the central media repository? (e.g., a specific path on a NAS like `/mnt/video_archive`).
2.  **Media Compression:** Should we implement media compression for long-term storage? If so, should it be on ingest (slower) or as a background job?
3.  **Offline Mode:** What is the desired behavior when the database is unavailable? Should operations fail gracefully, or should they queue locally and sync later?
4.  **Initial Focus:** Which workflow is the highest priority to migrate after `transcribe`? (`clips`, `diarize`, etc.)

This unified plan provides a clear, phased roadmap to transform the Vidops toolkit into a scalable, maintainable, and robust system.
