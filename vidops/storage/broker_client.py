from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import httpx

from vidops.config import StorageBrokerConfig

logger = logging.getLogger(__name__)


class StorageBrokerClient:
    """
    Lightweight HTTP client for the storage broker service.
    Handles artifact uploads/downloads via the broker instead of direct filesystem access.
    """

    def __init__(self, config: StorageBrokerConfig):
        self.enabled = bool(config.enabled and config.base_url and config.shared_token)
        self.base_url = config.base_url.rstrip("/") if config.base_url else ""
        self.token = config.shared_token or ""
        self.timeout = config.request_timeout

    def _headers(self) -> dict:
        # Use standard Bearer token auth
        return {"Authorization": f"Bearer {self.token}"}

    def upload_asset(
        self,
        local_path: Path,
        ytid: str,
        kind: str,
        relative_path: Optional[str] = None,
    ) -> Optional[str]:
        """
        Upload a file to the broker and register it as an asset.
        Returns the canonical relative path if successful.
        """
        if not self.enabled:
            return None

        rel = relative_path or local_path.name
        url = f"{self.base_url}/v1/assets/upload"

        try:
            client_kwargs = {"timeout": self.timeout}
            # Optional mTLS/CA settings if configured (available via global config)
            from vidops.config import load_config
            cfg = load_config()
            mtls_cert = getattr(cfg.storage_broker, "mtls_client_cert", None)
            mtls_key = getattr(cfg.storage_broker, "mtls_client_key", None)
            ca_bundle = getattr(cfg.storage_broker, "mtls_ca_cert", None)
            if ca_bundle:
                client_kwargs["verify"] = ca_bundle
            if mtls_cert and mtls_key:
                client_kwargs["cert"] = (mtls_cert, mtls_key)

            with httpx.Client(**client_kwargs) as client, local_path.open("rb") as fh:
                files = {"file": (local_path.name, fh)}
                data = {"ytid": ytid, "kind": kind, "relative_path": rel}
                resp = client.post(url, headers=self._headers(), data=data, files=files)
                resp.raise_for_status()
                payload = resp.json()
                logger.info("Broker uploaded asset %s (%s)", rel, kind)
                return payload.get("path")
        except Exception as exc:
            logger.warning("Storage broker upload failed for %s (%s): %s", rel, kind, exc)
            return None

    def download_asset(self, relative_path: str, destination: Path) -> bool:
        """
        Download an asset from the broker into the provided destination path.
        """
        if not self.enabled:
            return False

        url = f"{self.base_url}/v1/assets/download"
        params = {"relative_path": relative_path}
        try:
            client_kwargs = {"timeout": self.timeout}
            # Optional mTLS/CA settings if configured (available via global config)
            from vidops.config import load_config
            cfg = load_config()
            mtls_cert = getattr(cfg.storage_broker, "mtls_client_cert", None)
            mtls_key = getattr(cfg.storage_broker, "mtls_client_key", None)
            ca_bundle = getattr(cfg.storage_broker, "mtls_ca_cert", None)
            if ca_bundle:
                client_kwargs["verify"] = ca_bundle
            if mtls_cert and mtls_key:
                client_kwargs["cert"] = (mtls_cert, mtls_key)

            with httpx.Client(**client_kwargs) as client:
                resp = client.get(url, headers=self._headers(), params=params)
                resp.raise_for_status()
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(resp.content)
                logger.info("Broker downloaded asset %s -> %s", relative_path, destination)
                return True
        except Exception as exc:
            logger.warning("Failed to download asset %s via broker: %s", relative_path, exc)
            return False
