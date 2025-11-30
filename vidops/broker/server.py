from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse

from vidops.config import load_config
from vidops.dal import VideoRepository
from vidops.models import Asset

logger = logging.getLogger(__name__)

app = FastAPI(title="VidOps Storage Broker")


def get_settings():
    config = load_config()
    broker_cfg = config.storage_broker
    if not broker_cfg.enabled:
        raise RuntimeError("Storage broker is disabled in config.")
    return broker_cfg, config.paths.central_storage_root


def verify_token(request: Request, settings=Depends(get_settings)):
    broker_cfg, _ = settings
    # Expect Authorization: Bearer <token>
    auth = request.headers.get("Authorization", "")
    token = ""
    if auth.startswith("Bearer "):
        token = auth[len("Bearer ") :].strip()

    # Accept either a single token (str) or a dict mapping machine_alias -> token
    tokens = broker_cfg.shared_token
    valid = False
    alias = None
    if isinstance(tokens, dict):
        # When per-worker map is provided, accept any token value present
        for k, v in tokens.items():
            if token == v:
                valid = True
                alias = k
                break
    else:
        valid = bool(tokens) and (token == tokens)

    if not valid:
        src = request.client.host if request.client else "unknown"
        xff = request.headers.get("X-Forwarded-For", "-")
        logger.warning("Unauthorized broker request src=%s xff=%s", src, xff)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing token.")
    # record alias on request for logging
    if alias:
        request.state.worker_alias = alias
    return True


def _storage_path(relative_path: str, storage_root: str) -> Path:
    rel = Path(relative_path)
    return Path(storage_root) / rel


@app.post("/v1/assets/upload")
async def upload_asset(
    request: Request,
    ytid: str = Form(...),
    kind: str = Form(...),
    relative_path: str = Form(...),
    file: UploadFile = File(...),
    token=Depends(verify_token),
    settings=Depends(get_settings),
):
    broker_cfg, storage_root = settings
    dest_path = _storage_path(relative_path, storage_root)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    data = await file.read()
    dest_path.write_bytes(data)
    alias = getattr(request.state, "worker_alias", None)
    if alias:
        logger.info("Broker stored asset %s (%s) from %s", relative_path, kind, alias)
    else:
        logger.info("Broker stored asset %s (%s)", relative_path, kind)

    # Register asset in DB
    video_repo = VideoRepository()
    asset = Asset(path=relative_path, ytid=ytid, kind=kind, size_bytes=dest_path.stat().st_size)
    try:
        video_repo.register_asset(asset)
    except Exception as exc:
        logger.warning("Failed to register asset %s for %s: %s", relative_path, ytid, exc)

    return JSONResponse({"path": relative_path})


@app.get("/v1/assets/download")
async def download_asset(
    relative_path: str,
    request: Request,
    token=Depends(verify_token),
    settings=Depends(get_settings),
):
    _, storage_root = settings
    file_path = _storage_path(relative_path, storage_root)
    if not file_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    alias = getattr(request.state, "worker_alias", None)
    if alias:
        logger.info("Broker download asset %s to %s", relative_path, alias)
    return FileResponse(file_path)


@app.get("/healthz")
async def healthz():
    # Lightweight health endpoint for proxy checks
    return JSONResponse({"status": "ok"})


def run():
    from uvicorn import Config, Server

    broker_cfg, _ = get_settings()
    config = Config(
        app="vidops.broker.server:app",
        host=broker_cfg.listen_host,
        port=broker_cfg.listen_port,
        log_level="info",
    )
    server = Server(config)
    server.run()
