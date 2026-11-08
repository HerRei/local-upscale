#!/bin/bash
# Temporary (removed in the next commit): where and why the ROCm AppImage's worker hangs at startup.
# Usage: bash .github/debug-rocm-hang.sh ARTIFACT.AppImage
set -u
mkdir -p /mnt/lsr/dbg
(cd /mnt/lsr/dbg && "$1" --appimage-extract > /dev/null)
ROOT=/mnt/lsr/dbg/squashfs-root
W=$(find "$ROOT" -name localsr-worker -type f | head -1)
echo "worker: $W"
readelf -d "$W" | grep -i -E "rpath|runpath" || true
find "$ROOT/usr/lib" -maxdepth 1 -regextype egrep -iregex '.*(gomp|libomp|vulkan|rocprof|hsa|rocshmem|stdc|gcc_s|ibverbs|rdmacm).*' -printf '%f\n' | sort

run_case() {
  name=$1
  shift
  echo "=== $name"
  "$@" > "/mnt/lsr/dbg/$name.out" 2>&1 &
  pid=$!
  for _ in $(seq 1 90); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
  if kill -0 "$pid" 2>/dev/null; then
    echo "$name: HANG after 90 s"
    for p in $(pgrep -f localsr-worker); do
      sudo gdb -p "$p" -batch -ex "thread apply all bt 40" 2>&1 \
        | grep -v -e '^\[New LWP' -e '^warning:' -e '^Reading symbols' -e '^Downloading' | head -220
      sudo "$(command -v py-spy)" dump --native --pid "$p" 2>&1 | head -60
    done
    sudo pkill -9 -f localsr-worker || true
    kill -9 "$pid" 2>/dev/null || true
  else
    wait "$pid"
    echo "$name: exit $?"
  fi
  grep -a "calling init" "/mnt/lsr/dbg/$name.out" | tail -15 || true
  grep -a -v -e "calling init" -e "calling fini" -e "find library" -e "search path" -e "trying file" "/mnt/lsr/dbg/$name.out" | tail -25
}

run_case alone env -i HOME="$HOME" PATH=/usr/bin:/bin "$W" --backend-probe AMD-ROCm --output /mnt/lsr/dbg/alone.json
run_case alone-ld env -i HOME="$HOME" PATH=/usr/bin:/bin LD_DEBUG=libs "$W" --backend-probe AMD-ROCm --output /mnt/lsr/dbg/alone-ld.json
run_case appdir-lib env -i HOME="$HOME" PATH=/usr/bin:/bin LD_LIBRARY_PATH="$ROOT/usr/lib" LD_DEBUG=libs "$W" --backend-probe AMD-ROCm --output /mnt/lsr/dbg/appdir.json
run_case apprun env -i HOME="$HOME" PATH=/usr/bin:/bin APPDIR="$ROOT" LOCALSR_SMOKE_TIMEOUT_SECONDS=60 "$ROOT/AppRun" --headless-smoke-test
exit 1  # never let the debug build pass
