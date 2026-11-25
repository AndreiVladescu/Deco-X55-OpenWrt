# Development notes

How the port was developed and tested — build, RAM-boot test loop, backing up a stock unit.
See also [JOURNAL.md](JOURNAL.md) for the full story.

## Build

```sh
tools/setup_openwrt.sh        # clone OpenWrt at BASE_COMMIT, apply patches, feeds, defconfig
cd openwrt && make -j$(nproc)
```

Outputs (`openwrt/bin/targets/mediatek/filogic/`):
`…-initramfs-kernel.bin` (RAM boot) and `…-squashfs-sysupgrade.bin`.

Optionally, `make TPLINK_DECO_X55_V3_SECONDUBOOT=/path/to/seconduboot.bin` also builds
`…-squashfs-factory.ubi`, a raw UBI image for slot `ubi0` that contains TP-Link's second U-Boot.
That file is the `uboot` UBI volume from a stock slot; extract your own with
`ubireader_extract_images` from a dump of `ubi1`. It is never distributed.

## Test loop (no flash writes)

1. UART 3.3 V, 115200 8N1. `tools/serial_daemon.py /tmp/deco_console.sock` keeps the
   console open, logs everything to `backup/console_live.log` and stops every boot at the U-Boot prompt.
2. TFTP server on the PC serving `./tftp` (TP-Link U-Boot only talks to UDP 69).
3. `tools/ramboot.sh` → reboots, catches U-Boot, `tftpboot` + `bootm`, disables DHCP/RA on LAN
   (the test unit sits on the home LAN), adds `192.168.0.250`, waits for SSH (`tools/dssh`).

## Backing up a stock unit

Stock Linux ignores UART input and has no failsafe, so the backup boots the stock kernel from
U-Boot with a script on the kernel command line (`init=/bin/sh -- -c "…"`) that serves every
`/dev/mtdN` over the LAN with BusyBox `inetd`. See JOURNAL.md, "Backup done".

```sh
python3 tools/serial_daemon.py /tmp/deco_console.sock &   # then power-cycle the Deco
DECO_IP=192.168.0.250 python3 tools/net_receive.py backup/unitN 300 &
python3 tools/script_boot.py tools/scripts/04_backup_inetd_sw.sh 75
```

Flash dumps are **not** in this repository: they contain Wi-Fi passwords, TP-Link cloud keys and
per-device IDs.

## Safety rules

* Never write `boot`, `radio` or `factory_data`; never `saveenv` from a modified RAM environment.
* Every flash change has a tested way back (stock slot `ubi1`, MediaTek web recovery in U-Boot,
  full dumps).
* Never send a bare Enter to U-Boot (repeats the previous command).
