#!/usr/bin/env python3
"""Put the MT7981 UART back to stock 115200 (quot=2, sample_count=0xad, point=0x56) from 1 Mbaud."""
import os, serial, time
# Serial adapter: $DECO_TTY, else the first FTDI adapter by id. By id on purpose: other USB
# serial devices (headset dongles, ...) must never be opened by accident.
import glob
PORT = os.environ.get("DECO_TTY") or next(iter(sorted(glob.glob("/dev/serial/by-id/usb-FTDI_*"))), None)
if not PORT:
    raise SystemExit("no FTDI serial adapter found; set DECO_TTY=/dev/serial/by-id/...")
s = serial.Serial(PORT, 1_000_000, timeout=0.1)
s.write(b"echo ping_ok\n"); time.sleep(0.4)
if b"ping_ok" not in s.read(4096):
    raise SystemExit("not at 1 Mbaud U-Boot prompt")
line = b"mw.l 0x1100200c 0x83; mw.l 0x11002000 2; mw.l 0x11002004 0; mw.l 0x1100200c 3; mw.l 0x11002028 0xad; mw.l 0x1100202c 0x56"
for i in range(0, len(line), 8):
    s.write(line[i:i+8]); s.flush(); time.sleep(0.003)
s.write(b"\n"); s.flush(); time.sleep(0.3)
s.baudrate = 115200; time.sleep(0.2); s.reset_input_buffer()
s.write(b"echo ping_ok\n"); time.sleep(0.5)
print("restored 115200" if b"ping_ok\r\nMT7981>" in s.read(4096) else "restore FAILED")
