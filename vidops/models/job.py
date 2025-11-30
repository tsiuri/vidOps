from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Dict, Any
import uuid

class JobStatus(str, Enum):
    """Enumeration for the status of a job."""
    PENDING = "pending"
    CLAIMED = "claimed"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass
class Job:
    """
    Represents a generic job in a queue.
    This can be subclassed or composed for specific job types.
    """
    job_id: str = field(default_factory=lambda: f"job_{uuid.uuid4()}")
    job_type: str = "generic"
    status: JobStatus = JobStatus.PENDING
    priority: int = 0
    
    # Media identifiers
    ytid: Optional[str] = None
    media_path: Optional[str] = None

    # Job-specific configuration
    config: Dict[str, Any] = field(default_factory=dict)
    result: Dict[str, Any] = field(default_factory=dict)
    error_message: Optional[str] = None
    
    # Tracking
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    claimed_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    lease_expires_at: Optional[datetime] = None
    
    # Worker info
    claimed_by: Optional[str] = None

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "Job":
        """Creates a Job instance from a database row."""
        if 'job_id' not in row:
            raise ValueError("Row is missing required 'job_id' field")
        
        status_str = row.get('status', JobStatus.PENDING.value)
        try:
            status = JobStatus(status_str)
        except ValueError:
            status = JobStatus.FAILED

        return cls(
            job_id=row['job_id'],
            job_type=row.get('job_type', 'generic'),
            status=status,
            priority=row.get('priority', 0),
            ytid=row.get('ytid'),
            media_path=row.get('media_path'),
            config=row.get('config') or {},
            created_at=row.get('created_at', datetime.now(timezone.utc)),
            updated_at=row.get('updated_at', datetime.now(timezone.utc)),
            claimed_at=row.get('claimed_at'),
            started_at=row.get('started_at'),
            completed_at=row.get('completed_at'),
            lease_expires_at=row.get('lease_expires_at'),
            claimed_by=row.get('claimed_by'),
            result=row.get('result') or {},
            error_message=row.get('error_message'),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Converts the Job instance to a dictionary for database insertion."""
        payload = asdict(self)
        payload['status'] = self.status.value

        for key in ['created_at', 'updated_at', 'claimed_at', 'started_at', 'completed_at', 'lease_expires_at']:
            if payload.get(key) and payload[key].tzinfo is None:
                payload[key] = payload[key].replace(tzinfo=timezone.utc)

        return payload

if __name__ == "__main__":
    print("--- Testing Job Model ---")
    
    # Creation
    job_obj = Job(
        job_type="transcription", 
        ytid="dQw4w9WgXcQ",
        config={'model': 'small'}
    )
    print("Instance created:", job_obj)
    assert job_obj.status == JobStatus.PENDING
    assert job_obj.job_type == "transcription"

    # To Dict
    job_dict = job_obj.to_dict()
    print("Instance to dict:", job_dict)
    assert job_dict['status'] == 'pending'
    assert job_dict['config']['model'] == 'small'

    # From Row
    db_row = {
        'job_id': job_obj.job_id,
        'job_type': 'transcription', # Added for from_row test
        'status': 'claimed',
        'priority': 0,
        'ytid': 'dQw4w9WgXcQ',
        'media_path': '/path/to/media.mp4',
        'config': {'model': 'small'},
        'created_at': datetime.now(timezone.utc),
        'updated_at': datetime.now(timezone.utc),
        'claimed_at': datetime.now(timezone.utc),
        'started_at': None,
        'completed_at': None,
        'lease_expires_at': None,
        'claimed_by': 'test-worker-1',
        'error_message': None,
        'result': {}
    }
    job_from_db = Job.from_row(db_row)
    print("Instance from row:", job_from_db)
    assert job_from_db.status == JobStatus.CLAIMED
    assert job_from_db.ytid == 'dQw4w9WgXcQ'
    assert job_from_db.config['model'] == 'small' # Check config after remapping

    print("✅ Job model tests passed.")
