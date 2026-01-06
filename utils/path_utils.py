from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

from configuration import PathsConfig


def resolve_db_path(path_str: str, cfg: PathsConfig) -> Path:
    """
    Resolve a path string from the DB using path_map/path_prefix rules.
    """
    if not path_str:
        return Path()

    mapped = _apply_path_map(path_str, cfg.path_map)
    if mapped is not None:
        return mapped

    path = Path(path_str)
    if path.is_absolute() and cfg.path_prefix:
        return Path(cfg.path_prefix) / path.relative_to(path.anchor)
    if path.is_absolute():
        return path
    return Path(cfg.central_storage_root) / path_str


def _apply_path_map(path_str: str, path_map: Optional[Dict[str, str]]) -> Optional[Path]:
    if not path_map:
        return None

    normalized = _normalize_for_map(path_str)
    normalized_cmp = normalized.lower() if os.name == "nt" else normalized

    for prefix, replacement in sorted(
        path_map.items(), key=lambda item: len(str(item[0])), reverse=True
    ):
        prefix_norm = _normalize_for_map(str(prefix))
        prefix_cmp = prefix_norm.lower() if os.name == "nt" else prefix_norm
        if normalized_cmp == prefix_cmp or normalized_cmp.startswith(prefix_cmp + "/"):
            suffix = normalized[len(prefix_norm):].lstrip("/")
            return Path(replacement) / Path(suffix)

    return None


def _normalize_for_map(value: str) -> str:
    value = value.replace("\\", "/")
    if value == "/":
        return value
    if len(value) == 3 and value[1:] == ":/":
        return value
    return value.rstrip("/")
