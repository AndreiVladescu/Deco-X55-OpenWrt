#!/bin/bash
# Long stability test for the meshed Decos (run on the PC, e.g. overnight).
#   tools/soak2.sh <hours> <outdir> <main_ip> <satellite_ip>
# Every 60 s: health sample of each unit -> <outdir>/samples.csv, new kernel/log errors -> errors.log
# Every 10 min: 60 s iperf3 PC -> main (wire) -> mesh -> satellite, alternating direction -> iperf.csv
# Whole run: 1/s ping to both units -> ping_<unit>.log
cd "$(dirname "$0")/.."
H=${1:-12}; OUT=${2:-backup/soak2}; MAIN=${3:-192.168.0.250}; SAT=${4:-192.168.0.218}
mkdir -p "$OUT"; end=$(( $(date +%s) + H * 3600 )); [ "$H" = 0 ] && end=$(( $(date +%s) + 180 ))   # 0 = 3-min dry run
echo "time,unit,uptime_s,mem_avail_kB,load1,temp_mC,mesh_tx,mesh_rx,mesh_signal,batman_tq,batman_ifs,procs,rss_hostapd,rss_usteerd,rss_netifd,rss_dnsmasq,rss_rpcd,rss_uhttpd,ecc_corrected,ecc_failed,overlay_used_kB,conntrack" > "$OUT/samples.csv"
echo "time,direction,mbit" > "$OUT/iperf.csv"
: > "$OUT/errors.log"
for u in main=$MAIN satellite=$SAT; do
	n=${u%%=*}; ip=${u#*=}
	( while [ "$(date +%s)" -lt "$end" ]; do ping -D -O -i 1 -W 1 -w 600 "$ip"; done ) > "$OUT/ping_$n.log" 2>&1 &
	# error baseline: remember how many matching dmesg lines exist now
	DECO_IP=$ip tools/dssh 'dmesg | grep -ciE "warn|error|fail|oops|bug:|call trace|watchdog|hung|timeout"' > "$OUT/.base_$n" 2>/dev/null
done
DECO_IP=$SAT tools/dssh 'pgrep iperf3 >/dev/null || iperf3 -s -D' >/dev/null 2>&1

sample() {   # sample <name> <ip>
	local n=$1 ip=$2 base; base=$(cat "$OUT/.base_$n" 2>/dev/null || echo 0)
	local r; r=$(DECO_IP=$ip tools/dssh '
		rss() { p=$(pidof $1 | cut -d" " -f1); [ -n "$p" ] && awk "/VmRSS/{print \$2}" /proc/$p/status || echo 0; }
		st=$(iw dev mesh0 station dump 2>/dev/null)
		tx=$(echo "$st" | awk "/tx bitrate/{print \$3; exit}"); rx=$(echo "$st" | awk "/rx bitrate/{print \$3; exit}")
		sg=$(echo "$st" | awk "/^\tsignal:/{print \$2; exit}")
		tq=$(batctl o -H 2>/dev/null | head -1 | sed -n "s/.*(\s*\([0-9]*\)).*/\1/p")
		bif=$(batctl if 2>/dev/null | grep -c active)
		ecc_c=0; ecc_f=0; for m in /sys/class/mtd/mtd*/; do
			[ -f $m/corrected_bits ] && ecc_c=$((ecc_c + $(cat $m/corrected_bits))); [ -f $m/ecc_failures ] && ecc_f=$((ecc_f + $(cat $m/ecc_failures))); done
		echo "$(cut -d. -f1 /proc/uptime),$(awk "/MemAvailable/{print \$2}" /proc/meminfo),$(cut -d" " -f1 /proc/loadavg),$(cat /sys/class/thermal/thermal_zone0/temp),${tx:-0},${rx:-0},${sg:-0},${tq:-0},$bif,$(ls -d /proc/[0-9]* | wc -l),$(rss hostapd),$(rss usteerd),$(rss netifd),$(rss dnsmasq),$(rss rpcd),$(rss uhttpd),$ecc_c,$ecc_f,$(df -k /overlay | awk "NR==2{print \$3}"),$(cat /proc/sys/net/netfilter/nf_conntrack_count 2>/dev/null || echo 0)"
		echo "@@ERR"; dmesg | grep -iE "warn|error|fail|oops|bug:|call trace|watchdog|hung|timeout" | tail -n +'$((base + 1))'
		logread -l 200 2>/dev/null | grep -iE "daemon\.(err|crit)|kern\.(err|crit|alert|emerg)|crash|respawn|segfault" | tail -5' 2>/dev/null)
	if [ -z "$r" ]; then echo "$(date +%T),$n,UNREACHABLE" >> "$OUT/samples.csv"; return; fi
	echo "$(date +%T),$n,$(echo "$r" | head -1)" >> "$OUT/samples.csv"
	echo "$r" | sed -n '/^@@ERR$/,$p' | sed 1d | while IFS= read -r l; do
		grep -qxF "[$n] $l" "$OUT/errors.log" || echo "[$n] $l" >> "$OUT/errors.log"
	done
}

i=0; dir=""
while [ "$(date +%s)" -lt "$end" ]; do
	sample main "$MAIN"; sample satellite "$SAT"
	if [ $((i % 10)) -eq 0 ]; then
		mbit=$(iperf3 -c "$SAT" -t 60 -P 4 $dir -J 2>/dev/null | python3 -c \
			'import json,sys; print(int(json.load(sys.stdin)["end"]["sum_received"]["bits_per_second"]/1e6))' 2>/dev/null)
		if [ -z "$mbit" ]; then mbit=FAIL; DECO_IP=$SAT tools/dssh 'pgrep iperf3 >/dev/null || iperf3 -s -D' >/dev/null 2>&1; fi
		label="PC->sat"; [ -n "$dir" ] && label="sat->PC"
		echo "$(date +%T),$label,$mbit" >> "$OUT/iperf.csv"
		[ -z "$dir" ] && dir="-R" || dir=""
	fi
	i=$((i + 1)); sleep 60
done
DECO_IP=$SAT tools/dssh 'killall iperf3' >/dev/null 2>&1
wait
echo "done: $OUT"
