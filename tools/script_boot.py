#!/usr/bin/env python3
"""
Boot the stock kernel (from UBI slot ubi1) with init=/bin/sh -- -c "<script>".
UART RX is dead under the stock kernel, so the script is the only input.

The script must avoid  ;  $  =  \\  '  (U-Boot simple parser / kernel cmdline).
Chain commands with  ' && '  — each command gets '||:' appended automatically.

Usage: script_boot.py SCRIPTFILE [watch_seconds]
  SCRIPTFILE: one shell command per line ('#' comments allowed).
Needs tools/serial_daemon.py running and the board sitting at the U-Boot prompt.
"""
import os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE = os.path.join(HERE, "..", "backup", "console_live.log")
BASE = ("console=ttyS0,115200n1 loglevel=8 earlycon=uart8250,mmio32,0x11002000 "
        "ubi.mtd=ubi1 config_name=config@X50_3_0_0 init=/bin/sh")


def con(*a, t="30"):
    r = subprocess.run([sys.executable, os.path.join(HERE, "con.py"), *a, t],
                       capture_output=True, text=True)
    return r.stdout


lines = [l.strip() for l in open(sys.argv[1]) if l.strip() and not l.lstrip().startswith("#")]
for l in lines:
    bad = set(l) & set('"\\')
    if bad:
        sys.exit(f"forbidden char {bad} in: {l}")
script = " && ".join(f"{l}||:" for l in lines)
watch = float(sys.argv[2]) if len(sys.argv) > 2 else 120
print(f"script ({len(script)} chars)")

if "ping_ok" not in con("--uboot", "echo ping_ok", t="5"):
    sys.exit("board is not at the U-Boot prompt")
out = con("--uboot", "ubi part ubi1", t="30")
if "attached mtd" not in out and "ubi0: attached" not in out:
    print(out[-400:])
out = con("--uboot", "ubi read 0x46000000 kernel", t="30")
if "Read 3481784 bytes" not in out:
    sys.exit("kernel read failed:\n" + out[-400:])
# U-Boot's simple parser caps commands at 32 words, so the bootargs go in via
# an XMODEM-loaded text env ('env import -t'), RAM only, never saveenv.
want = f'{BASE} -- -c "{script}"'
if len(want) > 2000:   # arm64 COMMAND_LINE_SIZE is 2048; a cut script = syntax error = init exits = panic
    sys.exit(f"command line too long ({len(want)} bytes > 2000), shorten the script")
envf = os.path.join(HERE, "..", "backup", "bootargs_env.txt")
open(envf, "w").write(f"bootargs={want}\n")
size = os.path.getsize(envf)
x = con("--xmodem", os.path.abspath(envf), t="60")
if "[xmodem: ok" not in x:
    sys.exit("xmodem failed:\n" + x[-600:])
con("--uboot", f"env import -t 0x4c000000 {size:#x}", t="5")
# printenv truncates at ~1 KiB, so verify byte-exact via export + crc32
import re, zlib
con("--uboot", "env export -t 0x4d000000 bootargs", t="5")
c = con("--uboot", f"crc32 0x4d000000 {size:#x}", t="5")
m = re.search(r"==> ([0-9a-f]{8})", c)
exp = zlib.crc32(open(envf, "rb").read())
if not m or int(m.group(1), 16) != exp:
    sys.exit(f"bootargs crc mismatch: {c[-200:]} expected {exp:08x}")
print("bootargs OK, booting")
start = os.path.getsize(LIVE)
con("--raw", "bootm 0x46000000#config-X50_3_0_0\n", t="1")
time.sleep(watch)
with open(LIVE, "rb") as f:
    f.seek(start)
    txt = f.read().decode(errors="replace").replace("\r", "")
i = txt.find("Run /bin/sh as init process")
print(txt[i:] if i >= 0 else txt[-3000:])
