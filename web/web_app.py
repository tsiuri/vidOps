#!/usr/bin/env python3
"""
Web interface for browsing and searching transcript analysis results.
Simple Flask app for querying the analysis database.
"""

import os
import sys
from datetime import datetime
from flask import Flask, render_template, request, jsonify, g, redirect, url_for
from pathlib import Path

# Ensure local imports work when running as a script
ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "analysis"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from scripts.analysis.config_loader import load_local_config
from scripts.analysis.db_storage import AnalysisDatabase
from scripts.analysis.analysis_config import AnalysisConfig
from scripts.web_app.analyses_view import register_analyses_routes
from scripts.web_app.drill_api import create_drill_blueprint

app = Flask(__name__)

LOCAL_CFG = load_local_config()

# Helper to pick value: env > config file > default
def cfg(key: str, env_name: str, default):
    env_val = os.environ.get(env_name)
    if env_val is not None:
        return env_val
    if key in LOCAL_CFG:
        return LOCAL_CFG[key]
    return default

flask_request = request

# Database configuration from environment or defaults
DB_CONFIG = {
    'dbname': cfg('db_name', 'DB_NAME', 'transcripts'),
    'host': cfg('db_host', 'DB_HOST', 'localhost'),
    'port': int(cfg('db_port', 'DB_PORT', 5432)),
    'user': cfg('db_user', 'DB_USER', None),
    'password': cfg('db_password', 'DB_PASSWORD', None)
}


def get_db():
    """Get database connection for current request"""
    if 'db' not in g:
        g.db = AnalysisDatabase(**DB_CONFIG)
        g.db.connect()
    return g.db


@app.teardown_appcontext
def close_db(error):
    """Close database connection at end of request"""
    db = g.pop('db', None)
    if db is not None:
        # Commit changes if no error occurred
        if error is None and db.conn:
            db.conn.commit()
        db.disconnect()


# HTML Templates

HOME_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Transcript Analysis Browser</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            line-height: 1.6;
            color: #333;
            background: #f5f5f5;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }
        header {
            background: #2c3e50;
            color: white;
            padding: 30px 0;
            margin-bottom: 30px;
        }
        h1 { font-size: 2em; margin-bottom: 10px; }
        .subtitle { opacity: 0.9; font-size: 1.1em; }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .stat-card {
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .stat-value {
            font-size: 2.5em;
            font-weight: bold;
            color: #3498db;
        }
        .stat-label {
            color: #7f8c8d;
            margin-top: 5px;
        }
        .search-section {
            background: white;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            margin-bottom: 30px;
        }
        .search-section h2 {
            margin-bottom: 20px;
            color: #2c3e50;
        }
        .search-tabs {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
            border-bottom: 2px solid #ecf0f1;
        }
        .tab-button {
            padding: 10px 20px;
            background: none;
            border: none;
            cursor: pointer;
            font-size: 1em;
            color: #7f8c8d;
            border-bottom: 3px solid transparent;
            transition: all 0.3s;
        }
        .tab-button.active {
            color: #3498db;
            border-bottom-color: #3498db;
        }
        .tab-content {
            display: none;
        }
        .tab-content.active {
            display: block;
        }
        .search-form {
            display: flex;
            gap: 10px;
            margin-bottom: 20px;
        }
        input[type="text"], input[type="number"] {
            flex: 1;
            padding: 12px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 1em;
        }
        button {
            padding: 12px 24px;
            background: #3498db;
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 1em;
            transition: background 0.3s;
        }
        button:hover {
            background: #2980b9;
        }
        #results {
            margin-top: 20px;
        }
        .result-item {
            background: white;
            padding: 20px;
            margin-bottom: 15px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            border-left: 4px solid #3498db;
        }
        .result-header {
            display: flex;
            justify-content: space-between;
            margin-bottom: 10px;
        }
        .video-title {
            font-weight: bold;
            color: #2c3e50;
            font-size: 1.1em;
        }
        .video-date {
            color: #7f8c8d;
        }
        .chunk-info {
            color: #95a5a6;
            font-size: 0.9em;
            margin-bottom: 10px;
        }
        .summary {
            margin: 10px 0;
            line-height: 1.6;
        }
        .sentiment-badge {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.85em;
            font-weight: 500;
        }
        .sentiment-analytical { background: #e3f2fd; color: #1976d2; }
        .sentiment-excited { background: #fff3e0; color: #f57c00; }
        .sentiment-concerned { background: #fce4ec; color: #c2185b; }
        .sentiment-joking { background: #f3e5f5; color: #7b1fa2; }
        .sentiment-serious { background: #e8eaf6; color: #3f51b5; }
        .sentiment-neutral { background: #f5f5f5; color: #616161; }
        .highlight {
            background: #fff9c4;
            font-weight: 500;
        }
        .loading {
            text-align: center;
            padding: 40px;
            color: #7f8c8d;
        }
        .no-results {
            text-align: center;
            padding: 40px;
            color: #95a5a6;
        }
        .top-items {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
        }
        .top-list {
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .top-list h3 {
            margin-bottom: 15px;
            color: #2c3e50;
        }
        .top-list-item {
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid #ecf0f1;
        }
        .top-list-item:last-child {
            border-bottom: none;
        }
        .item-name {
            font-weight: 500;
        }
        .item-count {
            color: #7f8c8d;
        }
    </style>
</head>
<body>
    <header>
        <div class="container" style="display:flex;align-items:center;justify-content:space-between;">
            <div>
              <h1>Transcript Analysis Browser</h1>
              <div class="subtitle">Search and explore Hasan Piker broadcast analysis</div>
            </div>
            <nav>
              <a href="/" style="color:#fff;margin-right:12px;text-decoration:none;font-weight:600;">Home</a>
              <a href="/analyses" style="color:#fff;text-decoration:none;font-weight:600;">Analyses</a>
            </nav>
        </div>
    </header>

    <div class="container">
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-value">{{ stats.total_videos }}</div>
                <div class="stat-label">Analyzed Videos</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{{ stats.total_segments }}</div>
                <div class="stat-label">Segments</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{{ stats.unique_people }}</div>
                <div class="stat-label">People Mentioned</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{{ stats.unique_topics }}</div>
                <div class="stat-label">Topics Discussed</div>
            </div>
        </div>

        <div class="search-section">
            <h2>Search Analysis</h2>
            <div class="search-tabs">
                <button class="tab-button active" onclick="switchTab('fulltext')">Full Text</button>
                <button class="tab-button" onclick="switchTab('topic')">Topic</button>
                <button class="tab-button" onclick="switchTab('person')">Person</button>
                <button class="tab-button" onclick="switchTab('browse')">Browse</button>
            </div>

            <div id="tab-fulltext" class="tab-content active">
                <div class="search-form">
                    <input type="text" id="fulltext-query" placeholder="Search summaries (e.g., 'healthcare policy')">
                    <input type="number" id="fulltext-limit" placeholder="Limit" value="50" style="max-width: 100px;">
                    <button onclick="searchFulltext()">Search</button>
                </div>
            </div>

            <div id="tab-topic" class="tab-content">
                <div class="search-form">
                    <input type="text" id="topic-query" placeholder="Topic (e.g., 'ukraine', 'election')">
                    <input type="number" id="topic-limit" placeholder="Limit" value="50" style="max-width: 100px;">
                    <button onclick="searchTopic()">Search</button>
                </div>
            </div>

            <div id="tab-person" class="tab-content">
                <div class="search-form">
                    <input type="text" id="person-query" placeholder="Person name (e.g., 'Biden', 'AOC')">
                    <input type="number" id="person-limit" placeholder="Limit" value="50" style="max-width: 100px;">
                    <button onclick="searchPerson()">Search</button>
                </div>
            </div>

            <div id="tab-browse" class="tab-content">
                <div class="search-form">
                    <input type="number" id="browse-limit" placeholder="Limit" value="20" style="max-width: 100px;">
                    <button onclick="browseRecent()">Show Recent</button>
                </div>
            </div>

            <div id="results"></div>
        </div>

        <div class="top-items">
            <div class="top-list">
                <h3>Most Mentioned People</h3>
                {% for person in top_people[:10] %}
                <div class="top-list-item">
                    <span class="item-name">{{ person.person }}</span>
                    <span class="item-count">{{ person.total_mentions }} mentions</span>
                </div>
                {% endfor %}
            </div>

            <div class="top-list">
                <h3>Most Discussed Topics</h3>
                {% for topic in top_topics[:10] %}
                <div class="top-list-item">
                    <span class="item-name">{{ topic.topic }}</span>
                    <span class="item-count">{{ topic.total_occurrences }} occurrences</span>
                </div>
                {% endfor %}
            </div>
        </div>
    </div>

    <script>
        function switchTab(tabName) {
            document.querySelectorAll('.tab-button').forEach(btn => btn.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));

            var btn = document.querySelector("button[onclick=\"switchTab('" + tabName + "')\"]");
            if (btn) btn.classList.add('active');
            var content = document.getElementById("tab-" + tabName);
            if (content) content.classList.add('active');
        }

        function showLoading() {
            document.getElementById('results').innerHTML = '<div class="loading">Searching...</div>';
        }

        function showResults(data, type) {
            const resultsDiv = document.getElementById('results');

            if (!data || data.length === 0) {
                resultsDiv.innerHTML = '<div class="no-results">No results found</div>';
                return;
            }

            let html = '<h3 style="margin-bottom: 20px;">Found ' + data.length + ' result(s)</h3>';

            data.forEach(item => {
                const sentimentClass = 'sentiment-' + (item.sentiment || 'neutral');
                html += '<div class="result-item">';
                html += '<div class="result-header">';
                html += '<div class="video-title">' + escapeHtml(item.video_title || 'Unknown') + '</div>';
                html += '<div class="video-date">' + (item.video_date || '') + '</div>';
                html += '</div>';
                html += '<div class="chunk-info">Video ID: ' + item.ytid + ' | Chunk: ' + item.chunk_id + '</div>';
                if (item.sentiment) {
                    html += '<span class="sentiment-badge ' + sentimentClass + '">' + item.sentiment + '</span>';
                }
                if (item.person_name) {
                    html += ' <span class="highlight">Person: ' + escapeHtml(item.person_name) + '</span>';
                }
                if (item.topic) {
                    html += ' <span class="highlight">Topic: ' + escapeHtml(item.topic) + '</span>';
                }
                html += '<div class="summary">' + escapeHtml(item.summary || '') + '</div>';
                html += '</div>';
            });

            resultsDiv.innerHTML = html;
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        async function searchFulltext() {
            const query = document.getElementById('fulltext-query').value;
            const limit = document.getElementById('fulltext-limit').value || 50;

            if (!query) {
                alert('Please enter a search query');
                return;
            }

            showLoading();
            try {
                const response = await fetch(\`/api/search/fulltext?q=$\{encodeURIComponent(query)}&limit=$\{limit}\`);
                const data = await response.json();
                showResults(data, 'fulltext');
            } catch (error) {
                document.getElementById('results').innerHTML = '<div class="no-results">Error: ' + error.message + '</div>';
            }
        }

        async function searchTopic() {
            const query = document.getElementById('topic-query').value;
            const limit = document.getElementById('topic-limit').value || 50;

            if (!query) {
                alert('Please enter a topic');
                return;
            }

            showLoading();
            try {
                const response = await fetch(\`/api/search/topic?q=$\{encodeURIComponent(query)}&limit=$\{limit}\`);
                const data = await response.json();
                showResults(data, 'topic');
            } catch (error) {
                document.getElementById('results').innerHTML = '<div class="no-results">Error: ' + error.message + '</div>';
            }
        }

        async function searchPerson() {
            const query = document.getElementById('person-query').value;
            const limit = document.getElementById('person-limit').value || 50;

            if (!query) {
                alert('Please enter a person name');
                return;
            }

            showLoading();
            try {
                const response = await fetch(\`/api/search/person?q=$\{encodeURIComponent(query)}&limit=$\{limit}\`);
                const data = await response.json();
                showResults(data, 'person');
            } catch (error) {
                document.getElementById('results').innerHTML = '<div class="no-results">Error: ' + error.message + '</div>';
            }
        }

        async function browseRecent() {
            const limit = document.getElementById('browse-limit').value || 20;

            showLoading();
            try {
                const response = await fetch(\`/api/browse/recent?limit=$\{limit}\`);
                const data = await response.json();
                showResults(data, 'browse');
            } catch (error) {
                document.getElementById('results').innerHTML = '<div class="no-results">Error: ' + error.message + '</div>';
            }
        }
    </script>
</body>
</html>
'''


@app.route('/')
def index():
    """Home page with search interface"""
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

    return render_template('home.html', stats=stats, top_people=top_people, top_topics=top_topics, config={'DB_NAME': DB_CONFIG['dbname']})


@app.route('/api/search/fulltext')
def api_search_fulltext():
    """API endpoint for full-text search"""
    query = request.args.get('q', '')
    limit = min(int(request.args.get('limit', 50)), 200)

    db = get_db()
    results = db.search_segments_fulltext(query, limit)
    return jsonify(results)


@app.route('/api/search/topic')
def api_search_topic():
    """API endpoint for topic search"""
    query = request.args.get('q', '')
    limit = min(int(request.args.get('limit', 50)), 200)

    db = get_db()
    results = db.search_by_topic(query, limit)
    return jsonify(results)


@app.route('/api/search/person')
def api_search_person():
    """API endpoint for person search"""
    query = request.args.get('q', '')
    limit = min(int(request.args.get('limit', 50)), 200)

    db = get_db()
    results = db.search_by_person(query, limit)
    return jsonify(results)


@app.route('/api/browse/recent')
def api_browse_recent():
    """API endpoint for browsing recent analyses"""
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
    """API endpoint to browse recent video-level analyses (TLDR/Summary)"""
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

@app.route('/video/<ytid>')
def video_detail_page(ytid: str):
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
        'political_overview': row[10] or '',
        'controversies': row[11] or '',
        'issues': row[12] or [],
        'noteworthy_statements': row[13] or [],
        'personal_conflicts': row[14] or [],
        'speaker_overview': row[15] or '',
        'personal_themes': row[16] or []
    }

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

VIDEOS_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Videos Browser</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; background:#f5f5f5; }
    .container { max-width: 1100px; margin: 0 auto; padding: 20px; }
    header { background:#2c3e50; color:#fff; padding:20px 0; margin-bottom:20px; }
    h1 { margin: 0 0 5px 0; }
    .card { background:#fff; padding:20px; border-radius:8px; box-shadow:0 2px 4px rgba(0,0,0,.08); }
    .controls { display:flex; gap:10px; margin-bottom:15px; }
    input[type=text] { flex:1; padding:10px; border:1px solid #ddd; border-radius:4px; }
    input[type=number] { width:100px; padding:10px; border:1px solid #ddd; border-radius:4px; }
    button { padding:10px 16px; background:#3498db; border:none; color:#fff; border-radius:4px; cursor:pointer; }
    button:hover { background:#2980b9; }
    table { width:100%; border-collapse: collapse; }
    th, td { border-bottom:1px solid #eee; padding:10px; text-align:left; }
    th { background:#fafafa; }
    .muted { color:#7f8c8d; }
  </style>
  <script>
    async function listVideos() {
      const limit = document.getElementById('limit').value || 50;
      const res = await fetch(`/api/videos/list?limit=${limit}`);
      const data = await res.json();
      render(data);
    }
    async function searchVideos() {
      const q = document.getElementById('q').value.trim();
      const limit = document.getElementById('limit').value || 50;
      if (!q) return listVideos();
      const res = await fetch(`/api/videos/search?q=${encodeURIComponent(q)}&limit=${limit}`);
      const data = await res.json();
      render(data);
    }
    function render(rows) {
      const tbody = document.getElementById('rows');
      tbody.innerHTML = '';
      rows.forEach(r => {
        const tr = document.createElement('tr');
        const url = `https://youtube.com/watch?v=${r.ytid}`;
        tr.innerHTML = `
          <td><a href="${url}" target="_blank" rel="noopener">${r.ytid}</a></td>
          <td>${escapeHtml(r.title || '')}</td>
          <td class="muted">${r.title_date || ''}</td>
          <td class="muted">${r.upload_date || ''}</td>
          <td class="muted">${r.duration_sec ?? ''}</td>
          <td class="muted">${escapeHtml(r.channel || '')}</td>
        `;
        tbody.appendChild(tr);
      });
      document.getElementById('count').textContent = rows.length;
    }
    function escapeHtml(t){ const d=document.createElement('div'); d.textContent=t; return d.innerHTML; }
    window.addEventListener('DOMContentLoaded', listVideos);
  </script>
  </head>
  <body>
    <header><div class="container"><h1>Videos</h1><div class="muted">Browse/search core videos table</div></div></header>
    <div class="container">
      <div class="card">
        <div class="controls">
          <input id="q" type="text" placeholder="Search by title or ytid" />
          <input id="limit" type="number" value="50" min="1" max="500" />
          <button onclick="searchVideos()">Search</button>
          <button onclick="listVideos()">List Recent</button>
        </div>
        <div class="muted" style="margin-bottom:10px">Showing <span id="count">0</span> row(s)</div>
        <div style="overflow:auto">
          <table>
            <thead>
              <tr>
                <th>ytid</th>
                <th>title</th>
                <th>title_date</th>
                <th>upload_date</th>
                <th>duration_sec</th>
                <th>channel</th>
              </tr>
            </thead>
            <tbody id="rows"></tbody>
          </table>
        </div>
      </div>
    </div>
  </body>
  </html>
'''


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

# Register additional routes (analyses page/API)
register_analyses_routes(app, get_db)

# Register drill API routes
_drill_bp = create_drill_blueprint(get_db)
app.register_blueprint(_drill_bp)




@app.route('/analysis-configs')
def list_analysis_configs():
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
        db_name=DB_CONFIG['dbname'],
    )


@app.route('/analysis-configs/new', methods=['GET', 'POST'])
def new_analysis_config():
    from uuid import uuid4
    import json as _json

    db = get_db()
    error = None

    if flask_request.method == 'POST':
        name = flask_request.form.get('name') or 'New Config'
        analysis_type = flask_request.form.get('analysis_type') or 'normal'
        version = int(flask_request.form.get('version') or 1)
        is_default = bool(flask_request.form.get('is_default'))
        config_json_text = flask_request.form.get('config_json') or '{}'

        try:
            cfg_dict = _json.loads(config_json_text)
            # Generate ID if not provided
            if not cfg_dict.get('id'):
                cfg_dict['id'] = str(uuid4())
            # Validate via AnalysisConfig when pydantic is available
            if hasattr(AnalysisConfig, "model_validate"):
                AnalysisConfig.model_validate(cfg_dict)  # type: ignore[attr-defined]
            elif hasattr(AnalysisConfig, "parse_obj"):
                AnalysisConfig.parse_obj(cfg_dict)      # type: ignore[attr-defined]
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
    import json as _json

    db = get_db()
    row = db.get_analysis_config(config_id)
    if not row:
        return f"No config found with id={config_id}", 404

    cfg = row.get('config_json') or {}

    if flask_request.method == 'POST':
        name = flask_request.form.get('name') or row.get('name') or 'Unnamed'
        analysis_type = flask_request.form.get('analysis_type') or row.get('analysis_type') or 'normal'
        version = int(flask_request.form.get('version') or row.get('version') or 1)
        is_default = bool(flask_request.form.get('is_default'))
        config_json_text = flask_request.form.get('config_json') or '{}'
        error = None

        try:
            cfg_dict = _json.loads(config_json_text)
            # Ensure ID is set to the config_id we're editing
            cfg_dict['id'] = config_id
            if hasattr(AnalysisConfig, "model_validate"):
                AnalysisConfig.model_validate(cfg_dict)  # type: ignore[attr-defined]
            elif hasattr(AnalysisConfig, "parse_obj"):
                AnalysisConfig.parse_obj(cfg_dict)      # type: ignore[attr-defined]
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

    cfg_json = _json.dumps(cfg, indent=2, sort_keys=True)

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
    import json as _json
    db = get_db()
    row = db.get_analysis_config(config_id)
    if not row:
        return f"No config found with id={config_id}", 404
    cfg = row.get('config_json') or {}
    drills = cfg.get('drills') or []
    output_shapes = cfg.get('output_shapes') or {}
    drills_json = _json.dumps(drills, indent=2)
    output_shapes_json = _json.dumps(output_shapes, indent=2)
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
    import json as _json
    db = get_db()
    row = db.get_analysis_config(config_id)
    if not row:
        return f"No config found with id={config_id}", 404

    cfg = row.get('config_json') or {}
    error = None
    success = False

    if flask_request.method == 'POST':
        try:
            # Parse category map
            category_map_json_text = flask_request.form.get('category_map_json', '{}')
            category_map = _json.loads(category_map_json_text) if category_map_json_text else {}

            # Parse category cache
            category_cache_list_text = flask_request.form.get('category_cache_list', '')
            category_cache = [line.strip() for line in category_cache_list_text.split('\n') if line.strip()]

            # Parse filter list
            filter_list_json_text = flask_request.form.get('filter_list_json', '[]')
            filter_list = _json.loads(filter_list_json_text) if filter_list_json_text else []

            # Get pass parameters
            category_pass_enabled = bool(flask_request.form.get('category_pass_enabled'))
            category_pass_order = int(flask_request.form.get('category_pass_order', 40))
            category_cache_writeback = bool(flask_request.form.get('category_cache_writeback'))

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
        category_map_json=_json.dumps(category_map, indent=2),
        category_cache_list='\n'.join(category_cache),
        category_cache_writeback=category_cache_writeback,
        category_pass_enabled=category_pass_enabled,
        category_pass_order=category_pass_order,
        filter_list_json=_json.dumps(filter_list, indent=2),
        error=error,
        success=success,
    )


@app.route('/analysis-configs/<config_id>/chunk-analysis', methods=['GET', 'POST'])
def edit_chunk_analysis(config_id):
    """Edit chunk_analysis configuration for a config."""
    import json as _json
    db = get_db()
    row = db.get_analysis_config(config_id)
    if not row:
        return f"No config found with id={config_id}", 404

    cfg = row.get('config_json') or {}
    error = None
    success = False

    if flask_request.method == 'POST':
        try:
            # Get form values
            max_words = int(flask_request.form.get('max_words', 1000))
            overlap_words = int(flask_request.form.get('overlap_words', 150))
            per_speaker_tracks = bool(flask_request.form.get('per_speaker_tracks'))

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
    import json as _json
    db = get_db()
    row = db.get_analysis_config(config_id)
    if not row:
        return f"No config found with id={config_id}", 404

    cfg = row.get('config_json') or {}
    error = None
    success = False

    if flask_request.method == 'POST':
        try:
            # Get form values
            subchunks_enabled = bool(flask_request.form.get('subchunks_enabled'))
            subchunks_order = int(flask_request.form.get('subchunks_order', 20))

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
    import json as _json
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

    if flask_request.method == 'POST':
        try:
            # Get form values
            filter_mode = flask_request.form.get('filter_mode', 'all')
            speakers = flask_request.form.getlist('speakers')
            case_insensitive = bool(flask_request.form.get('case_insensitive'))

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
    print("Starting Transcript Analysis Browser...")
    print(f"Database: {DB_CONFIG['dbname']}@{DB_CONFIG['host']}")
    print("Open http://localhost:5000 in your browser")
    app.run(debug=True, host='0.0.0.0', port=5000)
