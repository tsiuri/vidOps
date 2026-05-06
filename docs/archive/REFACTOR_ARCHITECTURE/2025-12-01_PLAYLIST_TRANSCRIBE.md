# Playlist → Download → Transcribe (DB-Only, No Local Cache)

Use this flow to download a playlist and transcribe every video directly from DB/storage (workers will pull media from storage; no local cache required).

## 1) Enqueue playlist download and run the worker
```bash
cd ~/projects/overlord_test  # your project root (pull/ lives here)
PYTHONPATH=~/tools/vidops \
python ~/tools/vidops/vo_cli.py download enqueue "https://www.youtube.com/playlist?list=PLAYLIST_ID"

# Start the download worker (stop after one job)
PYTHONPATH=~/tools/vidops \
VIDOPS_PROJECT_ROOT=~/projects/overlord_test \
VIDOPS_WORKER_MAX_JOBS=1 \
python ~/tools/vidops/vo_cli.py worker start download
```

## 2) Gather YTIDs for the playlist from the DB
This query selects the most recent completed download job for the playlist URL and extracts the media assets created at or after that job’s start time.
```bash
PLAYLIST_URL="https://www.youtube.com/playlist?list=PLAYLIST_ID"
psql -h 192.168.0.187 -U billie -d transcripts -At -c "
WITH dl AS (
  SELECT job_id, created_at
  FROM jobs
  WHERE job_type='download' AND status='completed' AND media_path = '$PLAYLIST_URL'
  ORDER BY created_at DESC
  LIMIT 1
)
SELECT DISTINCT a.ytid
FROM assets a
JOIN dl ON a.created_at >= dl.created_at
WHERE a.kind='media';
" > ytids_from_playlist.txt
```

## 3) Enqueue transcription for each YTID
```bash
while read -r y; do
  [ -z "$y" ] && continue
  PYTHONPATH=~/tools/vidops \
  python ~/tools/vidops/vo_cli.py transcribe enqueue "$y" --model small --lang en --force
done < ytids_from_playlist.txt
```

## 4) Run the transcription worker
```bash
PYTHONPATH=~/tools/vidops \
VIDOPS_PROJECT_ROOT=~/projects/overlord_test \
python ~/tools/vidops/vo_cli.py worker start transcription
```

## Optional: Live queue watch
```bash
PYTHONPATH=~/tools/vidops \
python scripts/management/watch_jobs.py --interval 1 --limit 10
# q to quit; scroll with arrows/PageUp/PageDown
```

## One-liner: Transcribe everything from the latest download job for a playlist/channel URL
Assumption: you ran `download enqueue <URL>` and want to transcribe exactly what that job pulled. This looks up the most recent completed download job with that `media_path` and enqueues transcribes for its media assets.
```bash
URL="https://www.youtube.com/@AdmiralGorgg"
PYTHONPATH=~/tools/vidops VIDOPS_PROJECT_ROOT=~/projects/overlord_test \
ytids=$(psql -h 192.168.0.187 -U billie -d transcripts -At -c "
WITH dl AS (
  SELECT created_at FROM jobs
  WHERE job_type='download' AND status='completed' AND media_path = '$URL'
  ORDER BY created_at DESC LIMIT 1
)
SELECT DISTINCT ytid FROM assets a JOIN dl ON a.created_at >= dl.created_at WHERE a.kind='media';
") && \
for y in $ytids; do
  python ~/tools/vidops/vo_cli.py transcribe enqueue \"$y\" --model small --lang en --force;
done && \
python ~/tools/vidops/vo_cli.py worker start transcription
```
If multiple downloads for the same URL overlap in time, tighten the query (e.g., filter by a shorter time window or use job_id once jobs carry the playlist/channel ID).
