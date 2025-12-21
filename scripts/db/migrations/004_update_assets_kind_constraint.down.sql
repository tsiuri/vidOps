-- Revert asset kinds to the prior set.
ALTER TABLE assets DROP CONSTRAINT IF EXISTS assets_kind_check;
ALTER TABLE assets
    ADD CONSTRAINT assets_kind_check CHECK (
        kind = ANY (ARRAY[
            'info_json',
            'src_json',
            'mp4',
            'mkv',
            'webm',
            'm4a',
            'opus',
            'vtt',
            'words_ytt',
            'words_whisper',
            'cut',
            'transcript_vtt',
            'transcript_words',
            'clip',
            'analysis_json',
            'media',
            'clips_manifest'
        ])
    );
