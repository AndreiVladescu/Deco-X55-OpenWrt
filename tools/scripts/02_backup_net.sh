# stage 2: stream every mtd over ethernet (read-only), print sha256, reboot
mount -t proc p /proc
mount -t sysfs s /sys
mount -t tmpfs t /tmp
mount -t devtmpfs d /dev || mount -t tmpfs t /dev
[ -e /dev/mtd0 ] || mknod /dev/mtd0 c 90 0
[ -e /dev/mtd1 ] || mknod /dev/mtd1 c 90 2
[ -e /dev/mtd2 ] || mknod /dev/mtd2 c 90 4
[ -e /dev/mtd3 ] || mknod /dev/mtd3 c 90 6
[ -e /dev/mtd4 ] || mknod /dev/mtd4 c 90 8
[ -e /dev/mtd5 ] || mknod /dev/mtd5 c 90 10
[ -e /dev/mtd6 ] || mknod /dev/mtd6 c 90 12
[ -e /dev/mtd7 ] || mknod /dev/mtd7 c 90 14
echo V >/dev/watchdog
ls -l /dev/mtd0 /dev/mtd7 /dev/watchdog
ip link set eth0 up
ip link set eth3 up
ip addr add 192.168.1.1/24 dev eth3
sleep 8
ping -c 2 192.168.1.2
echo @@XFER
nc -l -p 9000 </dev/mtd0
nc -l -p 9001 </dev/mtd1
nc -l -p 9002 </dev/mtd2
nc -l -p 9003 </dev/mtd3
nc -l -p 9004 </dev/mtd4
nc -l -p 9005 </dev/mtd5
nc -l -p 9006 </dev/mtd6
nc -l -p 9007 </dev/mtd7
echo @@SHA
sha256sum /dev/mtd0 /dev/mtd1 /dev/mtd2 /dev/mtd3 /dev/mtd4 /dev/mtd5 /dev/mtd6 /dev/mtd7
echo @@END
sleep 2
reboot -f
