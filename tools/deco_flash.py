#!/usr/bin/env python3
"""
deco_flash.py: back up and install OpenWrt on a TP-Link Deco X55 v3 over its serial console.

Needs Python 3.8+ and pyserial (pip install pyserial). Works on Linux and Windows.

Wiring and network (see INSTALL.md):
  * 3.3 V USB-UART adapter on the Deco's UART header: GND, TX, RX only (never 3V3)
  * Ethernet cable from the PC to one of the Deco's LAN sockets (not WAN)
  * the PC's Ethernet adapter set to a static 192.168.1.2/24 (change with --pc-ip/--deco-ip)
  * the PC firewall must let the Deco reach UDP 69 (TFTP) and TCP 8000 (HTTP) on the PC

What it does, with the Deco powered off at the start:
  1. waits for the Deco to be powered on and stops TP-Link's U-Boot (Ctrl-C)
  2. backup (skip with --no-backup): boots the stock kernel from RAM with a script that serves
     every flash partition over the LAN, downloads all of them and verifies them against the
     SHA-256 sums the Deco computes itself and against the whole-flash dump
  3. TFTP-boots the OpenWrt initramfs image (RAM only)
  4. in the RAM-booted OpenWrt: downloads the sysupgrade image over HTTP, checks it, runs
     `sysupgrade -n` (writes OpenWrt to slot 0; the stock firmware in slot 1 is kept)
  5. waits for OpenWrt to boot from flash and checks it

Nothing is written to flash before step 4. U-Boot's environment is never saved.

  python3 deco_flash.py --port /dev/ttyUSB0          (Linux; TFTP port 69 needs root, see --help)
  py deco_flash.py --port COM5                         (Windows)
"""
import argparse, binascii, glob, hashlib, http.server, ipaddress, os, re, secrets, socket, struct
import sys, threading, time

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    sys.exit("pyserial is missing: pip install pyserial")

PROMPT = b"MT7981> "
FIT_CONFIG = "config-X50_3_0_0"
ADDR_STOCK_KERNEL = 0x46000000
ADDR_ENV = 0x4C000000
ADDR_ENV_EXPORT = 0x4D000000
ADDR_INITRAMFS = 0x50000000
# stock kernel command line for the backup boot (UART RX is dead under the stock kernel, so the
# backup runs as a script given on the command line)
STOCK_BOOTARGS = ("console=ttyS0,115200n1 loglevel=8 earlycon=uart8250,mmio32,0x11002000 "
                  "ubi.mtd=ubi{slot} config_name=config@X50_3_0_0 init=/bin/sh")
PARTS = [  # name, offset, size (TP-Link flash map, identical on every X55 v3)
    ("mtd0_boot", 0x0000000, 0x0200000), ("mtd1_u-boot-env", 0x0200000, 0x0100000),
    ("mtd2_ubi0", 0x0300000, 0x2A00000), ("mtd3_ubi1", 0x2D00000, 0x2A00000),
    ("mtd4_radio", 0x5700000, 0x0100000), ("mtd5_factory_data", 0x5800000, 0x0900000),
    ("mtd6_runtime_data", 0x6100000, 0x1100000), ("mtd7_wholeflash", 0x0000000, 0x7F80000),
]
BACKUP_SCRIPT = r"""
mount -t proc p /proc; mount -t sysfs s /sys; mount -t tmpfs t /tmp; mount -t tmpfs t /dev
for i in 0 1 2 3 4 5 6 7; do mknod /dev/mtd$i c 90 $((i*2)); echo 900$i stream tcp nowait root /bin/cat cat /dev/mtd$i >> /tmp/i.conf; done; mknod /dev/null c 1 3
echo 9099 stream tcp nowait root /sbin/reboot reboot -f >> /tmp/i.conf
ip link set lo up; for i in eth0 eth1 eth2 eth3; do ip link set $i up; done
switch reg w 2104 442000; switch reg w 2204 422000
brctl addbr br0; for i in eth1 eth2 eth3; do brctl addif br0 $i; done; ip link set br0 up
sleep 8
arping -D -c 2 -I br0 {ip} && ip addr add {ip}/{plen} dev br0
echo @@SHA; sha256sum /dev/mtd[0-7]; echo @@SHAEND
inetd /tmp/i.conf
echo @@READY
sleep 600
reboot -f
"""


def log(msg):
    print(time.strftime("%H:%M:%S ") + msg, flush=True)


class Fail(Exception):
    pass


# ------------------------------------------------------------------------------------ console
class Console:
    """Serial console with a background reader; everything received is kept and logged."""

    def __init__(self, port, logfile):
        self.ser = serial.Serial(port, 115200, timeout=0.02)
        self.buf = bytearray()
        self.lock = threading.Lock()
        self.paused = threading.Event()
        self.catch = False            # send Ctrl-C when U-Boot's countdown shows up
        self.log = open(logfile, "ab", buffering=0)
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        while True:
            if self.paused.is_set():
                time.sleep(0.01)
                continue
            d = self.ser.read(4096)
            if not d:
                continue
            self.log.write(d)
            with self.lock:
                self.buf.extend(d)
                tail = bytes(self.buf[-800:])
            if self.catch and (b"press ctrl-c" in tail or b"U-Boot 20" in tail):
                self.ser.write(b"\x03")

    def pos(self):
        with self.lock:
            return len(self.buf)

    def since(self, p):
        with self.lock:
            return bytes(self.buf[p:])

    def wait(self, pattern, timeout, start=None):
        """Wait until regex `pattern` (bytes) appears after `start`; return the match."""
        start = self.pos() if start is None else start
        rx = re.compile(pattern)
        t0 = time.time()
        while time.time() - t0 < timeout:
            m = rx.search(self.since(start))
            if m:
                return m
            time.sleep(0.05)
        return None

    def write(self, data):
        self.ser.write(data)
        self.ser.flush()

    def uboot(self, cmd, timeout=10):
        """Run a U-Boot command, return its output. Input is paced: U-Boot drops bursts."""
        if not cmd.strip():
            raise ValueError("never send a bare Enter to U-Boot: it repeats the last command")
        p = self.pos()
        b = cmd.encode() + b"\n"
        for i in range(0, len(b), 8):
            self.ser.write(b[i:i + 8])
            self.ser.flush()
            time.sleep(0.004)
        if not self.wait(re.escape(PROMPT) + rb"$", timeout, p):
            raise Fail(f"U-Boot command timed out: {cmd}\n{self.since(p)[-600:].decode(errors='replace')}")
        return self.since(p).decode(errors="replace").replace("\r", "")

    def sh(self, cmd, timeout=30):
        """Run a shell command in OpenWrt; return (exit code, output)."""
        tag = "__D" + secrets.token_hex(3) + "__"
        p = self.pos()
        self.write(f"{cmd}; echo {tag}$?\n".encode())
        m = self.wait(re.escape(tag.encode()) + rb"(\d+)", timeout, p)
        if not m:
            raise Fail(f"shell command timed out: {cmd}")
        out = self.since(p).decode(errors="replace").replace("\r", "")
        out = out.split(tag)[-2] if out.count(tag) >= 2 else out   # drop the echoed command line
        return int(m.group(1)), out

    def xmodem_send(self, data, timeout=60):
        """XMODEM-CRC sender for U-Boot 'loadx' (already started)."""
        def crc16(d):
            return binascii.crc_hqx(d, 0)
        self.paused.set()
        time.sleep(0.1)
        try:
            t0 = time.time()
            while self.ser.read(1) != b"C":
                if time.time() - t0 > timeout:
                    raise Fail("XMODEM: no start from U-Boot")
            blocks = [data[i:i + 128].ljust(128, b"\x1a") for i in range(0, len(data), 128)]
            for n, blk in enumerate(blocks, 1):
                pkt = bytes([1, n & 0xFF, 0xFF - (n & 0xFF)]) + blk + crc16(blk).to_bytes(2, "big")
                for _ in range(10):
                    self.write(pkt)
                    if self._read_byte(5) == b"\x06":
                        break
                else:
                    raise Fail(f"XMODEM: block {n} not acknowledged")
            for _ in range(10):
                self.write(b"\x04")
                if self._read_byte(3) == b"\x06":
                    return
            raise Fail("XMODEM: end of transfer not acknowledged")
        finally:
            self.paused.clear()

    def _read_byte(self, timeout):
        t0 = time.time()
        while time.time() - t0 < timeout:
            r = self.ser.read(1)
            if r:
                return r
        return b""


def catch_uboot(con, timeout):
    """Get the Deco to the U-Boot prompt. Returns once 'MT7981> ' answers."""
    tag = "deco_" + secrets.token_hex(3)
    con.catch = True                             # from now on, any U-Boot countdown gets Ctrl-C
    try:
        p = con.pos()
        con.write(f"echo {tag}\n".encode())      # already at the prompt?
        if con.wait(re.escape(tag.encode()) + rb"\r?\n" + re.escape(PROMPT), 2, p):
            return
        log("Waiting for the Deco. Power it on now (or power-cycle it).")
        if not con.wait(re.escape(PROMPT), timeout, p):
            raise Fail("no U-Boot prompt: check the UART wiring (Deco TX -> adapter RX, and GND)")
        time.sleep(0.5)
        for _ in range(5):                       # make sure the countdown is really gone
            p = con.pos()
            con.write(f"echo {tag}\n".encode())
            if con.wait(re.escape(tag.encode()) + rb"\r?\n" + re.escape(PROMPT), 3, p):
                log("Stopped at the U-Boot prompt.")
                return
        raise Fail("U-Boot prompt seen but it does not answer: is the adapter's TX wired to the Deco's RX?")
    finally:
        con.catch = False


# ------------------------------------------------------------------------------- PC servers
class TftpServer(threading.Thread):
    """Read-only TFTP server (RFC 1350 + blksize/tsize options) serving one file for any name."""

    def __init__(self, data, port):
        super().__init__(daemon=True)
        self.data = data
        self.done = threading.Event()
        self.active = set()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", port))

    def run(self):
        while True:
            pkt, peer = self.sock.recvfrom(2048)
            if len(pkt) > 2 and struct.unpack("!H", pkt[:2])[0] == 1 and peer not in self.active:  # RRQ
                self.active.add(peer)
                threading.Thread(target=self._send, args=(pkt, peer), daemon=True).start()

    def _send(self, pkt, peer):
        try:
            self._transfer(pkt, peer)
        finally:
            self.active.discard(peer)

    def _transfer(self, pkt, peer):
        fields = pkt[2:].split(b"\0")
        opts = {fields[i].decode().lower(): fields[i + 1].decode() for i in range(2, len(fields) - 1, 2)
                if fields[i]}
        log(f"TFTP: {peer[0]} requests {fields[0].decode(errors='replace')}")
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1.0)
        bs = 512
        oack = {}
        if "blksize" in opts:
            bs = max(8, min(int(opts["blksize"]), 1468))
            oack["blksize"] = str(bs)
        if "tsize" in opts:
            oack["tsize"] = str(len(self.data))

        def xchg(out, want_block):
            for _ in range(8):
                s.sendto(out, peer)
                try:
                    while True:
                        r, _ = s.recvfrom(1024)
                        op, blk = struct.unpack("!HH", r[:4])
                        if op == 4 and blk == want_block & 0xFFFF:
                            return True
                        if op == 5:
                            return False
                except socket.timeout:
                    continue
            return False

        if oack:
            body = b"".join(k.encode() + b"\0" + v.encode() + b"\0" for k, v in oack.items())
            if not xchg(struct.pack("!H", 6) + body, 0):
                return
        n = 1
        while True:
            chunk = self.data[(n - 1) * bs:n * bs]
            if not xchg(struct.pack("!HH", 3, n & 0xFFFF) + chunk, n):
                log("TFTP: transfer aborted")
                return
            if len(chunk) < bs:
                self.done.set()
                return
            n += 1


def start_http(data, port):
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/sysupgrade.bin":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            log(f"HTTP: sent the sysupgrade image to {self.client_address[0]}")

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ---------------------------------------------------------------------------------- steps
def stock_slot(con):
    """Pick a firmware slot that holds stock firmware (Linux 5.x) to run the backup from."""
    m = re.search(r"tp_boot_idx=(\d)", con.uboot("printenv tp_boot_idx"))
    active = int(m.group(1)) if m else 0
    for slot in (active, 1 - active):
        con.uboot(f"ubi part ubi{slot}", 30)
        out = con.uboot(f"ubi read {ADDR_STOCK_KERNEL:#x} kernel", 30)
        m = re.search(r"Read (\d+) bytes", out)
        if not m or int(m.group(1)) < 1_000_000:
            continue
        info = con.uboot(f"iminfo {ADDR_STOCK_KERNEL:#x}", 10)
        v = re.search(r"Linux-(\d+)\.", info)
        if v and int(v.group(1)) < 6:
            log(f"Stock firmware found in slot {slot} (active slot: {active}).")
            return slot
    return None


def backup(con, a, outdir):
    os.makedirs(outdir, exist_ok=True)
    slot = stock_slot(con)
    if slot is None:
        raise Fail("no slot with stock firmware found, cannot run the backup this way (use --no-backup)")
    net = ipaddress.ip_interface(f"{a.deco_ip}/{a.prefix}")
    lines = [l.strip() for l in BACKUP_SCRIPT.format(ip=a.deco_ip, plen=net.network.prefixlen).splitlines()
             if l.strip()]
    script = " && ".join(f"{l}||:" for l in lines)
    bootargs = STOCK_BOOTARGS.format(slot=slot) + f' -- -c "{script}"'
    if len(bootargs) > 2000:
        raise Fail("internal: backup command line too long")
    env = f"bootargs={bootargs}\n".encode()
    # U-Boot's parser caps a command at 32 words, so the bootargs go in as an XMODEM-loaded env
    p = con.pos()
    con.write(f"loadx {ADDR_ENV:#x}\n".encode())
    time.sleep(0.5)
    con.xmodem_send(env)
    if not con.wait(re.escape(PROMPT), 10, p):
        raise Fail("U-Boot did not come back after XMODEM")
    con.uboot(f"env import -t {ADDR_ENV:#x} {len(env):#x}")
    con.uboot(f"env export -t {ADDR_ENV_EXPORT:#x} bootargs")
    m = re.search(r"==> ([0-9a-f]{8})", con.uboot(f"crc32 {ADDR_ENV_EXPORT:#x} {len(env):#x}"))
    if not m or int(m.group(1), 16) != binascii.crc32(env):
        raise Fail("boot arguments did not arrive intact (crc mismatch)")
    log("Booting the stock kernel with the backup script (RAM only, nothing is written)...")
    p = con.pos()
    con.write(f"bootm {ADDR_STOCK_KERNEL:#x}#{FIT_CONFIG}\n".encode())
    m = con.wait(rb"(?s)@@SHA\r?\n(.*?)@@SHAEND", 120, p)
    if not m:
        raise Fail("the backup script did not report checksums; see the console log")
    device_sha = {f"mtd{n.decode()}": h.decode()
                  for h, n in re.findall(rb"([0-9a-f]{64})\s+/dev/mtd(\d)", m.group(1))}
    if len(device_sha) != len(PARTS):
        raise Fail(f"the Deco reported {len(device_sha)} checksums instead of {len(PARTS)}")
    if not con.wait(rb"@@READY", 30, p):
        raise Fail("the backup script did not start its file server")
    log("Downloading the flash (about 250 MB)...")
    sums = {}
    for n, (name, _, size) in enumerate(PARTS):
        h = hashlib.sha256()
        got = 0
        t0 = time.time()
        deadline = time.time() + 60
        while True:
            try:
                s = socket.create_connection((a.deco_ip, 9000 + n), timeout=3)
                break
            except OSError:
                if time.time() > deadline:
                    raise Fail(f"cannot connect to {a.deco_ip}:{9000 + n}: check the cable (LAN socket) "
                               f"and that the PC is {a.pc_ip}")
                time.sleep(0.5)
        s.settimeout(30)
        with open(os.path.join(outdir, name + ".bin"), "wb") as f:
            while True:
                d = s.recv(1 << 20)
                if not d:
                    break
                f.write(d)
                h.update(d)
                got += len(d)
        s.close()
        digest = h.hexdigest()
        ok = got == size and digest == device_sha.get(f"mtd{n}")
        log(f"  {name:20} {got:10d} bytes  {time.time() - t0:5.1f} s  {'OK' if ok else 'MISMATCH'}")
        if not ok:
            raise Fail(f"{name}: size or SHA-256 differs from what the Deco computed")
        sums[name] = digest
    whole = open(os.path.join(outdir, "mtd7_wholeflash.bin"), "rb").read()
    for name, off, size in PARTS[:-1]:
        if open(os.path.join(outdir, name + ".bin"), "rb").read() != whole[off:off + size]:
            raise Fail(f"{name} differs from its range in the whole-flash dump")
    with open(os.path.join(outdir, "SHA256SUMS"), "w") as f:
        for name, d in sums.items():
            f.write(f"{d}  {name}.bin\n")
    log(f"Backup complete and verified: {outdir}  (keep it, it holds this unit's Wi-Fi calibration)")
    try:
        socket.create_connection((a.deco_ip, 9099), timeout=3).close()   # makes the Deco reboot
    except OSError:
        log("Could not trigger the reboot; power-cycle the Deco when asked.")


def install(con, a, initramfs, sysupgrade):
    log("Loading OpenWrt into RAM over TFTP...")
    con.uboot(f"setenv ipaddr {a.deco_ip}")
    con.uboot(f"setenv serverip {a.pc_ip}")
    con.uboot(f"setenv netmask {ipaddress.ip_interface(f'{a.deco_ip}/{a.prefix}').netmask}")
    out = con.uboot(f"tftpboot {ADDR_INITRAMFS:#x} {os.path.basename(a.initramfs)}", 180)
    m = re.search(r"Bytes transferred = (\d+)", out)
    if not m or int(m.group(1)) != len(initramfs):
        hint = ""
        if "ARP Retry" in out or "Retry count exceeded" in out:
            hint = "\nU-Boot cannot reach the PC: cable in a LAN socket? PC address {}?".format(a.pc_ip)
        elif "TIMEOUT" in out.upper() or "T T T" in out:
            hint = "\nNo TFTP answer: allow UDP port 69 in the PC firewall."
        raise Fail("TFTP transfer failed" + hint + "\n" + out[-500:])
    log("Booting OpenWrt from RAM...")
    p = con.pos()
    con.write(f"bootm {ADDR_INITRAMFS:#x}#{FIT_CONFIG}\n".encode())
    if not con.wait(rb"Please press Enter to activate this console", 180, p):
        raise Fail("OpenWrt (RAM) did not finish booting; see the console log")
    shell_login(con)
    log("OpenWrt is running from RAM. Fetching the sysupgrade image from the PC...")
    code, out = con.sh(f"ip -4 addr show br-lan | grep -q 'inet {a.deco_ip}/' || "
                       f"ip addr add {a.deco_ip}/{a.prefix} dev br-lan", 20)
    ok = False
    for _ in range(30):
        if con.sh(f"ping -c 1 -W 1 {a.pc_ip} >/dev/null", 10)[0] == 0:
            ok = True
            break
        time.sleep(1)
    if not ok:
        raise Fail(f"OpenWrt cannot reach the PC at {a.pc_ip}")
    code, out = con.sh(f"wget -q -T 20 -O /tmp/sysupgrade.bin http://{a.pc_ip}:{a.http_port}/sysupgrade.bin", 120)
    if code != 0:
        raise Fail(f"download failed: allow TCP port {a.http_port} in the PC firewall\n{out}")
    want = hashlib.sha256(sysupgrade).hexdigest()
    code, out = con.sh("sha256sum /tmp/sysupgrade.bin", 30)
    if want not in out:
        raise Fail("the sysupgrade image arrived damaged (SHA-256 mismatch)")
    code, out = con.sh("sysupgrade -T /tmp/sysupgrade.bin", 60)
    if code != 0:
        raise Fail("sysupgrade rejected the image:\n" + out)
    log("Writing OpenWrt to flash (slot 0). Do not power off the Deco.")
    p = con.pos()
    con.write(b"sysupgrade -n /tmp/sysupgrade.bin\n")
    if not con.wait(rb"sysupgrade successful|Upgrade completed", 300, p):
        raise Fail("sysupgrade did not report success; do NOT power off, check the console log")
    log("Flash written. Waiting for OpenWrt to boot from flash...")
    if not con.wait(rb"Please press Enter to activate this console", 300, p):
        raise Fail("OpenWrt did not boot from flash; see the console log")
    shell_login(con)
    code, idx = con.sh("fw_printenv -n tp_boot_idx", 20)
    code2, _ = con.sh("grep -q ' /overlay ' /proc/mounts", 20)
    if "0" not in idx.split() or code2 != 0:
        raise Fail(f"booted, but not from flash as expected (tp_boot_idx: {idx.strip()!r})")
    _, rel = con.sh("grep DESCRIPTION /etc/openwrt_release", 10)
    log("OpenWrt is installed and booted from flash: " + rel.strip().split("=", 1)[-1])


def shell_login(con):
    time.sleep(1)
    p = con.pos()
    con.write(b"\n")
    m = con.wait(rb"login:\s*$|# $", 15, p)
    if m and m.group(0).startswith(b"login"):
        con.write(b"root\n")
        if con.wait(rb"Password:\s*$", 10):
            con.write(b"deco-modded\n")
    for _ in range(10):
        try:
            con.sh("true", 5)
            return
        except Fail:
            con.write(b"\n")
    raise Fail("no shell on the OpenWrt console")


# ----------------------------------------------------------------------------------- main
def find_image(arg, pattern):
    if arg:
        return arg
    here = os.path.dirname(os.path.abspath(__file__))
    hits = sorted(set(glob.glob(os.path.join(os.getcwd(), pattern)) + glob.glob(os.path.join(here, pattern))))
    if len(hits) != 1:
        sys.exit(f"cannot pick the image matching {pattern} ({len(hits)} found); pass it explicitly")
    return hits[0]


def check_sums(path, data):
    sums = os.path.join(os.path.dirname(os.path.abspath(path)), "SHA256SUMS")
    if not os.path.exists(sums):
        log(f"(no SHA256SUMS next to {os.path.basename(path)}, not checked)")
        return
    want = {l.split()[1].lstrip("*"): l.split()[0] for l in open(sums) if len(l.split()) == 2}
    name = os.path.basename(path)
    if name in want:
        if hashlib.sha256(data).hexdigest() != want[name]:
            sys.exit(f"{name}: SHA-256 does not match SHA256SUMS, download it again")
        log(f"{name}: SHA-256 OK")


def pick_port():
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        sys.exit("no serial ports found; plug in the USB-UART adapter")
    for i, p in enumerate(ports, 1):
        print(f"  {i}) {p.device}  {p.description}")
    while True:
        c = input("Serial port of the USB-UART adapter (number): ").strip()
        if c.isdigit() and 1 <= int(c) <= len(ports):
            return ports[int(c) - 1].device


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="serial port (COM5, /dev/ttyUSB0, ...); asked if omitted")
    ap.add_argument("--initramfs", help="…-initramfs-kernel.bin (default: found next to this script)")
    ap.add_argument("--sysupgrade", help="…-squashfs-sysupgrade.bin (default: found next to this script)")
    ap.add_argument("--pc-ip", default="192.168.1.2", help="this PC's static address (default 192.168.1.2)")
    ap.add_argument("--deco-ip", default="192.168.1.1", help="address for the Deco (default 192.168.1.1)")
    ap.add_argument("--prefix", default=24, type=int, help="prefix length (default 24)")
    ap.add_argument("--backup-dir", help="where to save the backup (default: deco-backup-<date>)")
    ap.add_argument("--no-backup", action="store_true", help="skip the flash backup")
    ap.add_argument("--backup-only", action="store_true", help="only back up, install nothing")
    ap.add_argument("--tftp-port", type=int, default=69, help=argparse.SUPPRESS)   # for tests only
    ap.add_argument("--http-port", type=int, default=8000, help="HTTP port for the sysupgrade image")
    ap.add_argument("--wait", type=int, default=600, help="seconds to wait for power-on (default 600)")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    a = ap.parse_args()

    a.port = a.port or pick_port()
    initramfs = sysupgrade = b""
    if not a.backup_only:
        a.initramfs = find_image(a.initramfs, "*tplink_deco-x55-v3-initramfs-kernel.bin")
        a.sysupgrade = find_image(a.sysupgrade, "*tplink_deco-x55-v3-squashfs-sysupgrade.bin")
        initramfs = open(a.initramfs, "rb").read()
        sysupgrade = open(a.sysupgrade, "rb").read()
        check_sums(a.initramfs, initramfs)
        check_sums(a.sysupgrade, sysupgrade)
    try:
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM).bind((a.pc_ip, 0))
    except OSError:
        sys.exit(f"this PC has no address {a.pc_ip}. Give the Ethernet adapter that goes to the Deco the "
                 f"static address {a.pc_ip}/{a.prefix} (no gateway), or pass --pc-ip.")
    servers = []
    if not a.backup_only:
        try:
            tftp = TftpServer(initramfs, a.tftp_port)
        except PermissionError:
            sys.exit("cannot open UDP port 69 (TFTP). On Linux run with sudo, or allow it once with:\n"
                     "  sudo sysctl net.ipv4.ip_unprivileged_port_start=69")
        except OSError as e:
            sys.exit(f"cannot open UDP port {a.tftp_port} (TFTP): {e}. Is another TFTP server running?")
        tftp.start()
        try:
            servers.append(start_http(sysupgrade, a.http_port))
        except OSError as e:
            sys.exit(f"cannot open TCP port {a.http_port} (HTTP): {e}. Pick another with --http-port.")
    outdir = a.backup_dir or os.path.abspath(time.strftime("deco-backup-%Y%m%d-%H%M%S"))
    do_backup = not a.no_backup

    print()
    print(f"  serial port : {a.port}")
    print(f"  network     : PC {a.pc_ip}, Deco {a.deco_ip}/{a.prefix} (cable in a LAN socket)")
    print(f"  backup      : {outdir if do_backup else 'SKIPPED'}")
    if not a.backup_only:
        print(f"  initramfs   : {a.initramfs}")
        print(f"  sysupgrade  : {a.sysupgrade}")
        print("  This installs OpenWrt into slot 0. The stock firmware in slot 1 is kept.")
    print()
    if not a.yes and input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
        sys.exit("aborted")

    logfile = os.path.abspath("deco_flash_console.log")
    con = Console(a.port, logfile)
    log(f"Console log: {logfile}")
    try:
        catch_uboot(con, a.wait)
        if do_backup:
            backup(con, a, outdir)
            if a.backup_only:
                log("Done (backup only). The Deco reboots into its normal firmware.")
                return
            catch_uboot(con, 180)
        install(con, a, initramfs, sysupgrade)
        log("Done. Log in at https://" + a.deco_ip + " as root / deco-modded and change the password.")
    except Fail as e:
        log("FAILED: " + str(e))
        log(f"Full console output: {logfile}")
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit("interrupted")


if __name__ == "__main__":
    main()
