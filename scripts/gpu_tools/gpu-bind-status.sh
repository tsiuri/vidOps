#!/usr/bin/env bash
# gpu-bind-status.sh — show driver ownership for GPU-class devices (all vendors)
set -euo pipefail

vendor_name(){
  case "$1" in
    0x10de) echo "NVIDIA" ;;
    0x1002) echo "AMD" ;;
    0x1022) echo "AMD" ;;
    0x8086) echo "Intel" ;;
    0x1234) echo "QEMU" ;;
    *) echo "$1" ;;
  esac
}

describe_device(){
  local bdf="$1"
  if command -v lspci >/dev/null 2>&1; then
    local raw
    raw=$(lspci -s "$bdf" 2>/dev/null || true)
    [[ -n "$raw" ]] || return 1
    printf "%s" "${raw#* }"
    return 0
  fi
  return 1
}

printf "%-12s %-8s %-10s %-18s %s\n" "BDF" "Vendor" "Class" "Driver" "Description"
for p in /sys/bus/pci/devices/*; do
  [[ -e "$p/class" ]] || continue
  cls=$(<"$p/class")
  case "$cls" in
    0x030000|0x030001|0x030002|0x030200|0x030400|0x040300) ;;
    *) continue ;;
  esac

  bdf=$(basename "$p")
  ven=$(<"$p/vendor")
  drv="-"
  if [[ -L "$p/driver" ]]; then drv=$(basename "$(readlink -f "$p/driver")"); fi
  if ! desc=$(describe_device "$bdf"); then
    desc="-"
  fi
  printf "%-12s %-8s %-10s %-18s %s\n" "$bdf" "$(vendor_name "$ven")" "$cls" "$drv" "$desc"
done
