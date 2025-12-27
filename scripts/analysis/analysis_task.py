from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Dict, Any, List


class TaskStatus(str, Enum):
    """Task lifecycle states for analysis tasks."""
    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class AnalysisTask:
    """
    In-memory representation of a row in the analysis_tasks table.

    This model is intentionally simple and matches the schema described in
    DISTRIBUTED_ANALYSIS_IMPLEMENTATION_GUIDE.md.
    """

    task_id: int
    job_id: str
    ytid: str
    chunk_id: int
    pass_id: str
    chunk_text: str

    chunk_metadata: Dict[str, Any] = field(default_factory=dict)
    required_capabilities: List[str] = field(default_factory=list)
    required_vram_gb: float = 0.0
    model_profile_id: Optional[int] = None

    status: TaskStatus = TaskStatus.PENDING
    result_json: Optional[Dict[str, Any]] = None

    claimed_by: Optional[str] = None
    claimed_at: Optional[datetime] = None
    lease_expires_at: Optional[datetime] = None

    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "AnalysisTask":
        """
        Build an AnalysisTask from a database row (dict-like).
        """
        if row is None:
            raise ValueError("Cannot build AnalysisTask from None row")
        if not isinstance(row, dict):
            try:
                from psycopg2.extras import RealDictRow
                if isinstance(row, RealDictRow):
                    row = dict(row)
                else:
                    raise TypeError
            except Exception:
                raise TypeError("Row must be dict-like with keys") from None
        raw_status = row.get("status") or TaskStatus.PENDING.value
        try:
            status = TaskStatus(raw_status)
        except ValueError:
            # Unknown status in DB – treat as FAILED so it surfaces
            status = TaskStatus.FAILED

        return cls(
            task_id=row.get("task_id", 0),
            job_id=row["job_id"],
            ytid=row["ytid"],
            chunk_id=row["chunk_id"],
            pass_id=row["pass_id"],
            chunk_text=row["chunk_text"],
            chunk_metadata=row.get("chunk_metadata") or {},
            required_capabilities=row.get("required_capabilities") or [],
            required_vram_gb=float(row.get("required_vram_gb") or 0.0),
            model_profile_id=row.get("model_profile_id"),
            status=status,
            result_json=row.get("result_json"),
            claimed_by=row.get("claimed_by"),
            claimed_at=row.get("claimed_at"),
            lease_expires_at=row.get("lease_expires_at"),
            started_at=row.get("started_at"),
            completed_at=row.get("completed_at"),
            error_message=row.get("error_message"),
            created_at=row.get("created_at") or datetime.now(timezone.utc),
            updated_at=row.get("updated_at") or datetime.now(timezone.utc),
        )

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to a dict suitable for database insertion/update.

        - Enum values are converted to their primitive representation.
        - datetimes are ensured to be timezone-aware (UTC).
        """
        data = asdict(self)
        data["status"] = self.status.value

        # Ensure timestamps have timezone info (default to UTC)
        for key in [
            "created_at",
            "updated_at",
            "claimed_at",
            "lease_expires_at",
            "started_at",
            "completed_at",
        ]:
            dt = data.get(key)
            if dt is not None and getattr(dt, "tzinfo", None) is None:
                data[key] = dt.replace(tzinfo=timezone.utc)

        return data
