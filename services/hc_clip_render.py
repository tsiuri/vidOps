# vidops/services/hc_clip_render.py

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from configuration import load_config
from dal import FilesystemCache, VideoRepository
from dal.hc_clips import HCClipRepository
import psycopg2.extras
from db import get_connection

logger = logging.getLogger(__name__)


def _stable_json(obj: Any) -> str:
    try:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except Exception:
        return str(obj)


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk_size)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _hash_fingerprint(*parts: Any) -> str:
    h = hashlib.sha256()
    for p in parts:
        if p is None:
            h.update(b"<null>")
        else:
            h.update(str(p).encode("utf-8", errors="replace"))
        h.update(b"\x00")
    return h.hexdigest()


class HCClipRenderService:
    """Explicit clip rendering + rebuild orchestration.

    This service performs synchronous rendering; it does not schedule background work.
    """

    def __init__(self):
        self.cfg = load_config()
        self.clips = HCClipRepository()
        self.videos = VideoRepository()
        self.fs = FilesystemCache()

    def _select_media_asset_path(self, ytid: str) -> Optional[Path]:
        # Prefer container video assets when present; fall back to "media".
        priority = ["mp4", "mkv", "webm", "media"]
        for kind in priority:
            asset = self.videos.get_primary_asset(ytid, kind)
            if not asset:
                continue
            p = asset.rel_path or asset.path
            if not p:
                continue
            resolved = self.fs.get_central_path(str(p))
            if resolved.exists():
                return resolved
        return None

    def _media_fingerprint(self, media_path: Path) -> Tuple[str, Dict[str, Any]]:
        # Deterministic fingerprint for the file content.
        fp = _sha256_file(media_path)
        meta = {"bytes": media_path.stat().st_size}
        return fp, meta

    def _get_output_root(self, override: Optional[str]) -> Path:
        # Opt-in: rendering is disabled unless explicitly enabled OR an override is provided.
        enabled = bool(getattr(getattr(self.cfg, "hc_clips", object()), "render_enabled", False))
        configured_root = getattr(getattr(self.cfg, "hc_clips", object()), "output_root", None)
        root = override or configured_root

        if not root:
            raise ValueError(
                "Clip rendering is opt-in. Provide output_root in the request or set hc_clips.output_root in config."
            )
        if not enabled and not override:
            raise ValueError(
                "Clip rendering is disabled by config (hc_clips.render_enabled=false). Set it true or pass output_root explicitly."
            )

        p = Path(root)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def render_clip(self, *, clip_id: str, actor: str, output_root: Optional[str] = None, render_params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Render a specific clip version (explicit, synchronous)."""

        clip = self.clips.get_clip(clip_id)
        if not clip:
            raise ValueError(f"clip not found: {clip_id}")

        if clip.get("status") not in ("planned", "failed"):
            raise ValueError(f"clip not renderable from status={clip.get('status')}")

        ytid = clip.get("ytid")
        if not ytid:
            raise ValueError("clip missing ytid; cannot resolve media")

        media_path = self._select_media_asset_path(str(ytid))
        if not media_path:
            raise FileNotFoundError(f"no media asset found on disk for ytid={ytid}")

        out_root = self._get_output_root(output_root)

        # Default render params: stream copy clip.
        rp = render_params or {}
        fmt = str(rp.get("format") or "mp4")
        mode = str(rp.get("mode") or "copy")  # copy | reencode

        # Deterministic fingerprints.
        media_fp, media_meta = self._media_fingerprint(media_path)
        hit_snapshot_fp = _hash_fingerprint("hit_snapshot", _stable_json(clip.get("hit_snapshot") or {}))
        clip_cfg_fp = _hash_fingerprint(
            "clip_cfg",
            str(clip.get("profile_name") or ""),
            str(clip.get("profile_version") or ""),
            str(clip.get("start_sec")),
            str(clip.get("end_sec")),
        )
        rp_fp = _hash_fingerprint("render_params", _stable_json(rp))
        render_input_fp = _hash_fingerprint("hc_clip_render", media_fp, hit_snapshot_fp, clip_cfg_fp, rp_fp)

        # Output path is content-addressed (fingerprint-linked) and versioned.
        logical_id = str(clip.get("clip_logical_id"))
        ver = int(clip.get("clip_version") or 1)
        subdir = out_root / "hc_clips" / str(clip.get("project_id")) / logical_id
        subdir.mkdir(parents=True, exist_ok=True)
        out_path = subdir / f"v{ver:04d}_{render_input_fp[:16]}.{fmt}"
        if out_path.exists():
            raise FileExistsError(f"refusing to overwrite existing clip output: {out_path}")

        # Transition planned/failed -> rendering
        self.clips.transition_status(clip_id=clip_id, from_status=str(clip.get("status")), to_status="rendering", actor=actor, reason="explicit render")

        # Store render plan metadata (best-effort)
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE hc_clips
                        SET render_params = %s::jsonb,
                            render_input_fingerprint = %s,
                            updated_at = NOW()
                        WHERE clip_id = %s
                        """,
                        (psycopg2.extras.Json(rp), render_input_fp, clip_id),
                    )
        except Exception:
            logger.warning("Failed to persist render plan for %s", clip_id, exc_info=True)

        # Execute ffmpeg.
        start = float(clip.get("start_sec") or 0.0)
        end = float(clip.get("end_sec") or 0.0)
        duration = max(0.0, end - start)

        if duration <= 0:
            self.clips.mark_render_result(
                clip_id=clip_id,
                status="failed",
                asset_path=None,
                asset_meta={},
                provenance_patch={"render": {"input_fingerprint": render_input_fp}},
                actor=actor,
                error="invalid time range",
            )
            raise ValueError("invalid clip time range")

        cmd = ["ffmpeg", "-hide_banner", "-y", "-ss", f"{start:.3f}", "-i", str(media_path), "-t", f"{duration:.3f}"]
        if mode == "reencode":
            cmd += ["-c:v", "libx264", "-c:a", "aac"]
        else:
            cmd += ["-c", "copy"]
        cmd += [str(out_path)]

        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0:
            err_tail = (proc.stderr or "").splitlines()[-40:]
            err_msg = "\n".join(err_tail)[:8000]
            self.clips.mark_render_result(
                clip_id=clip_id,
                status="failed",
                asset_path=None,
                asset_meta={"media_path": str(media_path), "media_fingerprint": media_fp, "ffmpeg": {"returncode": proc.returncode}},
                provenance_patch={
                    "render": {
                        "input_fingerprint": render_input_fp,
                        "params": rp,
                        "media_fingerprint": media_fp,
                        "hit_snapshot_fingerprint": hit_snapshot_fp,
                    }
                },
                actor=actor,
                error=err_msg,
            )
            raise RuntimeError(f"ffmpeg failed (code={proc.returncode})")

        # Success
        out_fp = _sha256_file(out_path)
        asset_meta = {
            "output_fingerprint": out_fp,
            "input_fingerprint": render_input_fp,
            "bytes": out_path.stat().st_size,
            "format": fmt,
            "mode": mode,
            "media_path": str(media_path),
            "media_fingerprint": media_fp,
            "media_meta": media_meta,
        }
        prov_patch = {
            "render": {
                "params": rp,
                "input_fingerprint": render_input_fp,
                "output_fingerprint": out_fp,
                "media_fingerprint": media_fp,
                "hit_snapshot_fingerprint": hit_snapshot_fp,
                "clip_cfg_fingerprint": clip_cfg_fp,
            }
        }
        self.clips.mark_render_result(
            clip_id=clip_id,
            status="ready",
            asset_path=str(out_path),
            asset_meta=asset_meta,
            provenance_patch=prov_patch,
            actor=actor,
            error=None,
        )

        return {
            "clip_id": str(clip_id),
            "status": "ready",
            "asset_path": str(out_path),
            "asset_meta": asset_meta,
        }

    def rebuild_clip(
        self,
        *,
        source_clip_id: str,
        actor: str,
        reason: Optional[str] = None,
        output_root: Optional[str] = None,
        render_params: Optional[Dict[str, Any]] = None,
        render_now: bool = True,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new clip version (append-only) and (optionally) render it.

        Rebuilds are user-initiated only and never overwrite prior versions.
        """

        new_id = self.clips.create_rebuild_version(
            source_clip_id=source_clip_id,
            run_id=run_id,
            actor=actor,
            reason=reason,
            render_params=render_params or {},
        )
        if not render_now:
            return {"clip_id": new_id, "status": "planned"}
        return self.render_clip(clip_id=new_id, actor=actor, output_root=output_root, render_params=render_params)
