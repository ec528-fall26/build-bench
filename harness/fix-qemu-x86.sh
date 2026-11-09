#!/usr/bin/env bash
# Replace the Ubuntu-packaged QEMU 8.2 x86 user-mode handlers (which crash with
# "QEMU internal SIGSEGV" on x86 grep, killing every amd64/i386 validator build
# on this arm64 host) with QEMU 10.2 from the tonistiigi/binfmt image, and make
# that survive reboots. Run as: sudo bash ~/fix-qemu.sh      Undo: sudo bash ~/fix-qemu.sh --undo
set -euo pipefail
UNIT=/etc/systemd/system/bb-qemu-binfmt.service
if [ "${1:-}" = --undo ]; then
  systemctl disable --now bb-qemu-binfmt.service 2>/dev/null || true; rm -f "$UNIT"; systemctl daemon-reload
  docker run --privileged --rm tonistiigi/binfmt --uninstall qemu-x86_64,qemu-i386 || true
  systemctl restart systemd-binfmt.service; echo "restored the Ubuntu handlers"; exit 0
fi
for h in qemu-x86_64 qemu-i386; do [ -e /proc/sys/fs/binfmt_misc/$h ] && echo -1 > /proc/sys/fs/binfmt_misc/$h; done
docker run --privileged --rm tonistiigi/binfmt --install amd64,386 >/dev/null
grep -q "^interpreter /usr/bin/qemu-x86_64" /proc/sys/fs/binfmt_misc/qemu-x86_64 && echo "qemu-x86_64 handler: QEMU 10.2 (tonistiigi/binfmt)"
cat > "$UNIT" <<UNIT
[Unit]
Description=Build-Bench: register QEMU 10.2 x86 user-mode handlers (Ubuntu 8.2 crashes on x86 grep)
After=docker.service systemd-binfmt.service
Requires=docker.service
[Service]
Type=oneshot
ExecStart=/bin/sh -c "for h in qemu-x86_64 qemu-i386; do [ -e /proc/sys/fs/binfmt_misc/\$h ] && echo -1 > /proc/sys/fs/binfmt_misc/\$h; done; docker run --privileged --rm tonistiigi/binfmt --install amd64,386"
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload && systemctl enable bb-qemu-binfmt.service >/dev/null && echo "enabled $UNIT (re-applies on boot)"
docker run --rm --platform linux/amd64 ubuntu:25.04 sh -c "echo abc | grep -c b" | grep -qx 1 && echo "x86 grep under emulation: OK"
