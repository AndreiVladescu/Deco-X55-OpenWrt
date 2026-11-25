# stage 5: any port (br0 = eth1+eth2+eth3), sha256 on-device first, serve each mtd via inetd, reboot on :9099
# kept compact: kernel command line is limited to 2048 bytes
mount -t proc p /proc; mount -t sysfs s /sys; mount -t tmpfs t /tmp; mount -t tmpfs t /dev
for i in 0 1 2 3 4 5 6 7; do mknod /dev/mtd$i c 90 $((i*2)); echo 900$i stream tcp nowait root /bin/cat cat /dev/mtd$i >> /tmp/i.conf; done; mknod /dev/null c 1 3
echo 9099 stream tcp nowait root /sbin/reboot reboot -f >> /tmp/i.conf
ip link set lo up; for i in eth0 eth1 eth2 eth3; do ip link set $i up; done
switch reg w 2104 442000; switch reg w 2204 422000
brctl addbr br0; for i in eth1 eth2 eth3; do brctl addif br0 $i; done; ip link set br0 up
grep -H . /sys/class/net/eth[123]/carrier
sleep 8
arping -D -c 2 -I br0 192.168.0.250 && ip addr add 192.168.0.250/24 dev br0
ip addr show br0
echo @@SHA; sha256sum /dev/mtd[0-7]; echo @@SHAEND
inetd /tmp/i.conf
echo @@READY
sleep 300
reboot -f
