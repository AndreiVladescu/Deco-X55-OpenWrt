#!/bin/sh
# Fresh OpenWrt checkout at the tested base commit + the Deco X55 v3 device patch series,
# plus this repository's packages (deco-mesh, deco-x55-defaults) as a local feed.
set -e
cd "$(dirname "$0")/.."
TOP=$(pwd)
BASE=$(cat openwrt-patches/BASE_COMMIT)
[ -d openwrt ] || git clone https://github.com/openwrt/openwrt.git openwrt
cd openwrt
git fetch -q origin
git checkout -q -B deco-x55-v3 "$BASE"
git am "$TOP"/openwrt-patches/*.patch
{ cat feeds.conf.default; echo "src-link deco $TOP/packages"; } > feeds.conf
./scripts/feeds update -a && ./scripts/feeds install -a
# package patches for feed packages (e.g. usteer: prefer 5 GHz roam targets)
for d in "$TOP"/openwrt-patches/*/; do
	pkg=$(basename "$d"); dst=$(find feeds -maxdepth 3 -type d -name "$pkg" -path "*/net/*" | head -1)
	[ -n "$dst" ] && mkdir -p "$dst/patches" && cp "$d"*.patch "$dst/patches/"
done
cp "$TOP/config/deco-x55-v3.seed" .config && make defconfig
cat <<'MSG'

Now:  make -j$(nproc)
Output: bin/targets/mediatek/filogic/*tplink_deco-x55-v3*
  -initramfs-kernel.bin   RAM boot (installation, recovery)
  -squashfs-sysupgrade.bin  install from the RAM-booted system / later upgrades
Optional raw UBI image (needs the OEM second U-Boot from your own dump):
  make TPLINK_DECO_X55_V3_SECONDUBOOT=$PWD/../oem/deco-x55-v3-seconduboot.bin
MSG
