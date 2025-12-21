import tempfile
import types
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from services.quickclip import QuickClipService


class QuickClipDownloadHelperTests(unittest.TestCase):
    def setUp(self):
        self._load_config = patch("services.quickclip.load_config", lambda: types.SimpleNamespace())
        self._load_config.start()
        self.service = QuickClipService(
            video_repo=Mock(),
            quickclip_repo=Mock(),
            download_service=types.SimpleNamespace(),
            clipping_service=Mock(),
            fs_cache=Mock(),
        )

    def tearDown(self):
        self._load_config.stop()

    def test_modern_download_accepts_overrides(self):
        records = []

        class ModernDownload:
            def enqueue_download(self, *, url, priority, ytdlp_overrides=None):
                records.append((url, priority, ytdlp_overrides))
                return types.SimpleNamespace(job_id="modern")

        self.service.download_service = ModernDownload()
        result = self.service._enqueue_download_with_overrides("http://example.com", 90, "best")
        self.assertEqual(result.job_id, "modern")
        self.assertTrue(records and records[0][2] is not None)

    def test_modern_download_type_error_fallback(self):
        attempts = []

        class FlakyDownload:
            def __init__(self):
                self.fail_once = True

            def enqueue_download(self, *, url, priority, ytdlp_overrides=None):
                attempts.append((url, priority, ytdlp_overrides))
                if self.fail_once:
                    self.fail_once = False
                    raise TypeError("unexpected param")
                return types.SimpleNamespace(job_id="fallback")

        self.service.download_service = FlakyDownload()
        result = self.service._enqueue_download_with_overrides("http://example.com", 10, "best")
        self.assertEqual(result.job_id, "fallback")
        self.assertEqual(len(attempts), 2)

    def test_legacy_download_without_overrides(self):
        records = []

        class LegacyDownload:
            def enqueue_download(self, url, priority):
                records.append((url, priority))
                return types.SimpleNamespace(job_id="legacy")

        self.service.download_service = LegacyDownload()
        result = self.service._enqueue_download_with_overrides("http://example.com", 5, "best")
        self.assertEqual(result.job_id, "legacy")
        self.assertEqual(records, [("http://example.com", 5)])

    def test_audio_only_asset_with_ogx_extension_triggers_redownload(self):
        calls = []

        class StubDownloadService:
            def enqueue_download(self, *, url, priority, ytdlp_overrides=None):
                calls.append((url, priority, ytdlp_overrides))
                return SimpleNamespace(job_id="dl-job")

        class StubClippingService:
            def enqueue_manifest_job(self, **kwargs):
                return SimpleNamespace(job_id="clip-job")

        class StubQuickClipRepo:
            def create_session(self, **kwargs):
                return None

            def create_clip(self, **kwargs):
                return None

            def update_session_stats(self, **kwargs):
                return None

        class StubVideoRepo:
            def __init__(self):
                self.asset = SimpleNamespace(rel_path="raw/video.ogx")
                self.video = SimpleNamespace(
                    ytid="abcdefghijk",
                    url=None,
                    title=None,
                    upload_date=None,
                    duration_sec=None,
                    channel=None,
                    channel_id=None,
                    extractor_key="youtube",
                    tags=[],
                    categories=[],
                )

            def get(self, ytid):
                return self.video

            def upsert(self, video):
                self.video = video

            def get_primary_asset(self, ytid, kind):
                return self.asset

        with tempfile.TemporaryDirectory() as tmpdir:
            service = QuickClipService(
                video_repo=StubVideoRepo(),
                quickclip_repo=StubQuickClipRepo(),
                download_service=StubDownloadService(),
                clipping_service=StubClippingService(),
                fs_cache=Mock(),
            )

            result = service.create_quickclip(
                url="https://youtu.be/abcdefghijk",
                spans=["0-1"],
                quality="best",
                output_dir=tmpdir,
                priority=1,
            )

        self.assertEqual(len(calls), 1, "Audio-only asset should force a new download job")
        self.assertEqual(result.get("download_job_id"), "dl-job")
        self.assertTrue(result.get("full_video_saved"))


if __name__ == "__main__":
    unittest.main()
