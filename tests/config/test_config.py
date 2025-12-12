# tests/config/test_config.py

import pytest
import os
from pathlib import Path
import yaml
import logging # Import logging
from configuration import load_config, Config, DatabaseConfig, TranscriptionConfig, PathsConfig, WorkerConfig, NvidiaConfig, CpuConfig

# Fixture to clean up environment variables and config files after each test
@pytest.fixture(autouse=True)
def clean_config_env():
    # Store original environment variables
    original_env = os.environ.copy()
    # Store path to a dummy config file
    dummy_config_path = Path("config.test.yaml")
    
    # Ensure no existing config instance interferes
    import configuration as config_module
    config_module._config_instance = None

    yield
    
    # Restore original environment variables
    os.environ.clear()
    os.environ.update(original_env)
    
    # Clean up dummy config file if it was created
    if dummy_config_path.exists():
        dummy_config_path.unlink()
    
    # Clear singleton instance again for subsequent tests if any
    config_module._config_instance = None


@pytest.fixture
def dummy_config_yaml(tmp_path):
    config_content = """
database:
  host: \"yaml-host\"
  port: 5433
  name: \"yaml_db\"
  user: \"yaml_user\"
  password: \"yaml_password\"

paths:
  central_storage_root: \"/yaml/storage\"
  local_temp_dir: \"yaml_tmp\"

transcription:
  model: \"large\"
  language: \"fr\"
  nvidia:
    compute_type: \"int8_float16\"
    vad_filter: false
  cpu:
    threads: 16

workers:
  heartbeat_interval: 120
  max_jobs: 5
  machine_alias: \"yaml-machine\"
"""
    f = tmp_path / "config.test.yaml"
    f.write_text(config_content)
    return f

@pytest.mark.unit
def test_load_config_defaults():
    """Verify load_config() returns a Config object with all default values."""
    config = load_config("nonexistent_config.yaml") # Ensure no YAML file is loaded

    assert isinstance(config, Config)
    assert config.database == DatabaseConfig()
    assert config.paths == PathsConfig()
    assert config.transcription == TranscriptionConfig()
    assert config.workers.heartbeat_interval == 60 # Default value
    assert config.workers.machine_alias == os.uname().nodename # Default uses hostname

@pytest.mark.unit
def test_load_config_yaml_override(dummy_config_yaml):
    """Verify load_config() correctly loads values from a config.yaml file."""
    config = load_config(str(dummy_config_yaml))

    assert config.database.host == "yaml-host"
    assert config.database.port == 5433
    assert config.database.name == "yaml_db"
    assert config.database.user == "yaml_user"
    assert config.database.password == "yaml_password"
    
    assert config.paths.central_storage_root == "/yaml/storage"
    assert config.paths.local_temp_dir == "yaml_tmp"

    assert config.transcription.model == "large"
    assert config.transcription.language == "fr"
    assert config.transcription.nvidia.compute_type == "int8_float16"
    assert config.transcription.nvidia.vad_filter is False
    assert config.transcription.cpu.threads == 16

    assert config.workers.heartbeat_interval == 120
    assert config.workers.max_jobs == 5
    assert config.workers.machine_alias == "yaml-machine"

@pytest.mark.unit
def test_load_config_env_override(dummy_config_yaml):
    """Verify load_config() correctly loads values from environment variables, overriding YAML and defaults."""
    os.environ["VIDOPS_DB_HOST"] = "env-host"
    os.environ["VIDOPS_DB_PORT"] = "5434"
    os.environ["WHISPER_MODEL"] = "distil-large"
    os.environ["NV_VAD_FILTER"] = "0" # Test bool conversion
    os.environ["VIDOPS_CENTRAL_STORAGE"] = "/env/storage"
    os.environ["VIDOPS_MACHINE_ALIAS"] = "env-machine"

    config = load_config(str(dummy_config_yaml))

    assert config.database.host == "env-host"
    assert config.database.port == 5434
    assert config.transcription.model == "distil-large"
    assert config.transcription.nvidia.vad_filter is False # Overridden from YAML's false to ENV's '0'
    assert config.paths.central_storage_root == "/env/storage"
    assert config.workers.machine_alias == "env-machine"
    
    # Ensure other YAML values are still respected
    assert config.database.name == "yaml_db"
    assert config.transcription.language == "fr"

@pytest.mark.unit
def test_load_config_env_priority_over_yaml_and_default():
    """Test ENV vars correctly override both YAML and defaults."""
    os.environ["DB_HOST"] = "env-host-direct" # Test alternative env var name
    os.environ["VIDOPS_DB_PORT"] = "5435"
    
    # Create a config.yaml that would set DB host
    Path("config.test.yaml").write_text("database:\n  host: yaml-host-direct\n  port: 5433")

    config = load_config("config.test.yaml")
    
    assert config.database.host == "env-host-direct"
    assert config.database.port == 5435 # Env var priority

@pytest.mark.unit
def test_load_config_mixed_types_and_error_handling(tmp_path, caplog):
    """Verify type conversions from environment variables and graceful handling of invalid YAML."""
    # Test invalid int from ENV
    os.environ["VIDOPS_DB_PORT"] = "not-an-int"
    
    # Ensure previous singleton is cleared
    import configuration as config_module
    config_module._config_instance = None

    config = load_config("nonexistent_config.yaml") # Ensure no YAML is loaded
    assert config.database.port == 5432 # Falls back to default

    # Test malformed YAML
    malformed_yaml_path = tmp_path / "malformed.yaml"
    malformed_yaml_path.write_text("database: [") # Invalid YAML
    
    # Ensure previous singleton is cleared before loading malformed YAML
    config_module._config_instance = None

    with caplog.at_level(logging.WARNING, logger="vidops.config"): # <--- MODIFIED: Specify logger
        config_malformed = load_config(str(malformed_yaml_path))
        assert "Could not parse" in caplog.text
        assert config_malformed.database.host == "127.0.0.1" # Falls back to default
        assert config_malformed.database.port == 5432
