# Back-compat entrypoint for CLI/web tooling that expected `web.app`.
# The unified Flask application now lives in `web/web_app.py`.

from .web_app import app


if __name__ == '__main__':
    print("Starting unified VidOps web server on http://0.0.0.0:5000")
    app.run(host='0.0.0.0', port=5000, debug=True)
