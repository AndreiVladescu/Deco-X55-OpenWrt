# stage 4 (= stage 3 + undo TP-Link switch port block): serve every mtd read-only via busybox inetd on the home LAN, reboot when told
mount -t proc p /proc
mount -t sysfs s /sys
mount -t tmpfs t /tmp
mount -t tmpfs t /dev
mknod /dev/mtd0 c 90 0
mknod /dev/mtd1 c 90 2
mknod /dev/mtd2 c 90 4
mknod /dev/mtd3 c 90 6
mknod /dev/mtd4 c 90 8
mknod /dev/mtd5 c 90 10
mknod /dev/mtd6 c 90 12
mknod /dev/mtd7 c 90 14
mknod /dev/null c 1 3
echo 9000 stream tcp nowait root /bin/cat cat /dev/mtd0 >>/tmp/i.conf
echo 9001 stream tcp nowait root /bin/cat cat /dev/mtd1 >>/tmp/i.conf
echo 9002 stream tcp nowait root /bin/cat cat /dev/mtd2 >>/tmp/i.conf
echo 9003 stream tcp nowait root /bin/cat cat /dev/mtd3 >>/tmp/i.conf
echo 9004 stream tcp nowait root /bin/cat cat /dev/mtd4 >>/tmp/i.conf
echo 9005 stream tcp nowait root /bin/cat cat /dev/mtd5 >>/tmp/i.conf
echo 9006 stream tcp nowait root /bin/cat cat /dev/mtd6 >>/tmp/i.conf
echo 9007 stream tcp nowait root /bin/cat cat /dev/mtd7 >>/tmp/i.conf
echo 9099 stream tcp nowait root /sbin/reboot reboot -f >>/tmp/i.conf
cat /tmp/i.conf
ip link set lo up
ip link set eth0 up
ip link set eth3 up
switch reg r 2104
switch reg r 2204
switch reg w 2104 442000
switch reg w 2204 422000
switch reg r 2104
switch reg r 2204
sleep 6
arping -D -c 2 -I eth3 192.168.0.250 && ip addr add 192.168.0.250/24 dev eth3
ip addr show eth3
ping -c 2 192.168.0.105
inetd /tmp/i.conf
netstat -ltn
echo @@READY
sleep 300
echo @@TIMEOUT
reboot -f
