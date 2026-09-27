#!/bin/sh
# Target: Linux, POSIX sh, awk, sleep, dan procps ps.
set -eu
export LC_ALL=C
printf 'Uptime\n'
awk '{printf "%.0f seconds (%.2f days)\n", $1, $1/86400}' /proc/uptime
printf '\nMemory usage (host /proc view)\n'
awk '
  /^MemTotal:/ {total=$2}
  /^MemAvailable:/ {available=$2; found=1}
  END {
    if (!total || !found) exit 1
    printf "Used: %.2f MiB / %.2f MiB (%.2f%%)\nAvailable: %.2f MiB\n", (total-available)/1024, total/1024, 100*(total-available)/total, available/1024
  }' /proc/meminfo
cpu_sample() {
  awk '/^cpu / {total=0; for(i=2;i<=9;i++) total+=$i; print total, $5+$6; exit}' /proc/stat
}
first=$(cpu_sample)
sleep 1
second=$(cpu_sample)
printf '\nCPU usage (1-second sample, all host CPUs)\n'
awk -v first="$first" -v second="$second" 'BEGIN {
  split(first,a); split(second,b); total=b[1]-a[1]; idle=b[2]-a[2]
  if (total<=0) exit 1
  printf "%.2f%%\n", 100*(total-idle)/total
}'
printf '\nTop 5 live processes by CPU (ps lifetime average; includes sleeping processes)\n'
# comm excludes command-line arguments, which can contain credentials.
processes=$(ps -eo pid,comm,pcpu,pmem --sort=-pcpu)
printf '%s\n' "$processes" | awk 'NR<=6'
