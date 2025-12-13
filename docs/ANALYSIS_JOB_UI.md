# Analysis Job Creation UI System

## Overview

The Analysis Job Creation UI system allows users to browse available analysis configurations and create analysis jobs directly through the web interface. This system integrates with the existing QuickClip and job queue infrastructure to provide a complete workflow for distributed video analysis.

## Features

### 1. Analysis Configuration Browsing

**Route:** `/analysis/configs`

Lists all available analysis configurations in the database, grouped by analysis type.

- Displays configuration name, version, and default status
- Shows configuration details like number of passes, chunk size
- Provides quick access to view and manage each configuration
- Grouped display for easy navigation

### 2. Configuration Detail View

**Route:** `/analysis/config/<config_id>`

View detailed information about a specific analysis configuration and create jobs.

Features:
- Display full configuration metadata
- Show analysis parameters (passes, chunk size, targets, capabilities)
- Form to select videos and clips for analysis
- Automatic session population based on selected video
- Real-time job creation with status feedback

### 3. Job Creation Service

**Route:** `/analysis/job/create` (POST)

Backend service that enqueues analysis jobs into the job queue.

Parameters:
- `config_id`: UUID of the analysis configuration
- `ytid`: YouTube video ID
- `session_id` (optional): QuickClip session ID for clip-based analysis

Creates `analysis-distributed` jobs with the following config structure:
```json
{
  "ytid": "video_id",
  "config_id": "config_uuid",
  "transcript_kind": "words_whisper_base",
  "session_id": "optional_session_id"
}
```

### 4. Job Status Monitoring

**Route:** `/analysis/job/<job_id>`

Display job status, progress, and results.

Features:
- Job metadata (status, priority, creation time)
- Worker assignment and timing information
- Video information panel
- Configuration reference
- Results display (when completed)
- Auto-refresh for running jobs

### 5. Results API

**Route:** `/analysis/job/<job_id>/results` (JSON)

Retrieve job results as JSON for programmatic access.

Response structure:
```json
{
  "job_id": "job_xxx",
  "status": "completed",
  "created_at": "2025-12-12T20:41:00",
  "completed_at": "2025-12-12T20:45:30",
  "ytid": "video_id",
  "config": {...},
  "result": {...},
  "analysis_data": {...}
}
```

## Architecture

### Database Integration

- **analysis_configs table**: Stores configuration definitions with JSON config data
- **jobs table**: Stores analysis-distributed jobs with references to configs
- **quickclip_sessions table**: Provides video and clip selection
- **videos table**: Stores video metadata for display

### Job Queue Integration

Analysis jobs are created as standard `analysis-distributed` job type entries in the generic job queue. They:
- Start in `PENDING` status
- Are claimed by GenericWorker instances
- Support job dependencies for pipeline workflows
- Store results in the job's result field upon completion

### QuickClip Integration

- Lists all available QuickClip sessions for video selection
- Supports both full-video and clip-specific analysis
- Session-based filtering for targeted analysis

## User Workflow

### Basic Analysis Job Creation

1. Navigate to `/analysis/configs`
2. Browse available configurations by analysis type
3. Click "View & Create Job" on desired configuration
4. In the detail page:
   - Select target video from dropdown
   - (Optional) Select specific QuickClip session for clip-based analysis
   - Click "Create Analysis Job"
5. System enqueues job and displays success message
6. Redirected to Jobs Browser to monitor progress

### Monitoring Job Progress

1. Click on created job from Jobs Browser
2. View real-time status on job detail page
3. Wait for completion (page auto-refreshes while running)
4. View results when job completes
5. Download results as JSON if needed

## Implementation Details

### Templates

1. **analysis_configs.html**
   - Lists all configurations grouped by analysis type
   - Card-based layout with configuration summary
   - Links to detail pages

2. **analysis_config_detail.html**
   - Full configuration metadata display
   - Job creation form with:
     - Video selection dropdown
     - Dynamic session list based on video
     - Submit and cancel buttons
   - Status feedback messages
   - Client-side form handling with fetch API

3. **analysis_job_detail.html**
   - Comprehensive job information display
   - Status badges with color coding
   - Video information section
   - Configuration reference
   - Results display (for completed jobs)
   - Auto-refresh for running jobs
   - Download results functionality

### Web Routes

All routes are defined in `web/web_app.py`:

```python
@app.route('/analysis/configs')
def analysis_configs_list():
    # List configurations

@app.route('/analysis/config/<config_id>')
def analysis_config_detail(config_id):
    # Show config details and job creation form

@app.route('/analysis/job/create', methods=['POST'])
def create_analysis_job():
    # Handle job creation

@app.route('/analysis/job/<job_id>')
def analysis_job_detail(job_id):
    # Show job status and results

@app.route('/analysis/job/<job_id>/results')
def analysis_job_results(job_id):
    # JSON results endpoint
```

### Helper Functions

**_enqueue_analysis_job(config_id, ytid, session_id=None)**

Internal service function that:
1. Retrieves analysis configuration from database
2. Parses configuration JSON
3. Builds job config with all necessary parameters
4. Creates Job instance with `analysis-distributed` type
5. Persists job to database via JobRepository
6. Returns created Job object or None on error

## Testing

Integration tests are provided in `tests/integration/test_analysis_workflow.py` that verify:

1. **Job Creation**: Creating analysis jobs with proper config
2. **Job Retrieval**: Fetching jobs by ID
3. **Job Status Updates**: Updating job status through the workflow
4. **Configuration Retrieval**: Loading available configurations
5. **Video/Session Retrieval**: Getting available videos and sessions

Run tests with:
```bash
python3 tests/integration/test_analysis_workflow.py
```

## Configuration Management

Analysis configurations are stored in the `analysis_configs` table with:
- **id**: UUID identifier
- **name**: Human-readable name
- **analysis_type**: Type of analysis
- **version**: Version number
- **is_default**: Whether this is the default config
- **config_json**: JSON configuration with:
  - passes: Analysis passes to execute
  - chunk_params: Chunking configuration
  - hot_targets: Analysis targets
  - capabilities: Available capabilities
  - transcript_kind: Type of transcript to use

## Error Handling

The system includes comprehensive error handling:
- Database connection errors are caught and logged
- Missing configurations return 404 errors
- Invalid job creation attempts show user-friendly error messages
- All errors are logged with full context for debugging

## Future Enhancements

Potential improvements for future versions:

1. **Batch Job Creation**: Create multiple jobs at once
2. **Job Templates**: Save and reuse job creation configurations
3. **Results Export**: Export results in various formats (CSV, Excel)
4. **Advanced Filtering**: Filter configurations by capabilities or tags
5. **Job Scheduling**: Schedule jobs for future execution
6. **Result Visualization**: Interactive visualization of analysis results
7. **Pipeline Support**: Chain multiple analysis configurations
8. **Performance Analytics**: Track job execution times and resource usage

## Related Documentation

- [QuickClip Browser](QUICKCLIP_WEB_TESTING.md)
- [Jobs Queue System](OVERLORD_ARCHITECTURE.md)
- [CLI Commands](CLI_COMMANDS.md)
