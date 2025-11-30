# vidops/models/video.py

from dataclasses import dataclass, field, asdict
from datetime import date, datetime, UTC
from typing import Optional, List, Any, Dict

@dataclass
class Video:
    """
    Represents a single video entry in the database.
    Maps to the 'videos' table.
    """
    ytid: str
    url: str
    title: str
    upload_date: Optional[date] = None
    duration_sec: Optional[int] = None
    channel: Optional[str] = None
    channel_id: Optional[str] = None
    extractor_key: Optional[str] = None
    tags: Optional[List[str]] = field(default_factory=list)
    categories: Optional[List[str]] = field(default_factory=list)
    upload_type: Optional[str] = None # e.g., 'vod', 'stream', 'clip'
    title_date: Optional[date] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "Video":
        """Creates a Video instance from a database row (dictionary)."""
        # Ensure all required fields are present
        if not all(k in row for k in ['ytid', 'url', 'title']):
            raise ValueError("Row is missing required fields for Video model")
            
        return cls(
            ytid=row.get('ytid'),
            url=row.get('url'),
            title=row.get('title'),
            upload_date=row.get('upload_date'),
            duration_sec=row.get('duration_sec'),
            channel=row.get('channel'),
            channel_id=row.get('channel_id'),
            extractor_key=row.get('extractor_key'),
            tags=row.get('tags') or [],
            categories=row.get('categories') or [],
            upload_type=row.get('upload_type'),
            title_date=row.get('title_date'),
            created_at=row.get('created_at', datetime.now(UTC)),
            updated_at=row.get('updated_at', datetime.now(UTC)),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Converts the Video instance to a dictionary for database insertion."""
        return asdict(self)

@dataclass
class Asset:
    """
    Represents a single file asset associated with a video.
    Maps to the 'assets' table.
    """
    path: str
    ytid: str
    kind: str  # e.g., 'media', 'transcript_vtt', 'info_json'
    size_bytes: Optional[int] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "Asset":
        """Creates an Asset instance from a database row."""
        if not all(k in row for k in ['path', 'ytid', 'kind']):
            raise ValueError("Row is missing required fields for Asset model")

        return cls(
            path=row.get('path'),
            ytid=row.get('ytid'),
            kind=row.get('kind'),
            size_bytes=row.get('bytes'),
            created_at=row.get('created_at', datetime.now(UTC))
        )

    def to_dict(self) -> Dict[str, Any]:
        """Converts the Asset instance to a dictionary."""
        data = asdict(self)
        # Database column is named 'bytes'
        data['bytes'] = data.pop('size_bytes')
        return data

if __name__ == '__main__':
    # Example Usage and Testing
    print("--- Testing Video Model ---")
    video_data = {
        'ytid': 'dQw4w9WgXcQ',
        'url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
        'title': 'Never Gonna Give You Up',
        'upload_date': date(1987, 7, 27),
        'duration_sec': 212,
        'channel': 'RickAstleyVEVO',
        'tags': ['music', 'pop'],
        'created_at': datetime.now(),
        'updated_at': datetime.now()
    }
    
    # From row
    video_obj = Video.from_row(video_data)
    print("Instance from row:", video_obj)
    assert video_obj.ytid == 'dQw4w9WgXcQ'
    
    # To dict
    video_dict = video_obj.to_dict()
    print("Instance to dict:", video_dict)
    assert video_dict['ytid'] == 'dQw4w9WgXcQ'
    print("✅ Video model tests passed.")

    print("\n--- Testing Asset Model ---")
    asset_data = {
        'path': '/storage/media/dQw4w9WgXcQ.mp4',
        'ytid': 'dQw4w9WgXcQ',
        'kind': 'media',
        'size_bytes': 12345678
    }

    # From row
    asset_obj = Asset.from_row(asset_data)
    print("Instance from row:", asset_obj)
    assert asset_obj.kind == 'media'

    # To dict
    asset_dict = asset_obj.to_dict()
    print("Instance to dict:", asset_dict)
    assert asset_dict['path'] == '/storage/media/dQw4w9WgXcQ.mp4'
    print("✅ Asset model tests passed.")
