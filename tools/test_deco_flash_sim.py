#!/usr/bin/env python3
"""
End-to-end test of deco_flash.py against a simulated Deco on a pseudo-terminal (Linux only).

The simulator imitates what deco_flash.py relies on: U-Boot (countdown, echo, loadx/XMODEM,
env import/export, crc32, ubi read, iminfo, tftpboot as a real TFTP client), the stock backup
boot (serves a random 128 MiB "flash" over TCP, prints the SHA-256 sums), and the OpenWrt shell
(wget over HTTP, sysupgrade, reboot into OpenWrt from flash).

  python3 tools/test_deco_flash_sim.py <initramfs.bin> <sysupgrade.bin> <workdir>
"""
import binascii, hashlib, os, pty, re, socket, struct, subprocess, sys, threading, time, tty
import urllib.request

INITRAMFS, SYSUPGRADE, WORK = (os.path.abspath(x) for x in sys.argv[1:4])
DECO_IP, PC_IP, TFTP_PORT, HTTP_PORT = "127.0.0.2", "127.0.0.1", 6969, 8099
PARTS = [(0x0, 0x200000), (0x200000, 0x100000), (0x300000, 0x2A00000), (0x2D00000, 0x2A00000),
         (0x5700000, 0x100000), (0x5800000, 0x900000), (0x6100000, 0x1100000), (0x0, 0x7F80000)]

master, slave = pty.openpty()
tty.setraw(slave)
SLAVE = os.ttyname(slave)


def out(s):
    os.write(master, s.encode() if isinstance(s, str) else s)


def readbyte(timeout=None):
    import select
    r, _, _ = select.select([master], [], [], timeout)
    return os.read(master, 1) if r else b""


class Deco:
    def __init__(self):
        self.env = {"tp_boot_idx": "1"}
        self.mem = {}
        self.flash = os.urandom(0x7F80000)
        self.installed = False
        self.events = []

    # ---- boot sequences (one thread; each boot_once() returns when the device reboots)
    def run(self):
        while True:
            self.boot_once()

    def boot_once(self):
        out("\r\nF0: 102B 0000\r\nNOTICE:  BL2: v2.7\r\n\r\nU-Boot 2022.07-rc3 (Aug 14 2026)\r\n")
        out("press ctrl-c or t to go to uboot cmdline\r\n")
        t0 = time.time()
        while time.time() - t0 < 2:
            if readbyte(0.05) == b"\x03":
                out("\r\nMT7981> ")
                kind = self.uboot()
                if kind == "stock":
                    return self.stock_backup()
                return self.openwrt(flash=False)
        out("Starting kernel ...\r\n")
        if self.installed:
            return self.openwrt(flash=True)
        out("[stock firmware running, UART RX ignored]\r\n")
        while True:            # stock firmware: ignore input forever
            readbyte(1)

    def readline_uboot(self):
        line = b""
        while True:
            c = readbyte()
            if c == b"\x03":
                out("<INTERRUPT>\r\nMT7981> ")
                line = b""
                continue
            if c in (b"\n", b"\r"):
                out("\r\n")
                return line.decode()
            line += c
            out(c)

    def uboot(self):
        while True:
            cmd = self.readline_uboot().strip()
            r = self.uboot_cmd(cmd)
            if r:
                return r
            out("MT7981> ")

    def uboot_cmd(self, cmd):
        a = cmd.split()
        if not a:
            return
        if a[0] == "echo":
            out(" ".join(a[1:]) + "\r\n")
        elif cmd == "printenv tp_boot_idx":
            out(f"tp_boot_idx={self.env['tp_boot_idx']}\r\n")
        elif a[:2] == ["ubi", "part"]:
            self.slot = int(a[2][-1])
            out(f"ubi0: attaching mtd{self.slot + 2}\r\n")
        elif a[:2] == ["ubi", "read"]:
            out("Read 3481784 bytes from volume kernel to 0000000046000000\r\n")
        elif a[0] == "iminfo":
            v = "6.18.64" if (self.installed and self.slot == 0) else "5.4.124"
            out(f"   FIT image found\r\n    Description:  ARM64 OpenWrt Linux-{v}\r\n")
        elif a[0] == "loadx":
            self.xmodem_rx(int(a[1], 16))
        elif a[:3] == ["env", "import", "-t"]:
            txt = self.mem[int(a[3], 16)][:int(a[4], 16)].decode()
            for l in txt.splitlines():
                k, v = l.split("=", 1)
                self.env[k] = v
        elif a[:3] == ["env", "export", "-t"]:
            self.mem[int(a[3], 16)] = f"{a[4]}={self.env[a[4]]}\n".encode()
        elif a[0] == "crc32":
            d = self.mem[int(a[1], 16)][:int(a[2], 16)]
            out(f"crc32 for {a[1]} ... ==> {binascii.crc32(d):08x}\r\n")
        elif a[0] == "setenv":
            self.env[a[1]] = a[2]
        elif a[0] == "tftpboot":
            self.tftp(int(a[1], 16), a[2])
        elif a[0] == "bootm":
            addr = int(a[1].split("#")[0], 16)
            out("## Loading kernel from FIT Image\r\nStarting kernel ...\r\n")
            return "stock" if addr == 0x46000000 else "ram"
        else:
            out(f"Unknown command '{a[0]}'\r\n")

    def xmodem_rx(self, addr):
        out(f"## Ready for binary (xmodem) download to {addr:#x} at 115200 bps...\r\n")
        data = b""
        while True:
            out(b"C")
            c = readbyte(1)
            if c == b"\x01":
                break
        n = 1
        while True:
            if c == b"\x04":
                out(b"\x06")
                break
            pkt = b"".join(readbyte(2) for _ in range(132))
            blk = pkt[2:130]
            assert pkt[0] == n & 0xFF and binascii.crc_hqx(blk, 0) == int.from_bytes(pkt[130:132], "big")
            data += blk
            n += 1
            out(b"\x06")
            c = readbyte(5)
        self.mem[addr] = data.rstrip(b"\x1a")
        out(f"\r\n## Total Size = {len(data):#x} = {len(data)} Bytes\r\n")

    def tftp(self, addr, name):
        out(f"Using ethernet@15100000 device\r\nTFTP from server {self.env['serverip']}; "
            f"our IP address is {self.env['ipaddr']}\r\nFilename '{name}'.\r\nLoading: ")
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind((DECO_IP, 0))
        s.settimeout(3)
        s.sendto(struct.pack("!H", 1) + name.encode() + b"\0octet\0blksize\0" + b"1468\0tsize\0" + b"0\0",
                 (self.env["serverip"], TFTP_PORT))
        r, srv = s.recvfrom(2048)
        assert struct.unpack("!H", r[:2])[0] == 6, "expected OACK"
        bs = int(r[2:].split(b"\0")[1])
        s.sendto(struct.pack("!HH", 4, 0), srv)
        data, n = b"", 1
        while True:
            r, _ = s.recvfrom(4 + bs)
            op, blk = struct.unpack("!HH", r[:4])
            if blk == n & 0xFFFF:
                data += r[4:]
                n += 1
            s.sendto(struct.pack("!HH", 4, blk), srv)
            if len(r) - 4 < bs:
                break
        self.mem[addr] = data
        out(f"#################\r\ndone\r\nBytes transferred = {len(data)} ({len(data):x} hex)\r\n")

    def stock_backup(self):
        args = self.env["bootargs"]
        assert f"ubi.mtd=ubi{self.slot}" in args and f"ip addr add {DECO_IP}/24 dev br0" in args, args
        self.events.append("stock backup boot")
        out("Run /bin/sh as init process\r\n@@SHA\r\n")
        for i, (o, n) in enumerate(PARTS):
            out(f"{hashlib.sha256(self.flash[o:o + n]).hexdigest()}  /dev/mtd{i}\r\n")
        out("@@SHAEND\r\n@@READY\r\n")
        srvs = []
        for i, (o, n) in enumerate(PARTS):
            sv = socket.socket()
            sv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sv.bind((DECO_IP, 9000 + i))
            sv.listen(1)
            srvs.append(sv)
            threading.Thread(target=self.serve, args=(sv, self.flash[o:o + n]), daemon=True).start()
        rb = socket.socket()
        rb.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        rb.bind((DECO_IP, 9099))
        rb.listen(1)
        rb.accept()[0].close()
        for sv in srvs + [rb]:
            sv.close()
        self.events.append("reboot trigger")
        out("reboot: Restarting system\r\n")

    def serve(self, sv, data):
        try:
            c, _ = sv.accept()
            c.sendall(data)
            c.close()
        except OSError:
            pass

    def openwrt(self, flash):
        time.sleep(0.5)
        out("[    5.0] procd: - init complete -\r\nPlease press Enter to activate this console.\r\n")
        self.flash_boot = flash
        line = b""
        while True:
            c = readbyte()
            if c not in (b"\n", b"\r"):
                line += c
                out(c)
                continue
            out("\r\n")
            cmd = line.decode()
            line = b""
            m = re.match(r"(.*); echo (__D\w+__)\$\?$", cmd)
            if not cmd:
                out(" OpenWrt banner\r\nroot@OpenWrt:/# ")
                continue
            if not m:
                if cmd.startswith("sysupgrade -n"):
                    assert self.sysupgrade_ok
                    self.events.append("sysupgrade -n")
                    out("Performing system upgrade...\r\nsysupgrade successful\r\nreboot: Restarting system\r\n")
                    self.installed = True
                    self.env = {"tp_boot_idx": "0"}
                    return
                out("root@OpenWrt:/# ")
                continue
            code, text = self.sh(m.group(1))
            out(text + f"{m.group(2)}{code}\r\nroot@OpenWrt:/# ")

    def sh(self, c):
        if c == "true" or c.startswith("ip -4 addr show br-lan") or c.startswith("ping "):
            return 0, ""
        if c.startswith("wget "):
            url = c.split()[-1]
            self.dl = urllib.request.urlopen(url).read()
            return 0, ""
        if c.startswith("sha256sum"):
            return 0, hashlib.sha256(self.dl).hexdigest() + "  /tmp/sysupgrade.bin\r\n"
        if c.startswith("sysupgrade -T"):
            self.sysupgrade_ok = True
            return 0, ""
        if c == "fw_printenv -n tp_boot_idx":
            return 0, self.env["tp_boot_idx"] + "\r\n"
        if c.startswith("grep -q ' /overlay '"):
            return (0 if self.flash_boot else 1), ""
        if c.startswith("grep DESCRIPTION"):
            return 0, "DISTRIB_DESCRIPTION='OpenWrt SNAPSHOT r38531+1-561fbe3b44'\r\n"
        return 127, f"sh: {c}: not found\r\n"


deco = Deco()
threading.Thread(target=lambda: (time.sleep(4), deco.run()), daemon=True).start()
here = os.path.dirname(os.path.abspath(__file__))
os.makedirs(WORK, exist_ok=True)
p = subprocess.run([sys.executable, os.path.join(here, "deco_flash.py"), "--port", SLAVE, "--yes",
                    "--initramfs", INITRAMFS, "--sysupgrade", SYSUPGRADE, "--pc-ip", PC_IP,
                    "--deco-ip", DECO_IP, "--tftp-port", str(TFTP_PORT), "--http-port", str(HTTP_PORT),
                    "--backup-dir", os.path.join(WORK, "backup"), "--wait", "30"], cwd=WORK, timeout=900)
print("simulator events:", deco.events)
bad = []
for i, (o, n) in enumerate(PARTS):
    names = sorted(f for f in os.listdir(os.path.join(WORK, "backup")) if f.startswith(f"mtd{i}_"))
    if not names:
        bad.append(f"mtd{i} missing")
        continue
    name = names[0]
    if open(os.path.join(WORK, "backup", name), "rb").read() != deco.flash[o:o + n]:
        bad.append(name)
print("backup files identical to the simulated flash:", not bad, bad)
print("initramfs delivered intact:", deco.mem.get(0x50000000) == open(INITRAMFS, "rb").read())
ok = p.returncode == 0 and not bad and deco.installed
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
