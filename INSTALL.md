# Installing OpenWrt on a TP-Link Deco X55 v3

> **Only for the Deco X55 v3** (MediaTek MT7981B). The X55 v1.x is a Qualcomm device — this will not
> work there. Check the label: hardware version **3.0**.
>
> Status: the firmware runs on two units (flash boot, sysupgrade, mesh, pairing all tested).
> The complete install **starting from stock firmware** with `sysupgrade -n` (step 2) is designed
> for and its parts are tested, but the full sequence from a stock slot has **not yet been run end
> to end** — wait for that confirmation before relying on it. Read every step before you start.

## What you get

* OpenWrt (Linux 6.18) with LuCI over HTTPS, root password **`deco-modded`** — change it on first login.
* Wi-Fi like the stock firmware: 5 GHz **HE160 on channel 44** (DFS → ~60 s radar check after boot),
  2.4 GHz HE40, one network name `Deco-OpenWrt` on both bands, WPA2/WPA3, 802.11k/v.
  **Wi-Fi is off until you set a password in LuCI** (standard OpenWrt behaviour).
* Hardware offload: PPE flow offloading and WED (Wi-Fi ↔ Ethernet).
* **Deco-style mesh with push-button pairing**: batman-adv over 802.11s on 5 GHz, wired backhaul
  over the Ethernet ports, usteer for roaming/band steering. LuCI → Network → **Mesh nodes**.
* WireGuard and OpenVPN (LuCI protocols), SQM QoS, UPnP, DDNS, per-device traffic stats (nlbwmon),
  Wake-on-LAN, `htop`, `iperf3`.
* The stock firmware stays installed in the second slot and can be booted again at any time.

## What you need

* A 3.3 V USB-UART adapter (**GND, TX, RX only — never VCC**) and the case opened; UART is 115200 8N1.
* A PC with a TFTP server and an Ethernet cable into one of the Deco's **LAN** sockets
  (the OEM U-Boot only talks to the switch ports, not to the WAN socket).
* The two images from the [latest release](https://github.com/AndreiVladescu/Deco-X55-OpenWrt/releases/latest)
  (check them against `SHA256SUMS`):
  `…-tplink_deco-x55-v3-initramfs-kernel.bin` and `…-tplink_deco-x55-v3-squashfs-sysupgrade.bin`.

## 0. Back up your flash first

Every unit has its own Wi-Fi calibration and MAC in `factory_data`. This repository's tools make a
full, hash-verified dump in about a minute (see [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) → "Backing up a stock unit"). Keep it.

## 1. Boot OpenWrt from RAM (nothing is written)

Power on and press **Ctrl-C** when U-Boot says `press ctrl-c or t to go to uboot cmdline`. Then:

```
MT7981> setenv ipaddr 192.168.1.1
MT7981> setenv serverip 192.168.1.2        # your PC
MT7981> tftpboot 0x50000000 openwrt-mediatek-filogic-tplink_deco-x55-v3-initramfs-kernel.bin
MT7981> bootm 0x50000000#config-X50_3_0_0
```

Do **not** run `saveenv`. Never press Enter on an empty U-Boot line (it repeats the last command).

## 2. Install

The RAM system is at `192.168.1.1` (user `root`, password `deco-modded`). Copy the sysupgrade image and flash it:

```
scp -O openwrt-…-squashfs-sysupgrade.bin root@192.168.1.1:/tmp/
ssh root@192.168.1.1 'sysupgrade -n /tmp/openwrt-…-squashfs-sysupgrade.bin'
```

This replaces the stock firmware in **slot 0** (`ubi0`) and keeps TP-Link's second-stage U-Boot that
lives there; slot 1 (`ubi1`) keeps the stock firmware untouched. The upgrade sets `tp_boot_idx=0`
so the TP-Link bootloader boots OpenWrt. The unit reboots into OpenWrt from flash (~25 s, plus
~60 s until 5 GHz is up).

## 3. First setup

* Plug your internet into the **WAN** socket, your PC into a LAN socket, open `https://192.168.1.1`.
* Change the root password, set a Wi-Fi password, enable Wi-Fi.

## 4. Adding more Decos (mesh)

1. On the Deco that has internet on WAN: press the reset button **briefly** (< 2 s) — or LuCI →
   Network → Mesh nodes → *Create a mesh here*. The LED blinks blue slowly: pairing window open (2 min).
2. On the new Deco (also flashed): press its reset button briefly. LED blinks fast blue.
3. In LuCI on the first Deco: Network → Mesh nodes → **Approve** the request.
4. The new Deco takes over the mesh and Wi-Fi settings, becomes a satellite (all its sockets are
   LAN ports) and its LED turns solid green.

Reset button: < 2 s pairing · 4–9 s reboot · ≥ 10 s factory reset (LED turns red at 10 s).

LED: solid green = in a mesh / ready · solid blue = not in a mesh · slow blue = pairing window open ·
fast blue = searching / request waiting for approval · red = factory reset will happen on release.

## Going back to the stock firmware

From OpenWrt: `fw_setenv tp_boot_idx 1 && reboot` — boots the untouched stock firmware in slot 1.
From U-Boot (UART): `setenv tp_boot_idx 1; saveenv; reset`.
To restore slot 0 byte-exact, write your backup's `mtd2_ubi0.bin` back (see docs/DEVELOPMENT.md).
