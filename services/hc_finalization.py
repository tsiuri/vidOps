# vidops/services/hc_finalization.py

from __future__ import annotations

from typing import Any, Dict, Optional

from dal import HCProjectRepository, HCHitRepository, HCClipRepository, HCFinalizationRepository


class HCProjectFinalizationService:
    """Phase 5: explicit, user-driven project finalization.

    - Marks hc_projects.status = 'finalized'
    - Persists a frozen list of hit/clip *versions* in hc_finalized_artifacts
    - Non-destructive: does not delete/prune/GC
    """

    def __init__(self) -> None:
        self.projects = HCProjectRepository()
        self.hits = HCHitRepository()
        self.clips = HCClipRepository()
        self.finalizations = HCFinalizationRepository()

    def finalize_project(
        self,
        project_id: str,
        *,
        actor: Optional[str] = None,
        reason: Optional[str] = None,
        policy: Optional[Dict[str, Any]] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        project = self.projects.get_project(project_id)
        if not project:
            raise ValueError("project not found")

        if project.get("status") == "archived":
            raise ValueError("cannot finalize an archived project")

        latest = self.finalizations.get_latest_finalization(project_id)
        if latest and latest.get("status") == "finalized" and not force:
            raise ValueError("project is already finalized")

        policy = policy or {}
        hit_policy = policy.get("hits") or {}
        clip_policy = policy.get("clips") or {}

        # Hits: default to pinned-only, active, latest, valid
        pinned_only = bool(hit_policy.get("pinned_only", True))
        hit_status = hit_policy.get("status", "active")
        hit_limit = int(hit_policy.get("limit", 20000))

        hit_rows = self.hits.list_hits(
            project_id,
            status=hit_status,
            pinned=True if pinned_only else None,
            validity_status="valid",
            latest_only=True,
            limit=hit_limit,
        )

        # Clips: default to latest, valid; optionally filter by status
        clip_status = clip_policy.get("status")
        clip_limit = int(clip_policy.get("limit", 20000))

        clip_rows = self.clips.list_clips(
            project_id,
            status=clip_status,
            validity_status="valid",
            limit=clip_limit,
        )

        artifacts = []
        for h in hit_rows:
            artifacts.append(
                {
                    "artifact_kind": "hit",
                    "artifact_id": h.get("hit_id"),
                    "artifact_logical_id": h.get("hit_logical_id"),
                    "artifact_version": h.get("hit_version"),
                }
            )
        for c in clip_rows:
            artifacts.append(
                {
                    "artifact_kind": "clip",
                    "artifact_id": c.get("clip_id"),
                    "artifact_logical_id": c.get("clip_logical_id"),
                    "artifact_version": c.get("clip_version"),
                }
            )

        snapshot = {
            "policy": {
                "hits": {
                    "pinned_only": pinned_only,
                    "status": hit_status,
                    "latest_only": True,
                    "validity_status": "valid",
                    "limit": hit_limit,
                },
                "clips": {
                    "status": clip_status,
                    "latest_only": True,
                    "validity_status": "valid",
                    "limit": clip_limit,
                },
            },
            "counts": {"hits": len(hit_rows), "clips": len(clip_rows)},
        }

        finalization_id = self.finalizations.create_finalization(
            project_id=project_id,
            finalized_by=actor,
            reason=reason,
            snapshot=snapshot,
            artifacts=artifacts,
        )

        # Mark project status finalized.
        self.projects.update_project(project_id, {"status": "finalized"})

        return {
            "finalization_id": finalization_id,
            "project_id": project_id,
            "status": "finalized",
            "snapshot": snapshot,
        }
