#!/usr/bin/env python3
"""
Dump SPI-NAND of a MT7981 device through U-Boot's console.

 1. Bumps the MT7981 UART (0x11002000, 40 MHz clk) to a higher baud by poking
    DLL/DLM + SAMPLE_COUNT/SAMPLE_POINT directly (U-Boot's baud table stops at
    115200). Nothing is written to flash; a reset restores 115200.
 2. `mtd read` the requested range into RAM once.
 3. Streams it back in chunks with `md.l`, verifies each chunk against
    U-Boot's `crc32`, retries on mismatch, and stores chunks so the dump
    can be resumed.

Usage: uboot_dump.py OUTFILE [--start 0] [--size 0x8000000] [--baud 1000000]
"""
import argparse, os, re, sys, time, zlib, struct
import serial

# Serial adapter: $DECO_TTY, else the first FTDI adapter by id. By id on purpose: other USB
# serial devices (headset dongles, ...) must never be opened by accident.
import glob
PORT = os.environ.get("DECO_TTY") or next(iter(sorted(glob.glob("/dev/serial/by-id/usb-FTDI_*"))), None)
if not PORT:
    raise SystemExit("no FTDI serial adapter found; set DECO_TTY=/dev/serial/by-id/...")
UART = 0x11002000
CLK = 40_000_000
RAM = 0x48000000
PROMPT = b"MT7981> "
LINE_RE = re.compile(rb"^([0-9a-f]{8}): ((?:[0-9a-f]{8} ?){1,4})", re.M)


class UBoot:
    def __init__(self, baud):
        self.s = serial.Serial(PORT, baud, timeout=0.05)

    def cmd(self, c, timeout=30, quiet=True):
        self.s.reset_input_buffer()
        # send slowly enough for U-Boot's polled rx path
        for i in range(0, len(c), 8):
            self.s.write(c[i:i + 8].encode())
            self.s.flush()
            time.sleep(0.002)
        self.s.write(b"\n")
        buf = bytearray()
        t0 = time.time()
        while time.time() - t0 < timeout:
            d = self.s.read(65536)
            if d:
                buf += d
                if buf.endswith(PROMPT):
                    break
        else:
            raise TimeoutError(f"timeout on {c!r}: tail={bytes(buf[-200:])!r}")
        return bytes(buf)

    def alive(self):
        try:
            # never send a bare Enter: U-Boot repeats the last command on it
            return b"ping_ok" in self.cmd("echo ping_ok", timeout=1.5)
        except TimeoutError:
            return False


def set_fast_baud(baud):
    s = int(round(CLK / baud))
    assert 1 <= s <= 256, "baud not reachable with quot=1"
    real = CLK / s
    print(f"[*] target {baud} -> MT7981 sample_count={s} => {real:.0f} baud")
    # Already fast?
    u = UBoot(baud)
    if u.alive():
        print("[*] console already at fast baud")
        return u
    u.s.close()
    for b in (1_000_000, 115200):
        u = UBoot(b)
        if u.alive():
            break
        u.s.close()
    else:
        sys.exit("[!] no U-Boot prompt at known bauds")
    line = (f"mw.l {UART+0x0c:#x} 0x83; mw.l {UART+0x00:#x} 1; mw.l {UART+0x04:#x} 0; "
            f"mw.l {UART+0x0c:#x} 3; mw.l {UART+0x24:#x} 3; "
            f"mw.l {UART+0x28:#x} {s-1:#x}; mw.l {UART+0x2c:#x} {(s-1)//2:#x}")
    for ch in line:
        u.s.write(ch.encode()); u.s.flush(); time.sleep(0.003)
    u.s.write(b"\n"); u.s.flush()
    time.sleep(0.3)
    u.s.close()
    u = UBoot(baud)
    time.sleep(0.2)
    for _ in range(3):
        if u.alive():
            print("[*] fast baud OK")
            return u
    sys.exit("[!] lost console after baud switch - power-cycle the device")


def parse_md(out, base, length):
    data = bytearray(length)
    seen = 0
    for m in LINE_RE.finditer(out):
        addr = int(m.group(1), 16)
        words = m.group(2).split()
        off = addr - base
        for i, w in enumerate(words):
            o = off + 4 * i
            if 0 <= o < length:
                struct.pack_into("<I", data, o, int(w, 16))
                seen += 4
    return bytes(data), seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--start", type=lambda x: int(x, 0), default=0)
    ap.add_argument("--size", type=lambda x: int(x, 0), default=0x8000000)
    ap.add_argument("--chunk", type=lambda x: int(x, 0), default=0x10000)
    ap.add_argument("--baud", type=int, default=1_000_000)
    ap.add_argument("--no-read", action="store_true", help="RAM already holds the data")
    ap.add_argument("--chunkdir", default=None)
    a = ap.parse_args()

    chunkdir = a.chunkdir or (a.out + ".chunks")
    os.makedirs(chunkdir, exist_ok=True)
    u = set_fast_baud(a.baud)

    if not a.no_read:
        print(f"[*] mtd read {a.start:#x}+{a.size:#x} -> RAM {RAM:#x}")
        t = time.time()
        r = u.cmd(f"mtd read spi-nand0 {RAM:#x} {a.start:#x} {a.size:#x}", timeout=600)
        print(r.decode(errors="replace").strip().splitlines()[-2:], f"{time.time()-t:.1f}s")
        if b"OK" not in r and b"Reading" not in r:
            sys.exit("[!] mtd read failed")

    n = a.size // a.chunk
    t0 = time.time(); done_bytes = 0
    for i in range(n):
        off = i * a.chunk
        fn = os.path.join(chunkdir, f"{a.start+off:08x}.bin")
        if os.path.exists(fn) and os.path.getsize(fn) == a.chunk:
            continue
        addr = RAM + off
        for attempt in range(5):
            try:
                c = u.cmd(f"crc32 {addr:#x} {a.chunk:#x}", timeout=5)
                m = re.search(rb"==> ([0-9a-f]{8})", c)
                want = int(m.group(1), 16)
                out = u.cmd(f"md.l {addr:#x} {a.chunk//4:#x}", timeout=a.chunk * 12 / a.baud * 10 + 10)
                data, seen = parse_md(out, addr, a.chunk)
                got = zlib.crc32(data)
                if seen == a.chunk and got == want:
                    break
                print(f"\n[!] chunk {a.start+off:#x}: crc {got:08x} != {want:08x} (seen {seen}), retry")
            except (TimeoutError, AttributeError) as e:
                print(f"\n[!] chunk {a.start+off:#x}: {e}, retry")
                time.sleep(0.5)
        else:
            sys.exit(f"[!] giving up on chunk {a.start+off:#x}")
        with open(fn + ".tmp", "wb") as f:
            f.write(data)
        os.rename(fn + ".tmp", fn)
        done_bytes += a.chunk
        el = time.time() - t0
        rate = done_bytes / el
        left = (n - i - 1) * a.chunk / rate
        print(f"\r[*] {a.start+off+a.chunk:#010x}/{a.start+a.size:#010x}  {rate/1024:.1f} KiB/s  ETA {left/60:.1f} min   ",
              end="", flush=True)

    print("\n[*] assembling")
    with open(a.out, "wb") as f:
        for i in range(n):
            with open(os.path.join(chunkdir, f"{a.start+i*a.chunk:08x}.bin"), "rb") as c:
                f.write(c.read())
    print(f"[*] wrote {a.out}")


if __name__ == "__main__":
    main()
