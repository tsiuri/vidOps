# vidops/config.py

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional, Dict, Any
import yaml
import socket
import logging

_logger = logging.getLogger(__name__) # Use standard logging

# --- Helper Functions ---

def get_env(env_vars: list[str], default: Any) -> Any:
    """Gets the first found environment variable from a list."""
    for var in env_vars:
        value = os.getenv(var)
        if value is not None and value != '': # <--- MODIFIED: Treat empty string as None
            # Attempt to cast to the same type as the default
            if isinstance(default, bool):
                return value.lower() in ('true', '1', 'yes')
            if isinstance(default, int):
                try:
                    return int(value)
                except (ValueError, TypeError):
                    continue
            if isinstance(default, float):
                try:
                    return float(value)
                except (ValueError, TypeError):
                    continue
            return value
    return default

# --- Configuration Dataclasses ---

@dataclass
class DatabaseConfig:
    """PostgreSQL Database Connection Settings"""
    host: str = "127.0.0.1"
    port: int = 5432
    name: str = "vidops"
    user: str = "user"
    password: Optional[str] = None

@dataclass
class PathsConfig:
    """Storage and Path Settings"""
    central_storage_root: str = "/mnt/storage/vidops"
    local_temp_dir: str = "tmp"

@dataclass
class NvidiaConfig:
    """NVIDIA (faster-whisper) Specific Settings"""
    compute_type: str = "float16"
    vad_filter: bool = True
    confidence_threshold: float = -0.7

@dataclass
class CpuConfig:
    """CPU Specific Settings"""
    compute_type: str = "int8_float32"
    threads: int = 8

@dataclass
class TranscriptionConfig:
    """Default settings for all transcription workers."""
    model: str = "small"
    language: str = "en"
    nvidia: NvidiaConfig = field(default_factory=NvidiaConfig)
    cpu: CpuConfig = field(default_factory=CpuConfig)

@dataclass
class WorkerConfig:
    """General settings for all worker types."""
    heartbeat_interval: int = 60
    max_jobs: int = 0
    machine_alias: str = field(default_factory=socket.gethostname)


@dataclass
class StorageBrokerConfig:
    """Settings for the optional storage broker service."""
    enabled: bool = False
    base_url: str = "http://127.0.0.1:8443"
    listen_host: str = "127.0.0.1"
    listen_port: int = 8443
    # Either a single shared token (str) or a per-worker map {machine_alias: token}
    shared_token: Optional[object] = None
    request_timeout: float = 60.0
    # Optional mTLS support for HTTPS client connections to reverse proxy
    mtls_client_cert: Optional[str] = None
    mtls_client_key: Optional[str] = None
    mtls_ca_cert: Optional[str] = None

@dataclass
class DownloadConfig:
    """Default yt-dlp settings for download enqueue/processing."""
    # Legacy pull.sh defaults: audio-only opus, quality 0
    format: str = "bestaudio/best"
    audio_only: bool = True
    audio_format: str = "opus"
    audio_quality: str = "0"
    embed_metadata: bool = True


@dataclass
class Config:
    """Root configuration object for the VidOps application."""
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    workers: WorkerConfig = field(default_factory=WorkerConfig)
    storage_broker: StorageBrokerConfig = field(default_factory=StorageBrokerConfig)
    download: DownloadConfig = field(default_factory=DownloadConfig)


# --- Loading Logic ---

def _load_config_from_dict(config_class, data: Dict[str, Any]):
    """Recursively creates a dataclass instance from a dictionary."""
    config_fields = {f.name: f for f in fields(config_class)}
    kwargs = {}
    
    for name, value in data.items():
        if name not in config_fields:
            continue
        
        field_type = config_fields[name].type
        
        # Handle nested dataclasses
        if hasattr(field_type, '__dataclass_fields__') and isinstance(value, dict):
            kwargs[name] = _load_config_from_dict(field_type, value)
        else:
            kwargs[name] = value
            
    return config_class(**kwargs)

def _apply_env_overrides(config_obj):
    """Recursively apply environment variable overrides to a dataclass instance."""
    # Mapping of dataclass field paths to environment variables
    # Format: "parent.child": ["ENV_VAR_1", "ENV_VAR_2"]
    ENV_MAP = {
        "database.host": ["VIDOPS_DB_HOST", "DB_HOST"],
        "database.port": ["VIDOPS_DB_PORT", "DB_PORT"],
        "database.name": ["VIDOPS_DB_NAME", "DB_NAME"],
        "database.user": ["VIDOPS_DB_USER", "DB_USER"],
        "database.password": ["VIDOPS_DB_PASSWORD", "DB_PASSWORD"],
        "paths.central_storage_root": ["VIDOPS_CENTRAL_STORAGE"],
        "download.format": ["VIDOPS_YTDLP_FORMAT"],
        "download.audio_only": ["VIDOPS_YTDLP_AUDIO_ONLY"],
        "download.audio_format": ["VIDOPS_YTDLP_AUDIO_FORMAT"],
        "download.audio_quality": ["VIDOPS_YTDLP_AUDIO_QUALITY"],
        "download.embed_metadata": ["VIDOPS_YTDLP_EMBED_METADATA"],
        "transcription.model": ["WHISPER_MODEL", "MODEL"],
        "transcription.language": ["WHISPER_LANGUAGE", "LANGUAGE"],
        "transcription.nvidia.compute_type": ["NV_COMPUTE"],
        "transcription.nvidia.vad_filter": ["NV_VAD_FILTER"],
        "workers.machine_alias": ["VIDOPS_MACHINE_ALIAS"],
        "workers.max_jobs": ["VIDOPS_WORKER_MAX_JOBS"],
        "workers.heartbeat_interval": ["VIDOPS_WORKER_HEARTBEAT_INTERVAL"],
        "storage_broker.enabled": ["VIDOPS_BROKER_ENABLED"],
        "storage_broker.base_url": ["VIDOPS_BROKER_BASE_URL"],
        "storage_broker.listen_host": ["VIDOPS_BROKER_HOST"],
        "storage_broker.listen_port": ["VIDOPS_BROKER_PORT"],
        "storage_broker.shared_token": ["VIDOPS_BROKER_TOKEN"],
        "storage_broker.mtls_client_cert": ["VIDOPS_BROKER_MTLS_CLIENT_CERT"],
        "storage_broker.mtls_client_key": ["VIDOPS_BROKER_MTLS_CLIENT_KEY"],
        "storage_broker.mtls_ca_cert": ["VIDOPS_BROKER_MTLS_CA_CERT"],
    }

    for path, env_vars in ENV_MAP.items():
        keys = path.split('.')
        current = config_obj
        for key in keys[:-1]:
            current = getattr(current, key)
        
        last_key = keys[-1]
        default_val = getattr(current, last_key)
        
        env_val = get_env(env_vars, default_val)
        if env_val != default_val:
            setattr(current, last_key, env_val)

    return config_obj


_config_instance: Optional[Config] = None

def _resolve_config_path(config_path: str) -> Path:
    """
    Resolve the config path, preferring:
    - Explicit absolute path (as given).
    - Relative to CWD if it exists.
    - Relative to the project root (env VIDOPS_PROJECT_ROOT or two levels above this file).
    """
    candidate = Path(config_path)
    if candidate.is_absolute() and candidate.exists():
        return candidate
    if candidate.exists():
        return candidate.resolve()

    project_root = Path(os.environ.get("VIDOPS_PROJECT_ROOT", Path(__file__).resolve().parents[2]))
    alt = project_root / config_path
    if alt.exists():
        return alt.resolve()
    return candidate.resolve()


def load_config(config_path: str = "config.yaml") -> Config:
    """
    Loads configuration with a clear precedence order and returns a singleton instance.

    Precedence Order (lowest to highest):
    1. Dataclass defaults (hardcoded).
    2. Values from the YAML file.
    3. Environment variables.
    """
    global _config_instance
    if _config_instance is not None:
        return _config_instance

    # 1. Start with dataclass defaults
    config = Config()

    # 2. Load from YAML file if it exists (resolve relative to project root if needed)
    yaml_data = {}
    resolved_path = _resolve_config_path(config_path)
    if resolved_path.exists():
        with open(resolved_path, 'r') as f:
            try:
                yaml_data = yaml.safe_load(f)
                if yaml_data:
                     # Re-apply defaults for nested dataclasses
                     # This is a bit tricky with simple dataclasses if data doesn't
                     # provide all keys. Using _load_config_from_dict directly
                     # creates new instances, so it might overwrite defaults
                     # if the YAML is sparse. 
                     # A more robust solution might involve deep merging dictionaries first.
                     # For now, we assume YAML is reasonably complete or uses defaults for missing.
                    
                    # Manual merge for top-level to preserve defaults
                    if 'database' in yaml_data: config.database = _load_config_from_dict(DatabaseConfig, yaml_data['database'])
                    if 'paths' in yaml_data: config.paths = _load_config_from_dict(PathsConfig, yaml_data['paths'])
                    if 'transcription' in yaml_data: 
                        trans_data = yaml_data['transcription']
                        config.transcription.model = trans_data.get('model', config.transcription.model)
                        config.transcription.language = trans_data.get('language', config.transcription.language)
                        if 'nvidia' in trans_data: config.transcription.nvidia = _load_config_from_dict(NvidiaConfig, trans_data['nvidia'])
                        if 'cpu' in trans_data: config.transcription.cpu = _load_config_from_dict(CpuConfig, trans_data['cpu'])
                    if 'workers' in yaml_data: config.workers = _load_config_from_dict(WorkerConfig, yaml_data['workers'])
                    if 'storage_broker' in yaml_data:
                        config.storage_broker = _load_config_from_dict(StorageBrokerConfig, yaml_data['storage_broker'])
                    if 'download' in yaml_data:
                        config.download = _load_config_from_dict(DownloadConfig, yaml_data['download'])

            except yaml.YAMLError as e:
                _logger.warning(f"Could not parse '{config_path}': {e}") # <--- MODIFIED: Use _logger
    
    # 3. Apply environment variable overrides
    config = _apply_env_overrides(config)
    
    _config_instance = config
    return _config_instance

# Example usage:
if __name__ == "__main__":
    # This block will only run when the script is executed directly
    # for testing purposes.
    
    # Create a dummy config.yaml for testing
    dummy_yaml = """
database:
  host: "yaml-host"
  port: 5433
  name: "yaml_db"

transcription:
  model: "large-v2"
  nvidia:
    compute_type: "int8"
"""
    with open("config.test.yaml", "w") as f:
        f.write(dummy_yaml)

    # Test Case 1: Loading from YAML
    print("--- Loading from YAML ---")
    cfg = load_config("config.test.yaml")
    assert cfg.database.host == "yaml-host"
    assert cfg.database.port == 5433
    assert cfg.transcription.model == "large-v2"
    assert cfg.transcription.nvidia.compute_type == "int8"
    print("✅ YAML loading works")

    # Test Case 2: Environment variable override
    print("\n--- Testing Environment Variable Overrides ---")
    os.environ["VIDOPS_DB_HOST"] = "env-host"
    os.environ["WHISPER_MODEL"] = "distil-large"
    
    # Reset singleton for reload
    _config_instance = None
    cfg = load_config("config.test.yaml")

    assert cfg.database.host == "env-host" # Overridden by env var
    assert cfg.transcription.model == "distil-large" # Overridden by env var
    assert cfg.database.port == 5433 # Still from YAML
    print("✅ Environment variable overrides work")

    # Clean up
    os.remove("config.test.yaml")
    del os.environ["VIDOPS_DB_HOST"]
    del os.environ["WHISPER_MODEL"]
    
    # Reset for any other module that might import this
    _config_instance = None
    print("\n--- All Tests Passed ---")
