# Windows Porting Guide

**Status:** Informational
**Created:** 2025-12-03
**Purpose:** Guide for running VidOps on Windows

---

## Overview

VidOps is approximately **95% Windows-compatible** out of the box. The Python codebase uses cross-platform libraries (pathlib, psycopg2, etc.) and should run natively on Windows with minimal changes. The main consideration is how to handle the legacy bash scripts.

---

## ✅ Already Cross-Platform (No Changes Needed)

### Python Codebase
All Python code in `vidops/` package works on Windows:
- **Path handling:** Uses `pathlib.Path` which handles Windows paths correctly
- **Database:** psycopg2 works natively on Windows
- **HTTP broker:** Storage broker client is platform-agnostic
- **CLI:** All `vo_cli.py` commands work on Windows

### Dependencies
All major dependencies have Windows support:
- ✅ Python 3.8+ (Windows native)
- ✅ PostgreSQL client (psycopg2 Windows binaries available)
- ✅ yt-dlp (cross-platform Python)
- ✅ ffmpeg (Windows binaries available from ffmpeg.org)
- ✅ faster-whisper + CUDA (NVIDIA provides Windows CUDA toolkit)
- ✅ PyTorch (Official Windows + CUDA builds)
- ✅ Resemblyzer (voice filtering works on Windows)

### Core Functionality
These work on Windows without modification:
- ✅ All CLI commands (`vo_cli.py`)
- ✅ All workers (download, transcribe, clip, analyze, diarize, voice)
- ✅ Database operations
- ✅ Storage broker client
- ✅ QuickClip system
- ✅ Job queue system
- ✅ Asset management

---

## ⚠️ Platform-Specific Considerations

### 1. Legacy Bash Scripts (Medium Effort)

**Issue:** Scripts in `scripts/` and `workspace.sh` are bash-only

**Solutions:**

**Option A: WSL2 (Recommended)**
- Run bash scripts in Windows Subsystem for Linux
- Zero code changes required
- Access Windows filesystem via `/mnt/c/`, `/mnt/d/`, etc.
- GPU passthrough works (CUDA available in WSL2)
- Best of both worlds

**Option B: PowerShell Ports**
- Rewrite scripts in PowerShell
- More native Windows experience
- Moderate effort (~1-2 weeks for critical scripts)
- Most scripts just wrap Python calls anyway

**Option C: Python CLI Only**
- Skip legacy scripts entirely
- Use Python workers for everything
- QuickClip and new systems don't need bash
- Legacy `workspace.sh` compatibility scripts can be dropped

### 2. File Paths (Low Effort)

**Issue:** Some paths are Unix-specific

**Current (Unix):**
```yaml
paths:
  central_storage_root: "/mnt/mainroot/mnt/13tb_sas/vidops/storage"
  local_temp_dir: "tmp"
```

**Windows Equivalent:**
```yaml
paths:
  central_storage_root: "Z:\\vidops\\storage"  # Mapped drive
  # OR
  central_storage_root: "\\\\server\\share\\vidops\\storage"  # UNC path
  local_temp_dir: "tmp"  # Works on both platforms
```

**Fix:** Make paths configurable per platform in `config.yaml`:
```yaml
paths:
  central_storage_root:
    windows: "Z:\\vidops\\storage"
    linux: "/mnt/mainroot/mnt/13tb_sas/vidops/storage"
```

Then add platform detection:
```python
import platform

def get_storage_root(config):
    if platform.system() == "Windows":
        return config.paths.central_storage_root.windows
    else:
        return config.paths.central_storage_root.linux
```

### 3. Storage/Network Shares (Low Effort)

**Unix mount points vs Windows drives:**
- Unix: `/mnt/mainroot/mnt/13tb_sas/`
- Windows: `Z:\` (mapped drive) or `\\server\share\` (UNC)

**Recommendation:** Use environment variable for flexibility:
```bash
# Unix
export VIDOPS_STORAGE_ROOT="/mnt/mainroot/mnt/13tb_sas/vidops/storage"

# Windows
set VIDOPS_STORAGE_ROOT=Z:\vidops\storage
# or
set VIDOPS_STORAGE_ROOT=\\server\share\vidops\storage
```

---

## 📋 Migration Approaches

### Approach 1: WSL2 (Fastest - 1 Hour)

**Best for:** Getting started quickly, development, full compatibility

**Setup:**
```powershell
# On Windows (PowerShell as Administrator):
wsl --install
wsl --set-default-version 2
# Reboot if needed

# Inside WSL (Ubuntu):
sudo apt update
sudo apt install python3.10 python3-pip python3-venv postgresql-client ffmpeg git

# Clone repo (can access Windows drives)
cd /mnt/c/Users/YourName/Projects/
git clone <repo-url>
cd vidops

# Setup Python environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Configure storage paths (Windows drives accessible via /mnt/)
export VIDOPS_STORAGE_ROOT="/mnt/z/vidops/storage"  # if Z: drive mapped

# Run as normal
python vo_cli.py quickclip create "URL" "120-145" -d "Test"
```

**Advantages:**
- ✅ Zero code changes
- ✅ Full bash script compatibility
- ✅ CUDA/GPU support (passes through to WSL2)
- ✅ Access Windows filesystem
- ✅ Can use Linux tools (grep, sed, etc.)

**Disadvantages:**
- ⚠️ Requires WSL2 installation
- ⚠️ Slight overhead (minimal with WSL2)

### Approach 2: Native Windows Python (3-5 Days)

**Best for:** Pure Windows environment, no WSL dependency

**Steps:**

1. **Install dependencies:**
   ```powershell
   # Python from python.org or Microsoft Store
   # ffmpeg from ffmpeg.org (add to PATH)
   # CUDA toolkit from NVIDIA (for GPU transcription)

   pip install -r requirements.txt
   ```

2. **Update config.yaml:**
   ```yaml
   paths:
     central_storage_root: "Z:\\vidops\\storage"  # or UNC path
     local_temp_dir: "tmp"  # relative paths work fine
   ```

3. **Test all workers:**
   - Download worker: Works as-is
   - Transcription worker: Requires CUDA drivers + GPU
   - Clipping worker: Requires ffmpeg in PATH
   - QuickClip: Works as-is (Python-only)
   - Voice filtering: Works as-is
   - All analysis/diarization: Works as-is

4. **Skip bash scripts** or use Python equivalents
   - Most `workspace.sh` functionality available via `vo_cli.py`
   - Legacy scripts can be phased out

**Advantages:**
- ✅ Native Windows performance
- ✅ No WSL dependency
- ✅ Simpler deployment

**Disadvantages:**
- ⚠️ Can't use legacy bash scripts
- ⚠️ Requires PATH configuration for ffmpeg

### Approach 3: Full Native Windows (1-2 Weeks)

**Best for:** Complete Windows integration, no Unix dependencies

**Additional work:**
- Port critical bash scripts to PowerShell
- Or rewrite in Python (preferred - consolidate into `vo_cli.py`)
- Create Windows-specific installers/setup scripts

**Scripts to consider porting:**
- `workspace.sh` → `workspace.ps1` or expand `vo_cli.py`
- Clipping scripts (many just wrap Python)
- Transcription scripts (mostly Python wrappers)

---

## Dependency Installation (Windows Native)

### Python
```powershell
# Download from python.org (3.10+)
# Or use Microsoft Store version
# Or use Chocolatey:
choco install python
```

### ffmpeg
```powershell
# Download from ffmpeg.org
# Extract to C:\ffmpeg\
# Add C:\ffmpeg\bin to PATH

# Or use Chocolatey:
choco install ffmpeg
```

### CUDA (for GPU transcription)
```powershell
# Download CUDA Toolkit from NVIDIA
# Requires NVIDIA GPU with compute capability 3.5+
# https://developer.nvidia.com/cuda-downloads
```

### Python Packages
```powershell
# Create virtual environment
python -m venv .venv
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# For GPU transcription:
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
```

### PostgreSQL Client
```powershell
# psycopg2 binary (easier on Windows)
pip install psycopg2-binary
```

---

## Configuration for Windows

### Example config.yaml
```yaml
database:
  host: "192.168.0.187"  # Can point to Linux DB server
  port: 5432
  name: "transcripts"
  user: "billie"
  password: "your-password"

paths:
  # Use Windows path format
  central_storage_root: "Z:\\vidops\\storage"
  # Or UNC path:
  # central_storage_root: "\\\\192.168.0.187\\vidops\\storage"

  local_temp_dir: "tmp"  # Relative paths work on both platforms

# Rest of config works as-is
```

### Environment Variables (Windows)
```powershell
# Set permanently (System Properties > Environment Variables)
# Or temporarily in PowerShell:
$env:VIDOPS_STORAGE_ROOT = "Z:\vidops\storage"
$env:VIDOPS_PROJECT_ROOT = "C:\Users\YourName\Projects\overlord_test"
```

---

## Testing Checklist

After setup, verify these work:

```powershell
# Activate virtual environment
.venv\Scripts\activate

# Test database connection
python vo_cli.py status

# Test download
python vo_cli.py download enqueue "https://youtube.com/watch?v=dQw4w9WgXcQ"

# Test QuickClip (fully Python, should work perfectly)
python vo_cli.py quickclip create "URL" "120-145" -d "Test"

# Test worker (if you have GPU + CUDA)
python vo_cli.py worker general

# Check worker sees jobs
python vo_cli.py status
```

---

## Known Issues & Workarounds

### Issue: Path Separators in TSV Files
**Problem:** Some scripts may generate TSV files with Unix paths
**Workaround:** Python code uses `pathlib` which handles this correctly
**Status:** Should not be an issue

### Issue: Line Endings (CRLF vs LF)
**Problem:** Windows uses CRLF, Unix uses LF
**Workaround:** Git handles this with `core.autocrlf`
**Status:** Configure git correctly

### Issue: Case Sensitivity
**Problem:** Windows filesystem is case-insensitive
**Workaround:** Be consistent with case in filenames
**Status:** Minor consideration

### Issue: File Locking
**Problem:** Windows locks files more aggressively than Unix
**Workaround:** Ensure proper file closing in Python code
**Status:** Unlikely to cause issues (code uses context managers)

---

## Performance Considerations

### Storage Access
- **Network shares (SMB):** May be slower than NFS on Unix
- **Mapped drives:** Perform better than UNC paths
- **Local storage:** Same performance as Unix

### GPU Transcription
- **Native Windows:** Same CUDA performance as Linux
- **WSL2:** Slight overhead but negligible for transcription

### Workers
- **Python workers:** Same performance on Windows
- **File I/O:** Windows may be slightly slower for many small files

---

## Recommendations

**For Most Users:**
1. Start with **WSL2 approach** (1 hour setup, zero code changes)
2. If WSL2 works well, stick with it
3. If you need native Windows, migrate to **Approach 2** later

**For Production Windows Deployments:**
1. Use **native Windows Python** (Approach 2)
2. Skip legacy bash scripts
3. Use Python workers exclusively
4. Map network storage as drive letter (better than UNC)

**For Development:**
1. WSL2 is ideal (full compatibility, fast iteration)
2. Can still edit files in Windows editors
3. Run Python/workers in WSL

---

## Future Enhancements

Potential improvements for better Windows support:

1. **Platform-aware config:**
   ```python
   class PlatformPaths:
       windows: str
       linux: str

   def get_path():
       return config.paths.storage.windows if is_windows() else config.paths.storage.linux
   ```

2. **Windows installer:**
   - Package as `.exe` with dependencies
   - Auto-configure ffmpeg, CUDA
   - GUI for configuration

3. **PowerShell utilities:**
   - `workspace.ps1` for common operations
   - Or expand `vo_cli.py` to cover all use cases

4. **Docker option:**
   - Run entire system in Docker Desktop for Windows
   - No PATH configuration needed
   - Consistent Linux environment

---

## Summary

**Current Windows Compatibility:** ~95%

**To Get to 100%:**
- WSL2 approach: **1 hour** (just install WSL)
- Native approach: **3-5 days** (config updates + testing)
- Full native (no bash): **1-2 weeks** (script rewrites)

**Recommended Path:**
1. Install WSL2
2. Run VidOps as-is (zero code changes)
3. Evaluate if native Windows needed later

The Python codebase is already cross-platform thanks to proper use of pathlib and platform-agnostic libraries. The only real consideration is how to handle legacy bash scripts, and WSL2 solves that completely.

---

## Quick Start (WSL2)

```powershell
# Windows PowerShell (as Administrator)
wsl --install

# After reboot, in WSL Ubuntu:
sudo apt update && sudo apt install -y python3.10 python3-pip ffmpeg postgresql-client git
cd /mnt/c/Users/YourName/
git clone <your-repo>
cd vidops
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Configure and run
cp config.yaml.example config.yaml
# Edit config.yaml with your settings
python vo_cli.py quickclip create "URL" "120-145" -d "First Windows clip!"
```

Done! 🎉
