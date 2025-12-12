"""Analyses page and API for viewing a single video's full analysis."""

from __future__ import annotations

from flask import render_template_string, jsonify, request


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
        div.innerHTML = `<strong>Chunk ${c.chunk_id}</strong> — ${escapeHtml(c.sentiment || '')}<br><div>${escapeHtml(c.summary || '')}</div>`;
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


def register_analyses_routes(app, get_db):
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
        def fetch_spans(sql: str):
            db.cursor.execute(sql, (ytid,))
            return [dict(row) for row in db.cursor.fetchall()]

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
