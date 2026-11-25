#!/usr/bin/env python3
"""Connect to the Deco's nc listeners (one port per mtd) and save each stream."""
import socket, sys, time, os, hashlib
HOST = os.environ.get("DECO_IP", "192.168.1.1")
OUT = sys.argv[1]
parts = [("mtd0_boot", 0x200000), ("mtd1_u-boot-env", 0x100000), ("mtd2_ubi0", 0x2a00000),
         ("mtd3_ubi1", 0x2a00000), ("mtd4_radio", 0x100000), ("mtd5_factory_data", 0x900000),
         ("mtd6_runtime_data", 0x1100000), ("mtd7_wholeflash", 0x7f80000)]
deadline = time.time() + float(sys.argv[2]) if len(sys.argv) > 2 else time.time() + 600
for n, (name, size) in enumerate(parts):
    port = 9000 + n
    while True:
        try:
            s = socket.create_connection((HOST, port), timeout=2); break
        except OSError:
            if time.time() > deadline: sys.exit(f"gave up waiting for {name}")
            time.sleep(0.5)
    s.settimeout(30); h = hashlib.sha256(); got = 0; t0 = time.time()
    with open(os.path.join(OUT, name + ".bin"), "wb") as f:
        while True:
            d = s.recv(1 << 20)
            if not d: break
            f.write(d); h.update(d); got += len(d)
    s.close()
    dt = time.time() - t0
    ok = "OK" if got == size else f"SIZE MISMATCH (want {size})"
    print(f"{name:20} {got:10d} bytes {dt:5.1f}s {got/dt/1e6 if dt else 0:6.1f} MB/s  {h.hexdigest()}  {ok}", flush=True)
# tell the Deco we are done (inetd port 9099 runs reboot -f)
try:
    socket.create_connection((HOST, 9099), timeout=3).close(); print("sent reboot trigger")
except OSError as e:
    print("reboot trigger failed:", e)

