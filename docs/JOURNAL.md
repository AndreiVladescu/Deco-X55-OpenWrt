# Porting OpenWrt to the TP-Link Deco X55 v3 — working journal

A running log of the port: what was tried, what failed, why, and what finally worked.
Written as raw material for an article. Times are local (EET). Secrets (Wi-Fi PSKs,
cloud keys, per-unit IDs) are deliberately left out.

---

## Day 1 — 2026-11-23

### 19:30 — Starting point

Two TP-Link Deco X55 v3 (AX3000) mesh units. One is opened up with a 3.3 V FTDI FT232R
on the UART header (115200 8N1). A passive capture of a normal boot (`deco_boot_uart.txt`)
tells us most of the hardware story before touching anything:

* **SoC:** MediaTek MT7981B (Filogic 820), 2× Cortex-A53 @ 1.3 GHz
* **RAM:** 512 MiB DDR3
* **Flash:** 128 MiB GigaDevice SPI-NAND (2 KiB pages, 128 KiB blocks, on-die ECC), 0 bad blocks
* **Ethernet:** GMAC0 → external MediaTek **MT7531** switch over 2500base-x (two LAN ports);
  GMAC1 → MT7981's internal gigabit PHY (WAN). Three ports total.
* **Wi-Fi:** MT7981's built-in 2×2 2.4 GHz + 2×2 5 GHz (HE160). Stock uses MediaTek's
  proprietary `mt_wifi` driver; OpenWrt would use the open-source `mt76`.
* **Stock software:** MediaTek SDK OpenWrt fork, Linux 5.4.124, BusyBox 1.22.1. `/etc/openwrt_release`
  still says "Attitude Adjustment 12.09" (!).

The **boot chain** is the interesting part:

```
BootROM → BL2 (TF-A 2.7) → BL31 → TP-Link U-Boot 2022.07 (in "boot" mtd)
        → "seconduboot" (a 2nd U-Boot stored as a UBI volume inside the active firmware slot)
        → FIT kernel (UBI volume "kernel") → squashfs root (UBI volume "rootfs", via ubiblock)
```

TP-Link keeps **two firmware slots** (`root0`/`root1`, 42 MiB UBI each) and picks one with
`tp_boot_idx` in the U-Boot environment. This unit runs slot 1. Slot 0 holds an older firmware.

The stock FIT image carries device trees for **X50 v3, X55 Pro 1.0 and X50-PoE 2.0** — TP-Link
builds one firmware for a family of MT7981 Decos and selects the DTB with `config_name`.

Research: no OpenWrt port exists for the X55 v3. Forum work on "Deco X55" is the v1.6
(Qualcomm IPQ5018) — completely different hardware — and the X55 Pro (MT7981 with 2.5G PHYs).

### 19:35 — Plan

1. Back up the entire flash of both units, verified, before anything else.
2. Reverse-engineer the stock firmware offline from the dump.
3. Write an OpenWrt device tree; boot OpenWrt **from RAM only** for bring-up.
4. Install into the *inactive* stock slot, keeping TP-Link's bootloader and the other slot as
   a fallback.
5. Mesh (batman-adv over 802.11s + wired backhaul), roaming, feature parity.

### 19:35–19:40 — Backup attempt #1: through U-Boot over serial

U-Boot is reachable (Ctrl-C at "press ctrl-c or t"), and it's a fairly complete build:
`mtd`, `nand`, `ubi`, `tftpboot`, `loady`, `httpd`, `md`, `crc32`… but **no `tftpput`**, so
it can download but never upload. The only way out is text: `md.l` hex dumps on the console.

Problem: at 115200 baud, hex output (≈4.2 chars per byte) would take **~13.5 hours** for 128 MiB.
U-Boot refuses baud rates above 115200 (`## Baudrate 921600 bps not supported`).

Trick: the MT7981 UART runs from a 40 MHz clock and has MediaTek's high-speed sample-count
registers. Writing them directly with `mw.l` (divisor 1, sample count 40) gives **exactly
1,000,000 baud** — which the FT232R also hits exactly (3 MHz / 3). No flash writes, and a reset
restores 115200. Then: `mtd read` into RAM once, stream back 64 KiB at a time with `md.l`,
verify each chunk against U-Boot's own `crc32`. ~23 KiB/s → ~95 min for the full chip.

`tools/uboot_dump.py` — the bootloader (2 MiB) and U-Boot env (1 MiB) came out clean.

### 19:39 — First casualty: the PC's USB controller

Mid-dump the **PC's AMD xHCI controller died** (`xHCI host controller not responding, assume
dead`), taking down everything on it — including the serial adapter *and my wireless headset
dongle*. This happened again later, also during a sustained 1 Mbaud transfer. Lesson: keep the
serial adapter on a different USB controller from anything you care about, and don't push long
transfers over serial. (Controller reset without logging out:
`echo 0000:0c:00.3 > /sys/bus/pci/drivers/xhci_hcd/unbind` then `…/bind`.)

Also learned the hard way: **a bare Enter in U-Boot repeats the last command.** My "is it
alive?" probe kept re-running `md`, which continues where it left off. Probes now send
`echo ping_ok` instead.

### 19:45 — Backup attempt #2: stock Linux failsafe → Ethernet

Idea: boot stock Linux into OpenWrt failsafe (press `f` during preinit), get a root shell,
stream `/dev/mtd*` over Ethernet in seconds.

* Caught `Press the [f] key…` and sent `f` + Enter → ignored. TP-Link set the wait to zero.
* Booted the stock kernel by hand from U-Boot (`ubi read` + `bootm`) with `FAILSAFE=true` on
  the command line → ignored too. **TP-Link removed failsafe.**
* Serial SysRq (BREAK + `b`) to reboot → not enabled. Each retry needed a manual power-cycle.

### 20:00 — Root shell… that can't hear me

Booted the stock kernel with `init=/bin/sh`: got `/ #` — and then nothing. Not even the echo of
typed characters. The kernel's tty layer echoes input itself, so no echo means the kernel never
receives a byte. Confirmed from the other side: **TP-Link's kernel only transmits on the UART.**
RX works in U-Boot, not in Linux. The stock device tree configures the UART normally, so it's
done in their kernel. (Their `(none) login:` prompt is therefore decorative.)

Also nailed down a small mystery from earlier: a `ubi part` command seemed to trigger a full
boot — it was actually a power-cycle coinciding with it. Logging *every* byte from now on
(`tools/serial_daemon.py`: keeps the port open, logs everything, and automatically stops every
boot at the U-Boot prompt — after it once missed a double power-cycle, it re-arms on every new
BootROM banner).

### 20:15 — Workaround: scripted boots

If Linux can't read the console, give it the commands up front. The kernel passes everything
after `--` on its command line to init as arguments:

```
init=/bin/sh -- -c "mount -t proc p /proc && … && reboot -f"
```

The script ends with `reboot -f`, the daemon catches U-Boot again, and the next iteration can
start — no more manual power-cycles.

Two U-Boot obstacles along the way:

1. U-Boot's simple command parser splits on `;` and expands `$`, so the script avoids both and
   chains commands as `cmd||: && next||: && …` (everything runs even if one command fails).
2. The parser also caps a command at **32 words** (`CONFIG_SYS_MAXARGS`), and the expanded
   `${bootargs}` counts. Fix: write `bootargs=…` into a text file, send it into RAM with
   XMODEM (`loadx`, CRC-checked), then `env import -t`. RAM only, never `saveenv`. (And
   `printenv` silently truncates at ~1 KiB, so verification is `env export` + `crc32`.)

Discovery boot results: BusyBox has `nc` (client only), `inetd`, `tftp`, `dd`, `sha256sum`;
`/dev` is the read-only squashfs (no devtmpfs) so device nodes need `mknod` on a tmpfs.

### 20:30 — The switch that wouldn't switch

Gave the Deco a free address on the home LAN, served each `/dev/mtdN` through BusyBox `inetd`
(`9000 stream tcp nowait root /bin/cat cat /dev/mtd0` …) and pulled from the PC. Link up at
1 Gbps — but **not even ARP got through**.

The answer was in the normal boot log: TP-Link's kernel modifies the MT7531 port control
registers during switch setup (`mt7531_setup [change]reg:2104 mcr_cur:2470`), and their
userspace later "recovers" them (`switch_mt7530_recovery … set reg:2204 value:0x422000`).
With `init=/bin/sh` that recovery never runs, so the LAN ports stay blocked. Writing the same
two values with the stock `switch reg w` tool fixed it instantly.

### 20:40 — Backup done

All 8 MTD partitions over the LAN in **~25 seconds** (10.6 MB/s — BusyBox `cat` through inetd),
plus the last 512 KiB (outside any partition) via U-Boot. Verification:

* every file matches the SHA-256 the Deco computed on-device;
* every partition is byte-identical to its range inside `wholeflash`;
* the bootloader + env match the earlier independent UART dumps;
* unpartitioned areas are all `0xff`; the would-be bad-block-table area is empty (0 bad blocks).

`backup/unit1/nand_full_128MiB.bin` + per-partition files + `SHA256SUMS`. Not a single byte was
written to the Deco's flash in the process.

Decision: unit #2 stays untouched until OpenWrt is proven on unit #1.

### 20:50 — First look inside the dump

* Slot 0 and slot 1 hold **different** stock firmware versions (two stock fallbacks in the backup).
* `radio` partition (1 MiB) is **completely empty**. The Wi-Fi calibration actually lives as a
  *file* — `MT7981_EEPROM.bin` (20 KiB, magic `0x7981`, plus an identical `_BAK` copy) — inside
  the `factory_data` UBIFS volume, next to `default-mac` (base MAC) and `product-info`.
  → The OpenWrt port can't use the usual "EEPROM at raw flash offset" nvmem cell; it has to
  read the calibration from a UBIFS file (or copy it into a UBI volume once).
* Stock device tree: reset button on GPIO 1 (polled), MT7531 reset on GPIO 39, seven GPIO LEDs
  (4, 5, 9, 11, 12, 27, 28), PHY calibration via nvmem.

### 21:00 — Reading the stock firmware offline

* Both slots carry a different build of the second U-Boot too (slot 1: Aug 2026, the one that boots).
* Both U-Boots key off `tp_boot_idx`; the second one refuses to boot without a matching
  `config_name` (`error: multi model has not config_name!`). An OpenWrt FIT that keeps the
  configuration name `config-X50_3_0_0` should slot straight into TP-Link's chain.
* The first U-Boot also contains MediaTek's **web recovery page** and `mtkupgrade` — a second
  safety net besides the stock fallback slot.
* MAC scheme, worked out from the *other* Deco's backhaul BSSIDs in the mesh logs:
  LAN = base, WAN = base+1, 2.4 GHz = base+2, 5 GHz = base+3 (base = `default-mac`).
* The stock DTB lists every LED GPIO variant for every model in the family
  (`red`, `red_new`, `red_neww`, …); the stock `leds` daemon simply drives all of them.
  Which ones are really wired on the X55 v3 has to be found on hardware.
* Stock ships regional default EEPROMs `MT7981_ePAeLNA_EEPROM_X50_*.bin` → the board has
  external PAs/LNAs (the per-unit calibrated EEPROM in `factory_data` is what we use).

### 21:15 — OpenWrt tree, and a lucky template

Cloned OpenWrt `main` (Linux 6.18, mt76 current). Searching the tree for TP-Link's
"calibration inside a UBIFS file" pattern turned up the **Mercusys Halo H90X** (a TP-Link-built
mesh unit) — and it uses the **exact same flash map** as the Deco (`ubi1` @ 0x2d00000/42 MiB,
`factory_data` @ 0x5800000 with UBIFS volume `ubi_factory_data`), the same 2×LAN + WAN-on-
internal-PHY layout, and the same `default-mac` format. The **MR80X v3** (MT7981B) provides the
device-tree template, and its install strategy is exactly ours: OpenWrt in `ubi0`, stock left
in `ubi1` read-only.

What the Deco needs on top:

| piece | how |
|---|---|
| `uboot` UBI volume | `UBINIZE_PARTS := uboot=:<seconduboot.bin>` → static volume id 0, ahead of `kernel`/`rootfs` (same order as stock) |
| FIT config name | `DEVICE_DTS_CONFIG := config-X50_3_0_0` |
| caldata | hotplug links `mt7981_eeprom_mt7976_dbdc.bin` → `/tmp/factory_data/MT7981_EEPROM.bin` |
| Wi-Fi MACs | new case in `11_fix_wifi_mac`: phy0 = base+2, phy1 = base+3 |
| everything else | add `tplink,deco-x55-v3` to the H90X cases (mount factory_data, LAN/WAN MACs, 2-LAN network) and to the `CI_UBIPART=ubi0` sysupgrade list; env at `u-boot-env` 0x0/0x20000 (CRC verified on the dump) |

Files: `target/linux/mediatek/dts/mt7981b-tplink-deco-x55-v3.dts`, `image/filogic.mk`, and six
one-line additions in `filogic/base-files`. The OEM second U-Boot stays out of the OpenWrt tree
(`oem/`), taken from the unit's own dump.

Toolchain build started in the background (16 threads).

### 21:30 — First OpenWrt boot

Build: 7 minutes (16 threads, toolchain already built). Images: `initramfs-kernel.bin` (9.6 MiB),
`squashfs-factory.ubi` (UBI with `uboot` static vol 0, `kernel`, `rootfs`, `rootfs_data`),
`squashfs-sysupgrade.bin`. FIT has exactly one configuration, `config-X50_3_0_0`.

For RAM boots, a TFTP server on the PC (dnsmasq, UDP 69, one ufw rule for the Deco's address —
TP-Link's U-Boot can't use another TFTP port). U-Boot pulls 9.6 MiB at ~2 MiB/s (a 1 MiB probe
did 18 MiB/s), then `bootm 0x50000000#config-X50_3_0_0`. Nothing touches flash.

**It booted first time** — Linux 6.18.64, `Machine model: TP-Link Deco X55 v3`:

* SPI-NAND + partitions OK (mainline identifies the chip as ESMT, ID 0x8c; TP-Link's driver said GigaDevice).
* MT7531 DSA switch up (`lan1`, `lan2`, 2.5G CPU link); `lan2` linked at 1 Gbps to the home switch.
* `factory_data` attached **read-only**, UBIFS mounted R/O ("recovery deferred" — nothing written).
* **UART RX works under OpenWrt** — a real interactive console at last.
* MACs exactly as stock: LAN `…53:0c` (base), WAN `…53:0d`, 2.4 GHz `…53:0e`, 5 GHz `…53:0f`.
* Wi-Fi: mt76 asks for `mt7981_eeprom_mt7976_dbdc.bin`, the hotplug script hands it the unit's
  own `MT7981_EEPROM.bin`; the EEPROM in the driver is byte-identical to the factory file.
  Scans see 19 networks on 2.4 GHz and 26 on 5 GHz.
* All 7 LED GPIOs and the reset button registered (button idle = high, active-low ✔).

Gotchas: the Deco's LAN port sits on the home network, so the default OpenWrt DHCP server and
IPv6 RA got switched off immediately (OpenWrt's dnsmasq does check for a foreign DHCP server first,
but no need to gamble). Long commands pasted over the serial console lose characters, so after
the first boot everything goes over SSH on a temporary LAN address (`tools/dssh`).

### 22:30 — LEDs, button, WAN, Wi-Fi and offload on real traffic

* **LED:** one RGB LED. Toggling the seven candidate GPIOs one at a time (with someone looking at
  the LED) gave: red = GPIO 9, green = GPIO 12, blue = GPIO 28, all active-high. The four
  active-low "variants" (4, 5, 11, 27) from the shared OEM device tree do nothing on the X55 v3
  and were removed from the DTS. Lesson: fast blink patterns are useless for this — one pin at a
  time, ask, next.
* **Reset button:** GPIO 1, active-low, clean press/release events through OpenWrt's
  `gpio-button-hotplug` (with the real reset action temporarily replaced by a logger).
* **Ports:** physical socket mapping found by moving the cable: two switch ports (`lan1`, `lan2`)
  and the WAN socket on the internal PHY (`eth1`, 1 Gbps, DHCP from the home router).
* **Wi-Fi AP:** WPA2/WPA3 mixed, both bands. 5 GHz at **HE160** on channel 36 (60 s DFS CAC for
  52–64). 2.4 GHz configured HE40 but correctly falls back to 20 MHz because of 19 neighbouring
  networks (20/40 coexistence) — not forced.
* **Hardware offload:** software + hardware flow offloading enabled (PPE) and WED v2 for Wi-Fi.
  Speed test from a phone on 5 GHz: ~350 Mbps, **14–24 flows bound in the PPE, CPU 86–100 % idle**.
* **Why "only" 350 Mbps:** the phone (Redmi Note 15 Pro) associated *without* HE and with a single
  spatial stream (`he: false`, VHT 1ss, 80 MHz), on both bands — the AP advertises HE160, 1024-QAM,
  SU/MU beamforming. 1ss VHT80 tops out at 390/433 Mbit/s PHY, ~330 Mbps TCP — which is what we got.
* Signal at 1 m was only −56 dBm (chains −64/−65): the opened unit probably has its antennas off or
  badly placed.
* First image used `wpad-basic` — no 802.11s mesh. Next build: full `wpad-mbedtls`.
* Gotcha: after moving the cable from LAN to WAN, SSH replies vanished — the temporary
  192.168.0.250 alias on `br-lan` and the new WAN lease were in the same /24, so replies went out
  the (now empty) LAN bridge. Removing the alias fixed it.
* Gotcha: reloading `mt7915e` by hand (to enable WED) renumbers the radios `phy2`/`phy3`, so the
  MAC hotplug script (keyed on phy 0/1) falls back to MediaTek placeholder MACs until reboot.

### 22:50 — How does TP-Link's own mesh work? (read from the stock firmware)

Before choosing the OpenWrt mesh design, the stock firmware was checked for what the Deco
actually does. Its mesh is TP-Link's **AWN** stack on top of MediaTek's closed `mt_wifi` driver:

| function | stock Deco X55 v3 | evidence |
|---|---|---|
| mesh controller | `awnd` ("AWN"), `awnweight`, `hybrid-center` | `/usr/sbin`, boot log `[AWN:…]` |
| wireless backhaul | satellites run **station interfaces** (`apcli0` 2.4 GHz, `apclix0` 5 GHz) that join a hidden backhaul SSID on the main unit, in MediaTek multi-WDS 4-address mode — **both bands at once** | `ApCliMWDS=1`, log `LINK_WIFI_2G:1, LINK_WIFI_5G:1` |
| wired / PLC backhaul | automatic Ethernet backhaul, PLC support in code | `eth_link_state`, `plc backhaul` messages |
| uplink choice | TP-Link vendor IE in beacons carrying level, uplink rate, path rate → best parent | `update_wifi_tpie_mtk … uplinkRate` |
| traffic separation | VLANs over the backhaul: LAN = 1, guest = 2, WAN/IPTV = 3 | `repacd … vlan id:1/2/3`, `TrafficSeparationEnabled: 1` |
| interface/VLAN wiring | `repacd` + `whc-*` scripts (derived from Qualcomm's SON "whc" tooling) | `/etc/init.d/repacd`, `lib/functions/whc-*.sh` |
| config sync between units | TIPC messaging + `sync-server`, group membership in `group-info` (role FAP/RE + key) | `tipc-server`, `runtime_data/group-info` |
| roaming | **802.11k on, 802.11v on, 802.11r off** (`FtSupport=0`, log `ftenable=0`); steering by `nrd`; driver band steering off | mt_wifi `.dat` profiles |
| radio features | MU-MIMO DL+UL on, OFDMA DL+UL on, BSS colour auto, TWT off, WED (`WHNAT=1`) on; stock 5 GHz ran **HE160 on channel 44** (DFS 52–64 included) | `.dat`, boot log `11axa:HT160 channel=44` |
| onboarding | Deco app + cloud; pre-shared group keys in `factory_data/product-info` (`preconf_*`) | |

OpenWrt mapping chosen for the port:

* backhaul: **802.11s (SAE-encrypted) on 5 GHz + batman-adv** on top, with the Ethernet ports also
  in batman → same "wired or wireless, whatever is best, fail over automatically" behaviour as AWN,
  multi-hop capable. batman's TQ metric plays the role of TP-Link's uplink-rate IE.
* traffic separation: VLANs over `bat0` (main / guest), like stock's VLAN 1/2.
* roaming: hostapd 802.11k/v (+ optional 802.11r) via full `wpad-mbedtls`, `usteer` for band
  steering and AP-to-AP client hand-off (usteer nodes talk to each other over the LAN).
* no equivalent: app onboarding, cloud, automatic config sync (to be replaced by identical
  configs generated from one template), TP-Link's own uplink IE.

### 22:55 — The phone that wouldn't do Wi-Fi 6

The test phone (Redmi Note 15 Pro) shows "Wi-Fi 5" on the OpenWrt AP and associates without HE and
with one spatial stream, on both bands. hostapd has `ieee80211ax=1`, HE160, beamformers on. Trying
HE80 on channel 36 (no DFS) next to rule out an HE160/DFS-specific client quirk.

### 23:05 — Decisions

* HE80 on channel 36 (no DFS) changed nothing for the test phone — still "Wi-Fi 5", 1 spatial
  stream. Parked: retest with a newer Wi-Fi 6/6E client.
* **5 GHz stays HE160 on channel 44, like the stock firmware** (block 36–64, DFS 52–64, 60 s CAC),
  accepting the radar risk for the mesh link.
* `TODO.md` started: next steps, mesh plan, feature parity, tests, known issues, ideas.

### 23:20 — Image v2

* Full `wpad-mbedtls` (802.11s, 802.11k/v/r) instead of `wpad-basic`, `batman-adv` + `batctl-full`,
  `usteer`, LuCI pages, `luci-ssl`, `iw-full`. Build: 1 minute.
* Personal test defaults live in an overlay (`openwrt-files/`, copied to `openwrt/files/`), not in the
  device patch: Wi-Fi like stock (HE160 ch 44 / HE40), the test SSIDs, WPA2/WPA3 + PMF,
  802.11k/v on, 802.11r off (as stock), flow offloading on, WED via `/etc/modules.conf`
  (`options mt7915e wed_enable=Y`), and TEST-only firewall rules for management from WAN.
* Device patch: Wi-Fi MACs now chosen by **band** (`iw phy … | grep 5180.0 MHz`) instead of phy number.
* Gotcha: TP-Link's U-Boot only drives GMAC0 → the switch, so **TFTP RAM boots need the cable in a
  LAN socket**; with the cable in WAN it just says `ARP Retry count exceeded`. Also the PC firewall
  only allows TFTP from 192.168.0.250, so U-Boot always uses that address now.
* Verified on v2: both APs up (HE160 ch 44 after 40 s CAC), stock MACs on a normal boot, WED + PPE
  offload active, hostapd with HE + MU beamformer + 802.11k neighbour reports + 802.11v BSS
  transition + PMF, usteer running, batman-adv loaded, radio supports mesh point mode.
* Paused here for phone tests: stock Deco vs OpenWrt Deco, iPhone 15 and Redmi Note 15 Pro.

### Phone tests (round 1) — Redmi Note 15 Pro on the OpenWrt Deco

| band | phone shows | fast.com | Ookla | Deco side |
|---|---|---|---|---|
| 5 GHz (AP at HE160 ch 44) | Wi-Fi 5 | 320 Mbps | 290 Mbps | VHT-MCS 9, 80 MHz, **1 SS**, −44 dBm @ 50 cm, expected 330 Mbps |
| 2.4 GHz (HE40 → 20 MHz) | Wi-Fi 5 | 25 Mbps | 30 Mbps | channel 6 **41 % busy** (neighbours), 1 SS HT ≈ 65 Mbit/s PHY |

Antennas not in their factory position (unit opened). Throughput sits at the client's
link-limited ceiling on both bands. Pending: same phone on the stock Deco, iPhone 15 on both.

### 23:45 — First flash install: OpenWrt boots from NAND through TP-Link's chain

Pre-flight on the RAM-booted v2:
* `fw_printenv` reads TP-Link's env cleanly (`/dev/mtd1 0x0 0x20000 0x20000`), `tp_boot_idx=1`.
* target `mtd2` ("ubi0", stock slot 0, older firmware) not attached; live SHA-256 still identical to
  the verified backup `mtd2_ubi0.bin` → byte-exact restore possible.
* kernel built without `MTD_CMDLINE_PARTS` → TP-Link's `mtdparts=` on the command line is ignored;
  bootloader `bootargs` *are* used (`ubi.mtd=ubi0 … init=/etc/preinit`), and the kernel has
  `ubiblock_create_auto_rootfs` → root found from the `rootfs` volume.

Install (from RAM-booted OpenWrt, no TP-Link tool involved):
```
cat factory.ubi | ssh root@deco 'cat > /tmp/factory.ubi'   # sha256 verified both sides
ubiformat /dev/mtd2 -f /tmp/factory.ubi -y                  # keeps erase counters
fw_setenv tp_boot_idx 0                                     # only env change, CRC valid
```
Result: `uboot` volume byte-identical to TP-Link's seconduboot; volumes `uboot`(static) /
`kernel` / `rootfs` / `rootfs_data` (26 MiB).

Reboot, completely hands-off (serial daemon in watch-only mode):
TP-Link U-Boot → attaches `ubi0` → reads `uboot`, `rootfs`, `kernel` → seconduboot →
`Using 'config-X50_3_0_0'` → crc32+sha1 OK → Linux 6.18 → `ubiblock0_2 (rootfs) set to be root
filesystem` → overlay on `ubi0_3` (UBIFS) → console in ~24 s. A second unattended reboot confirmed
persistence. Stock firmware remains in `ubi1`, untouched and read-only.

The unit now lives on the home LAN as an access point: LAN static 192.168.0.250 (gw/DNS
192.168.0.1), LAN DHCP + RA off, test SSIDs, HE160 ch 44.

Way back to stock, any of: `fw_setenv tp_boot_idx 1` (OpenWrt) / `setenv tp_boot_idx 1; saveenv`
(U-Boot) → boots the untouched stock slot; or write `backup/unit1/mtd2_ubi0.bin` back to 0x300000
from U-Boot (TFTP + `mtd write`); or MediaTek web recovery.

## Day 2 — 2026-11-24

### 16:30 — "It didn't start OpenWrt" — it never got the chance

After a power cycle the unit was unreachable. The serial log ended at 22:47 the night before:
the FT232R had **dropped off USB at 23:00 exactly when the Deco was powered off**, and came back
(as `ttyUSB1`) at 16:29:49 when it was powered on — the daemon was still holding the dead `ttyUSB0`.
The console showed the unit sitting at `MT7981>`, `tp_boot_idx=0` intact: the adapter's
re-enumeration during power-on glitched its TX line and U-Boot took it as the "press ctrl-c or t"
key. `reset` → OpenWrt booted from flash in 18 s, as it should.

Takeaways: with the adapter attached, a cold start can be interrupted; the adapter seems to be
affected by the Deco's power (check that only GND/TX/RX are wired, not VCC); the serial daemon must
reopen the port when the adapter re-enumerates.

### Phone tests (round 2) — iPhone 15, stock Deco vs OpenWrt Deco

| client | stock Deco (5 GHz) | OpenWrt Deco (5 GHz, HE160) | OpenWrt Deco (2.4 GHz) |
|---|---|---|---|
| iPhone 15 | 520–530 Mbps | 520–530 Mbps | ~100 Mbps |
| Redmi Note 15 Pro | – | 290–320 Mbps (Wi-Fi 5, 1 SS) | 25–30 Mbps |

**Parity with the stock firmware on 5 GHz** (stock unit has a single AP, so its result is 5 GHz).

### 17:00 — Unit #2 backed up

Unit #2 (the stock mesh's main unit, base MAC `…71:CC`, `tp_boot_idx=1`) got the same treatment —
with two lessons:

* The first attempt **panicked the stock kernel**: `Attempted to kill init! exitcode=0x200`. The
  script had grown to 1988 characters; with the base arguments that's past arm64's 2048-byte
  `COMMAND_LINE_SIZE`, the tail was cut, `/bin/sh -c` hit an unterminated quote and exited.
  Rewritten with loops (now that bootargs arrive via XMODEM + `env import`, `;` and `$` are fine):
  873 characters. `script_boot.py` now refuses anything over 2000 bytes.
* The script now bridges all three ports (`br0` = eth1 + eth2 + eth3), so the cable can be in any
  socket, and prints on-device SHA-256 of every mtd before serving.

Result: 8 partitions in ~25 s, all matching the on-device hashes, plus the 512 KiB tail via U-Boot.
**Both units carry byte-identical bootloader and firmware slots** (`boot`, `ubi0`, `ubi1`, empty
`radio`) — factory images, never upgraded. Per unit: `u-boot-env`, `factory_data` (own MAC, own
Wi-Fi calibration — the EEPROM files differ), `runtime_data`.

### 17:20 — Unit #2 flashed

Same procedure as unit #1: TFTP RAM boot (v2), pre-checks (env readable, `tp_boot_idx=1`, `mtd2`
unattached and identical to the backup), `ubiformat /dev/mtd2 -f factory.ubi`, `fw_setenv tp_boot_idx 0`
(only env change), hands-off reboot → TP-Link chain → OpenWrt from flash in 24 s. Unit #2 comes up
with its *own* identity from its own `factory_data`: LAN `…71:cc`, 2.4 GHz `…71:ce`, 5 GHz `…71:cf`
— exactly the BSSIDs its stock firmware used. Home-LAN AP config: LAN 192.168.0.251/24 (unit #1 is
.250), DHCP/RA off.

Gotcha: OpenWrt 25's default LAN has no `netmask` option (the address is CIDR-less), so setting only
`network.lan.ipaddr=192.168.0.251` gave a **/32** — set `netmask` too.

Decision: the user wants **push-button pairing with approval** (Deco-app-like) instead of a
pre-shared mesh key.

### 17:20 — The mesh is up: batman-adv over 802.11s at HE160

Unit #1 had no cable and the serial adapter sat on unit #2, so the way in was **through unit #2's
radio**: a temporary client interface `sta0` on unit #2's 5 GHz phy (needs its own MAC —
`SIOCSIFFLAGS` otherwise), `wpa_supplicant` into unit #1's test AP, a /32 host route, and
`tools/dssh1` (SSH with unit #2 as jump host). Side result: Deco-to-Deco client link came up at
**1921.5 Mbit/s, HE160, HE-MCS 9, 2 SS** — the Wi-Fi 5 phone was the limit, not the AP.
(Unit #1's IPv6 link-local didn't answer — parked.)

Mesh config (`tools/mesh_setup.sh`, run on each node):
* `wireless.mesh0`: 802.11s on radio1, `mesh_id deco-x55-mesh`, **SAE**, `mesh_fwding 0`
  (batman does the forwarding), same channel as the APs (44, HE160).
* `network.mesh` = `batadv_hardif` (mtu 1536) → `network.bat0` = `batadv` (BATMAN_IV, BLA, DAT);
  `bat0` added to `br-lan`.
* Unit #1 first, with an **automatic rollback** (restore network+wireless after 300 s unless
  `/tmp/mesh_ok` appears) — losing the only path to a cable-less, console-less node was the risk.
  Then unit #2.

Result after the 60 s DFS CAC:
* 802.11s `plink ESTAB`, batman neighbours on both sides, unit #1 reachable from the LAN through
  the mesh, internet on unit #1 via unit #2.
* **Backhaul PHY rate 2401.9 Mbit/s both ways — HE160, HE-MCS 11 (1024-QAM), 2 SS** — exactly the
  "2402 Mbps" on the box.
* iperf3 between the units themselves (CPU-generated traffic, 4 streams): **665 Mbit/s** unit #1 →
  unit #2, **590 Mbit/s** unit #2 → unit #1.

Gotcha: the PC's ARP cache still held unit #2's MAC for 192.168.0.250 from its RAM boot → first
pings failed; confirmation was done through unit #2 in time, the cache refreshed seconds later.

### 18:00 — usteer across nodes; writing push-button pairing (`deco-mesh`)

* **usteer** needed nothing: with `bat0` bridging both LANs, each usteerd sees the other node's APs
  and publishes them as 802.11k neighbour reports (`rrm_nr`). Band steering between 2.4/5 GHz needs
  the same SSID on both bands (the test setup uses two names).

**Pairing design** (package `packages/deco-mesh`, Deco/EasyMesh-style onboarding):

1. Short press on a meshed node → **registrar**: temporary WPA2 AP `DecoPair-xxxxxx` on 2.4 GHz
   (no DFS wait) with **WPS push-button** armed, its own /24 (`10.234.0.1`), DHCP, a firewall zone
   that only allows DHCP + TCP 8099, and a second uhttpd instance serving one CGI on that link.
2. Short press on a new node → **joiner**: takes its 2.4 GHz radio down, adds a client interface to
   the already-running `wpa_supplicant` over ubus (`config_add`), runs `wps_start` — no `wpa_cli`
   needed — gets an address, sends a join request (MAC, model, random nonce).
3. LuCI on the registrar (**Network → Mesh nodes**) shows "1 Deco wants to join" with
   **Approve / Reject**; the LED blinks fast blue while a request waits.
4. On approval the joiner (polling with its nonce) receives a whitelisted settings list (mesh ID,
   SAE key, channels/width/country, AP SSIDs/keys/802.11k/v/r flags) as `name base64(value)` lines
   — nothing is executed — applies batman-adv + 802.11s, turns itself into a bridge-only satellite
   (LAN by DHCP, no DHCP/RA of its own), LED solid green.
5. Reset button: < 2 s pairing, 4–9 s reboot, ≥ 10 s factory reset (LED goes red at 10 s).

Registrar side tested on unit #2: pairing AP up, join service only on 10.234.0.1:8099, request →
pending in rpc/LuCI → wrong nonce refused → approve → settings delivered → window closes cleanly
(no leftover wireless/firewall/uhttpd/dhcp config). Gotcha: **hostapd silently disables WPS on a
hidden SSID** (`ignore_broadcast_ssid configuration forced WPS to be disabled`) → the pairing AP is
visible, only during the window.

### 18:50 — Pairing works end to end

Swapped roles for the test: the one cable moved to unit #1 (registrar, reachable by wire), unit #2
(with the serial console as a safety line) left the mesh and became the joiner. Four bugs on the
way, each found from the logs on both sides:

1. joiner stuck after WPS: **`dnsmasq reload` doesn't add new DHCP ranges** → restart instead…
2. …and only after the `dmpair` interface is up — dnsmasq skips ranges for interfaces that are down
   when it starts — and the section needs an explicit **`dhcpv4='server'`** in this OpenWrt release.
3. a sanity `ping` of the registrar was rejected by the pairing zone's own firewall (only DHCP and
   the join port are allowed) → removed, the request itself proves the link.
4. "settings without mesh key": **this BusyBox has no `base64`** → encode/decode with `ucode`'s
   built-in `b64enc`/`b64dec` (handles spaces, `$`, `;`, quotes, UTF-8).

Getting fixes onto an offline joiner: a 16 KB script doesn't survive the lossy serial input, so the
joiner was put back into the mesh with ~20 *short* `uci` commands over serial, updated over the
network, and taken out again (one-line fixes went in as a single `sed` over serial).

Final dry run: WPS connected in 5 s, join request 3 s later, pending in LuCI/rpc as
"TP-Link Deco X55 v3 a8:29:dc:xx:xx:01", approved → settings applied 4 s later → unit #2 back in
the mesh as a DHCP satellite (192.168.0.218 from the home router), internet through the mesh,
backhaul again **2401.9 Mbit/s HE160 MCS 11 2 SS**. Next: the same with the physical buttons and
the LuCI Approve button.

### 19:00 — First real pairing with the buttons + LuCI

The user pressed the reset buttons and clicked **Approve** in LuCI (Network → Mesh nodes):
request 15:53:36, approved 15:54:06, settings delivered 15:54:07, unit #2 solid green. Unit #1 kept
blinking blue: not a hang — the window simply ran its full 120 s after the successful pairing.
Fixed: the window now closes **15 s after a successful delivery** (nobody else waiting), and old
requests are cleared when a new window opens.

Verification after pairing:
* mesh 802.11s HE160 ch 44, batman TQ 255/255 both ways, **2401.9 Mbit/s** both directions, −42 dBm
* unit #2 is a proper satellite: LAN by DHCP (192.168.0.218), no DHCP server, same SSIDs/keys as the
  registrar on both bands; internet, DNS and NTP fine; usteer sees the other node's 2 APs
* no pairing leftovers (config, AP, firewall, uhttpd), 0 DHCP offers on the home LAN
* forwarded throughput PC → unit #1 (wire) → mesh → unit #2: **446 Mbit/s**, reverse **550 Mbit/s**
  (unit #2's CPU is also the iperf3 endpoint)
* reboot of unit #2: back in the mesh by itself in ~50 s (incl. DFS CAC), same address, LED green

### 19:40 — Community image v3

Goal: an image anyone can install, behaving like a stock OpenWrt build plus the Deco extras.

* **No TP-Link binary in the published images.** `nand_do_upgrade` only removes `kernel`, `rootfs`,
  `rootfs_data` — the OEM `uboot` volume already in every unit's slot 0 survives. So the public
  install is RAM boot → `sysupgrade`, and `platform.sh` sets `tp_boot_idx=0`. A raw `factory.ubi`
  is only built when the seconduboot binary is passed in explicitly.
* **`deco-x55-defaults`** (first boot): root password `deco-modded` only if none is set; Wi-Fi like
  stock (HE160 ch 44, HE40, **country from the unit's own `factory_data`**, one SSID
  `Deco-OpenWrt` on both bands, 802.11k/v, PMF; Wi-Fi off until configured), PPE offload, WED.
  First version **overwrote the test units' Wi-Fi on upgrade** (v2 had no "defaults done" marker) —
  now every value is only changed while it still equals OpenWrt's own generated default.
* **`deco-mesh` 0.2**: *create mesh* (a new unit with internet on WAN becomes the main unit on a
  button press), wired backhaul (batman on VLAN 42 of each Ethernet port, BLA on), satellites turn
  their WAN socket into a LAN port, joiners copy the main unit's AP on/off state (an earlier
  version would have enabled an open AP on a fresh install), LuCI buttons Create / Join / Leave.
* Extras: WireGuard, OpenVPN (`luci-proto-openvpn` — `luci-app-openvpn` no longer exists), SQM,
  UPnP, DDNS, nlbwmon, Wake-on-LAN, htop, iperf3, LuCI over HTTPS.
* Packaging gotchas (apk refuses file conflicts): `/etc/rc.button/reset` belongs to base-files and
  `/etc/modules.conf` to ubox → installed/appended by first-boot scripts instead.
* My SSH tooling broke the moment the password feature worked — helpers now use `sshpass`.
* Both units upgraded with settings kept; one SSID on both bands on both units; mesh back at
  2401.9 Mbit/s; 60-minute soak test (iperf3 through the mesh + health sampling) started.

### 20:50 — Roaming test with a phone walking between the units

Unit #2 moved to the hallway (~8 m, walls and a door): mesh still −51 dBm, 2161/1921 Mbit/s
(HE-MCS 10/9 at 160 MHz). Test client: Redmi Note 15 Pro (Wi-Fi 5, no 802.11k/v advertised).

1. **usteer defaults are passive** — first walk: the phone stayed on unit #1 the whole time (the
   user's "3/5 → 5/5 bars" was the phone's own display). Defaults only band-steer.
2. Enabled roaming (trigger −70 → finally **−60 dBm**, other AP ≥ 8 dB better), faster band steering
   (2 × 10 s instead of 5 × 30 s). Result, all by **802.11v BSS transition requests the phone
   accepted** (`BSS-TM-RESP status_code=0`) — no forced disconnects:
   unit #1 5 GHz → unit #2 2.4 GHz → (band steering) unit #2 5 GHz → walking back → unit #1 2.4 GHz
   → (band steering) unit #1 5 GHz at −45 dBm.
3. usteer tends to pick the 2.4 GHz radio of the target (2.4 always looks louder); band steering
   corrects within ~20 s.
4. Tuning baked into deco-mesh 0.3 (`usteer_tune`, applied on create/join, only for unset options).

Tooling lessons: my first trackers lied — a stale station entry, then zsh not word-splitting
`set -- $u` (both "units" were unit #1). `tools/roamwatch.sh` (bash, asks hostapd who is really
associated) works. The units log in UTC, the PC in EET.

Soak test: first ~15 min recorded, but the throughput column failed (`FAIL`) — soak.sh bug to fix;
health columns fine. Stopped for the roaming test.

### 21:30 — Can it be flashed without opening the case? No.

Checked every TP-Link upgrade path in the backup:

* **Stock upgrade** (Deco app / local LuCI `admin/firmware.lua` → `/usr/bin/upgrade` →
  `nvrammanager -u`): `nvrammanager` carries its own bignum RSA and verifies an **RSA-2048 PSS**
  signature (`rsa2048 verify ok!`, `RSA PSS Verify Error 1..3`), plus MD5, firmware type, a support
  list (productId, hwId, oemId, productVer, language) and the software version.
* **U-Boot web recovery** ("Firmware Upgrade" page at 192.168.0.1, `GPIO_RESET`): the same TP-Link
  `nm_` code is compiled into the first U-Boot — `Verify sig error! to check MD5`, `RSA1`,
  support-list match on product name / hardware version / special id.
* **TFTP auto-recovery**: none (no fixed recovery filename, only the interactive `tftpboot`).

→ Only TP-Link-signed images are accepted; the first install needs UART (or a vulnerability in the
stock firmware — none known for the X55 v3, not pursued). Upgrades afterwards are plain
`sysupgrade`. Possible bonus: the web recovery may restore *stock* without UART — trigger to test.

New public README (hobbyist tone), developer notes moved to `docs/DEVELOPMENT.md`.

### 22:10 — Release v0.1, usteer patch, 12-hour soak started

* **usteer patch** (`openwrt-patches/usteer/100-prefer-5ghz-roam-target.patch`): among qualifying
  roam candidates usteer took the strongest raw signal, so 2.4 GHz always won. 5 GHz candidates now
  get a 10 dB bonus when ranking (whether to roam is unchanged). Copied into the feed by
  `setup_openwrt.sh`.
* Overclocking looked at and dropped: the MT7981 has no cpufreq — BL2 programs the CPU PLL to
  1.3 GHz once, regulators are fixed. A permanent OC means rewriting BL2 in the `boot` partition
  (the one thing never touched; secure boot / no voltage headroom = brick risk) for ≤10 % on the
  few CPU-bound paths. Thermal trips: 85/115/120/125 °C, idle ~56–64 °C. Possible later as a
  runtime-only experiment.
* `LICENSE` (GPL-2.0, from OpenWrt), `release/v0.1/` (images, SHA256SUMS, manifest, release notes
  that say plainly what is and isn't tested).
* `tools/soak2.sh`: 1-minute health samples (uptime/reboots, memory + per-daemon RSS, temperature,
  mesh PHY rates, batman TQ, NAND ECC counters, overlay, conntrack, new kernel/log errors),
  60 s iperf3 every 10 min alternating direction, 1/s ping to both units. Detached with
  `setsid nohup`. `tools/soak_report.py` summarises it. Started 20:10, both units on the v0.1 image.

---

## Tools written so far (`tools/`)

| tool | purpose |
|---|---|
| `uboot_dump.py` | NAND → RAM → `md.l` at 1 Mbaud (UART register poke), CRC per chunk, resumable |
| `uart_restore.py` | put the UART back to 115200 from 1 Mbaud |
| `serial_daemon.py` / `con.py` | persistent console: logs everything, auto-stops boots at U-Boot, paced input, XMODEM sender |
| `script_boot.py` | boot stock kernel with `init=/bin/sh -- -c "<script>"`, bootargs via XMODEM + `env import` |
| `scripts/04_backup_inetd_sw.sh` | on-device: mknod mtds, unblock MT7531 ports, serve mtds via inetd, reboot |
| `net_receive.py` | pull each mtd from the Deco, hash, trigger reboot |

### 21:36 — Soak test stopped early (1.4 h of 12)

Unit 2 had to move, so the soak ran 1 h 25 min instead of 12 h. What it shows so far
(full numbers in `backup/soak2_v0.1/REPORT.md`):

* no reboots, no unreachable samples, no NAND ECC events, overlay unchanged;
* memory flat: MemAvailable 325 → 324 MiB, daemon RSS changes of a few kB at most;
* SoC 64–66 °C on the main unit, 57–59 °C on the satellite, load average around 0.1;
* mesh PHY 1.7–2.2 Gbit/s, batman TQ never below 241/255, signal −50…−56 dBm;
* iperf3 through the mesh: 224–466 Mbit/s, median ~360 one way and ~414 the other;
* ping from the PC: 0 lost to the main unit, 6 of 5077 (0.12 %) to the satellite, single
  seconds, none in a row;
* the only error lines are from boot (hostapd/wpa_supplicant teardown messages), none during the run.

Clean, but short — an overnight run is still needed before calling it stable.

## Day 3 — 2026-11-25

### 19:45 — Over-the-network upgrade check before closing unit 2

Unit 1 is assembled now. Before unit 2's case closes for good, I checked that the path every
future update will take works, with the serial console still attached as a safety net:
copy the image over SSH (`scp -O`), `sysupgrade -T` to validate it, then `sysupgrade` with
settings kept.

* The upgrade took about 15 s. TP-Link's boot chain then booted slot 0 again; the mesh was back
  about 2 minutes after the command.
* TP-Link's second-stage U-Boot volume (`ubi0_0`) has the same md5 before and after
  (`a6d6fb7f…`), and `tp_boot_idx` is still 0.
* The whole UCI config has the same md5 before and after (`uci export | md5sum`), so
  network, Wi-Fi, mesh and system settings all came through unchanged.
* The mesh came back at HE160, 2401.9 Mbit/s both ways, TQ 255. Pinging unit 2 through the mesh:
  0 of 20 lost.
* The only error lines in the log are the two known early-boot "random MAC" kernel messages
  (fixed moments later by `10_fix_eth_mac.sh`) and the known hostapd teardown line.

Result: unit 2 can be closed. Later updates need only SSH or LuCI; serial is needed only to
recover an image that doesn't boot at all.

### 20:00 — Wired backhaul: no loop, but the failover took 70 seconds

With both units cabled to the same home switch, there are two paths between them: the cable
through the switch, and the Wi-Fi mesh. The first check was the dangerous one: is there a
broadcast storm? No. There were no duplicate replies, about 6 packets/s on the ports, and both
CPUs were idle. batman's bridge loop avoidance (BLA) did its job: each unit lists the other as a
"backbone gateway" on the same LAN, and batman's link quality over the cable is 254–255.

Then I asked for the cable on unit 2 to be pulled for about 40 s. Unit 2 was unreachable the
whole time and only came back when the cable went back in. I repeated it with
`ip link set lan1 down` for 2 minutes while logging unit 1's BLA tables and bridge FDB every
2 seconds. That made the cause clear:

* unit 1 kept unit 2 as a backbone gateway for **~70 s** after the port went down
  (`BATADV_BLA_BACKBONE_TIMEOUT` = 6 × the 10 s BLA period, plus one period of purge granularity);
* during that time, BLA on unit 1 drops unit 2's frames arriving over the mesh. That's by
  design: they "should" arrive from the LAN. Unit 1's bridge also kept unit 2's MAC on lan1;
* the moment the backbone entry expired, the FDB moved unit 2 to bat0 and traffic flowed.

The fix is a one-line batman-adv patch: BLA period 10 s → 1 s
(`openwrt-patches/batman-adv/100-faster-bla-backbone-timeout.patch`). The backbone timeout becomes
6 s and the claim timeout 10 s. The loop detection interval (6 s) stays above its 3 s timeout. The
cost is one small announcement frame per gateway per second.

After the fix: **failover to the mesh in 6.8 s** (35–38 pings lost at 5/s), switching back to
the cable in under a second.

A side effect showed up: the kernel bridge on each unit started logging "received packet on bat0
with own address as source address" once per second instead of every 10 s. BLA announcements
carry the MAC of batman's primary interface, here `lan1.42`, and a VLAN device inherits the port's
MAC, which is also br-lan's MAC. batman re-injects its announcements into the bridge through bat0,
so the bridge sees its own MAC coming in. It's harmless, since the bridge just doesn't learn from
it, but at 1/s it would push useful lines out of the log buffer within minutes. deco-mesh 0.4 now
creates the backhaul VLANs as explicit `8021q` devices with their own locally administered MAC
(first octet `0x02 | port index << 2`, e.g. `06:29:dc:…` for lan1), and a uci-default migrates
existing installs. Result: zero warnings at steady state.

Both units now run this build, upgraded over SSH with settings kept (identical `uci export`
md5 before and after).

### 20:30 — Real cable pull

I asked for the cable on unit 2 to be pulled for ~30 s with a 5/s ping running from the PC: link down at
18:29:23.7, answering over the mesh from 18:29:30.4, so **6.7 s** (33 pings lost). Plugged back:
batman moved to the cable within a second without a single loss. Same as the software test.
Wired backhaul is done.
