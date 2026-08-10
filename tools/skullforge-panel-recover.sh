#!/bin/bash
# Power-cycles the TFT panel and fixes permissions on its device node.
#
# Uses per-port USB runtime suspend/resume on the panel's own port, NOT a
# controller-wide xhci_hcd unbind/rebind. The difference matters: unbind/
# rebind only resets the USB controller's logical connection and does NOT
# actually cut power to the port. Runtime suspend of the specific port DOES
# cut real power.
#
# The port's sysfs path is machine-specific (it's a fixed physical USB
# port), so it's passed as $1 - SkullForge's recovery.py always passes the
# user's configured usb_port_path. Falls back to a common default if
# called with no argument.
#
# Installed to /usr/local/sbin/ and run via a scoped NOPASSWD sudoers rule
# - see packaging/install-system-files.sh.
set -euo pipefail

USB_PORT_PATH="${1:-/sys/bus/usb/devices/1-8}"
VID_PID="04d9:fd01"

if [ ! -d "$USB_PORT_PATH" ]; then
    echo "USB port path $USB_PORT_PATH does not exist on this machine." >&2
    echo "Find the right one with: lsusb -t   (then check /sys/bus/usb/devices/<bus>-<port>)" >&2
    exit 1
fi

echo "Suspending panel's USB port to cut power..."
echo -n auto > "$USB_PORT_PATH/power/control"

STATUS=""
for _ in $(seq 1 15); do
    sleep 1
    STATUS=$(cat "$USB_PORT_PATH/power/runtime_status" 2>/dev/null || echo "")
    if [ "$STATUS" = "suspended" ]; then
        break
    fi
done
if [ "$STATUS" != "suspended" ]; then
    echo "Port never suspended (status: $STATUS) - power cycle may not have worked!" >&2
fi

echo "Holding power off for 3s..."
sleep 3

echo "Resuming panel's USB port..."
echo -n on > "$USB_PORT_PATH/power/control"

STATUS=""
for _ in $(seq 1 15); do
    sleep 1
    STATUS=$(cat "$USB_PORT_PATH/power/runtime_status" 2>/dev/null || echo "")
    if [ "$STATUS" = "active" ]; then
        break
    fi
done
if [ "$STATUS" != "active" ]; then
    echo "Port never became active (status: $STATUS)!" >&2
    exit 1
fi

echo "Waiting for panel to appear..."
BUSDEV=""
for _ in $(seq 1 30); do
    sleep 1
    BUSDEV=$(lsusb -d "$VID_PID" 2>/dev/null | awk '{print $2"/"$4}' | tr -d ':' || true)
    if [ -n "$BUSDEV" ]; then
        break
    fi
done
if [ -z "$BUSDEV" ]; then
    echo "Panel ($VID_PID) not detected after power cycle (waited 30s)!" >&2
    exit 1
fi

NODE="/dev/bus/usb/$BUSDEV"
# Belt-and-suspenders: the udev rule (see data/udev/99-skullforge.rules)
# should already grant access without this, but chmod as a fallback in
# case the udev rule isn't installed or the device re-enumerated before it
# applied.
chmod 666 "$NODE" 2>/dev/null || true
echo "Done. Panel node $NODE is now accessible."
