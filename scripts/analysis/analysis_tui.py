#!/usr/bin/env python3
"""Simple curses TUI for managing analysis configs.

Features:
  - List configs from the analysis_configs table
  - Open selected config in $EDITOR for editing as JSON

This is intentionally minimal and only acts as a config editor; all
actual analysis execution still runs via the existing CLI wrappers.
"""

from __future__ import annotations

import curses
import os
import subprocess
import tempfile
from typing import Any, List

from .config_loader import load_local_config
from .db_storage import AnalysisDatabase


def get_db() -> AnalysisDatabase:
    cfg = load_local_config() or {}
    sections = cfg.get("__sections__", {}) if isinstance(cfg, dict) else {}

    def pick(section: str, key: str, default: Any = None) -> Any:
        sec = sections.get(section, {}) if isinstance(sections, dict) else {}
        if key in sec:
            return sec[key]
        if key in cfg:
            return cfg[key]
        return os.environ.get(key.upper(), default)

    dbname = pick("db", "db_name", "transcripts")
    host = pick("db", "db_host", "localhost")
    port = int(pick("db", "db_port", 5432))
    user = pick("db", "db_user", None)
    password = pick("db", "db_password", None)

    db = AnalysisDatabase(dbname=dbname, host=host, port=port, user=user, password=password)
    db.connect()
    return db


def edit_config_in_editor(config: dict[str, Any]) -> dict[str, Any] | None:
    import json

    editor = os.environ.get("EDITOR", "nano")
    with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as tmp:
        path = tmp.name
        tmp.write(json.dumps(config.get("config_json") or {}, indent=2, sort_keys=True))
        tmp.flush()
    try:
        subprocess.call([editor, path])
        with open(path, "r", encoding="utf-8") as f:
            new_text = f.read()
        new_cfg = json.loads(new_text)
    except Exception:
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    config["config_json"] = new_cfg
    return config


def run(stdscr):
    curses.curs_set(0)
    db = get_db()
    configs: List[dict[str, Any]] = db.list_analysis_configs()
    idx = 0

    while True:
        stdscr.clear()
        stdscr.addstr(0, 0, "Analysis Configs (q=quit, Enter=edit in $EDITOR)")
        for i, cfg in enumerate(configs):
            marker = "-> " if i == idx else "   "
            line = f"{marker}{cfg.get('id')} | {cfg.get('name')} | {cfg.get('analysis_type')} v{cfg.get('version')}"
            try:
                stdscr.addstr(2 + i, 0, line[: curses.COLS - 1])
            except curses.error:
                # Ignore drawing errors on small terminals
                pass
        stdscr.refresh()

        ch = stdscr.getch()
        if ch in (ord("q"), ord("Q")):
            break
        elif ch in (curses.KEY_UP, ord("k")):
            idx = max(0, idx - 1)
        elif ch in (curses.KEY_DOWN, ord("j")):
            idx = min(len(configs) - 1, idx + 1)
        elif ch in (curses.KEY_ENTER, 10, 13):
            if not configs:
                continue
            # Fetch fresh from DB in case it was edited elsewhere
            cfg = db.get_analysis_config(configs[idx]["id"])
            if cfg is None:
                continue
            updated = edit_config_in_editor(cfg.copy())
            if updated is not None:
                db.upsert_analysis_config(
                    updated["id"],
                    updated.get("name") or cfg.get("name") or "Unnamed",
                    updated.get("analysis_type") or cfg.get("analysis_type") or "normal",
                    int(updated.get("version") or cfg.get("version") or 1),
                    updated.get("config_json") or {},
                    is_default=bool(updated.get("is_default") or cfg.get("is_default")),
                )
                if db.conn:
                    db.conn.commit()
                configs = db.list_analysis_configs()

    db.disconnect()


def main():
    curses.wrapper(run)


if __name__ == "__main__":
    main()
