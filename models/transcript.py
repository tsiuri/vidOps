# vidops/models/transcript.py

from dataclasses import dataclass, field, asdict
from datetime import datetime, UTC
from typing import Optional, List, Any, Dict

@dataclass
class Word:
    """
    Represents a single transcribed word with timing information.
    Maps to the 'words' table.
    """
    ytid: str
    source: str # e.g., 'whisper-medium', 'yt_auto'
    word: str
    start_sec: float
    end_sec: float
    confidence: Optional[float] = None
    idx: Optional[int] = None
    segment_id: Optional[int] = None
    job_id: Optional[str] = None

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "Word":
        """Creates a Word instance from a database row."""
        if not all(k in row for k in ['ytid', 'source', 'word', 'start_sec', 'end_sec']):
            raise ValueError("Row is missing required fields for Word model")

        return cls(
            ytid=row['ytid'],
            source=row['source'],
            word=row['word'],
            start_sec=float(row['start_sec']),
            end_sec=float(row['end_sec']),
            confidence=float(row['confidence']) if row.get('confidence') is not None else None,
            idx=row.get('idx'),
            segment_id=row.get('segment_id'),
            job_id=row.get('job_id'),
        )
    
    def to_tuple(self, job_id: Optional[str] = None) -> tuple:
        """Converts the Word instance to a tuple for bulk database insertion."""
        return (
            self.ytid,
            self.source,
            self.idx,
            self.word,
            self.start_sec,
            self.end_sec,
            self.confidence,
            self.segment_id,
            job_id if job_id is not None else self.job_id,
        )


@dataclass
class Transcript:
    """

    Represents the full transcription metadata for a video.
    Maps to the 'transcripts' table.
    """
    ytid: str
    kind: str # e.g., 'words_whisper_medium', 'vtt_whisper_medium'
    lang: str = 'en'
    path: Optional[str] = None # Path in central storage
    word_count: Optional[int] = None
    segment_count: Optional[int] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # The actual content is not stored in the object by default,
    # but can be loaded on demand.
    words: Optional[List[Word]] = field(default=None, repr=False)
    vtt_content: Optional[str] = field(default=None, repr=False)

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "Transcript":
        """Creates a Transcript instance from a database row."""
        if not all(k in row for k in ['ytid', 'kind']):
            raise ValueError("Row is missing required fields for Transcript model")

        return cls(
            ytid=row['ytid'],
            kind=row['kind'],
            lang=row.get('lang', 'en'),
            path=row.get('path'),
            word_count=row.get('word_count'),
            segment_count=row.get('segment_count'),
            created_at=row.get('created_at', datetime.now(UTC)),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Converts the Transcript instance to a dictionary for database insertion."""
        d = asdict(self)
        # Exclude content fields from the dictionary
        del d['words']
        del d['vtt_content']
        return d

if __name__ == "__main__":
    print("--- Testing Word Model ---")
    word_data = {
        'ytid': 'dQw4w9WgXcQ',
        'source': 'whisper-medium',
        'word': 'Never',
        'start_sec': 0.5,
        'end_sec': 1.0,
        'confidence': -0.123,
        'idx': 0
    }
    word_obj = Word.from_row(word_data)
    print("Instance from row:", word_obj)
    assert word_obj.word == 'Never'

    word_tuple = word_obj.to_tuple()
    print("Instance to tuple:", word_tuple)
    assert word_tuple[3] == 'Never'
    print("✅ Word model tests passed.")

    print("\n--- Testing Transcript Model ---")
    transcript_data = {
        'ytid': 'dQw4w9WgXcQ',
        'kind': 'words_whisper_medium',
        'lang': 'en',
        'word_count': 123,
    }
    transcript_obj = Transcript.from_row(transcript_data)
    transcript_obj.words = [word_obj] # Add content after creation
    print("Instance from row:", transcript_obj)
    assert transcript_obj.kind == 'words_whisper_medium'

    transcript_dict = transcript_obj.to_dict()
    print("Instance to dict:", transcript_dict)
    assert 'words' not in transcript_dict
    assert transcript_dict['ytid'] == 'dQw4w9WgXcQ'
    print("✅ Transcript model tests passed.")
