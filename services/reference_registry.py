# vidops/services/reference_registry.py
"""
Canonical registry helpers for diarization reference sets.

- Compute a manifest (per-file hashes, aggregate hash) for a reference directory
- Persist manifest.json alongside reference.json
- Register/update metadata in the diarization_references table
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from db import get_connection


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(reference_dir: Path) -> dict:
    """
    Build a manifest for all files under reference_dir (recursive), excluding manifest.json itself.
    Returns a dict with file entries and an aggregate hash.
    """
    files = []
    for path in sorted(reference_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(reference_dir)
        if rel.name == "manifest.json":
            continue
        files.append(
            {
                "path": str(rel),
                "size": path.stat().st_size,
                "sha256": _file_sha256(path),
            }
        )
    # Aggregate hash over sorted "path:sha256" lines for determinism
    agg = hashlib.sha256()
    for entry in files:
        agg.update(f"{entry['path']}:{entry['sha256']}\n".encode("utf-8"))
    return {"files": files, "aggregate_hash": agg.hexdigest()}


@dataclass
class ReferenceRecord:
    name: str
    path: str
    model: Optional[str] = None
    transcript_kind: Optional[str] = None
    clip_count: Optional[int] = None
    manifest_path: Optional[str] = None
    aggregate_hash: Optional[str] = None


class ReferenceRegistry:
    """
    Persist and retrieve diarization reference metadata.
    """

    def register(
        self,
        name: str,
        reference_dir: Path,
        model: Optional[str] = None,
        transcript_kind: Optional[str] = None,
    ) -> ReferenceRecord:
        """
        Compute manifest, write manifest.json, and upsert diarization_references row.
        """
        manifest = build_manifest(reference_dir)
        manifest_path = reference_dir / "manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        clip_count = sum(1 for f in manifest["files"] if f["path"].endswith(".wav"))
        record = ReferenceRecord(
            name=name,
            path=str(reference_dir),
            model=model,
            transcript_kind=transcript_kind,
            clip_count=clip_count,
            manifest_path=str(manifest_path),
            aggregate_hash=manifest["aggregate_hash"],
        )

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO diarization_references
                      (name, path, model, transcript_kind, clip_count, manifest_path, aggregate_hash)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (name)
                    DO UPDATE SET
                        path = EXCLUDED.path,
                        model = EXCLUDED.model,
                        transcript_kind = EXCLUDED.transcript_kind,
                        clip_count = EXCLUDED.clip_count,
                        manifest_path = EXCLUDED.manifest_path,
                        aggregate_hash = EXCLUDED.aggregate_hash,
                        updated_at = NOW()
                    """,
                    (
                        record.name,
                        record.path,
                        record.model,
                        record.transcript_kind,
                        record.clip_count,
                        record.manifest_path,
                        record.aggregate_hash,
                    ),
                )
        return record

    def list_records(self, limit: Optional[int] = None) -> List[ReferenceRecord]:
        with get_connection() as conn:
            with conn.cursor() as cur:
                sql = """
                    SELECT name, path, model, transcript_kind, clip_count, manifest_path, aggregate_hash
                    FROM diarization_references
                    ORDER BY updated_at DESC
                """
                if limit:
                    sql += " LIMIT %s"
                    cur.execute(sql, (limit,))
                else:
                    cur.execute(sql)
                rows = cur.fetchall()
                records: List[ReferenceRecord] = []
                for row in rows:
                    records.append(
                        ReferenceRecord(
                            name=row[0],
                            path=row[1],
                            model=row[2],
                            transcript_kind=row[3],
                            clip_count=row[4],
                            manifest_path=row[5],
                            aggregate_hash=row[6],
                        )
                    )
                return records

    def get(self, name: str) -> Optional[ReferenceRecord]:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT name, path, model, transcript_kind, clip_count, manifest_path, aggregate_hash
                    FROM diarization_references
                    WHERE name=%s
                    """,
                    (name,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return ReferenceRecord(
                    name=row[0],
                    path=row[1],
                    model=row[2],
                    transcript_kind=row[3],
                    clip_count=row[4],
                    manifest_path=row[5],
                    aggregate_hash=row[6],
                )
