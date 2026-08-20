"""
sync_time.py - Sync your PC's clock to the ESP32-C3 cube clock over USB serial.

Requirements:
    pip install pyserial

Usage:
    python sync_time.py                 # auto-detect the port, sync once, exit
    python sync_time.py COM5            # use a specific port
    python sync_time.py COM5 --watch    # re-sync every hour (handy if you leave it running)
    python sync_time.py --list          # just list available serial ports and exit

The board should be running the matching sketch (telegrama.ino), which listens
for a line of the form:

    SETTIME HH:MM:SS\n

and replies with either:

    OK HH:MM:SS      (accepted)
    ERR bad time...  (rejected)
"""

import sys
import time
import argparse
from datetime import datetime

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("pyserial is required. Install it with:\n    pip install pyserial")
    sys.exit(1)

BAUD_RATE = 115200
SERIAL_TIMEOUT = 3  # seconds to wait for a response after sending the time


def list_serial_ports():
    ports = list(list_ports.comports())
    if not ports:
        print("No serial ports found.")
        return []
    print("Available serial ports:")
    for p in ports:
        desc = p.description or "Unknown device"
        print(f"  {p.device:<10} {desc}")
    return ports


def guess_port():
    """
    Try to find a likely ESP32 board automatically by looking at common
    USB-serial chip descriptions used on ESP32-C3 boards (CP210x, CH340, etc).
    Falls back to the first available port if nothing obviously matches.
    """
    ports = list(list_ports.comports())
    if not ports:
        return None

    keywords = ["CP210", "CH340", "CH9102", "USB-SERIAL", "USB Serial", "ESP32", "UART"]
    for p in ports:
        desc = (p.description or "") + " " + (p.manufacturer or "")
        if any(k.lower() in desc.lower() for k in keywords):
            return p.device

    # nothing matched a known chip description; just take the first port
    return ports[0].device


def sync_time(port_name, verbose=True):
    now = datetime.now()
    time_str = now.strftime("%H:%M:%S")

    if verbose:
        print(f"Opening {port_name} at {BAUD_RATE} baud...")

    try:
        with serial.Serial(port_name, BAUD_RATE, timeout=SERIAL_TIMEOUT) as ser:
            # give the board a moment; many boards reset on serial open
            time.sleep(2)
            ser.reset_input_buffer()

            command = f"SETTIME {time_str}\n"
            ser.write(command.encode("ascii"))

            if verbose:
                print(f"Sent: {command.strip()}")

            deadline = time.time() + SERIAL_TIMEOUT
            reply = ""
            while time.time() < deadline:
                line = ser.readline().decode(errors="replace").strip()
                if line:
                    reply = line
                    if reply.startswith("OK") or reply.startswith("ERR"):
                        break

            if verbose:
                if reply.startswith("OK"):
                    print(f"Board confirmed: {reply}")
                elif reply.startswith("ERR"):
                    print(f"Board rejected the time: {reply}")
                elif reply:
                    print(f"Board said: {reply}")
                else:
                    print("No response from board (command was still sent).")

            return reply.startswith("OK")

    except serial.SerialException as e:
        print(f"Serial error on {port_name}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Sync PC time to the ESP32 cube clock.")
    parser.add_argument("port", nargs="?", default=None,
                         help="Serial port, e.g. COM5. Auto-detected if omitted.")
    parser.add_argument("--list", action="store_true", help="List available serial ports and exit.")
    parser.add_argument("--watch", action="store_true",
                         help="Keep running and re-sync once every hour.")
    parser.add_argument("--interval", type=int, default=3600,
                         help="Seconds between syncs when --watch is used (default: 3600).")
    args = parser.parse_args()

    if args.list:
        list_serial_ports()
        return

    port_name = args.port or guess_port()
    if not port_name:
        print("Could not find a serial port. Plug in the board, or check Device Manager,")
        print("then run again with the port explicitly, e.g.:  python sync_time.py COM5")
        list_serial_ports()
        sys.exit(1)

    if not args.port:
        print(f"Auto-detected port: {port_name}  (use --list to see all ports)")

    ok = sync_time(port_name)

    if args.watch:
        print(f"Watching... re-syncing every {args.interval} seconds. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(args.interval)
                sync_time(port_name)
        except KeyboardInterrupt:
            print("\nStopped.")

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
