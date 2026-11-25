# stage 1: discovery only (read-only), then reboot
mount -t proc p /proc
mount -t sysfs s /sys
mount -t tmpfs t /tmp
echo V >/dev/watchdog
echo @@BB
busybox 2>&1
echo @@BIN
ls /bin /sbin /usr/bin /usr/sbin
echo @@MTD
cat /proc/mtd
ls /dev/mtd* /dev/ubi*
echo @@NET
ip link set eth0 up
ip link set eth1 up
ip link set eth2 up
ip link set eth3 up
sleep 8
grep . /sys/class/net/eth0/carrier /sys/class/net/eth1/carrier /sys/class/net/eth2/carrier /sys/class/net/eth3/carrier
ip link
echo @@END
sleep 3
reboot -f
