#!/bin/sh
# RAM-boot an OpenWrt initramfs on the Deco (no flash writes) and make it reachable over SSH.
#
#   tools/ramboot.sh [image]      default: latest tplink_deco-x55-v3 initramfs from the build
#
# Needs: tools/serial_daemon.py running, dnsmasq TFTP serving ./tftp on 192.168.0.105,
# the Deco either at the U-Boot prompt or running OpenWrt reachable via tools/dssh.
set -e
cd "$(dirname "$0")/.."
IMG=${1:-openwrt/bin/targets/mediatek/filogic/openwrt-mediatek-filogic-tplink_deco-x55-v3-initramfs-kernel.bin}
DECO_IP=${DECO_IP:-192.168.0.250}
HOST_IP=${HOST_IP:-192.168.0.105}
# U-Boot always uses .250: the PC firewall only allows TFTP from that address
UBOOT_IP=192.168.0.250
LOG=backup/console_live.log
con() { python3 tools/con.py "$@"; }

cp "$IMG" tftp/deco.itb
echo "[*] image: $IMG ($(stat -c%s tftp/deco.itb) bytes)"

n_uboot=$(grep -ac 'daemon: stopped at U-Boot' "$LOG" || true)
# the OpenWrt shell echoes too, so require the U-Boot prompt right after the output
if con --uboot "echo ping_ok" 3 | tr -d '\r' | grep -A1 '^ping_ok$' | grep -q 'MT7981>'; then
	echo "[*] already at U-Boot"
else
	echo "[*] rebooting running system"
	tools/dssh reboot >/dev/null 2>&1 || con --raw "reboot
" 1 >/dev/null
	t=0
	while [ "$(grep -ac 'daemon: stopped at U-Boot' "$LOG")" -le "$n_uboot" ]; do
		sleep 1; t=$((t+1)); [ $t -gt 120 ] && { echo "[!] no U-Boot prompt after 120 s"; exit 1; }
	done
	sleep 1
fi

con --uboot "setenv ipaddr $UBOOT_IP" 5 >/dev/null
con --uboot "setenv serverip $HOST_IP" 5 >/dev/null
con --uboot "setenv bootargs console=ttyS0,115200n8 earlycon=uart8250,mmio32,0x11002000" 5 >/dev/null
con --uboot "tftpboot 0x50000000 deco.itb" 60 | grep -E 'Bytes transferred|Error|Retry' || true
start=$(stat -c%s "$LOG")
con --raw "bootm 0x50000000#config-X50_3_0_0
" 1 >/dev/null
echo "[*] booting..."
t=0
until tail -c +$((start+1)) "$LOG" | grep -aq 'Please press Enter to activate this console'; do
	sleep 1; t=$((t+1))
	if tail -c +$((start+1)) "$LOG" | grep -aqE 'Kernel panic|Unable to handle kernel'; then
		echo "[!] kernel crashed:"; tail -c +$((start+1)) "$LOG" | tr -d '\r' | grep -aE -A15 'Kernel panic|Unable to handle' | head -30; exit 1
	fi
	[ $t -gt 120 ] && { echo "[!] no console after 120 s"; exit 1; }
done
sleep 3
con --raw "
" 2 >/dev/null
if python3 tools/con.py 'cat /sys/class/net/eth1/carrier' 10 | tr -d '\r' | grep -qx 1; then
	# cable on WAN: the unit is a router behind the home router; LAN = Wi-Fi only, keep its DHCP.
	# Management from WAN is opened by the test-defaults overlay (TEST-* firewall rules).
	echo "[*] cable on WAN, waiting for DHCP lease"
	t=0; DECO_IP=""
	while [ -z "$DECO_IP" ]; do
		DECO_IP=$(python3 tools/con.py 'ip -4 -o addr show eth1' 10 | tr -d '\r' | sed -n 's/.*inet \([0-9.]*\)\/.*/\1/p' | head -1)
		[ -n "$DECO_IP" ] && break
		sleep 3; t=$((t+3)); [ $t -gt 60 ] && { echo "[!] no WAN lease"; exit 1; }
	done
else
	# cable on a LAN port = the home LAN: no DHCP / RA from the test unit; temporary address for SSH
	con 'uci set dhcp.lan.ignore=1; uci set dhcp.lan.ra=disabled; uci set dhcp.lan.dhcpv6=disabled' 10 >/dev/null
	con 'uci commit dhcp; /etc/init.d/dnsmasq restart; /etc/init.d/odhcpd restart' 20 >/dev/null
	con "ip addr add $DECO_IP/24 dev br-lan" 10 >/dev/null
fi
export DECO_IP
t=0
until tools/dssh true 2>/dev/null; do
	sleep 2; t=$((t+2)); [ $t -gt 60 ] && { echo "[!] ssh not reachable at $DECO_IP"; exit 1; }
done
echo "[*] DECO_IP=$DECO_IP"
echo "[*] up: $(tools/dssh 'cat /tmp/sysinfo/model; uname -r; uptime')"
