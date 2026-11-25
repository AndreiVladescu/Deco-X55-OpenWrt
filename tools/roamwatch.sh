#!/bin/bash
# Roaming watch: prints where a client is associated (per hostapd) and steering/association events.
#   tools/roamwatch.sh <client_mac> <name=ip> [<name=ip> ...]
cd "$(dirname "$0")/.."
PH=$1; shift
for n in "$@"; do
	name=${n%%=*}; ip=${n#*=}
	( DECO_IP=$ip tools/dssh 'logread -f' 2>/dev/null | grep --line-buffered -i "$PH" \
		| grep --line-buffered -iE "AP-STA|BSS-TM|kick|roam|steer|disassoc|deauth|transition" | grep --line-buffered -v "reduce threshold" \
		| sed -u -E "s/^.*(daemon|user)\.[a-z]+ /[$name] /" ) &
done
last=""
while true; do
	now=""
	for n in "$@"; do
		name=${n%%=*}; ip=${n#*=}
		a=$(DECO_IP=$ip tools/dssh "for i in phy0-ap0 phy1-ap0; do ubus call hostapd.\$i get_clients 2>/dev/null | grep -q '$PH' && echo \"\$i \$(iw dev \$i station get $PH 2>/dev/null | awk '/^\tsignal:/{print \$2}')dBm\"; done" 2>/dev/null)
		[ -n "$a" ] && now="$now $name:$a"
	done
	[ -z "$now" ] && now=" (not associated)"
	k=$(echo "$now" | awk '{print $1}')
	[ "$k" != "$last" ] && { echo "[where] $(date +%T)$now"; last="$k"; }
	sleep 3
done
