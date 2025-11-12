#!/usr/bin/env bash
# gpu-to-nvidia.sh — rebind NVIDIA GPU back to the host drivers (nvidia + snd_hda_intel)
set -euo pipefail

log(){ printf '\033[1;34m==> %s\033[0m\n' "$*"; }
warn(){ printf '\033[1;33m!!  %s\033[0m\n' "$*" >&2; }
die(){ printf '\033[1;31mxx  %s\033[0m\n' "$*" >&2; exit 1; }

require_root(){ [[ $EUID -eq 0 ]] || die "Run as root."; }

load_nvidia_stack(){
  local fail=0
  for mod in nvidia nvidia_modeset nvidia_uvm nvidia_drm; do
    if ! modprobe "$mod" >/dev/null 2>&1; then
      warn "Could not load module $mod (device still bound or module unavailable?)."
      fail=1
    fi
  done
  return $fail
}

load_audio_stack(){
  if ! modprobe snd_hda_intel >/dev/null 2>&1; then
    warn "Could not load module snd_hda_intel (HDMI audio may remain detached)."
    return 1
  fi
  return 0
}

current_driver(){
  local bdf="$1" link="/sys/bus/pci/devices/$bdf/driver"
  if [[ -L "$link" ]]; then
    basename "$(readlink -f "$link")"
  else
    echo ""
  fi
}

discover_nvidia_functions(){
  local out_gpu=() out_hda=()
  for devpath in /sys/bus/pci/devices/*; do
    [[ -e "$devpath/vendor" && -e "$devpath/class" ]] || continue
    local ven devcls bdf
    ven=$(<"$devpath/vendor")
    devcls=$(<"$devpath/class")
    bdf=$(basename "$devpath")
    [[ "$ven" == "0x10de" ]] || continue
    case "$devcls" in
      0x030000|0x030200) out_gpu+=("$bdf") ;;  # VGA/3D -> nvidia
      0x040300)          out_hda+=("$bdf") ;;  # HDMI audio -> snd_hda_intel
    esac
  done
  printf '%s\0' "${out_gpu[@]}" "${out_hda[@]}"
}

unbind_if_vfio(){
  local bdf="$1"
  local drv; drv=$(current_driver "$bdf")
  [[ "$drv" == "vfio-pci" ]] || return 0
  printf "%s" "$bdf" > "/sys/bus/pci/drivers/vfio-pci/unbind"
  log "Unbound $bdf from vfio-pci"
}

unbind_current_driver(){
  local bdf="$1"
  local drvlink="/sys/bus/pci/devices/$bdf/driver"
  [[ -L "$drvlink" ]] || return 0
  local drv; drv=$(basename "$(readlink -f "$drvlink")")
  if [[ -e "/sys/bus/pci/devices/$bdf/driver/unbind" ]]; then
    printf "%s" "$bdf" > "/sys/bus/pci/devices/$bdf/driver/unbind"
    log "Unbound $bdf from $drv"
  fi
}

bind_to(){
  local bdf="$1" drv="$2"
  local bind_path="/sys/bus/pci/drivers/$drv/bind"
  if [[ ! -e "$bind_path" ]]; then
    case "$drv" in
      nvidia)
        load_nvidia_stack || true
        ;;
      snd_hda_intel)
        load_audio_stack || true
        ;;
    esac
    [[ -e "$bind_path" ]] || die "Driver $drv has no bind file."
  fi

  local cur; cur=$(current_driver "$bdf")
  if [[ "$cur" == "$drv" ]]; then
    log "$bdf already bound to $drv"
    return 0
  elif [[ -n "$cur" ]]; then
    unbind_current_driver "$bdf"
    udevadm settle || true
  fi

  echo "$drv" > "/sys/bus/pci/devices/$bdf/driver_override"
  local attempt success=0
  for attempt in 1 2 3; do
    if printf "%s" "$bdf" > "$bind_path"; then
      success=1
      break
    fi
    warn "Bind attempt $attempt for $bdf -> $drv failed (current driver: $(current_driver "$bdf")). Retrying..."
    sleep 1
    unbind_current_driver "$bdf"
    udevadm settle || true
  done
  echo "" > "/sys/bus/pci/devices/$bdf/driver_override"
  (( success )) || die "Unable to bind $bdf to $drv (device remains busy)."
  log "Bound $bdf -> $drv"
}

main(){
  require_root

  # Load host drivers first so bind works (will retry after unbinding if needed)
  load_nvidia_stack || true
  load_audio_stack || true
  udevadm settle || true

  mapfile -d '' ALL < <(discover_nvidia_functions)
  [[ ${#ALL[@]} -gt 0 ]] || die "No NVIDIA functions found."

  # Split functions by class again to decide target drivers
  local gpus=() hdas=()
  for bdf in "${ALL[@]}"; do
    local cls
    cls=$(<"/sys/bus/pci/devices/$bdf/class")
    case "$cls" in
      0x030000|0x030200) gpus+=("$bdf") ;;
      0x040300)          hdas+=("$bdf") ;;
    esac
  done

  # Unbind from vfio if currently attached
  for bdf in "${gpus[@]}" "${hdas[@]}"; do unbind_if_vfio "$bdf"; done
  udevadm settle || true

  # Retry loading drivers now that the functions are free from vfio
  load_nvidia_stack || warn "NVIDIA modules still not loading; check dmesg if binding fails."
  load_audio_stack || warn "snd_hda_intel still unavailable; HDMI audio will stay detached."

  # Bind back to the appropriate drivers
  for bdf in "${gpus[@]}"; do bind_to "$bdf" "nvidia"; done
  for bdf in "${hdas[@]}"; do bind_to "$bdf" "snd_hda_intel"; done

  udevadm settle || true
  log "Done. GPU is now owned by host drivers (nvidia + snd_hda_intel)."
  log "If you use a display manager, start it again (e.g., systemctl start gdm)."
}

main "$@"
