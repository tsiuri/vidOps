"""Flexible configuration loader for the analysis system."""

from __future__ import annotations

import configparser
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

# Search order: prefer INI/.cfg, then JSON. All paths live under config/.
CONFIG_CANDIDATES: List[Path] = [
    Path("config/config.local.cfg"),
    Path("config/config.cfg"),
    Path("config/config.local.json"),
    Path("config/config.json"),
]


def _coerce_value(val: str) -> Any:
    """Best-effort coercion for config values."""
    s = str(val).strip()
    low = s.lower()
    if low in {"true", "yes", "on"}:
        return True
    if low in {"false", "no", "off"}:
        return False
    try:
        if "." in s:
            f = float(s)
            # Avoid coercing things like "00123" unintentionally
            if not s.lstrip("+-").startswith("0") or s.count(".") == 1:
                return f
        i = int(s)
        return i
    except Exception:
        return s


def _load_cfg(path: Path) -> Dict[str, Any]:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str  # preserve case
    parser.read(path, encoding="utf-8")
    data: Dict[str, Any] = {}
    for section in parser.sections():
        data[section] = {k: _coerce_value(v) for k, v in parser.items(section)}
    data["__source__"] = str(path)
    return data


def _load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return {"__source__": str(path)}
    data["__source__"] = str(path)
    return data


def _normalize(data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize to include sections, shared flattening, and source metadata."""
    sections: Dict[str, Dict[str, Any]] = {}
    flat: Dict[str, Any] = {}

    for key, val in data.items():
        if key == "__source__":
            continue
        if isinstance(val, Mapping):
            sections[key] = {k: _coerce_value(v) for k, v in val.items()}
        else:
            flat[key] = _coerce_value(val)

    # Flatten shared into the top-level for backward compatibility
    shared = sections.get("shared", {})
    normalized = {
        **shared,
        **flat,
        **sections,
        "__sections__": sections,
        "__source__": data.get("__source__"),
    }
    return normalized


def load_local_config() -> Dict[str, Any]:
    """
    Load configuration from the first available candidate file.

    Search order (stop at first readable file):
    - config/config.local.cfg
    - config/config.cfg
    - config/config.local.json
    - config/config.json
    """
    for path in CONFIG_CANDIDATES:
        if not path.exists():
            continue
        try:
            if path.suffix.lower() in {".cfg", ".ini"}:
                return _normalize(_load_cfg(path))
            if path.suffix.lower() == ".json":
                return _normalize(_load_json(path))
        except Exception:
            # Ignore malformed config and continue to the next file
            continue
    return {"__sections__": {}, "__source__": None}


def log_effective_config(label: str, data: Mapping[str, Any], redact_keys: Iterable[str] | None = None) -> None:
    """Pretty-print effective configuration with simple redaction for secrets."""
    import pprint

    def scrub(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: ("***" if redact and k in redact else scrub(v)) for k, v in obj.items()}
        if isinstance(obj, list):
            return [scrub(x) for x in obj]
        return obj

    redact = set(redact_keys or [])
    print(f"{label}:")
    pprint.pprint(scrub(dict(data)), width=100, compact=True)
    print()


def load_hot_targets(path: str | Path | None) -> List[Dict[str, Any]]:
    """Load hot target definitions from JSON; returns an empty list on failure."""
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            payload = json.load(f)
        targets = payload.get("targets") if isinstance(payload, dict) else payload
        if isinstance(targets, list):
            return [t for t in targets if isinstance(t, dict)]
    except Exception:
        return []
    return []
