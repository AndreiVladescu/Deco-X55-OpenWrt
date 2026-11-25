#!/usr/bin/env python3
"""
Persistent console session for the Deco (FT232R only).

* Keeps the serial port open, appends every byte to backup/console_live.log.
* Stage machine (automatic, so no timing gaps):
    wait_uboot : on "U-Boot 20" / "press ctrl-c" -> spam Ctrl-C until prompt
    (then idle; commands come from the client)
    on "Run /bin/sh as init process" + "/ #" -> send `echo V > /dev/watchdog`
       immediately (magic close: stops mtk-wdt, nowayout=0)
* Unix socket server: client sends a line, daemon writes it to the console
  and returns everything until the marker / prompt.

Usage: serial_daemon.py SOCKET_PATH
Client: tools/con.py 'command' [timeout] [--uboot]
"""
import os, re, socket, sys, threading, time
import serial

# Serial adapter: $DECO_TTY, else the first FTDI adapter by id. By id on purpose: other USB
# serial devices (headset dongles, ...) must never be opened by accident.
import glob
PORT = os.environ.get("DECO_TTY") or next(iter(sorted(glob.glob("/dev/serial/by-id/usb-FTDI_*"))), None)
if not PORT:
    raise SystemExit("no FTDI serial adapter found; set DECO_TTY=/dev/serial/by-id/...")
HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "..", "backup", "console_live.log")
NOCATCH = os.environ.get("DECO_NOCATCH", "/tmp/deco_nocatch")

sock_path = sys.argv[1]
ser = serial.Serial(PORT, 115200, timeout=0.02)
log = open(LOG, "ab", buffering=0)
lock = threading.Lock()
rx = bytearray()          # everything received since start
state = {"catch_uboot": True, "wdt_done": False, "shell_seen": False}
xfer = threading.Event()   # set while an XMODEM transfer owns the port


def crc16(data):
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def xmodem_send(path):
    """XMODEM-CRC sender for U-Boot 'loadx' (already started)."""
    data = open(path, "rb").read()
    blocks = [data[i:i + 128].ljust(128, b"\x1a") for i in range(0, len(data), 128)]
    t0 = time.time()
    while True:                                  # wait for receiver 'C'
        c = ser.read(1)
        if c == b"C":
            break
        if time.time() - t0 > 30:
            return "no 'C' from receiver"
    for n, blk in enumerate(blocks, 1):
        pkt = bytes([0x01, n & 0xFF, 0xFF - (n & 0xFF)]) + blk + crc16(blk).to_bytes(2, "big")
        for _ in range(10):
            ser.write(pkt); ser.flush()
            t1 = time.time(); r = b""
            while time.time() - t1 < 5 and not r:
                r = ser.read(1)
            if r == b"\x06":
                break
        else:
            return f"block {n} not ACKed"
    for _ in range(10):
        ser.write(b"\x04"); ser.flush()
        t1 = time.time(); r = b""
        while time.time() - t1 < 3 and not r:
            r = ser.read(1)
        if r == b"\x06":
            return f"ok {len(data)} bytes, {len(blocks)} blocks"
    return "EOT not ACKed"


def reader():
    while True:
        if xfer.is_set():
            time.sleep(0.01); continue
        d = ser.read(4096)
        if not d:
            continue
        log.write(d)
        with lock:
            rx.extend(d)
            tail = bytes(rx[-4096:])
        if b"F0: 102B" in d or b"NOTICE:  BL2:" in d:      # new power-on/reset: re-arm
            if not state["catch_uboot"]:
                log.write(b"\n[daemon: new boot seen, re-arming U-Boot catch]\n")
            state.update(catch_uboot=True, wdt_done=False, shell_seen=False)
        # touch $DECO_NOCATCH (/tmp/deco_nocatch) to let boots run through untouched (watch only)
        if os.path.exists(NOCATCH):
            state["catch_uboot"] = False
        if state["catch_uboot"] and (b"press ctrl-c" in tail[-600:] or b"U-Boot 20" in tail[-600:]):
            ser.write(b"\x03")
            if tail.endswith(b"MT7981> "):
                state["catch_uboot"] = False
                log.write(b"\n[daemon: stopped at U-Boot]\n")
        if not state["shell_seen"] and b"Run /bin/sh as init process" in tail:
            state["shell_seen"] = True
        if state["shell_seen"] and not state["wdt_done"] and tail.rstrip().endswith(b"#"):
            ser.write(b"echo V > /dev/watchdog; echo WDT_STOPPED\n")
            state["wdt_done"] = True
            log.write(b"\n[daemon: sent watchdog magic close]\n")


def handle(conn):
    req = conn.recv(65536).decode()
    mode, timeout, cmd = req.split("\x00", 2)
    timeout = float(timeout)
    with lock:
        start = len(rx)
    if mode == "xmodem":                       # cmd = local file path
        ser.write(b"loadx 0x4c000000\n"); ser.flush()
        time.sleep(0.5)
        xfer.set(); time.sleep(0.05)
        try:
            res = xmodem_send(cmd)
        finally:
            xfer.clear()
        time.sleep(1.0)
        with lock:
            out = bytes(rx[start:])
        conn.sendall(out + f"\n[xmodem: {res}]".encode()); conn.close(); return
    if mode == "raw":
        ser.write(cmd.encode())
        time.sleep(timeout)
        with lock:
            out = bytes(rx[start:])
    else:
        if mode == "sh":
            tag = f"__E{int(time.time()*1000) % 100000}__"
            ser.write(f"{cmd}; echo {tag}$?\n".encode())
            done = re.compile(re.escape(tag).encode() + rb"\d+")
        else:  # uboot: pace input, U-Boot polls the UART and drops bursts
            b = cmd.encode() + b"\n"
            for i in range(0, len(b), 8):
                ser.write(b[i:i + 8]); ser.flush(); time.sleep(0.004)
            done = re.compile(rb"MT7981> $")
        t0 = time.time(); out = b""
        while time.time() - t0 < timeout:
            with lock:
                out = bytes(rx[start:])
            if done.search(out):
                break
            time.sleep(0.05)
        else:
            out += b"\n[!! TIMEOUT]"
    conn.sendall(out)
    conn.close()


threading.Thread(target=reader, daemon=True).start()
if os.path.exists(sock_path):
    os.unlink(sock_path)
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(sock_path); srv.listen(4)
log.write(b"\n[daemon: started]\n")
print("daemon ready", flush=True)
while True:
    c, _ = srv.accept()
    threading.Thread(target=handle, args=(c,), daemon=True).start()
