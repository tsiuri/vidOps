# configuration.py

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional, Dict, Any, List
import yaml
import socket
import logging

_logger = logging.getLogger(__name__) # Use standard logging

# --- Transcript Quality Hierarchy ---

# Transcript quality hierarchy from best to worst
# Used when selecting "best" available transcript for a given ytid
TRANSCRIPT_QUALITY_HIERARCHY = [
    # Whisper models (words format) - higher quality first
    "words_whisper_large-v3",
    "words_whisper_large-v2",
    "words_whisper_large",
    "words_whisper_turbo",
    "words_whisper_medium",
    "words_whisper_small",
    "words_whisper_base",
    "words_whisper_tiny",

    # VTT format transcripts (if words not available)
    "vtt_whisper_large-v3",
    "vtt_whisper_large-v2",
    "vtt_whisper_large",
    "vtt_whisper_turbo",
    "vtt_whisper_medium",
    "vtt_whisper_small",
    "vtt_whisper_base",
    "vtt_whisper_tiny",

    # YouTube auto-captions (lowest quality)
    "words_ytt",
    "vtt",
]

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
    path_prefix: str = ""  # Prefix to prepend to absolute paths from DB (e.g., /mnt/mainroot for remote mounts)

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
class WorkspaceConfig:
    """Workspace disk space management settings."""
    # Maximum total workspace size (GB). Worker terminates if exceeded. 0 = disabled.
    max_workspace_size_gb: float = 100.0
    # How often to check workspace size (in heartbeats)
    size_check_interval: int = 150
    # Whether to monitor tmp/ directory separately
    monitor_tmp_separately: bool = True
    # Maximum tmp/ size (GB) if monitoring separately
    max_tmp_size_gb: float = 50.0


@dataclass
class StorageBrokerConfig:
    """Settings for the optional storage broker service."""
    enabled: bool = False
    listen_host: str = "127.0.0.1"
    listen_port: int = 8443
    base_url: str = "http://127.0.0.1:8443"
    shared_token: Any = None
    mtls_client_cert: Optional[str] = None
    mtls_client_key: Optional[str] = None
    mtls_ca_cert: Optional[str] = None
    request_timeout: int = 60


@dataclass
class HCClipsConfig:
    """Hits & Clips: explicit clip rendering settings (Phase 4).

    Rendering is opt-in. If render_enabled is false, rendering can still be
    performed by explicitly passing an output_root to the render/rebuild API.
    """

    render_enabled: bool = False
    # Where rendered clip files are written. If unset, callers must pass
    # output_root explicitly.
    output_root: Optional[str] = None

@dataclass
class DownloadConfig:
    """Default yt-dlp settings for download enqueue/processing."""
    # Legacy pull.sh defaults: audio-only opus, quality 0
    format: str = "bestaudio/best"
    audio_only: bool = True
    audio_format: str = "opus"
    audio_quality: str = "0"
    embed_metadata: bool = True
    use_archive: bool = True
    archive_path: str = "pull/download_archive.txt"
    no_overwrites: bool = True
    cookies_browser: Optional[str] = None  # e.g., "firefox", "chrome"
    sleep_requests: int = 0
    sleep_interval: int = 0
    sleep_max_interval: int = 0
    retries: int = 5
    fragment_retries: int = 5
    extractor_retries: int = 3
    concurrent_fragments: int = 1
    write_auto_subs: bool = True
    sub_langs: str = "en"
    no_transcript_log: str = "logs/no_transcripts_available.txt"

@dataclass
class OllamaConfig:
    """Configuration for Ollama LLM backend used in transcript analysis."""
    url: str = "http://localhost:11434"
    model: str = "qwen2.5:7b-instruct"
    timeout: int = 300
    request_timeout: int = 60

@dataclass
@dataclass
class DiarizationConfig:
    """Default settings for diarization workers and jobs."""
    # Diarization model to use (e.g., 'pyannote', 'resemblyzer')
    model: str = "pyannote"
    # Device to run diarization on: 'cuda', 'cpu', or 'auto' (auto-detect)
    device: str = "auto"
    # Audio chunking for processing long files
    chunk_seconds: float = 15.0
    overlap_seconds: float = 2.5
    # Speaker matching thresholds
    similarity_threshold: float = 0.6
    gap_threshold: float = 0.15
    # Reference-based speaker matching (optional, for improved accuracy)
    match_threshold: float = 0.75
    match_margin: float = 0.01
    match_force_best: bool = True
    # Reference builder defaults
    refs_clips_count: int = 50
    refs_clips_per_video: int = 1
    refs_max_clips: int = 50
    refs_min_words: int = 3
    refs_max_words: int = 6
    refs_min_clip_seconds: float = 1.0
    refs_max_clip_seconds: float = 6.0
    refs_audio_channels: int = 1
    refs_audio_rate: int = 16000


@dataclass
class AnalysisConfig:
    """Distributed transcript analysis system configuration."""
    # Ollama LLM backend
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    # Default analysis settings
    chunk_size_words: int = 1000
    chunk_overlap_words: int = 150
    # Logging level for analysis workers
    log_mode: str = "quiet"  # 'quiet', 'info', 'debug'
    # Default available VRAM for analysis workers (GB)
    default_vram_gb: float = 0.0
    # Default model profile id for analysis workers
    default_model_profile_id: Optional[int] = None


@dataclass
class HousekeepingConfig:
    """Housekeeping (idle worker maintenance) configuration."""
    # Enable housekeeping jobs when workers are idle
    enabled: bool = True
    # Number of consecutive failed job claims before triggering housekeeping
    trigger_idle_attempts: int = 10
    # Maximum housekeeping jobs to enqueue per idle cycle
    max_per_cycle: int = 5
    # Minimum age (in seconds) for videos before they're eligible for housekeeping
    min_video_age_seconds: int = 3600
    # Priority for housekeeping jobs (very low so real work always wins)
    priority: int = 10
    # Priority offset for transcription upgrades (priority - upgrade_priority_offset)
    upgrade_priority_offset: int = 5
    # Which tasks to run during idle (e.g., 'transcribe_default')
    tasks: List[str] = field(default_factory=lambda: ["transcribe_default"])


# Default Ollama base port - GPU N uses port BASE + N
OLLAMA_BASE_PORT = 11434


@dataclass
class GpuProfileConfig:
    """Per-GPU configuration profile.

    Each GPU can have its own VRAM, Ollama endpoint, and model preferences.
    Ollama URL defaults to localhost:{OLLAMA_BASE_PORT + gpu_index} if not specified.
    """
    # Display name for this GPU (optional, e.g., "RTX 4090")
    name: Optional[str] = None
    # Available VRAM for this GPU (GB)
    vram_gb: Optional[float] = None
    # Ollama URL override for this GPU (default: http://localhost:{11434 + gpu_index})
    ollama_url: Optional[str] = None
    # Model name override for analysis on this GPU
    model_name: Optional[str] = None
    # Model profile id override for analysis on this GPU
    model_profile_id: Optional[int] = None


@dataclass
class GpusConfig:
    """Container for per-GPU profile configurations.

    Maps GPU index (0, 1, 2...) to GpuProfileConfig.
    Access via gpus.profiles[gpu_index] or get_gpu_profile(gpu_index).
    """
    profiles: Dict[int, GpuProfileConfig] = field(default_factory=dict)

    def get_profile(self, gpu_index: int) -> GpuProfileConfig:
        """Get profile for a GPU, creating a default if not configured."""
        if gpu_index in self.profiles:
            return self.profiles[gpu_index]
        # Return default profile with computed Ollama URL
        return GpuProfileConfig(
            ollama_url=f"http://localhost:{OLLAMA_BASE_PORT + gpu_index}"
        )


@dataclass
class Config:
    """Root configuration object for the VidOps application."""
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    diarization: DiarizationConfig = field(default_factory=DiarizationConfig)
    workers: WorkerConfig = field(default_factory=WorkerConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    storage_broker: StorageBrokerConfig = field(default_factory=StorageBrokerConfig)
    hc_clips: HCClipsConfig = field(default_factory=HCClipsConfig)
    download: DownloadConfig = field(default_factory=DownloadConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    housekeeping: HousekeepingConfig = field(default_factory=HousekeepingConfig)
    gpus: GpusConfig = field(default_factory=GpusConfig)


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
        "paths.path_prefix": ["VIDOPS_PATH_PREFIX", "DB_PATH_PREFIX"],
        "hc_clips.render_enabled": ["VIDOPS_HC_CLIPS_RENDER_ENABLED"],
        "hc_clips.output_root": ["VIDOPS_HC_CLIPS_OUTPUT_ROOT"],
        "download.format": ["VIDOPS_YTDLP_FORMAT"],
        "download.audio_only": ["VIDOPS_YTDLP_AUDIO_ONLY"],
        "download.audio_format": ["VIDOPS_YTDLP_AUDIO_FORMAT"],
        "download.audio_quality": ["VIDOPS_YTDLP_AUDIO_QUALITY"],
        "download.embed_metadata": ["VIDOPS_YTDLP_EMBED_METADATA"],
        "download.use_archive": ["VIDOPS_YTDLP_USE_ARCHIVE"],
        "download.archive_path": ["VIDOPS_YTDLP_ARCHIVE_PATH"],
        "download.no_overwrites": ["VIDOPS_YTDLP_NO_OVERWRITES"],
        "download.cookies_browser": ["VIDOPS_YTDLP_COOKIES_BROWSER"],
        "download.sleep_requests": ["VIDOPS_YTDLP_SLEEP_REQUESTS"],
        "download.sleep_interval": ["VIDOPS_YTDLP_SLEEP_INTERVAL"],
        "download.sleep_max_interval": ["VIDOPS_YTDLP_SLEEP_MAX_INTERVAL"],
        "download.retries": ["VIDOPS_YTDLP_RETRIES"],
        "download.fragment_retries": ["VIDOPS_YTDLP_FRAGMENT_RETRIES"],
        "download.extractor_retries": ["VIDOPS_YTDLP_EXTRACTOR_RETRIES"],
        "download.concurrent_fragments": ["VIDOPS_YTDLP_CONCURRENT_FRAGMENTS"],
        "download.write_auto_subs": ["VIDOPS_YTDLP_WRITE_AUTO_SUBS"],
        "download.sub_langs": ["VIDOPS_YTDLP_SUB_LANGS"],
        "download.no_transcript_log": ["VIDOPS_NO_TRANSCRIPT_LOG"],
        "transcription.model": ["WHISPER_MODEL", "MODEL"],
        "transcription.language": ["WHISPER_LANGUAGE", "LANGUAGE"],
        "transcription.nvidia.compute_type": ["NV_COMPUTE"],
        "transcription.nvidia.vad_filter": ["NV_VAD_FILTER"],
        "diarization.model": ["DIARIZATION_MODEL"],
        "diarization.device": ["DIARIZATION_DEVICE"],
        "diarization.chunk_seconds": ["DIARIZATION_CHUNK_SECONDS"],
        "diarization.overlap_seconds": ["DIARIZATION_OVERLAP_SECONDS"],
        "diarization.similarity_threshold": ["DIARIZATION_SIMILARITY_THRESHOLD"],
        "diarization.gap_threshold": ["DIARIZATION_GAP_THRESHOLD"],
        "diarization.match_threshold": ["DIARIZATION_MATCH_THRESHOLD"],
        "workers.machine_alias": ["VIDOPS_MACHINE_ALIAS"],
        "workers.max_jobs": ["VIDOPS_WORKER_MAX_JOBS"],
        "workers.heartbeat_interval": ["VIDOPS_WORKER_HEARTBEAT_INTERVAL"],
        "workspace.max_workspace_size_gb": ["VIDOPS_MAX_WORKSPACE_SIZE_GB"],
        "workspace.size_check_interval": ["VIDOPS_WORKSPACE_CHECK_INTERVAL"],
        "workspace.monitor_tmp_separately": ["VIDOPS_MONITOR_TMP_SEPARATELY"],
        "workspace.max_tmp_size_gb": ["VIDOPS_MAX_TMP_SIZE_GB"],
        "storage_broker.enabled": ["VIDOPS_BROKER_ENABLED"],
        "storage_broker.base_url": ["VIDOPS_BROKER_BASE_URL"],
        "storage_broker.listen_host": ["VIDOPS_BROKER_HOST"],
        "storage_broker.listen_port": ["VIDOPS_BROKER_PORT"],
        "storage_broker.shared_token": ["VIDOPS_BROKER_TOKEN"],
        "storage_broker.mtls_client_cert": ["VIDOPS_BROKER_MTLS_CLIENT_CERT"],
        "storage_broker.mtls_client_key": ["VIDOPS_BROKER_MTLS_CLIENT_KEY"],
        "storage_broker.mtls_ca_cert": ["VIDOPS_BROKER_MTLS_CA_CERT"],
        "analysis.ollama.url": ["OLLAMA_URL", "ANALYSIS_OLLAMA_URL"],
        "analysis.ollama.model": ["OLLAMA_MODEL", "ANALYSIS_OLLAMA_MODEL"],
        "analysis.ollama.timeout": ["OLLAMA_TIMEOUT", "ANALYSIS_OLLAMA_TIMEOUT"],
        "analysis.ollama.request_timeout": ["OLLAMA_REQUEST_TIMEOUT", "ANALYSIS_REQUEST_TIMEOUT"],
        "analysis.chunk_size_words": ["ANALYSIS_CHUNK_SIZE"],
        "analysis.chunk_overlap_words": ["ANALYSIS_CHUNK_OVERLAP"],
        "analysis.log_mode": ["ANALYSIS_LOG_MODE"],
        "analysis.default_vram_gb": ["ANALYSIS_VRAM_GB", "VIDOPS_ANALYSIS_VRAM_GB"],
        "analysis.default_model_profile_id": ["ANALYSIS_MODEL_PROFILE_ID"],
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
_config_path_used: Optional[Path] = None

def _resolve_config_path(config_path: str) -> Path:
    """
    Resolve the config path, preferring:
    - Explicit absolute path (as given).
    - Relative to CWD if it exists.
    - Relative to the project root (env VIDOPS_PROJECT_ROOT or repo root).
    - Fallback: repo root (parents of this file) if project root has no config.
    Returns the resolved path.
    """
    candidate = Path(config_path)
    if candidate.is_absolute() and candidate.exists():
        return candidate
    if candidate.exists():
        return candidate.resolve()

    # Prefer explicit project root env
    env_root = os.environ.get("VIDOPS_PROJECT_ROOT")
    if env_root:
        project_root = Path(env_root)
        alt = project_root / config_path
        if alt.exists():
            return alt.resolve()
        # If VIDOPS_PROJECT_ROOT is set but lacks a config, fall back to repo roots
    # Try to locate a nearby config.yaml by walking parents of this file
    here = Path(__file__).resolve()
    for parent in here.parents:
        probe = parent / config_path
        if probe.exists():
            return probe.resolve()
    # Fallback: original candidate (absolute resolution)
    return candidate.resolve()


def load_config(config_path: str = "config.yaml") -> Config:
    """
    Loads configuration with a clear precedence order and returns a singleton instance.

    Precedence Order (lowest to highest):
    1. Dataclass defaults (hardcoded).
    2. Values from the YAML file.
    3. Environment variables.
    """
    global _config_instance, _config_path_used
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
                    if 'diarization' in yaml_data:
                        config.diarization = _load_config_from_dict(DiarizationConfig, yaml_data['diarization'])
                    if 'workers' in yaml_data: config.workers = _load_config_from_dict(WorkerConfig, yaml_data['workers'])
                    if 'storage_broker' in yaml_data:
                        config.storage_broker = _load_config_from_dict(StorageBrokerConfig, yaml_data['storage_broker'])
                    if 'download' in yaml_data:
                        config.download = _load_config_from_dict(DownloadConfig, yaml_data['download'])
                    if 'analysis' in yaml_data:
                        analysis_data = yaml_data['analysis']
                        if 'ollama' in analysis_data:
                            config.analysis.ollama = _load_config_from_dict(OllamaConfig, analysis_data['ollama'])
                        config.analysis.chunk_size_words = analysis_data.get('chunk_size_words', config.analysis.chunk_size_words)
                        config.analysis.chunk_overlap_words = analysis_data.get('chunk_overlap_words', config.analysis.chunk_overlap_words)
                        config.analysis.log_mode = analysis_data.get('log_mode', config.analysis.log_mode)
                        config.analysis.default_vram_gb = analysis_data.get('default_vram_gb', config.analysis.default_vram_gb)
                        config.analysis.default_model_profile_id = analysis_data.get(
                            'default_model_profile_id',
                            config.analysis.default_model_profile_id,
                        )

                    # Parse per-GPU profiles
                    if 'gpus' in yaml_data:
                        gpus_data = yaml_data['gpus']
                        for gpu_key, gpu_data in gpus_data.items():
                            try:
                                gpu_index = int(gpu_key)
                                profile = GpuProfileConfig(
                                    name=gpu_data.get('name'),
                                    vram_gb=gpu_data.get('vram_gb'),
                                    ollama_url=gpu_data.get('ollama_url'),
                                    model_name=gpu_data.get('model_name'),
                                    model_profile_id=gpu_data.get('model_profile_id'),
                                )
                                # Default ollama_url if not specified
                                if profile.ollama_url is None:
                                    profile.ollama_url = f"http://localhost:{OLLAMA_BASE_PORT + gpu_index}"
                                config.gpus.profiles[gpu_index] = profile
                            except (ValueError, TypeError) as e:
                                _logger.warning(f"Invalid GPU key '{gpu_key}' in config (expected integer): {e}")

            except yaml.YAMLError as e:
                _logger.warning(f"Could not parse '{config_path}': {e}") # <--- MODIFIED: Use _logger
    
    # 3. Apply environment variable overrides
    config = _apply_env_overrides(config)
    
    _config_instance = config
    _config_path_used = resolved_path
    return _config_instance

def get_config_path() -> Optional[Path]:
    """Return the resolved config path, if loaded."""
    return _config_path_used

def get_project_root() -> Path:
    """
    Resolve the project root:
    1) VIDOPS_PROJECT_ROOT if set
    2) Current working directory
    3) Directory containing the loaded config.yaml (if known)
    """
    env_root = os.environ.get("VIDOPS_PROJECT_ROOT")
    if env_root:
        return Path(env_root)
    if Path.cwd():
        return Path.cwd()
    if _config_path_used:
        return _config_path_used.parent
    return Path(__file__).resolve().parents[2]

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
