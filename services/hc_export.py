"""vidops/services/hc_export.py

Phase 7: Explicit Export & Packaging (optional).

This module implements *explicit*, user-initiated export of a project's
already-produced artifacts into a deterministic package (directory + zip).

Constraints (binding):
- No automation / no background work
- No recomputation (does not render, rebuild, or enqueue runs)
- No DB mutations (observational only)
- Storage-safe (never overwrites existing exports; uses unique export ids)
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from configuration import get_project_root
from dal import HCProjectRepository, HCFinalizationRepository, HCClipRepository, HCHitRepository


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_name(s: str) -> str:
    # Conservative filesystem-safe slug (no fancy deps).
    keep = []
    for ch in (s or ""):
        if ch.isalnum() or ch in ("-", "_", "."):
            keep.append(ch)
        elif ch.isspace():
            keep.append("_")
    out = "".join(keep).strip("_.")
    return out or "project"


@dataclass
class ExportResult:
    export_id: str
    export_dir: Path
    zip_path: Path
    manifest: Dict[str, Any]


class HCProjectExportService:
    """Phase 7 export service.

    Produces a directory:
      <output_root>/<project_slug>__<project_id>/<export_id>/
        manifest.json
        clips/...

    And a zip:
      <output_root>/<project_slug>__<project_id>/<export_id>.zip
    """

    def __init__(self):
        self.projects = HCProjectRepository()
        self.finalizations = HCFinalizationRepository()
        self.clips = HCClipRepository()
        self.hits = HCHitRepository()

    def export_project(
        self,
        project_id: str,
        *,
        actor: str,
        output_root: Optional[str] = None,
        require_finalized: bool = True,
        include_assets: bool = True,
    ) -> ExportResult:
        project = self.projects.get_project(project_id)
        if not project:
            raise ValueError(f"project not found: {project_id}")

        # Defaults: keep exports inside workspace unless explicitly overridden.
        base = Path(output_root) if output_root else (Path(get_project_root()) / "exports")
        base.mkdir(parents=True, exist_ok=True)

        status = str(project.get("status") or "")
        if require_finalized and status != "finalized":
            raise ValueError("project is not finalized; export requires finalized unless overridden")

        export_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
        project_slug = _safe_name(str(project.get("name") or "project"))
        root_dir = base / f"{project_slug}__{project_id}"
        export_dir = root_dir / export_id
        if export_dir.exists():
            # Extremely unlikely due to uuid, but do not overwrite.
            raise ValueError(f"export dir already exists: {export_dir}")
        export_dir.mkdir(parents=True, exist_ok=False)

        # Determine the exact artifact set to export.
        finalization = self.finalizations.get_latest_finalization(project_id)
        artifacts: List[Dict[str, Any]] = []
        if status == "finalized" and finalization:
            artifacts = self.finalizations.list_finalized_artifacts(str(finalization["finalization_id"]))
        elif not require_finalized:
            # Export latest versions (observational snapshot). This is allowed only
            # when explicitly requested.
            # NOTE: this does not write to DB; it is just a read of latest tables.
            latest_hits = self.hits.list_hits(project_id, latest_only=True, limit=100000)
            latest_clips = self.clips.list_clips(project_id, limit=100000)
            for h in latest_hits:
                artifacts.append(
                    {
                        "artifact_kind": "hit",
                        "artifact_id": str(h.get("hit_id")),
                        "artifact_logical_id": str(h.get("hit_logical_id")),
                        "artifact_version": int(h.get("hit_version") or 0),
                    }
                )
            for c in latest_clips:
                artifacts.append(
                    {
                        "artifact_kind": "clip",
                        "artifact_id": str(c.get("clip_id")),
                        "artifact_logical_id": str(c.get("clip_logical_id")),
                        "artifact_version": int(c.get("clip_version") or 0),
                    }
                )

        # Build manifest + copy assets (if requested).
        clips_dir = export_dir / "clips"
        if include_assets:
            clips_dir.mkdir(parents=True, exist_ok=True)

        exported_clips: List[Dict[str, Any]] = []
        exported_hits: List[Dict[str, Any]] = []
        missing_assets: List[Dict[str, Any]] = []

        for a in artifacts:
            kind = str(a.get("artifact_kind") or "")
            if kind == "hit":
                hit = self.hits.get_hit(str(a.get("artifact_id")))
                if not hit:
                    exported_hits.append({"hit_id": str(a.get("artifact_id")), "missing": True})
                    continue
                spans = []
                try:
                    spans = self.hits.get_hit_spans(str(hit["hit_id"]))
                except Exception:
                    spans = []
                exported_hits.append(
                    {
                        "hit_id": str(hit.get("hit_id")),
                        "hit_logical_id": str(hit.get("hit_logical_id")),
                        "hit_version": int(hit.get("hit_version") or 0),
                        "ytid": hit.get("ytid"),
                        "label": hit.get("label"),
                        "status": hit.get("status"),
                        "validity_status": hit.get("validity_status"),
                        "spans": spans,
                    }
                )
            elif kind == "clip":
                clip = self.clips.get_clip(str(a.get("artifact_id")))
                if not clip:
                    exported_clips.append({"clip_id": str(a.get("artifact_id")), "missing": True})
                    continue

                asset_path = clip.get("asset_path")
                clip_entry: Dict[str, Any] = {
                    "clip_id": str(clip.get("clip_id")),
                    "clip_logical_id": str(clip.get("clip_logical_id")),
                    "clip_version": int(clip.get("clip_version") or 0),
                    "label": clip.get("label"),
                    "status": clip.get("status"),
                    "validity_status": clip.get("validity_status"),
                    "ytid": clip.get("ytid"),
                    "start_sec": float(clip.get("start_sec") or 0.0),
                    "end_sec": float(clip.get("end_sec") or 0.0),
                    "profile_name": clip.get("profile_name"),
                    "profile_version": clip.get("profile_version"),
                    "hit_id": str(clip.get("hit_id")) if clip.get("hit_id") else None,
                    "asset": None,
                }

                if include_assets and asset_path:
                    src = Path(str(asset_path))
                    if src.exists() and src.is_file():
                        # Deterministic-ish name within export.
                        fn = _safe_name(f"clip_{clip_entry['clip_logical_id']}_v{clip_entry['clip_version']}")
                        ext = src.suffix or ".bin"
                        dst = clips_dir / f"{fn}{ext}"
                        if dst.exists():
                            # No overwrites; add suffix.
                            dst = clips_dir / f"{fn}__{uuid.uuid4().hex[:6]}{ext}"
                        shutil.copy2(src, dst)
                        sha = _sha256_file(dst)
                        clip_entry["asset"] = {
                            "source_path": str(src),
                            "export_relpath": str(dst.relative_to(export_dir)),
                            "bytes": dst.stat().st_size,
                            "sha256": sha,
                        }
                    else:
                        missing_assets.append(
                            {
                                "clip_id": clip_entry["clip_id"],
                                "asset_path": str(asset_path),
                                "reason": "asset_path missing on disk",
                            }
                        )
                exported_clips.append(clip_entry)

        manifest: Dict[str, Any] = {
            "schema": "vidops.hc_export_manifest.v1",
            "export_id": export_id,
            "exported_at": _utc_now_iso(),
            "actor": actor,
            "project": {
                "project_id": str(project.get("project_id")),
                "name": project.get("name"),
                "status": project.get("status"),
            },
            "finalization": {
                "finalization_id": str(finalization.get("finalization_id")) if finalization else None,
                "finalized_at": str(finalization.get("finalized_at")) if finalization else None,
                "finalized_by": finalization.get("finalized_by") if finalization else None,
                "reason": finalization.get("reason") if finalization else None,
            },
            "options": {
                "require_finalized": bool(require_finalized),
                "include_assets": bool(include_assets),
            },
            "hits": exported_hits,
            "clips": exported_clips,
            "warnings": {
                "missing_assets": missing_assets,
            },
        }

        (export_dir / "manifest.json").write_text(_stable_json(manifest) + "\n", encoding="utf-8")

        # Zip the export directory.
        zip_path = root_dir / f"{export_id}.zip"
        if zip_path.exists():
            raise ValueError(f"zip already exists: {zip_path}")
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for p in export_dir.rglob("*"):
                if p.is_file():
                    zf.write(p, arcname=str(p.relative_to(export_dir)))

        return ExportResult(export_id=export_id, export_dir=export_dir, zip_path=zip_path, manifest=manifest)
