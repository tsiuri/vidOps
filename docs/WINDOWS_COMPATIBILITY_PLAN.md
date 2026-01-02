# VidOps Windows Compatibility Plan

**Status:** Draft
**Created:** 2026-01-01
**Author:** Architecture Planning

## Executive Summary

VidOps is currently Linux-focused with several platform-specific dependencies. This document outlines a plan to achieve **full Windows parity** while maintaining Linux functionality. The core Python workers (download, transcription, analysis, clipping) are largely compatible, but several services rely on bash scripts and Linux-specific system calls.

**Target:** Full platform parity (all features work on both Windows and Linux)
**Estimated Effort:** 2-3 weeks for complete implementation
**Quick Win Option:** 3-5 days for core workers only (if needed)

**Approved Design Decisions:**
- ✅ Pure Python rewrite of shell scripts (Option A)
- ✅ Process Manager abstraction layer (Option B)
- ✅ Migrate to psutil for metrics (Option A)
- ✅ Strict pathlib enforcement (Option A)
- ✅ Hybrid Ollama management: manual startup → Windows services (Hybrid)

---

## Current Compatibility Assessment

### ✅ Already Compatible (No Changes Needed)

**Core Infrastructure:**
- PostgreSQL database connectivity (network-based, platform-agnostic)
- Python codebase (95% uses pathlib and cross-platform libraries)
- FFmpeg/yt-dlp integration (binaries available for Windows)
- Ollama integration (native Windows support)
- PyTorch + CUDA (full Windows support)
- faster-whisper (Windows-compatible)
- PyAnnote diarization (Windows-compatible)

**Workers That Should Work:**
- Download worker (`workers/download.py`)
- Transcription worker (`workers/transcription.py`)
- Analysis worker (`workers/analysis_distributed.py`)
- Clipping worker (`workers/clipping.py`)
- Generic worker (`workers/general.py`) - with modifications

### ⚠️ Needs Modification

**Critical Issues:**

1. **Hardcoded Unix Paths** (5 occurrences)
   - `services/diarization.py` lines 672, 791
   - Impact: Immediate failure on Windows
   - Effort: 1 hour

2. **Bash Script Dependencies** (4 services)
   - `services/stitching.py` - video stitching via workspace.sh
   - `services/subtitle.py` - subtitle download/conversion
   - `services/voice_filter.py` - voice filtering
   - `workers/general.py` - disk usage via `du` command
   - Impact: These services won't work on Windows
   - Effort: 1-2 weeks (port to Python)

3. **Unix Process Management** (2 critical locations)
   - `services/diarization.py` - `os.killpg()`, `start_new_session=True`
   - `services/memory_monitor.py` - signal handling
   - Impact: Diarization worker fails on Windows
   - Effort: 4-8 hours (add platform detection)

4. **Linux /proc Filesystem** (3 workers)
   - `workers/general.py`, `workers/diarization.py`, `workers/analysis_distributed.py`
   - Impact: Metrics/monitoring disabled on Windows
   - Effort: 2-4 hours (use psutil library)

5. **Signal Handling** (all workers)
   - SIGTERM/SIGKILL used for graceful shutdown
   - Impact: Less graceful on Windows, but mostly handled
   - Effort: 2 hours (add guards)

---

## Design Decisions Required

### Decision 1: Shell Script Migration Strategy

**Options:**

**A) Pure Python Rewrite (Recommended)**
- **Pros:** Full cross-platform support, maintainable, testable
- **Cons:** Most effort upfront (1-2 weeks)
- **Affected:** `workspace.sh` functions: `stitch`, `dl-subs`, `convert-captions`, `voice`
- **Example:** Replace `bash workspace.sh stitch` with Python FFmpeg wrapper

**B) Dual Implementation (Shell + PowerShell/Batch)**
- **Pros:** Preserves existing Linux functionality
- **Cons:** Maintenance burden, testing complexity
- **Implementation:** Detect platform, invoke `.sh` on Linux, `.ps1`/`.bat` on Windows

**C) WSL2 Requirement for Windows**
- **Pros:** Zero code changes needed
- **Cons:** User must install WSL2, limited true Windows support
- **Implementation:** Documentation-only change

**Recommendation:** **Option A** - Pure Python rewrite
- Long-term maintainability
- Better error handling
- Easier testing
- Natural evolution from workspace.sh

---

### Decision 2: Process Management Abstraction

**Options:**

**A) Platform-Specific Branches**
```python
if platform.system() == 'Windows':
    proc = subprocess.Popen(..., creationflags=CREATE_NEW_PROCESS_GROUP)
else:
    proc = subprocess.Popen(..., start_new_session=True)
```
- **Pros:** Simple, explicit
- **Cons:** Scattered conditionals

**B) Process Manager Abstraction Layer**
```python
class ProcessManager:
    def spawn_isolated(self, cmd, env):
        """Platform-aware process spawning"""

class LinuxProcessManager(ProcessManager):
    # killpg, start_new_session

class WindowsProcessManager(ProcessManager):
    # taskkill, CREATE_NEW_PROCESS_GROUP
```
- **Pros:** Clean separation, testable
- **Cons:** More boilerplate

**Recommendation:** **Option B** - Create `utils/process_manager.py`
- Centralizes platform logic
- Easier to test both paths
- Cleaner service code

---

### Decision 3: Metrics/Monitoring Strategy

**Current:** Direct `/proc` filesystem reads for memory/CPU

**Options:**

**A) Migrate to psutil Library**
```python
import psutil
process = psutil.Process()
mem = process.memory_info().rss  # Works everywhere
cpu = process.cpu_percent()
```
- **Pros:** Cross-platform, feature-rich, maintained
- **Cons:** New dependency

**B) Platform-Specific Implementations**
- **Pros:** No new dependencies
- **Cons:** Complex, reinventing wheel

**C) Disable Metrics on Windows**
- **Pros:** Zero effort
- **Cons:** No monitoring visibility

**Recommendation:** **Option A** - Use `psutil`
- Already in many Python environments
- Eliminates all `/proc` reads
- Better metrics than current implementation

---

### Decision 4: Path Handling Standards

**Current:** Mix of pathlib and string paths

**Options:**

**A) Strict pathlib Policy**
- All paths must use `pathlib.Path()`
- No string concatenation with `/`
- Linting enforced

**B) Allow String Paths**
- Current approach
- Trust developers

**Recommendation:** **Option A** - Enforce pathlib
- Add pre-commit hook to check for hardcoded `/`
- Audit existing code (already mostly compliant)

---

### Decision 5: Ollama Service Management

**Context:** This decision is about how Ollama instances are started and managed on Windows, not whether Python can communicate with them (it can - Ollama's HTTP API is identical on all platforms).

**Linux Current State:** Two systemd services that auto-start on boot:
- `ollama-server1.service` → port 11434, `CUDA_VISIBLE_DEVICES=1` (GPU 0 - 3060)
- `ollama-server2.service` → port 11435, `CUDA_VISIBLE_DEVICES=0` (GPU 1 - 3090)

Each service auto-restarts on failure and runs in the background.

**Windows Challenge:** No systemd equivalent - need alternative service management

**Options:**

**A) Manual Startup** ⚡ Quickest Implementation
- User runs batch scripts to start Ollama instances before starting workers
- Example `start-ollama-servers.bat`:
  ```batch
  @echo off
  echo Starting Ollama GPU 0 (3060) on port 11434...
  start "Ollama-GPU0" cmd /k "set CUDA_VISIBLE_DEVICES=1 && ollama serve --host 0.0.0.0:11434"

  echo Starting Ollama GPU 1 (3090) on port 11435...
  start "Ollama-GPU1" cmd /k "set CUDA_VISIBLE_DEVICES=0 && ollama serve --host 127.0.0.1:11435"

  echo Ollama servers started. Keep these windows open.
  pause
  ```
- **Pros:**
  - Simple implementation (just ship batch files)
  - No installation complexity
  - Easy for users to modify/debug
- **Cons:**
  - Must manually start after reboot
  - No auto-restart on crash
  - Terminal windows stay open

**B) Windows Services** 🏆 Production-Ready
- Use NSSM (Non-Sucking Service Manager) to wrap Ollama as Windows Services
- PowerShell installer creates and configures services
- Example installer snippet:
  ```powershell
  # Download NSSM if not present
  # Install service for GPU 0
  nssm install OllamaGPU0 "C:\Program Files\Ollama\ollama.exe" serve
  nssm set OllamaGPU0 AppEnvironmentExtra CUDA_VISIBLE_DEVICES=1
  nssm set OllamaGPU0 AppParameters "--host 0.0.0.0:11434"
  nssm set OllamaGPU0 Start SERVICE_AUTO_START

  # Install service for GPU 1
  nssm install OllamaGPU1 "C:\Program Files\Ollama\ollama.exe" serve
  nssm set OllamaGPU1 AppEnvironmentExtra CUDA_VISIBLE_DEVICES=0
  nssm set OllamaGPU1 AppParameters "--host 127.0.0.1:11435"
  nssm set OllamaGPU1 Start SERVICE_AUTO_START

  # Start services
  nssm start OllamaGPU0
  nssm start OllamaGPU1
  ```
- **Pros:**
  - Auto-start on boot (like Linux systemd)
  - Auto-restart on crash
  - Runs in background (no terminal windows)
  - Professional deployment experience
  - Can be managed via Windows Services GUI
- **Cons:**
  - Requires admin rights to install
  - Slightly more complex setup
  - NSSM dependency (small, portable executable)

**C) Python-Managed Subprocesses** 🤔 Not Recommended
- VidOps spawns Ollama processes when workers start
- Workers manage Ollama lifecycle (start/stop/restart)
- **Pros:**
  - Fully automated, no user setup
- **Cons:**
  - Ollama processes die when worker exits (bad for long-running services)
  - Multiple workers could spawn duplicate Ollama instances (resource waste)
  - Complex process management logic in VidOps
  - Not how Ollama is designed to be used (it's a service, not a library)
  - User might want Ollama running even when workers aren't (for testing, manual use)

**Recommendation:** **Hybrid Approach**

**Phase 1 (Quick Win):** Manual startup
- Ship `scripts/windows/start-ollama-servers.bat`
- Documentation: "Run this script before starting workers"
- Users can modify batch file for their GPU setup
- Gets Windows support working immediately

**Phase 2 (Production Polish):** Windows Services
- Create PowerShell installer: `scripts/windows/install-ollama-services.ps1`
- One-time setup with admin rights
- After installation, works exactly like Linux (auto-start, auto-restart)
- Users can uninstall via `uninstall-ollama-services.ps1` or Windows Services GUI

**Python Communication:** Identical on Windows and Linux
```python
# This code works EXACTLY the same on Windows
import requests
response = requests.post('http://localhost:11434/api/generate', json={
    'model': 'qwen2.5:7b-instruct',
    'prompt': 'test'
})
```

**Windows Ollama Features (Same as Linux):**
- ✅ HTTP API identical
- ✅ Supports multiple instances on different ports
- ✅ CUDA_VISIBLE_DEVICES environment variable works
- ✅ Model management identical (`ollama pull`, `ollama ps`, etc.)
- ✅ Same performance characteristics

**Only Difference:** Service management mechanism (systemd vs batch/NSSM), but once running, behavior is identical.

---

## Implementation Plan

### Phase 1: Core Compatibility (3-5 days) - **Minimal Viable Windows**

**Goal:** Get download, transcription, analysis, and clipping workers running on Windows

**Tasks:**
1. Fix hardcoded Unix paths (1 hour)
   - `services/diarization.py`: Use `sys.executable` instead of hardcoded venv path

2. Add platform guards for process management (4 hours)
   - Create `utils/process_manager.py`
   - Update `services/diarization.py` to use it

3. Migrate /proc reads to psutil (2-4 hours)
   - Add psutil dependency
   - Update `workers/general.py`, `workers/diarization.py`, `workers/analysis_distributed.py`

4. Replace `du` command with Python (1 hour)
   - Use `shutil.disk_usage()` or recursive pathlib

5. Update signal handling (2 hours)
   - Add try/except for Windows-unsupported signals

6. Create Windows setup guide (2 hours)
   - Ollama manual startup
   - CUDA setup
   - Config path examples

**Deliverables:**
- ✅ Download worker functional
- ✅ Transcription worker functional (CUDA-accelerated)
- ✅ Analysis worker functional (via Ollama)
- ✅ Clipping worker functional
- ✅ Generic worker functional (claims above job types)
- ❌ Diarization works but needs manual Ollama setup
- ❌ Stitching/subtitles/voice remain Linux-only

---

### Phase 2: Shell Script Migration (1-2 weeks)

**Goal:** Eliminate bash dependencies, full service parity

**Tasks:**

1. **Stitching Service** (3-4 days)
   - Reverse-engineer `workspace.sh stitch` behavior
   - Implement pure Python FFmpeg wrapper
   - Create `services/stitching_native.py`
   - Test parity with bash version

2. **Subtitle Service** (2-3 days)
   - Port `workspace.sh dl-subs` → Python yt-dlp calls
   - Port `workspace.sh convert-captions` → Python FFmpeg
   - Update `services/subtitle.py`

3. **Voice Filter Service** (2-3 days)
   - Reverse-engineer voice filtering pipeline
   - Implement Python wrapper
   - Update `services/voice_filter.py`

**Deliverables:**
- ✅ All services pure Python
- ✅ Full Windows parity
- ✅ Easier to maintain and test

---

### Phase 3: Production Readiness (3-5 days)

**Goal:** Polished Windows experience

**Tasks:**

1. **Installer/Setup Scripts** (2 days)
   - PowerShell installer script
   - Ollama Windows service setup (NSSM)
   - CUDA toolkit verification
   - Config template generation

2. **Testing Matrix** (2 days)
   - Test all worker types on Windows 10/11
   - Test with/without CUDA
   - Multi-GPU configuration testing
   - Document any Windows-specific quirks

3. **Documentation** (1 day)
   - Windows installation guide
   - Troubleshooting section
   - GPU configuration examples
   - Performance tuning

**Deliverables:**
- ✅ One-command installer
- ✅ Automated Ollama service setup
- ✅ Comprehensive Windows docs
- ✅ CI testing on Windows (optional)

---

## Implementation Details

### 1. Process Manager Abstraction

**File:** `utils/process_manager.py`

```python
"""Cross-platform process management utilities."""
import os
import platform
import signal
import subprocess
from abc import ABC, abstractmethod
from typing import Optional, Dict, List

class ProcessManager(ABC):
    """Abstract base for platform-specific process management."""

    @abstractmethod
    def spawn_isolated(self, cmd: List[str], env: Optional[Dict] = None,
                      cwd: Optional[str] = None) -> subprocess.Popen:
        """Spawn a process in isolated group for clean termination."""
        pass

    @abstractmethod
    def kill_process_tree(self, proc: subprocess.Popen, timeout: float = 5.0):
        """Kill process and all descendants."""
        pass

class LinuxProcessManager(ProcessManager):
    def spawn_isolated(self, cmd, env=None, cwd=None):
        return subprocess.Popen(
            cmd, env=env, cwd=cwd,
            start_new_session=True,  # Unix process group
            text=True
        )

    def kill_process_tree(self, proc, timeout=5.0):
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)

class WindowsProcessManager(ProcessManager):
    def spawn_isolated(self, cmd, env=None, cwd=None):
        return subprocess.Popen(
            cmd, env=env, cwd=cwd,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            text=True
        )

    def kill_process_tree(self, proc, timeout=5.0):
        try:
            # Use taskkill to kill process tree
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                         check=False, capture_output=True)
        except Exception:
            proc.kill()

def get_process_manager() -> ProcessManager:
    """Factory: returns platform-appropriate manager."""
    if platform.system() == 'Windows':
        return WindowsProcessManager()
    else:
        return LinuxProcessManager()
```

**Usage in services:**
```python
from utils.process_manager import get_process_manager

proc_mgr = get_process_manager()
proc = proc_mgr.spawn_isolated(cmd, env=env, cwd=workspace_root)
# ...later...
proc_mgr.kill_process_tree(proc)
```

---

### 2. Metrics Migration to psutil

**Before:**
```python
# Linux-only
with open("/proc/self/status", "r") as f:
    for line in f:
        if line.startswith("VmRSS:"):
            mem_kb = int(line.split()[1])
```

**After:**
```python
import psutil
process = psutil.Process()
mem_bytes = process.memory_info().rss
```

**Benefits:**
- Works on Windows, Linux, macOS
- More reliable
- Additional metrics available (CPU %, I/O, threads)

---

### 3. Path Handling Examples

**Before:**
```python
py_bin = Path(os.environ.get("DIAR_PYTHON_BIN", "/home/billie/tools/vidops/.venv/bin/python"))
```

**After:**
```python
import sys
py_bin = Path(sys.executable)  # Current Python interpreter, works anywhere
```

**Or for venv detection:**
```python
def get_venv_python() -> Path:
    """Get Python binary from current or detected venv."""
    # Already in venv
    if hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
        return Path(sys.executable)

    # Look for venv in project
    project_root = Path(__file__).parent.parent
    venv_dir = project_root / ".venv"

    if platform.system() == 'Windows':
        venv_python = venv_dir / "Scripts" / "python.exe"
    else:
        venv_python = venv_dir / "bin" / "python"

    return venv_python if venv_python.exists() else Path(sys.executable)
```

---

### 4. Stitching Service Migration Example

**Current:** `services/stitching.py` calls `bash workspace.sh stitch`

**New Approach:** Pure Python FFmpeg wrapper

```python
# services/stitching_native.py
import subprocess
from pathlib import Path
from typing import List

class VideoStitcher:
    """Cross-platform video stitching using FFmpeg directly."""

    def stitch_videos(self, input_files: List[Path], output_file: Path,
                     method: str = "concat") -> None:
        """Stitch videos using specified method.

        Args:
            input_files: List of video files to stitch
            output_file: Output file path
            method: 'concat' (fast) or 'filter' (re-encode)
        """
        if method == "concat":
            self._concat_stitch(input_files, output_file)
        elif method == "filter":
            self._filter_stitch(input_files, output_file)
        else:
            raise ValueError(f"Unknown stitch method: {method}")

    def _concat_stitch(self, inputs: List[Path], output: Path):
        """Fast concatenation (no re-encode)."""
        # Create concat file list
        concat_file = output.parent / f"{output.stem}_concat.txt"
        with open(concat_file, 'w') as f:
            for video in inputs:
                f.write(f"file '{video.absolute()}'\n")

        cmd = [
            'ffmpeg', '-f', 'concat', '-safe', '0',
            '-i', str(concat_file),
            '-c', 'copy',  # No re-encode
            str(output)
        ]
        subprocess.run(cmd, check=True)
        concat_file.unlink()  # Cleanup

    def _filter_stitch(self, inputs: List[Path], output: Path):
        """Re-encode concatenation (slower, handles format mismatches)."""
        # Build filter complex
        filter_parts = []
        for i in range(len(inputs)):
            filter_parts.append(f"[{i}:v][{i}:a]")

        filter_complex = (
            f"{''.join(filter_parts)}"
            f"concat=n={len(inputs)}:v=1:a=1[outv][outa]"
        )

        cmd = ['ffmpeg']
        for video in inputs:
            cmd.extend(['-i', str(video)])

        cmd.extend([
            '-filter_complex', filter_complex,
            '-map', '[outv]', '-map', '[outa]',
            str(output)
        ])
        subprocess.run(cmd, check=True)
```

**Integration:**
```python
# In StitchingService.process_job()
from services.stitching_native import VideoStitcher

stitcher = VideoStitcher()
stitcher.stitch_videos(
    input_files=[clip1_path, clip2_path],
    output_file=output_path,
    method=job.config.get('stitch_method', 'concat')
)
```

---

## Testing Strategy

### Unit Tests
```python
# tests/test_process_manager.py
import platform
import pytest
from utils.process_manager import get_process_manager

def test_process_manager_platform_specific():
    mgr = get_process_manager()
    if platform.system() == 'Windows':
        assert mgr.__class__.__name__ == 'WindowsProcessManager'
    else:
        assert mgr.__class__.__name__ == 'LinuxProcessManager'

def test_spawn_and_kill():
    mgr = get_process_manager()
    proc = mgr.spawn_isolated(['python', '-c', 'import time; time.sleep(10)'])
    assert proc.poll() is None  # Still running
    mgr.kill_process_tree(proc)
    proc.wait(timeout=2)
    assert proc.poll() is not None  # Terminated
```

### Integration Tests (Windows)
- Run each worker type with sample jobs
- Verify GPU scheduling works
- Test multi-GPU configuration
- Measure performance parity with Linux

### CI/CD
- GitHub Actions: Add Windows runner
- Test matrix: Windows 10, Windows 11, Linux
- CUDA versions: 11.8, 12.x

---

## Migration Checklist

### Phase 1 (Minimal Viable)
- [ ] Fix hardcoded Unix paths in `services/diarization.py`
- [ ] Create `utils/process_manager.py`
- [ ] Update `services/diarization.py` to use ProcessManager
- [ ] Add psutil dependency to `requirements.txt`
- [ ] Migrate /proc reads to psutil (3 files)
- [ ] Replace `du` command in `workers/general.py`
- [ ] Add signal handling guards
- [ ] Create `docs/WINDOWS_SETUP.md`
- [ ] Test download worker on Windows
- [ ] Test transcription worker on Windows
- [ ] Test analysis worker on Windows
- [ ] Test clipping worker on Windows

### Phase 2 (Full Parity)
- [ ] Implement `services/stitching_native.py`
- [ ] Port subtitle download to Python
- [ ] Port caption conversion to Python
- [ ] Port voice filter to Python
- [ ] Integration tests for all services
- [ ] Update documentation

### Phase 3 (Production)
- [ ] Create PowerShell installer
- [ ] Windows service setup for Ollama
- [ ] Performance benchmarking
- [ ] CI/CD pipeline updates
- [ ] Final documentation pass

---

## Open Questions

1. **Virtual environment paths:** Should we auto-detect or require explicit config?
2. **Ollama instances:** Single instance with model switching vs multiple instances?
3. **Config file format:** Support both Unix (/) and Windows (\) paths transparently?
4. **GPU enumeration:** Is the nvidia-smi/CUDA reversal common on Windows?
5. **FFmpeg location:** Assume in PATH or ship with installer?

---

## Risks and Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Performance degradation on Windows | Medium | Low | Benchmark early, optimize if needed |
| GPU scheduling issues | High | Medium | Extensive multi-GPU testing |
| Shell script behavior differences | Medium | Medium | Comprehensive integration tests |
| Path separator bugs | Low | Medium | Strict pathlib enforcement |
| Ollama Windows instability | High | Low | Document issues, provide alternatives |

---

## Success Criteria

**Minimal Viable Windows Support:**
- [ ] Core workers (download, transcribe, analyze, clip) run successfully
- [ ] GPU scheduling works correctly (single and multi-GPU)
- [ ] Documentation allows first-time Windows setup in <30 minutes
- [ ] No Linux functionality regression

**Full Windows Parity:**
- [ ] All services functional (including stitching, subtitles, voice filter)
- [ ] Automated installer for Windows
- [ ] Performance within 10% of Linux
- [ ] CI pipeline validates both platforms

---

## Next Steps

1. **Immediate:** Review this plan with stakeholders
2. **Decision Point:** Choose shell script migration strategy (Pure Python vs Dual vs WSL2)
3. **Decision Point:** Confirm process management approach (branches vs abstraction)
4. **Kickoff Phase 1:** Begin with hardcoded path fixes (quick wins)

---

## Appendix: File Audit Summary

### Critical Files Requiring Changes

| File | Issue | Effort | Priority |
|------|-------|--------|----------|
| `services/diarization.py` | Hardcoded paths, killpg, /proc | 4-6h | P0 |
| `services/stitching.py` | Bash dependency | 3-4d | P1 |
| `services/subtitle.py` | Bash dependency | 2-3d | P1 |
| `services/voice_filter.py` | Bash dependency | 2-3d | P1 |
| `workers/general.py` | du command, /proc | 2h | P0 |
| `workers/diarization.py` | /proc reads | 1h | P0 |
| `workers/analysis_distributed.py` | /proc reads | 1h | P0 |

**Total Estimated Effort:**
- Phase 1 (Core): 12-16 hours
- Phase 2 (Full): 7-10 days
- Phase 3 (Production): 3-5 days

**Total: 2-3 weeks for complete Windows compatibility**
