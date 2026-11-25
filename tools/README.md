# tools

Helpers used while porting. They were written for one bench, so check the defaults before use:
the test network was `192.168.0.0/24`, with the PC at `192.168.0.105` and the Deco at
`192.168.0.250`. Most scripts take `DECO_IP` / `HOST_IP` from the environment.

| tool | what it does |
|---|---|
| `deco_flash.py`, `deco_flash.bat` | **backup + install in one go** over the serial console and LAN (Linux/Windows); see INSTALL.md. Not yet run on a real unit |
| `test_deco_flash_sim.py` | end-to-end test of `deco_flash.py` against a simulated Deco on a pseudo-terminal (Linux) |
| `setup_openwrt.sh` | clone OpenWrt at the tested commit, apply the device patch and package patches, add `packages/` as a feed, load the build config |
| `serial_daemon.py` | keeps the serial console open, logs every byte, stops each boot at the U-Boot prompt (unless `/tmp/deco_nocatch` exists), XMODEM sender. Serial port: `$DECO_TTY`, else the first FTDI adapter in `/dev/serial/by-id/` |
| `con.py` | client for the daemon: run a shell or U-Boot command, wait for its output |
| `script_boot.py` | boot the **stock** kernel from U-Boot with a shell script on its command line (`init=/bin/sh -- -c …`); boot arguments go in over XMODEM + `env import`, so U-Boot's 32-word limit doesn't apply |
| `scripts/0*.sh` | scripts for `script_boot.py`: discovery, and full-flash backups served over the LAN with BusyBox `inetd` |
| `net_receive.py` | PC side of those backups: pulls every partition, checks the SHA-256 sums the Deco computed |
| `uboot_dump.py` | backup through U-Boot alone (`md.l` hex dumps at 1 Mbaud, CRC-checked per chunk). Slow; see the journal for why it was replaced |
| `uart_restore.py` | put the UART back to 115200 after `uboot_dump.py` |
| `ramboot.sh` | development loop: reboot, catch U-Boot, TFTP-boot an initramfs image, wait for SSH |
| `dssh`, `dscp` | ssh/scp to a test unit with the default password (`sshpass`) |
| `soak2.sh`, `soak_report.py` | long stability test of two meshed units (health samples, iperf3 through the mesh, ping), and its summary |
| `roamwatch.sh` | follow which unit and band a phone is associated with during a walk test |

Never write the `boot`, `radio` or `factory_data` partitions, and never `saveenv` from a modified
RAM environment.
