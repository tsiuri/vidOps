#!/usr/bin/env python3
"""
Apply schema.sql using connection details from config.local.json/config.json or env vars.

Env vars override config file; config overrides defaults.
"""

import os
import shutil
import sys
import subprocess
from pathlib import Path

from .config_loader import load_local_config


def pick(cfg, key, env_name, default):
    if env_name in os.environ and os.environ[env_name]:
        return os.environ[env_name]
    if key in cfg and cfg[key] not in (None, ""):
        return cfg[key]
    return default


def main():
    cfg = load_local_config()
    db_host = pick(cfg, "db_host", "DB_HOST", "localhost")
    db_port = pick(cfg, "db_port", "DB_PORT", "5432")
    db_name = pick(cfg, "db_name", "DB_NAME", "transcripts")
    db_user = pick(cfg, "db_user", "DB_USER", None)
    db_password = pick(cfg, "db_password", "DB_PASSWORD", None)

    schema_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("schema.sql")
    if not schema_path.exists():
        print(f"Schema file not found: {schema_path}", file=sys.stderr)
        sys.exit(1)

    psql = shutil.which("psql")
    if not psql:
        print("psql not found on PATH; please install PostgreSQL client tools.", file=sys.stderr)
        sys.exit(1)

    env = os.environ.copy()
    env["PGHOST"] = str(db_host)
    env["PGPORT"] = str(db_port)
    env["PGDATABASE"] = str(db_name)
    if db_user:
        env["PGUSER"] = str(db_user)
    if db_password:
        env["PGPASSWORD"] = str(db_password)

    print(f"Applying schema to {db_name} at {db_host}:{db_port} using {schema_path} ...")
    cmd = [psql, "-f", str(schema_path)]
    try:
        subprocess.run(cmd, check=True, env=env)
        print("✓ Schema applied successfully")
    except subprocess.CalledProcessError as e:
        print(f"Schema application failed with exit code {e.returncode}", file=sys.stderr)
        sys.exit(e.returncode)


if __name__ == "__main__":
    main()
