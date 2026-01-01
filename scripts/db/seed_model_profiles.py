#!/usr/bin/env python3
"""
Seed analysis_model_profiles by serving each Ollama model with default settings
and measuring VRAM usage.

This script:
1. Lists all available Ollama models
2. For each model:
   - Extracts default parameters from the Modelfile (via ollama show)
   - Serves the model with those defaults to load it into VRAM
   - Measures VRAM usage (via nvidia-smi)
   - Inserts a row into analysis_model_profiles with the measured VRAM requirement

Usage:
    python scripts/db/seed_model_profiles.py [--dry-run] [--models MODEL1,MODEL2,...]

    --dry-run: Print what would be inserted without writing to DB
    --models: Comma-separated list of specific models to seed (default: all)
    --ollama-url: Ollama API endpoint (default: http://localhost:11434)
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Dict, Any

import psycopg2
import requests

# Add project root to path
SCRIPT_DIR = Path(__file__).parent.absolute()
PROJECT_ROOT = SCRIPT_DIR.parent.parent  # scripts/db -> scripts -> project root
sys.path.insert(0, str(PROJECT_ROOT))

from configuration import Config


def parse_modelfile(model_name: str) -> Dict[str, Any]:
    """
    Extract VRAM-affecting parameters from model's Modelfile.

    Only includes parameters that affect memory usage:
    - num_ctx: context window size (affects KV cache)
    - num_batch: batch size
    - num_keep: number of tokens to keep from initial prompt

    Inference parameters (temperature, top_p, stop, etc.) are NOT included.
    """
    try:
        result = subprocess.run(
            ["ollama", "show", model_name, "--modelfile"],
            capture_output=True,
            text=True,
            check=True
        )

        # Only these parameters affect VRAM usage
        vram_affecting_params = {
            "num_ctx",      # Context window size - creates KV cache
            "num_batch",    # Batch size
            "num_keep",     # Tokens to keep from prompt (minor effect)
        }

        params = {}
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("PARAMETER "):
                parts = line.split(None, 2)  # PARAMETER key value
                if len(parts) == 3:
                    key = parts[1]
                    value = parts[2]

                    # Only include VRAM-affecting parameters
                    if key not in vram_affecting_params:
                        continue

                    # Parse as number
                    try:
                        if '.' in value:
                            params[key] = float(value)
                        else:
                            params[key] = int(value)
                    except ValueError:
                        print(f"  Warning: Could not parse {key}={value} as number")

        return params
    except subprocess.CalledProcessError as e:
        print(f"Warning: Could not get Modelfile for {model_name}: {e}")
        return {}


def get_model_vram_from_ollama_ps(model_name: str) -> Optional[float]:
    """Get model VRAM usage from ollama ps output."""
    try:
        result = subprocess.run(
            ["ollama", "ps"],
            capture_output=True,
            text=True,
            check=True
        )

        # Parse output like:
        # NAME             ID              SIZE      PROCESSOR    CONTEXT    UNTIL
        # granite3.3:8b    fd429f23b909    6.1 GB    100% GPU     4096       3 minutes from now
        for line in result.stdout.splitlines()[1:]:  # Skip header
            parts = line.split()
            if not parts:
                continue

            name = parts[0]
            if name == model_name and len(parts) >= 4:
                # SIZE is in format "6.1 GB" or "128 MB"
                size_str = parts[2]
                unit = parts[3]

                size_val = float(size_str)
                if unit == "MB":
                    return size_val / 1024.0
                elif unit == "GB":
                    return size_val
                else:
                    print(f"  Warning: Unknown unit {unit}")
                    return None

        # Model not found in ps output
        return None
    except (subprocess.CalledProcessError, ValueError, IndexError) as e:
        print(f"  Warning: Could not parse ollama ps output: {e}")
        return None


def unload_model(model_name: str, ollama_url: str = "http://localhost:11434") -> None:
    """Unload a model from Ollama to free VRAM by sending keep_alive=0."""
    try:
        requests.post(
            f"{ollama_url}/api/generate",
            json={
                "model": model_name,
                "prompt": "",
                "keep_alive": 0
            },
            timeout=10
        )
        print(f"  Unloaded {model_name}")
        time.sleep(2)  # Give it time to free VRAM
    except Exception as e:
        print(f"  Warning: Could not unload {model_name}: {e}")


def serve_model_and_measure_vram(
    model_name: str,
    options: Dict[str, Any],
    ollama_url: str = "http://localhost:11434"
) -> Optional[float]:
    """
    Serve the model with given options and measure VRAM usage.

    Returns VRAM usage in GB, or None if measurement failed.
    """
    # Send a test prompt to load the model into VRAM
    print(f"  Loading model with test prompt...")
    try:
        response = requests.post(
            f"{ollama_url}/api/generate",
            json={
                "model": model_name,
                "prompt": "Say 'OK' if you understand.",
                "stream": False,
                "options": options
            },
            timeout=120
        )
        response.raise_for_status()
    except requests.RequestException as e:
        error_msg = str(e)
        # Check if it's an embedding model (400 Bad Request)
        if "400" in error_msg:
            print(f"  Skipping: This appears to be an embedding model (doesn't support /api/generate)")
        else:
            print(f"  Error: Failed to serve model: {e}")
            # Try to get more details from the response
            try:
                if hasattr(e, 'response') and e.response is not None:
                    print(f"  Response body: {e.response.text[:500]}")
            except:
                pass
        return None

    # Wait a moment for model to fully load
    time.sleep(1)

    # Get VRAM usage from ollama ps
    vram_gb = get_model_vram_from_ollama_ps(model_name)
    if vram_gb is None:
        print(f"  Warning: Model not found in 'ollama ps' output")
        return None

    print(f"  Model VRAM (from ollama ps): {vram_gb:.2f} GB")
    return vram_gb


def list_ollama_models() -> list[str]:
    """Get list of available Ollama models."""
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            check=True
        )

        models = []
        for line in result.stdout.splitlines()[1:]:  # Skip header
            parts = line.split()
            if parts:
                models.append(parts[0])  # First column is model name

        return models
    except subprocess.CalledProcessError as e:
        print(f"Error: Could not list Ollama models: {e}")
        return []


def insert_model_profile(
    conn: psycopg2.extensions.connection,
    model_name: str,
    options: Dict[str, Any],
    required_vram_gb: float,
    notes: str = "",
    dry_run: bool = False
) -> Optional[int]:
    """Insert a model profile into analysis_model_profiles."""
    if dry_run:
        print(f"\n[DRY RUN] Would insert:")
        print(f"  model_name: {model_name}")
        print(f"  options: {json.dumps(options, indent=2)}")
        print(f"  required_vram_gb: {required_vram_gb}")
        print(f"  notes: {notes}")
        return None

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO analysis_model_profiles (model_name, options, required_vram_gb, notes)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (model_name, options) DO UPDATE
                SET required_vram_gb = EXCLUDED.required_vram_gb,
                    notes = EXCLUDED.notes,
                    updated_at = NOW()
            RETURNING id
            """,
            (model_name, json.dumps(options), required_vram_gb, notes)
        )
        profile_id = cur.fetchone()[0]
        conn.commit()
        print(f"  Inserted/updated profile id={profile_id}")
        return profile_id


def main():
    parser = argparse.ArgumentParser(description="Seed analysis_model_profiles with VRAM measurements")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be inserted without writing to DB")
    parser.add_argument("--models", help="Comma-separated list of models to seed (default: all)")
    parser.add_argument("--ollama-url", default="http://localhost:11434", help="Ollama API endpoint")
    parser.add_argument("--db-host", default="192.168.0.187", help="PostgreSQL host")
    parser.add_argument("--db-port", type=int, default=5432, help="PostgreSQL port")
    parser.add_argument("--db-name", default="transcripts", help="PostgreSQL database name")
    parser.add_argument("--db-user", default="billie", help="PostgreSQL user")
    parser.add_argument("--db-password", default="z", help="PostgreSQL password")
    args = parser.parse_args()

    # Load config (for potential future use)
    config = Config()

    # Get model list
    if args.models:
        models = [m.strip() for m in args.models.split(',')]
    else:
        models = list_ollama_models()

    if not models:
        print("No models found to seed")
        return 1

    print(f"Found {len(models)} models to seed: {', '.join(models)}\n")

    # Connect to DB
    conn = None
    if not args.dry_run:
        try:
            conn = psycopg2.connect(
                host=args.db_host,
                port=args.db_port,
                dbname=args.db_name,
                user=args.db_user,
                password=args.db_password
            )
            print(f"Connected to database {args.db_name} at {args.db_host}:{args.db_port}")
        except psycopg2.Error as e:
            print(f"Error: Could not connect to database: {e}")
            return 1

    # Process each model
    try:
        for model_name in models:
            print(f"\n{'='*60}")
            print(f"Processing: {model_name}")
            print('='*60)

            # Get default parameters from Modelfile
            params = parse_modelfile(model_name)
            print(f"  Default parameters: {json.dumps(params, indent=2) if params else '(none found)'}")

            # Serve and measure VRAM
            vram_gb = serve_model_and_measure_vram(model_name, params, args.ollama_url)
            if vram_gb is None:
                print(f"  Skipping {model_name} due to measurement failure")
                continue

            # Insert into DB
            notes = f"Auto-seeded with default Modelfile parameters on {time.strftime('%Y-%m-%d')}"
            if conn or args.dry_run:
                insert_model_profile(conn, model_name, params, vram_gb, notes, args.dry_run)

            # Unload the model to free VRAM for the next one
            unload_model(model_name, args.ollama_url)

    finally:
        if conn:
            conn.close()

    print("\n" + "="*60)
    print("Seeding complete!")
    print("="*60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
