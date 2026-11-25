#!/usr/bin/env python3
"""Client for serial_daemon.py.  con.py [--uboot|--raw] 'command' [timeout]"""
import os, socket, sys
SOCK = os.environ.get("DECO_SOCK", "/tmp/deco_console.sock")
args = sys.argv[1:]
mode = "sh"
if args and args[0] in ("--uboot", "--raw", "--xmodem"):
    mode = args.pop(0)[2:]
cmd = args[0]
timeout = args[1] if len(args) > 1 else "20"
if mode == "uboot" and not cmd.strip():
    sys.exit("refusing bare Enter: U-Boot would repeat the last command")
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect(SOCK)
s.sendall(f"{mode}\x00{timeout}\x00{cmd}".encode())
out = b""
while True:
    d = s.recv(65536)
    if not d:
        break
    out += d
print(out.decode(errors="replace").replace("\r", ""))
