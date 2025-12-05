# vidops/web/app.py

import os
from pathlib import Path
from flask import Flask, render_template, send_file, request, abort
from vidops.dal import QuickClipRepository, VideoRepository
from vidops.config import get_project_root

app = Flask(__name__)

def get_repos():
    """Initialize repositories for database access."""
    return QuickClipRepository(), VideoRepository()


@app.route('/')
def index():
    """Main page - list all videos with QuickClip sessions."""
    qc_repo, video_repo = get_repos()

    # Get all sessions grouped by video
    sessions = qc_repo.list_recent_sessions(limit=1000)

    # Group by ytid
    videos_map = {}
    for session in sessions:
        ytid = session['ytid']
        if ytid not in videos_map:
            videos_map[ytid] = {
                'ytid': ytid,
                'title': session.get('title', ytid),
                'url': session.get('url', ''),
                'sessions': [],
                'total_clips': 0,
            }
        videos_map[ytid]['sessions'].append(session)
        videos_map[ytid]['total_clips'] += session.get('clips_count', 0)

    videos = sorted(videos_map.values(), key=lambda x: x['sessions'][0]['created_at'], reverse=True)

    return render_template('index.html', videos=videos)


@app.route('/video/<ytid>')
def video_detail(ytid):
    """Show all QuickClip sessions for a specific video."""
    qc_repo, video_repo = get_repos()

    # Get video info
    video = video_repo.get(ytid)

    # Get all sessions for this video
    all_sessions = qc_repo.list_recent_sessions(limit=1000)
    sessions = [s for s in all_sessions if s['ytid'] == ytid]

    # Get clips for each session
    for session in sessions:
        session['clips'] = qc_repo.get_session_clips(session['session_id'])

    return render_template('video.html', video=video, ytid=ytid, sessions=sessions)


@app.route('/session/<session_id>')
def session_detail(session_id):
    """Show details of a specific QuickClip session with playable clips."""
    qc_repo, video_repo = get_repos()

    session = qc_repo.get_session(session_id)
    if not session:
        abort(404, "Session not found")

    clips = qc_repo.get_session_clips(session_id)

    # Get video info
    video = video_repo.get(session['ytid'])

    # Find clip files in session directory
    project_root = get_project_root()
    session_dir = project_root / session['session_dir']

    clip_files = []
    if session_dir.exists():
        for ext in ['mp4', 'mkv', 'webm', 'mp3', 'opus', 'mka']:
            clip_files.extend(session_dir.glob(f"*.{ext}"))

    # Match clips with files
    for clip in clips:
        clip['file'] = None
        clip['file_exists'] = False
        # Try to find matching file
        for clip_file in clip_files:
            # Check if filename contains the clip's timestamps
            if f"{clip['start_sec']:.2f}-{clip['end_sec']:.2f}" in clip_file.name:
                clip['file'] = str(clip_file.relative_to(project_root))
                clip['file_exists'] = True
                break

    return render_template('session.html', session=session, clips=clips, video=video)


@app.route('/play/<path:clip_path>')
def play_clip(clip_path):
    """Stream a clip file for playback."""
    project_root = get_project_root()
    file_path = project_root / clip_path

    if not file_path.exists():
        abort(404, "Clip file not found")

    return send_file(file_path)


@app.route('/search')
def search():
    """Search QuickClip sessions."""
    qc_repo, video_repo = get_repos()

    query = request.args.get('q', '').strip()

    if not query:
        return render_template('search.html', results=[], query='')

    results = qc_repo.search_sessions(query)

    # Enrich with clip counts
    for result in results:
        clips = qc_repo.get_session_clips(result['session_id'])
        result['clips'] = clips

    return render_template('search.html', results=results, query=query)


if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=5000)
