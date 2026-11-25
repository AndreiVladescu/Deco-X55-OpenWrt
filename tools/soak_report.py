#!/usr/bin/env python3
"""Summarise a tools/soak2.sh run:  python3 tools/soak_report.py backup/soak2_v0.1"""
import csv, re, statistics as st, sys, os, collections

d = sys.argv[1]
rows = list(csv.DictReader(open(os.path.join(d, "samples.csv"))))

def num(v):
    try: return float(v)
    except (TypeError, ValueError): return None

def slope_per_hour(pts):          # least squares, pts = [(t_seconds, value)]
    if len(pts) < 3: return 0.0
    ts = [p[0] for p in pts]; vs = [p[1] for p in pts]
    mt, mv = st.mean(ts), st.mean(vs)
    den = sum((t - mt) ** 2 for t in ts)
    return 0.0 if den == 0 else sum((t - mt) * (v - mv) for t, v in zip(ts, vs)) / den * 3600

print(f"# Soak report: {d}\n")
for unit in ("main", "satellite"):
    u = [r for r in rows if r["unit"] == unit]
    ok = [r for r in u if r.get("uptime_s") not in (None, "", "UNREACHABLE")]
    unreach = len(u) - len(ok)
    up = [num(r["uptime_s"]) for r in ok]
    reboots = sum(1 for a, b in zip(up, up[1:]) if b < a)
    hours = (up[-1] - up[0]) / 3600 if len(up) > 1 and not reboots else len(u) / 60
    print(f"## {unit}: {len(u)} samples (~{hours:.1f} h), unreachable {unreach}, **reboots {reboots}**")
    if not ok: continue
    t0 = up[0]; tt = [x - t0 for x in up]
    def series(col): return [(t, num(r[col])) for t, r in zip(tt, ok) if num(r[col]) is not None]
    mem = series("mem_avail_kB")
    print(f"* MemAvailable: start {mem[0][1]/1024:.0f} MiB, min {min(v for _,v in mem)/1024:.0f} MiB, "
          f"end {mem[-1][1]/1024:.0f} MiB, trend {slope_per_hour(mem):+.0f} kB/h")
    for p in ("hostapd", "usteerd", "netifd", "dnsmasq", "rpcd", "uhttpd"):
        s = series(f"rss_{p}")
        if s: print(f"  * {p:8} RSS {s[0][1]:.0f} → {s[-1][1]:.0f} kB (trend {slope_per_hour(s):+.1f} kB/h)")
    temp = [v / 1000 for _, v in series("temp_mC")]
    print(f"* SoC temperature: min {min(temp):.1f} °C, avg {st.mean(temp):.1f} °C, max {max(temp):.1f} °C")
    load = [v for _, v in series("load1")]
    print(f"* load1: avg {st.mean(load):.2f}, max {max(load):.2f}")
    for col in ("mesh_tx", "mesh_rx"):
        c = collections.Counter(r[col] for r in ok)
        print(f"* {col} PHY rates (Mbit/s): " + ", ".join(f"{k}×{v}" for k, v in c.most_common(6)))
    tq = [v for _, v in series("batman_tq")]
    print(f"* batman TQ: min {min(tq):.0f}, avg {st.mean(tq):.0f}; samples with TQ < 200: {sum(1 for x in tq if x < 200)}")
    sig = [v for _, v in series("mesh_signal")]
    print(f"* mesh signal: {min(sig):.0f} … {max(sig):.0f} dBm")
    ecc_c = [v for _, v in series("ecc_corrected")]; ecc_f = [v for _, v in series("ecc_failed")]
    print(f"* NAND ECC: corrected {ecc_c[0]:.0f} → {ecc_c[-1]:.0f}, failed {ecc_f[0]:.0f} → {ecc_f[-1]:.0f}")
    ov = [v for _, v in series("overlay_used_kB")]
    print(f"* overlay used: {ov[0]:.0f} → {ov[-1]:.0f} kB; processes {series('procs')[0][1]:.0f} → {series('procs')[-1][1]:.0f}")
    print()

ip = list(csv.DictReader(open(os.path.join(d, "iperf.csv"))))
print("## iperf3 PC → main (wire) → mesh → satellite, 60 s, 4 streams")
for dirn in ("PC->sat", "sat->PC"):
    vals = [num(r["mbit"]) for r in ip if r["direction"] == dirn]
    good = [v for v in vals if v is not None]
    if good:
        print(f"* {dirn}: {len(good)} runs, min {min(good):.0f}, median {st.median(good):.0f}, max {max(good):.0f} Mbit/s, "
              f"failed {len(vals) - len(good)}")
print()

print("## ping from the PC (1/s)")
for unit in ("main", "satellite"):
    f = os.path.join(d, f"ping_{unit}.log")
    if not os.path.exists(f): continue
    txt = open(f).read()
    rtts = [float(x) for x in re.findall(r"time=([\d.]+) ms", txt)]
    lost = len(re.findall(r"no answer yet", txt))
    total = len(rtts) + lost
    if total:
        print(f"* {unit}: {total} pings, lost {lost} ({100*lost/total:.3f} %), "
              f"RTT median {st.median(rtts):.2f} ms, p99 {sorted(rtts)[int(len(rtts)*0.99)-1]:.2f} ms, max {max(rtts):.1f} ms")
print()

errs = open(os.path.join(d, "errors.log")).read().strip().splitlines()
print(f"## new log/kernel errors: {len(errs)} distinct lines")
for l in errs[:40]:
    print(f"    {l[:160]}")
