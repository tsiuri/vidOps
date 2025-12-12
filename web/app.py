# vidops/web/app.py

import os
import json
from pathlib import Path
from uuid import uuid4
from flask import Flask, render_template, render_template_string, send_file, request, abort, redirect, url_for, g, jsonify
from dal import QuickClipRepository, VideoRepository
from dal.analysis_task_repository import AnalysisDatabase
from configuration import get_project_root, load_config

app = Flask(__name__)

# Database configuration for analysis configs
_config = load_config()
_db_config = {
    'host': _config.database.host,
    'port': _config.database.port,
    'dbname': _config.database.name,
    'user': _config.database.user,
    'password': _config.database.password,
}

def get_db():
    """Get database connection for current request."""
    if 'db' not in g:
        g.db = AnalysisDatabase(
            host=_config.database.host,
            port=_config.database.port,
            dbname=_config.database.name,
            user=_config.database.user,
            password=_config.database.password,
        )
        g.db.connect()
    return g.db

@app.teardown_appcontext
def close_db(error):
    """Close database connection at end of request."""
    db = g.pop('db', None)
    if db is not None:
        if error is None and db.conn:
            try:
                db.conn.commit()
            except Exception:
                pass
        db.disconnect()

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


# =====================================================================
# TRANSCRIPT ANALYSIS BROWSER (ported from db-and-analysis)
# =====================================================================

@app.route('/home')
def analysis_home():
    """Home page with search interface for transcript analysis."""
    db = get_db()

    # Default stats if analysis schema isn't present
    stats = {
        'total_videos': 0,
        'total_segments': 0,
        'unique_people': 0,
        'unique_topics': 0
    }
    top_people = []
    top_topics = []

    # Try analysis stats; fall back gracefully to core videos count
    try:
        db.cursor.execute(
            """
            SELECT COUNT(DISTINCT ytid) AS total_videos,
                   COUNT(*) AS total_segments
            FROM analyzed_segments
            """
        )
        video_stats = db.cursor.fetchone()
        stats['total_videos'] = video_stats[0] if video_stats else 0
        stats['total_segments'] = video_stats[1] if video_stats else 0
        # Enrich with rollups if views exist
        try:
            db.cursor.execute("SELECT COUNT(DISTINCT normalized_term) FROM video_terms WHERE term_type='person'")
            stats['unique_people'] = db.cursor.fetchone()[0]
        except Exception:
            pass
        try:
            db.cursor.execute("SELECT COUNT(DISTINCT normalized_term) FROM video_terms WHERE term_type='topic'")
            stats['unique_topics'] = db.cursor.fetchone()[0]
        except Exception:
            pass
        try:
            db.cursor.execute("SELECT * FROM top_people_overall LIMIT 10")
            top_people = [
                {'person': row[0], 'videos_mentioned': row[1], 'total_mentions': row[2]}
                for row in db.cursor.fetchall()
            ]
        except Exception:
            top_people = []
        try:
            db.cursor.execute("SELECT * FROM top_topics_overall LIMIT 10")
            top_topics = [
                {'topic': row[0], 'videos_discussed': row[1], 'total_occurrences': row[2]}
                for row in db.cursor.fetchall()
            ]
        except Exception:
            top_topics = []
    except Exception:
        # If analyzed tables don't exist, at least show core videos count
        try:
            db.cursor.execute("SELECT COUNT(*) FROM videos")
            stats['total_videos'] = db.cursor.fetchone()[0]
        except Exception:
            pass

    return render_template('home.html', stats=stats, top_people=top_people, top_topics=top_topics, config={'DB_NAME': _db_config['dbname']})


@app.route('/api/search/fulltext')
def api_search_fulltext():
    """API endpoint for full-text search."""
    query = request.args.get('q', '')
    limit = min(int(request.args.get('limit', 50)), 200)

    db = get_db()
    results = db.search_segments_fulltext(query, limit)
    return jsonify(results)


@app.route('/api/search/topic')
def api_search_topic():
    """API endpoint for topic search."""
    query = request.args.get('q', '')
    limit = min(int(request.args.get('limit', 50)), 200)

    db = get_db()
    results = db.search_by_topic(query, limit)
    return jsonify(results)


@app.route('/api/search/person')
def api_search_person():
    """API endpoint for person search."""
    query = request.args.get('q', '')
    limit = min(int(request.args.get('limit', 50)), 200)

    db = get_db()
    results = db.search_by_person(query, limit)
    return jsonify(results)


@app.route('/api/browse/recent')
def api_browse_recent():
    """API endpoint for browsing recent analyses."""
    limit = min(int(request.args.get('limit', 20)), 100)

    db = get_db()
    db.cursor.execute("""
        SELECT
            seg.ytid,
            v.title as video_title,
            v.title_date as video_date,
            seg.chunk_id,
            seg.summary,
            seg.sentiment
        FROM analyzed_segments seg
        JOIN videos v USING (ytid)
        ORDER BY seg.analyzed_at DESC, seg.chunk_id
        LIMIT %s
    """, (limit,))

    results = [
        {
            'ytid': row[0],
            'video_title': row[1],
            'video_date': str(row[2]) if row[2] else None,
            'chunk_id': row[3],
            'summary': row[4],
            'sentiment': row[5]
        }
        for row in db.cursor.fetchall()
    ]

    return jsonify(results)


@app.route('/api/browse/videos')
def api_browse_videos():
    """API endpoint to browse recent video-level analyses (TLDR/Summary)."""
    limit = min(int(request.args.get('limit', 20)), 100)

    db = get_db()
    results = []
    try:
        db.cursor.execute(
            """
            SELECT
                va.ytid,
                v.title AS video_title,
                v.title_date AS video_date,
                va.tldr_one_sentence,
                va.summary_paragraph,
                va.dominant_sentiment,
                va.batch_id,
                va.analysis_type,
                va.request_text,
                va.political_overview,
                va.controversies,
                va.issues,
                va.noteworthy_statements,
                va.personal_conflicts,
                va.speaker_overview,
                va.personal_themes
            FROM video_analysis va
            JOIN videos v USING (ytid)
            ORDER BY va.analyzed_at DESC
            LIMIT %s
            """,
            (limit,)
        )
        rows = db.cursor.fetchall()
        for r in rows:
            results.append({
                'ytid': r[0],
                'video_title': r[1],
                'video_date': str(r[2]) if r[2] else None,
                'tldr_one_sentence': r[3] or '',
                'summary_paragraph': r[4] or '',
                'dominant_sentiment': r[5] or None,
                'batch_id': r[6],
                'analysis_type': r[7] or 'normal',
                'request_text': r[8] or 'none',
                'political_overview': r[9] or '',
                'controversies': r[10] or '',
                'issues': r[11] or [],
                'noteworthy_statements': r[12] or [],
                'personal_conflicts': r[13] or [],
                'speaker_overview': r[14] or '',
                'personal_themes': r[15] or []
            })
    except Exception:
        # Fallback for older schema (without political fields)
        db.cursor.execute(
            """
            SELECT
                va.ytid,
                v.title AS video_title,
                v.title_date AS video_date,
                va.tldr_one_sentence,
                va.summary_paragraph,
                va.dominant_sentiment,
                va.batch_id,
                va.analysis_type,
                va.request_text
            FROM video_analysis va
            JOIN videos v USING (ytid)
            ORDER BY va.analyzed_at DESC
            LIMIT %s
            """,
            (limit,)
        )
        rows = db.cursor.fetchall()
        for r in rows:
            results.append({
                'ytid': r[0],
                'video_title': r[1],
                'video_date': str(r[2]) if r[2] else None,
                'tldr_one_sentence': r[3] or '',
                'summary_paragraph': r[4] or '',
                'dominant_sentiment': r[5] or None,
                'batch_id': r[6],
                'analysis_type': r[7] or 'normal',
                'request_text': r[8] or 'none'
            })

    return jsonify(results)


# -----------------------------
# Video detail (page + APIs)
# -----------------------------

@app.route('/video-analysis/<ytid>')
def video_analysis_page(ytid: str):
    return render_template('video_detail.html', ytid=ytid)


@app.route('/api/video/<ytid>/detail')
def api_video_detail(ytid: str):
    db = get_db()
    # Fetch video + analysis
    try:
        db.cursor.execute(
            """
            SELECT
                v.ytid,
                v.title,
                v.title_date,
                va.total_chunks,
                va.dominant_sentiment,
                va.tldr_one_sentence,
                va.summary_paragraph,
                va.batch_id,
                va.analysis_type,
                va.request_text,
                va.political_overview,
                va.controversies,
                va.issues,
                va.noteworthy_statements,
                va.personal_conflicts,
                va.speaker_overview,
                va.personal_themes
            FROM videos v
            JOIN video_analysis va USING (ytid)
            WHERE v.ytid = %s
            """,
            (ytid,)
        )
        row = db.cursor.fetchone()
    except Exception:
        db.cursor.execute(
            """
            SELECT
                v.ytid,
                v.title,
                v.title_date,
                va.total_chunks,
                va.dominant_sentiment,
                va.tldr_one_sentence,
                va.summary_paragraph,
                va.batch_id,
                va.analysis_type,
                va.request_text
            FROM videos v
            JOIN video_analysis va USING (ytid)
            WHERE v.ytid = %s
            """,
            (ytid,)
        )
        row = db.cursor.fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404

    detail = {
        'ytid': row[0],
        'title': row[1],
        'title_date': str(row[2]) if row[2] else None,
        'total_chunks': row[3],
        'dominant_sentiment': row[4],
        'tldr_one_sentence': row[5] or '',
        'summary_paragraph': row[6] or '',
        'batch_id': row[7],
        'analysis_type': row[8] or 'normal',
        'request_text': row[9] or 'none',
    }
    # Add extended fields if present
    if len(row) > 10:
        detail.update({
            'political_overview': row[10] or '',
            'controversies': row[11] or '',
            'issues': row[12] or [],
            'noteworthy_statements': row[13] or [],
            'personal_conflicts': row[14] or [],
            'speaker_overview': row[15] or '',
            'personal_themes': row[16] or []
        })

    # People
    db.cursor.execute(
        """
        SELECT person_name, mention_count
        FROM video_people
        WHERE ytid = %s
        ORDER BY mention_count DESC, person_name ASC
        LIMIT 100
        """,
        (ytid,)
    )
    people = [{ 'person_name': r[0], 'mention_count': r[1] } for r in db.cursor.fetchall()]

    # Topics
    db.cursor.execute(
        """
        SELECT topic, occurrence_count
        FROM video_topics
        WHERE ytid = %s
        ORDER BY occurrence_count DESC, topic ASC
        LIMIT 100
        """,
        (ytid,)
    )
    topics = [{ 'topic': r[0], 'occurrence_count': r[1] } for r in db.cursor.fetchall()]

    detail['people'] = people
    detail['topics'] = topics
    return jsonify(detail)


@app.route('/api/video/<ytid>/segments')
def api_video_segments(ytid: str):
    limit = min(int(request.args.get('limit', 200)), 2000)
    db = get_db()
    db.cursor.execute(
        """
        SELECT id, chunk_id, word_count, sentiment, summary, model
        FROM analyzed_segments
        WHERE ytid = %s
        ORDER BY chunk_id ASC
        LIMIT %s
        """,
        (ytid, limit)
    )
    rows = db.cursor.fetchall()
    out = [
        {
            'id': r[0],
            'chunk_id': r[1],
            'word_count': r[2],
            'sentiment': r[3],
            'summary': r[4],
            'model': r[5]
        }
        for r in rows
    ]
    return jsonify(out)


# -----------------------------
# Videos: pre-existing DB data
# -----------------------------

@app.route('/videos')
def videos_page():
    return render_template('videos.html')


@app.route('/api/videos/list')
def api_videos_list():
    limit = min(int(request.args.get('limit', 50)), 500)
    offset = int(request.args.get('offset', 0))
    order = request.args.get('order') or "title_date DESC NULLS LAST, ytid"
    db = get_db()
    try:
        rows = db.list_videos(limit=limit, offset=offset, order_by=order)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify(rows)


@app.route('/api/videos/search')
def api_videos_search():
    q = request.args.get('q', '')
    limit = min(int(request.args.get('limit', 50)), 500)
    db = get_db()
    try:
        rows = db.search_videos(q, limit)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify(rows)


# -----------------------------
# DBSearch: schema + ad-hoc query
# -----------------------------

@app.route('/dbsearch')
def dbsearch_page():
    return render_template('dbsearch.html')


@app.route('/api/db/schema')
def api_db_schema():
    db = get_db()
    include_views = request.args.get('include_views', 'false').lower() == 'true'
    tables = db.list_tables(include_views=include_views)
    # Attach columns
    for t in tables:
        try:
            t['columns'] = db.get_table_columns(t['name'])
        except Exception:
            t['columns'] = []
    return jsonify({'tables': tables})


@app.route('/api/db/query')
def api_db_query():
    table = request.args.get('table')
    if not table:
        return jsonify({'error': 'Missing table'}), 400
    cols = request.args.get('columns')
    columns = [c for c in cols.split(',')] if cols else None
    search = request.args.get('search')
    try:
        limit = min(int(request.args.get('limit', 50)), 500)
    except Exception:
        limit = 50
    order_by = request.args.get('order')

    db = get_db()
    try:
        rows = db.query_table(table, columns, search, limit=limit, order_by=order_by)
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    return jsonify(rows)


# -----------------------------
# Analyses view (full analysis for a video)
# -----------------------------

ANALYSES_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Analyses | Transcript Browser</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin:0; background:#f5f5f5; color:#222; }
    header { background:#111827; color:#fff; padding:18px 24px; display:flex; justify-content:space-between; align-items:center; }
    .brand { font-weight:700; letter-spacing:0.5px; }
    nav a { color:#cbd5e1; margin-left:16px; text-decoration:none; font-weight:500; }
    nav a:hover { color:#fff; }
    .container { max-width:1200px; margin:20px auto 60px; padding:0 20px; }
    .card { background:#fff; border-radius:10px; box-shadow:0 6px 20px rgba(0,0,0,0.08); padding:20px; margin-bottom:18px; }
    h1 { margin:0 0 12px; font-size:1.6rem; }
    h2 { margin:0 0 10px; font-size:1.2rem; }
    .search-row { display:flex; gap:10px; flex-wrap:wrap; }
    input[type="text"] { flex:1; padding:12px; border:1px solid #d1d5db; border-radius:8px; font-size:1rem; }
    button { padding:12px 18px; border:none; border-radius:8px; background:#2563eb; color:#fff; font-weight:600; cursor:pointer; }
    button:hover { background:#1d4ed8; }
    .grid { display:grid; gap:12px; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }
    .pill { display:inline-block; padding:6px 10px; border-radius:999px; background:#e5e7eb; margin:4px 6px 4px 0; font-size:0.9rem; }
    .section-title { margin:12px 0 8px; font-weight:700; }
    .list { list-style:none; padding:0; margin:0; }
    .list li { padding:6px 0; border-bottom:1px solid #f1f5f9; }
    .muted { color:#6b7280; font-size:0.95rem; }
    .error { color:#b91c1c; margin-top:8px; }
    .tagline { color:#6b7280; margin-bottom:12px; }
    .span-card { background:#f8fafc; border-radius:8px; padding:10px; margin-bottom:8px; }
  </style>
</head>
<body>
  <header>
    <div class="brand">Transcript Analysis</div>
    <nav>
      <a href="/">Home</a>
      <a href="/analyses">Analyses</a>
    </nav>
  </header>
  <div class="container">
    <div class="card">
      <h1>View Analysis</h1>
      <div class="tagline">Enter a YouTube ID to view all stored analysis (video summary, spans, chunks, targets).</div>
      <div class="search-row">
        <input type="text" id="ytid" placeholder="e.g., swCY8u1jHs4">
        <button onclick="loadAnalysis()">Load</button>
      </div>
      <div id="error" class="error"></div>
    </div>

    <div id="content" style="display:none;">
      <div class="card">
        <h2 id="title"></h2>
        <div class="muted" id="meta"></div>
        <p id="tldr"></p>
        <p id="summary"></p>
        <div class="grid" id="video-meta"></div>
      </div>

      <div class="card">
        <h2>Spans</h2>
        <div class="section-title">Conflict/Targets</div>
        <div id="conflict-spans"></div>
        <div class="section-title">Topics</div>
        <div id="topic-spans"></div>
        <div class="section-title">People</div>
        <div id="person-spans"></div>
      </div>

      <div class="card">
        <h2>Chunks</h2>
        <div id="chunks"></div>
      </div>
    </div>
  </div>

  <script>
    function escapeHtml(text) {
      const div = document.createElement('div');
      div.textContent = text || '';
      return div.innerHTML;
    }

    function renderSpans(elemId, spans, labelField) {
      const el = document.getElementById(elemId);
      el.innerHTML = '';
      if (!spans || !spans.length) {
        el.innerHTML = '<div class="muted">None</div>';
        return;
      }
      spans.forEach(s => {
        const div = document.createElement('div');
        div.className = 'span-card';
        const label = escapeHtml(s[labelField] || '');
        const parties = (s.parties || []).join(', ');
        const time = (s.start_sec != null || s.end_sec != null) ? ` [${s.start_sec || ''} - ${s.end_sec || ''}]` : '';
        div.innerHTML = `<strong>${label}</strong>${time}<br><span class="muted">${escapeHtml(parties)}</span><div>${escapeHtml(s.context || '')}</div>`;
        el.appendChild(div);
      });
    }

    function renderChunks(chunks) {
      const el = document.getElementById('chunks');
      el.innerHTML = '';
      if (!chunks || !chunks.length) {
        el.innerHTML = '<div class="muted">No chunks found</div>';
        return;
      }
      chunks.forEach(c => {
        const div = document.createElement('div');
        div.className = 'span-card';
        div.innerHTML = `<strong>Chunk ${c.chunk_id}</strong> - ${escapeHtml(c.sentiment || '')}<br><div>${escapeHtml(c.summary || '')}</div>`;
        el.appendChild(div);
      });
    }

    async function loadAnalysis() {
      const ytid = document.getElementById('ytid').value.trim();
      const errorEl = document.getElementById('error');
      errorEl.textContent = '';
      if (!ytid) {
        errorEl.textContent = 'Please enter a YouTube ID';
        return;
      }
      try {
        const resp = await fetch(`/api/analyses/${encodeURIComponent(ytid)}`);
        if (!resp.ok) {
          const data = await resp.json();
          throw new Error(data.error || 'Not found');
        }
        const data = await resp.json();
        document.getElementById('content').style.display = 'block';
        document.getElementById('title').textContent = data.title || ytid;
        document.getElementById('meta').textContent = `Video ID: ${data.ytid} | Date: ${data.title_date || 'n/a'} | Sentiment: ${data.dominant_sentiment || 'n/a'}`;
        document.getElementById('tldr').textContent = data.tldr_one_sentence || '';
        document.getElementById('summary').textContent = data.summary_paragraph || '';
        renderSpans('conflict-spans', data.conflict_spans, 'description');
        renderSpans('topic-spans', data.topic_spans, 'topic');
        renderSpans('person-spans', data.person_spans, 'person_name');
        renderChunks(data.chunks);
      } catch (err) {
        document.getElementById('content').style.display = 'none';
        errorEl.textContent = err.message;
      }
    }
  </script>
</body>
</html>
"""


@app.route("/analyses")
def analyses_page():
    return render_template_string(ANALYSES_TEMPLATE)


@app.route("/api/analyses/<ytid>")
def api_analysis(ytid: str):
    db = get_db()
    # Video-level
    db.cursor.execute(
        """
        SELECT
            v.ytid,
            v.title,
            v.title_date,
            va.total_chunks,
            va.dominant_sentiment,
            va.tldr_one_sentence,
            va.summary_paragraph,
            va.political_overview,
            va.controversies,
            va.issues,
            va.noteworthy_statements,
            va.personal_conflicts,
            va.speaker_overview,
            va.personal_themes
        FROM videos v
        JOIN video_analysis va USING (ytid)
        WHERE v.ytid=%s
        """,
        (ytid,),
    )
    row = db.cursor.fetchone()
    if not row:
        return jsonify({"error": "Not found"}), 404
    detail = {
        "ytid": row[0],
        "title": row[1],
        "title_date": str(row[2]) if row[2] else None,
        "total_chunks": row[3],
        "dominant_sentiment": row[4],
        "tldr_one_sentence": row[5] or "",
        "summary_paragraph": row[6] or "",
        "political_overview": row[7] or "",
        "controversies": row[8] or "",
        "issues": row[9] or [],
        "noteworthy_statements": row[10] or [],
        "personal_conflicts": row[11] or [],
        "speaker_overview": row[12] or "",
        "personal_themes": row[13] or [],
    }
    # Spans
    db.cursor.execute(
        """
        SELECT id, ytid, parties, description, start_sec, end_sec, chunk_ids, context, sentiment, polarity, source_pass
        FROM target_spans WHERE ytid=%s ORDER BY start_sec NULLS LAST, id
        """,
        (ytid,),
    )
    detail["conflict_spans"] = [dict(row) for row in db.cursor.fetchall()]

    db.cursor.execute(
        """
        SELECT id, ytid, topic, normalized_topic, start_sec, end_sec, chunk_ids, context, sentiment, source_pass
        FROM topic_spans WHERE ytid=%s ORDER BY start_sec NULLS LAST, id
        """,
        (ytid,),
    )
    detail["topic_spans"] = [dict(row) for row in db.cursor.fetchall()]

    db.cursor.execute(
        """
        SELECT id, ytid, person_name, normalized_name, start_sec, end_sec, chunk_ids, context, sentiment, polarity, source_pass
        FROM person_spans WHERE ytid=%s ORDER BY start_sec NULLS LAST, id
        """,
        (ytid,),
    )
    detail["person_spans"] = [dict(row) for row in db.cursor.fetchall()]

    # Chunks (limited)
    db.cursor.execute(
        """
        SELECT chunk_id, sentiment, summary
        FROM analyzed_segments
        WHERE ytid=%s
        ORDER BY chunk_id
        LIMIT 200
        """,
        (ytid,),
    )
    detail["chunks"] = [
        {"chunk_id": r[0], "sentiment": r[1], "summary": r[2]} for r in db.cursor.fetchall()
    ]

    return jsonify(detail)


# =====================================================================
# ANALYSIS CONFIGURATION MANAGEMENT
# =====================================================================

@app.route('/analysis-configs')
def list_analysis_configs():
    """List all analysis configurations."""
    db = get_db()
    configs = db.list_analysis_configs()

    class Obj(dict):
        __getattr__ = dict.get

    # Attach drill_summary for UI
    configs_obj = []
    for c in configs:
        cfg_json = c.get('config_json') or {}
        drills = cfg_json.get('drills') or []
        summary = ""
        if drills:
            dep_count = sum(len(d.get('depends_on') or []) for d in drills)
            summary = f"{len(drills)} drills, {dep_count} deps"
        obj = Obj(c)
        obj.drill_summary = summary
        configs_obj.append(obj)

    return render_template(
        'analysis_configs.html',
        configs=configs_obj,
        db_name=_config.database.name,
    )


@app.route('/analysis-configs/new', methods=['GET', 'POST'])
def new_analysis_config():
    """Create a new analysis configuration."""
    db = get_db()
    error = None

    if request.method == 'POST':
        name = request.form.get('name') or 'New Config'
        analysis_type = request.form.get('analysis_type') or 'normal'
        version = int(request.form.get('version') or 1)
        is_default = bool(request.form.get('is_default'))
        config_json_text = request.form.get('config_json') or '{}'

        try:
            cfg_dict = json.loads(config_json_text)
            # Generate ID if not provided
            if not cfg_dict.get('id'):
                cfg_dict['id'] = str(uuid4())
        except Exception as e:
            error = f"Invalid config JSON: {e}"
            return render_template(
                'analysis_config_editor.html',
                config_id=None,
                name=name,
                analysis_type=analysis_type,
                version=version,
                is_default=is_default,
                max_words=cfg_dict.get('chunk_params', {}).get('max_words', 1000) if cfg_dict else 1000,
                overlap_words=cfg_dict.get('chunk_params', {}).get('overlap_words', 150) if cfg_dict else 150,
                per_speaker_tracks=cfg_dict.get('chunk_params', {}).get('per_speaker_tracks', False) if cfg_dict else False,
                enabled_passes=[p.get('id') for p in (cfg_dict.get('passes', []) if cfg_dict else [])],
                hot_targets=cfg_dict.get('hot_targets', []) if cfg_dict else [],
                strict_pass_validation=cfg_dict.get('strict_pass_validation', False) if cfg_dict else False,
                config_json=config_json_text,
                config_json_parsed=cfg_dict,
                error=error,
            )

        config_id = cfg_dict.get('id')
        db.upsert_analysis_config(config_id, name, analysis_type, version, cfg_dict, is_default=is_default)
        if db.conn:
            db.conn.commit()
        return redirect(url_for('edit_analysis_config', config_id=config_id))

    # GET – show a blank form
    return render_template(
        'analysis_config_editor.html',
        config_id=None,
        name='',
        analysis_type='normal',
        version=1,
        is_default=False,
        max_words=1000,
        overlap_words=150,
        per_speaker_tracks=False,
        enabled_passes=[],
        hot_targets=[],
        strict_pass_validation=False,
        config_json='',
        config_json_parsed={},
        error=error,
    )


@app.route('/analysis-configs/<config_id>', methods=['GET', 'POST'])
def edit_analysis_config(config_id):
    """Edit an analysis configuration."""
    db = get_db()
    row = db.get_analysis_config(config_id)
    if not row:
        return f"No config found with id={config_id}", 404

    cfg = row.get('config_json') or {}

    if request.method == 'POST':
        name = request.form.get('name') or row.get('name') or 'Unnamed'
        analysis_type = request.form.get('analysis_type') or row.get('analysis_type') or 'normal'
        version = int(request.form.get('version') or row.get('version') or 1)
        is_default = bool(request.form.get('is_default'))
        config_json_text = request.form.get('config_json') or '{}'
        error = None

        try:
            cfg_dict = json.loads(config_json_text)
            # Ensure ID is set to the config_id we're editing
            cfg_dict['id'] = config_id
        except Exception as e:
            error = f"Invalid config JSON: {e}"
            return render_template(
                'analysis_config_editor.html',
                config_id=config_id,
                name=name,
                analysis_type=analysis_type,
                version=version,
                is_default=is_default,
                max_words=cfg_dict.get('chunk_params', {}).get('max_words', 1000) if cfg_dict else 1000,
                overlap_words=cfg_dict.get('chunk_params', {}).get('overlap_words', 150) if cfg_dict else 150,
                per_speaker_tracks=cfg_dict.get('chunk_params', {}).get('per_speaker_tracks', False) if cfg_dict else False,
                enabled_passes=[p.get('id') for p in (cfg_dict.get('passes', []) if cfg_dict else [])],
                hot_targets=cfg_dict.get('hot_targets', []) if cfg_dict else [],
                strict_pass_validation=cfg_dict.get('strict_pass_validation', False) if cfg_dict else False,
                config_json=config_json_text,
                config_json_parsed=cfg_dict,
                error=error,
            )

        db.upsert_analysis_config(config_id, name, analysis_type, version, cfg_dict, is_default=is_default)
        if db.conn:
            db.conn.commit()
        row = db.get_analysis_config(config_id)
        cfg = row.get('config_json') or {}

    cfg_json = json.dumps(cfg, indent=2, sort_keys=True)

    # Extract data for form display
    max_words = cfg.get('chunk_params', {}).get('max_words', 1000)
    overlap_words = cfg.get('chunk_params', {}).get('overlap_words', 150)
    per_speaker_tracks = cfg.get('chunk_params', {}).get('per_speaker_tracks', False)
    enabled_passes = [p.get('id') for p in (cfg.get('passes', []) or [])]
    hot_targets = cfg.get('hot_targets', [])
    strict_pass_validation = cfg.get('strict_pass_validation', False)

    return render_template(
        'analysis_config_editor.html',
        config_id=row['id'],
        name=row['name'],
        analysis_type=row['analysis_type'],
        version=row['version'],
        is_default=row['is_default'],
        max_words=max_words,
        overlap_words=overlap_words,
        per_speaker_tracks=per_speaker_tracks,
        enabled_passes=enabled_passes,
        hot_targets=hot_targets,
        strict_pass_validation=strict_pass_validation,
        config_json=cfg_json,
        config_json_parsed=cfg,
        error=None,
    )


@app.route('/analysis-configs/<config_id>/drills', methods=['GET'])
def edit_drills(config_id):
    """HTML page for editing drills belonging to a config."""
    db = get_db()
    row = db.get_analysis_config(config_id)
    if not row:
        return f"No config found with id={config_id}", 404
    cfg = row.get('config_json') or {}
    drills = cfg.get('drills') or []
    output_shapes = cfg.get('output_shapes') or {}
    drills_json = json.dumps(drills, indent=2)
    output_shapes_json = json.dumps(output_shapes, indent=2)
    return render_template(
        'drill_editor.html',
        config_id=config_id,
        config_name=row.get('name') or 'Unnamed',
        drills_json=drills_json,
        output_shapes_json=output_shapes_json,
    )


@app.route('/analysis-configs/<config_id>/categories-pass', methods=['GET', 'POST'])
def edit_categories_pass(config_id):
    """Edit categories_pass configuration for a config."""
    db = get_db()
    row = db.get_analysis_config(config_id)
    if not row:
        return f"No config found with id={config_id}", 404

    cfg = row.get('config_json') or {}
    error = None
    success = False

    if request.method == 'POST':
        try:
            # Parse category map
            category_map_json_text = request.form.get('category_map_json', '{}')
            category_map = json.loads(category_map_json_text) if category_map_json_text else {}

            # Parse category cache
            category_cache_list_text = request.form.get('category_cache_list', '')
            category_cache = [line.strip() for line in category_cache_list_text.split('\n') if line.strip()]

            # Parse filter list
            filter_list_json_text = request.form.get('filter_list_json', '[]')
            filter_list = json.loads(filter_list_json_text) if filter_list_json_text else []

            # Get pass parameters
            category_pass_enabled = bool(request.form.get('category_pass_enabled'))
            category_pass_order = int(request.form.get('category_pass_order', 40))
            category_cache_writeback = bool(request.form.get('category_cache_writeback'))

            # Update config
            cfg['backend_params'] = cfg.get('backend_params', {})
            cfg['backend_params']['category_map'] = category_map
            cfg['backend_params']['category_cache'] = category_cache
            cfg['backend_params']['category_cache_writeback'] = category_cache_writeback
            cfg['backend_params']['category_filter_list'] = filter_list

            # Update the pass in passes list
            if not cfg.get('passes'):
                cfg['passes'] = []

            # Find or create categories_pass
            categories_pass = None
            for p in cfg['passes']:
                if p.get('id') == 'categories_pass':
                    categories_pass = p
                    break

            if categories_pass:
                categories_pass['enabled'] = category_pass_enabled
                categories_pass['order'] = category_pass_order
            else:
                # Create it if not found
                cfg['passes'].append({
                    'id': 'categories_pass',
                    'phase': 'chunk',
                    'enabled': category_pass_enabled,
                    'order': category_pass_order,
                })

            # Save to database
            db.upsert_analysis_config(
                config_id,
                row.get('name'),
                row.get('analysis_type'),
                row.get('version', 1),
                cfg,
                is_default=row.get('is_default', False)
            )
            if db.conn:
                db.conn.commit()

            success = True
        except Exception as e:
            error = f"Error saving configuration: {str(e)}"

    # After successful POST, re-fetch the config from database to get updated values
    if success:
        row = db.get_analysis_config(config_id)
        if row:
            cfg = row.get('config_json') or {}

    # Extract current values
    backend_params = cfg.get('backend_params', {})
    category_map = backend_params.get('category_map', {})
    category_cache = backend_params.get('category_cache', [])
    category_cache_writeback = backend_params.get('category_cache_writeback', True)
    filter_list = backend_params.get('category_filter_list', ['music', 'classical', 'review'])

    # Find categories_pass in passes
    categories_pass = None
    for p in cfg.get('passes', []):
        if p.get('id') == 'categories_pass':
            categories_pass = p
            break

    category_pass_enabled = categories_pass.get('enabled', True) if categories_pass else True
    category_pass_order = categories_pass.get('order', 40) if categories_pass else 40

    return render_template(
        'categories_pass_editor.html',
        config_id=config_id,
        category_map_json=json.dumps(category_map, indent=2),
        category_cache_list='\n'.join(category_cache),
        category_cache_writeback=category_cache_writeback,
        category_pass_enabled=category_pass_enabled,
        category_pass_order=category_pass_order,
        filter_list_json=json.dumps(filter_list, indent=2),
        error=error,
        success=success,
    )


@app.route('/analysis-configs/<config_id>/chunk-analysis', methods=['GET', 'POST'])
def edit_chunk_analysis(config_id):
    """Edit chunk_analysis configuration for a config."""
    db = get_db()
    row = db.get_analysis_config(config_id)
    if not row:
        return f"No config found with id={config_id}", 404

    cfg = row.get('config_json') or {}
    error = None
    success = False

    if request.method == 'POST':
        try:
            # Get form values
            max_words = int(request.form.get('max_words', 1000))
            overlap_words = int(request.form.get('overlap_words', 150))
            per_speaker_tracks = bool(request.form.get('per_speaker_tracks'))

            # Validate ranges
            if max_words < 100 or max_words > 5000:
                raise ValueError("max_words must be between 100 and 5000")
            if overlap_words < 0 or overlap_words > 2000:
                raise ValueError("overlap_words must be between 0 and 2000")

            # Update config chunk_params
            if 'chunk_params' not in cfg:
                cfg['chunk_params'] = {}

            cfg['chunk_params']['max_words'] = max_words
            cfg['chunk_params']['overlap_words'] = overlap_words
            cfg['chunk_params']['per_speaker_tracks'] = per_speaker_tracks

            # Save to database
            db.upsert_analysis_config(
                config_id,
                row.get('name'),
                row.get('analysis_type'),
                row.get('version', 1),
                cfg,
                is_default=row.get('is_default', False)
            )
            if db.conn:
                db.conn.commit()

            success = True
        except Exception as e:
            error = f"Error saving configuration: {str(e)}"

    # After successful POST, re-fetch the config from database to get updated values
    if success:
        row = db.get_analysis_config(config_id)
        if row:
            cfg = row.get('config_json') or {}

    # Extract current values
    chunk_params = cfg.get('chunk_params', {})
    max_words = chunk_params.get('max_words', 1000)
    overlap_words = chunk_params.get('overlap_words', 150)
    per_speaker_tracks = chunk_params.get('per_speaker_tracks', False)

    return render_template(
        'chunk_analysis_editor.html',
        config_id=config_id,
        max_words=max_words,
        overlap_words=overlap_words,
        per_speaker_tracks=per_speaker_tracks,
        error=error,
        success=success,
    )


@app.route('/analysis-configs/<config_id>/subchunks', methods=['GET', 'POST'])
def edit_subchunks(config_id):
    """Edit subchunks configuration for a config."""
    db = get_db()
    row = db.get_analysis_config(config_id)
    if not row:
        return f"No config found with id={config_id}", 404

    cfg = row.get('config_json') or {}
    error = None
    success = False

    if request.method == 'POST':
        try:
            # Get form values
            subchunks_enabled = bool(request.form.get('subchunks_enabled'))
            subchunks_order = int(request.form.get('subchunks_order', 20))

            # Validate order
            if subchunks_order < 0 or subchunks_order > 100:
                raise ValueError("subchunks_order must be between 0 and 100")

            # Update the subchunks pass in passes list
            if not cfg.get('passes'):
                cfg['passes'] = []

            # Find or create subchunks pass
            subchunks_pass = None
            for p in cfg['passes']:
                if p.get('id') == 'subchunks':
                    subchunks_pass = p
                    break

            if subchunks_pass:
                subchunks_pass['enabled'] = subchunks_enabled
                subchunks_pass['order'] = subchunks_order
            else:
                cfg['passes'].append({
                    'id': 'subchunks',
                    'phase': 'chunk',
                    'enabled': subchunks_enabled,
                    'order': subchunks_order,
                })

            # Save to database
            db.upsert_analysis_config(
                config_id,
                row.get('name'),
                row.get('analysis_type'),
                row.get('version', 1),
                cfg,
                is_default=row.get('is_default', False)
            )
            if db.conn:
                db.conn.commit()

            success = True
        except Exception as e:
            error = f"Error saving configuration: {str(e)}"

    # After successful POST, re-fetch the config from database to get updated values
    if success:
        row = db.get_analysis_config(config_id)
        if row:
            cfg = row.get('config_json') or {}

    # Extract current values
    subchunks_pass = None
    for p in cfg.get('passes', []):
        if p.get('id') == 'subchunks':
            subchunks_pass = p
            break

    subchunks_enabled = subchunks_pass.get('enabled', True) if subchunks_pass else True
    subchunks_order = subchunks_pass.get('order', 20) if subchunks_pass else 20

    return render_template(
        'subchunks_editor.html',
        config_id=config_id,
        subchunks_enabled=subchunks_enabled,
        subchunks_order=subchunks_order,
        error=error,
        success=success,
    )


@app.route('/analysis-configs/<config_id>/speaker-filter', methods=['GET', 'POST'])
def edit_speaker_filter(config_id):
    """Edit speaker filter configuration for a config."""
    db = get_db()
    row = db.get_analysis_config(config_id)
    if not row:
        return f"No config found with id={config_id}", 404

    cfg = row.get('config_json') or {}
    error = None
    success = False

    # Get available speakers from diarized_timestamps table
    available_speakers = []
    try:
        with db.conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT speaker_name
                FROM diarized_timestamps
                ORDER BY speaker_name
            """)
            available_speakers = [row[0] for row in cur.fetchall()]
    except Exception as e:
        # Table might not exist or have no data, continue without error
        pass

    if request.method == 'POST':
        try:
            # Get form values
            filter_mode = request.form.get('filter_mode', 'all')
            speakers = request.form.getlist('speakers')
            case_insensitive = bool(request.form.get('case_insensitive'))

            # Validate filter mode
            if filter_mode not in ('all', 'include', 'exclude'):
                raise ValueError(f"Invalid filter mode: {filter_mode}")

            # Create speaker filter config
            speaker_filter_config = {
                'mode': filter_mode,
                'speakers': speakers,
                'case_insensitive': case_insensitive
            }

            # Update config chunk_params
            if 'chunk_params' not in cfg:
                cfg['chunk_params'] = {}

            cfg['chunk_params']['speaker_filter'] = speaker_filter_config

            # Save to database
            db.upsert_analysis_config(
                config_id,
                row.get('name'),
                row.get('analysis_type'),
                row.get('version', 1),
                cfg,
                is_default=row.get('is_default', False)
            )
            if db.conn:
                db.conn.commit()

            success = True
        except Exception as e:
            error = f"Error saving configuration: {str(e)}"

    # After successful POST, re-fetch the config from database to get updated values
    if success:
        row = db.get_analysis_config(config_id)
        if row:
            cfg = row.get('config_json') or {}

    # Extract current values
    chunk_params = cfg.get('chunk_params', {})
    speaker_filter = chunk_params.get('speaker_filter', {})
    filter_mode = speaker_filter.get('mode', 'all')
    selected_speakers = speaker_filter.get('speakers', [])
    case_insensitive = speaker_filter.get('case_insensitive', True)

    return render_template(
        'speaker_filter_editor.html',
        config_id=config_id,
        filter_mode=filter_mode,
        selected_speakers=selected_speakers,
        case_insensitive=case_insensitive,
        available_speakers=available_speakers,
        error=error,
        success=success,
    )


if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=5000)
