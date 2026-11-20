#!/bin/sh
# Run in Linux or WSL. No network or root privileges needed.
set -eu
cd "$(dirname "$0")"
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
install -d "$stage/DEBIAN" "$stage/usr/lib/wb-mqtt-dreame/vendor" "$stage/usr/share/wb-mqtt-dreame" "$stage/usr/share/doc/wb-mqtt-dreame" "$stage/usr/bin" "$stage/usr/lib/systemd/system" "$stage/usr/lib/sysusers.d" dist
install -m 0644 packaging/control "$stage/DEBIAN/"
install -m 0755 packaging/postinst packaging/prerm packaging/postrm "$stage/DEBIAN/"
install -m 0644 src/*.py "$stage/usr/lib/wb-mqtt-dreame/"
install -m 0644 src/vendor/*.py "$stage/usr/lib/wb-mqtt-dreame/vendor/"
install -m 0644 LICENSE "$stage/usr/share/doc/wb-mqtt-dreame/copyright"
install -m 0755 packaging/prepare-state "$stage/usr/lib/wb-mqtt-dreame/"
install -m 0755 packaging/wb-dreame "$stage/usr/bin/"
install -m 0644 packaging/*.service "$stage/usr/lib/systemd/system/"
install -D -m 0644 packaging/dreame-navigation.json "$stage/usr/share/wb-mqtt-homeui/custom-menu/dreame-navigation.json"
install -D -m 0644 packaging/dreame-nginx.conf "$stage/usr/share/wb-mqtt-homeui/nginx/includes/wb-mqtt-dreame.conf"
install -D -m 0644 packaging/wb-mqtt-dreame.schema.json "$stage/usr/share/wb-mqtt-confed/schemas/wb-mqtt-dreame.schema.json"
install -d "$stage/usr/share/wb-mqtt-dreame/web"
install -m 0644 web/*.html web/*.css web/*.js "$stage/usr/share/wb-mqtt-dreame/web/"
install -m 0644 packaging/default.json "$stage/usr/share/wb-mqtt-dreame/"
printf '{}\n' > "$stage/usr/share/wb-mqtt-dreame/empty.json"
printf 'u wb-dreame - "Dreame MQTT driver" /nonexistent\n' > "$stage/usr/lib/sysusers.d/wb-mqtt-dreame.conf"
version=$(sed -n 's/^Version: //p' packaging/control)
export SOURCE_DATE_EPOCH=1795132800
dpkg-deb --root-owner-group -Zxz --build "$stage" "dist/wb-mqtt-dreame_${version}_all.deb"
