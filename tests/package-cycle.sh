#!/bin/sh
# Disposable dpkg database and simulated persistent mount, without booting systemd.
set -eu
cd "$(dirname "$0")/.."
r=$(mktemp -d)
trap 'rm -rf "$r"' EXIT
sys="$r/root"
mkdir -p "$sys/var/lib/dpkg/info" "$sys/var/lib/dpkg/updates" "$sys/etc" "$sys/mnt/data/etc"
touch "$sys/var/lib/dpkg/status"
cp /usr/share/base-passwd/passwd.master "$sys/etc/passwd"
cp /usr/share/base-passwd/group.master "$sys/etc/group"
version=$(sed -n 's/^Version: //p' packaging/control)
deb=$(realpath -e "dist/wb-mqtt-dreame_${version}_all.deb")
pkg() {
    dpkg --root="$sys" --force-script-chrootless --force-not-root --force-depends "$@"
}
pkg -i "$deb"
d="$sys/mnt/data/etc/wb-mqtt-dreame"
test ! -e "$d/state/mqtt-legacy-preview17"
test "$(stat -c %a "$d/account.json")" = 600
test -f "$sys/usr/lib/systemd/system/wb-mqtt-dreame.service"
test -L "$sys/etc/systemd/system/multi-user.target.wants/wb-mqtt-dreame.service"
menu="$sys/usr/share/wb-mqtt-homeui/custom-menu/dreame-navigation.json"
test -f "$menu"
test -f "$sys/usr/share/wb-mqtt-homeui/nginx/includes/wb-mqtt-dreame.conf"
test -f "$sys/usr/share/wb-mqtt-confed/schemas/wb-mqtt-dreame.schema.json"
test -f "$sys/usr/share/wb-mqtt-dreame/web/index.html"
sed -i 's/30/55/' "$d/config.json"
printf '%s\n' '{"test":"credential-fixture"}' > "$d/account.json"
printf '%s\n' '{"test":"token-fixture"}' > "$d/state/session.json"
printf '%s\n' 'fixture' > "$d/underlay.jpg"
printf '%s\n' '{}' > "$d/underlay.json"
printf '%s\n' 'fixture' > "$d/state/jobs.sqlite3"
printf '%s\n' '{}' > "$d/state/underlay-alignment.json"
printf '%s\n' '{}' > "$d/state/underlay-footprint.json"
printf '%s\n' '"99bdf523e1514cf3b2080691f191b6f5"' > "$d/state/mqtt-owner.json"
printf '%s\n' '{"fixture":"Кухня"}' > "$d/state/room-names.json"
printf '%s\n' '{"settings":{"repeats":2}}' > "$d/state/entrance.json"
mkdir "$d/state/product-photos-v1"
printf 'fixture' > "$d/state/product-photos-v1/robot.webp"
(cd "$d" && sha256sum state/product-photos-v1/robot.webp state/entrance.json config.json account.json state/session.json underlay.jpg underlay.json state/jobs.sqlite3 state/underlay-alignment.json state/underlay-footprint.json state/mqtt-owner.json state/room-names.json) > "$r/before"
check() { (cd "$d" && sha256sum -c "$r/before"); }
pkg -i "$deb"
check
dpkg-deb -R "$deb" "$r/new"
sed -i '/^Version:/s/$/+test1/' "$r/new/DEBIAN/control"
dpkg-deb --root-owner-group -b "$r/new" "$r/next.deb"
pkg -i "$r/next.deb"
check
if [ "$version" != '0.1.0~preview17' ]; then
    test ! -e "$d/state/mqtt-legacy-preview17"
    sed -i '/^Version:/c\Version: 0.1.0~preview17' "$r/new/DEBIAN/control"
    dpkg-deb --root-owner-group -b "$r/new" "$r/legacy.deb"
    pkg -i "$r/legacy.deb"
    test ! -e "$d/state/mqtt-legacy-preview17"
    pkg -i "$deb"
    check
fi
test -f "$d/state/mqtt-legacy-preview17"
pkg -r wb-mqtt-dreame
check
test ! -e "$sys/usr/bin/wb-dreame"
test ! -e "$menu"
pkg -i "$deb"
check
# Replace rootfs and its dpkg database, keep the data tree.
mv "$sys" "$r/old"
mkdir -p "$sys/mnt" "$sys/var/lib/dpkg/updates" "$sys/var/lib/dpkg/info" "$sys/etc"
touch "$sys/var/lib/dpkg/status"
cp /usr/share/base-passwd/passwd.master "$sys/etc/passwd"
cp /usr/share/base-passwd/group.master "$sys/etc/group"
mv "$r/old/mnt/data" "$sys/mnt/data"
pkg -i "$deb"
check
touch "$d/local-note"
pkg -P wb-mqtt-dreame
test -f "$d/local-note"
test ! -e "$d/config.json"
test ! -e "$d/account.json"
test ! -e "$d/state/session.json"
test ! -e "$d/state/jobs.sqlite3"
test ! -e "$d/underlay.jpg"
test ! -e "$d/underlay.json"
test ! -e "$d/state/underlay-alignment.json"
test ! -e "$d/state/underlay-footprint.json"
test ! -e "$d/state/mqtt-owner.json"
test ! -e "$d/state/room-names.json"
test ! -e "$d/state/entrance.json"
test ! -e "$d/state/product-photos-v1"
test ! -e "$d/state/mqtt-legacy-preview17"
echo 'PASS install, reinstall, version upgrade, remove, reinstall, replacement rootfs, selective purge'
