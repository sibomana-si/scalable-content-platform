#!/usr/bin/env bash
#
# Hold the laptop's power policy still for a load test, and record what it was.
#
#   scripts/perf_env.sh lock            assert AC power, pin the `performance` profile
#   scripts/perf_env.sh report [FILE]   write one JSON snapshot of the machine state
#   scripts/perf_env.sh unlock          restore the `balanced` profile
#
# Why a profile and not `cpupower frequency-set`: power-profiles-daemon is active on this
# machine and owns both the governor and the energy performance preference. It reverts a raw
# sysfs write, and it does so mid-run without saying anything, which is the worst failure
# available because the run still produces numbers.
#
# The gain is variance, not throughput. On intel_pstate in active mode `powersave` already
# reaches turbo under load; `performance` pins the HWP floor, which takes ramp-up delay out of
# the P99 tail. It also reaches the 45 W PL1 clamp sooner, not later. The point is that runs A,
# B and C meet the same machine.
#
# `lock` disarms its own trap on success, so a locked machine stays locked for the caller. The
# caller owns the unlock: scripts/run_load_matrix.sh installs an EXIT trap for exactly that.

set -euo pipefail

SETTLE_SECONDS="${SETTLE_SECONDS:-3}"
K6_CPUSET="${K6_CPUSET:-12-19}"
CPU_SYS=/sys/devices/system/cpu
RAPL=/sys/class/powercap/intel-rapl:0

read_first() {
  # Print the contents of the first path that exists, or the fallback.
  local fallback="$1"
  shift
  local path
  for path in "$@"; do
    if [[ -r "$path" ]]; then
      tr -d '\n' <"$path"
      return 0
    fi
  done
  printf '%s' "$fallback"
}

sum_over_cpus() {
  # Core throttling is per core, so the package figure alone hides a single hot core.
  local leaf="$1" total=0 path
  for path in "$CPU_SYS"/cpu[0-9]*/thermal_throttle/"$leaf"; do
    [[ -r "$path" ]] || continue
    total=$((total + $(cat "$path")))
  done
  printf '%s' "$total"
}

microwatts_to_watts() {
  local value="$1"
  if [[ "$value" == "null" ]]; then
    printf 'null'
  else
    awk -v uw="$value" 'BEGIN { printf "%.1f", uw / 1000000 }'
  fi
}

json_snapshot() {
  local governor epp profile no_turbo min_pct max_pct pl1 pl2 package core ac load
  governor=$(read_first unknown "$CPU_SYS/cpu0/cpufreq/scaling_governor")
  epp=$(read_first unknown "$CPU_SYS/cpu0/cpufreq/energy_performance_preference")
  profile=$(powerprofilesctl get 2>/dev/null || printf 'unknown')
  no_turbo=$(read_first null "$CPU_SYS/intel_pstate/no_turbo")
  min_pct=$(read_first null "$CPU_SYS/intel_pstate/min_perf_pct")
  max_pct=$(read_first null "$CPU_SYS/intel_pstate/max_perf_pct")
  pl1=$(microwatts_to_watts "$(read_first null "$RAPL/constraint_0_power_limit_uw")")
  pl2=$(microwatts_to_watts "$(read_first null "$RAPL/constraint_1_power_limit_uw")")
  package=$(read_first 0 "$CPU_SYS/cpu0/thermal_throttle/package_throttle_count")
  core=$(sum_over_cpus core_throttle_count)
  ac=$(read_first 0 /sys/class/power_supply/AC/online /sys/class/power_supply/ACAD/online)
  load=$(cut -d' ' -f1 /proc/loadavg)

  cat <<JSON
{
  "timestamp": "$(date --iso-8601=seconds)",
  "hostname": "$(hostname)",
  "kernel": "$(uname -r)",
  "cpu_model": "$(sed -n 's/^model name[[:space:]]*: //p' /proc/cpuinfo | head -1)",
  "nproc": $(nproc),
  "governor": "${governor}",
  "energy_performance_preference": "${epp}",
  "power_profile": "${profile}",
  "no_turbo": ${no_turbo},
  "min_perf_pct": ${min_pct},
  "max_perf_pct": ${max_pct},
  "rapl_pl1_watts": ${pl1},
  "rapl_pl2_watts": ${pl2},
  "package_throttle_count": ${package},
  "core_throttle_count": ${core},
  "k6_cpuset": "${K6_CPUSET}",
  "ac_online": ${ac},
  "loadavg": ${load}
}
JSON
}

cmd_lock() {
  local ac
  ac=$(read_first 0 /sys/class/power_supply/AC/online /sys/class/power_supply/ACAD/online)
  if [[ "$ac" != "1" ]]; then
    echo "perf_env: the laptop is on battery. Plug it in: on battery the numbers mean nothing." >&2
    exit 1
  fi

  # Armed only for the settle window. A clean lock must leave the machine locked.
  trap 'cmd_unlock; exit 130' INT TERM
  powerprofilesctl set performance
  sleep "$SETTLE_SECONDS"
  trap - INT TERM

  echo "perf_env: locked · profile=$(powerprofilesctl get)" \
    "governor=$(read_first unknown "$CPU_SYS/cpu0/cpufreq/scaling_governor")" \
    "epp=$(read_first unknown "$CPU_SYS/cpu0/cpufreq/energy_performance_preference")" \
    "k6_cpuset=${K6_CPUSET}"
}

cmd_report() {
  local target="${1:-}"
  if [[ -n "$target" ]]; then
    mkdir -p "$(dirname "$target")"
    json_snapshot >"$target"
    echo "perf_env: wrote ${target}"
  else
    json_snapshot
  fi
}

cmd_unlock() {
  powerprofilesctl set balanced
  echo "perf_env: unlocked · profile=$(powerprofilesctl get)"
}

case "${1:-}" in
  lock) cmd_lock ;;
  report) cmd_report "${2:-}" ;;
  unlock) cmd_unlock ;;
  *)
    echo "usage: $0 {lock|report [FILE]|unlock}" >&2
    exit 2
    ;;
esac
