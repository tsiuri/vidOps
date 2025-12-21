# vidops/models/worker.py

from dataclasses import dataclass, field, asdict
from datetime import datetime, UTC
from enum import Enum
from typing import Optional, Dict, Any, List
import uuid

class WorkerStatus(str, Enum):
    """Enumeration for the status of a worker."""
    REGISTERING = "registering"
    IDLE = "idle"
    BUSY = "busy"
    STOPPING = "stopping"
    ERRORED = "errored"
    STALE = "stale" # Status assigned by Overlord, not by worker itself

@dataclass
class Worker:
    """
    Represents a worker instance in the system.
    Maps to the 'workers' table.
    """
    machine_alias: str # e.g., 'transcription', 'diarization'
    worker_type: str
    worker_id: str = field(default_factory=lambda: f"worker_{uuid.uuid4()}")
    status: WorkerStatus = WorkerStatus.REGISTERING
    
    # Capabilities & State
    capabilities: List[str] = field(default_factory=list) # e.g., ['gpu_0', 'model_medium']
    current_job_id: Optional[str] = None
    
    # Tracking
    registered_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_heartbeat: datetime = field(default_factory=lambda: datetime.now(UTC))
    
    # Metadata
    pid: Optional[int] = None
    hostname: Optional[str] = None
    
    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "Worker":
        """Creates a Worker instance from a database row."""
        if not all(k in row for k in ['worker_id', 'machine_alias', 'worker_type']):
            raise ValueError("Row is missing required fields for Worker model")

        status_str = row.get('status', 'errored')
        try:
            status = WorkerStatus(status_str)
        except ValueError:
            status = WorkerStatus.ERRORED

        return cls(
            worker_id=row['worker_id'],
            machine_alias=row['machine_alias'],
            worker_type=row['worker_type'],
            status=status,
            capabilities=row.get('capabilities') or [],
            current_job_id=row.get('current_job_id'),
            registered_at=row.get('registered_at', datetime.now(UTC)),
            last_heartbeat=row.get('last_heartbeat', datetime.now(UTC)),
            pid=row.get('pid'),
            hostname=row.get('hostname'),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Converts the Worker instance to a dictionary for database insertion."""
        data = asdict(self)
        data['status'] = self.status.value
        return data

if __name__ == "__main__":
    print("--- Testing Worker Model ---")
    
    # Creation
    worker_obj = Worker(
        machine_alias="gpu-rig-1",
        worker_type="transcription",
        capabilities=['gpu', 'cuda', 'model_large-v2'],
        pid=12345
    )
    print("Instance created:", worker_obj)
    assert worker_obj.status == WorkerStatus.REGISTERING
    assert worker_obj.machine_alias == "gpu-rig-1"

    # To Dict
    worker_dict = worker_obj.to_dict()
    print("Instance to dict:", worker_dict)
    assert worker_dict['status'] == 'registering'
    assert worker_dict['capabilities'][0] == 'gpu'

    # From Row
    db_row = worker_dict
    db_row['status'] = 'idle'
    worker_from_db = Worker.from_row(db_row)
    print("Instance from row:", worker_from_db)
    assert worker_from_db.status == WorkerStatus.IDLE
    assert worker_from_db.pid == 12345

    print("✅ Worker model tests passed.")