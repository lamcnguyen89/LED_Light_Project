"""Advertise the configured .local hostname through Avahi on Raspberry Pi OS."""
import argparse
import ipaddress
import json
import re
import signal
import subprocess
from threading import Event

from shared.config import read_environment


def normalize_alias(value):
    """Accept one DNS hostname label, optionally followed by .local."""
    name = value.strip().lower()
    if not name:
        return ""
    name = name.removesuffix(".").removesuffix(".local")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", name):
        raise ValueError(
            "LEDART_WEB_ALIAS must be 1–63 letters, digits, or hyphens, "
            "starting and ending with a letter or digit (optional .local suffix)."
        )
    return name + ".local"


def get_ipv4_address():
    """Use the first active interface with a global-scope IPv4 address."""
    result = subprocess.run(
        ["ip", "-j", "-4", "address", "show", "up", "scope", "global"],
        check=True, capture_output=True, text=True, timeout=5,
    )
    for interface in json.loads(result.stdout):
        if interface.get("operstate") == "DOWN":
            continue
        for address in interface.get("addr_info", []):
            if address.get("family") == "inet" and address.get("scope") == "global":
                value = ipaddress.IPv4Address(address["local"])
                if not (value.is_loopback or value.is_link_local or value.is_unspecified):
                    return str(value)
    return None


def stop_publisher(publisher):
    if publisher is not None and publisher.poll() is None:
        publisher.terminate()
        try:
            publisher.wait(timeout=5)
        except subprocess.TimeoutExpired:
            publisher.kill()
            publisher.wait(timeout=5)


def publish_alias(hostname, stopped):
    publisher = None
    current_address = None
    print(f"Advertising {hostname}; waiting for a LAN IPv4 address if needed.", flush=True)
    try:
        while not stopped.is_set():
            if publisher is not None and publisher.poll() is not None:
                raise RuntimeError(
                    f"Avahi publisher exited with status {publisher.returncode}. "
                    "Check Avahi logs and choose a unique LEDART_WEB_ALIAS."
                )
            address = get_ipv4_address()
            if address != current_address:
                stop_publisher(publisher)
                publisher = None
                current_address = address
                if address is not None:
                    print(f"Publishing {hostname} -> {address}", flush=True)
                    # No reverse record: retain the Pi's original reverse hostname.
                    # Foreground mode lets systemd clean up the publisher on stop.
                    publisher = subprocess.Popen([
                        "avahi-publish-address", "--no-reverse", "--no-fail",
                        hostname, address,
                    ])
                else:
                    print("LAN IPv4 address lost; alias withdrawn until reconnected.", flush=True)
            stopped.wait(10)
    finally:
        stop_publisher(publisher)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-config", action="store_true", help="Validate the alias and exit.")
    args = parser.parse_args(argv)
    try:
        hostname = normalize_alias(read_environment().get("LEDART_WEB_ALIAS", ""))
    except ValueError as exc:
        parser.exit(1, f"{exc}\n")
    if args.check_config:
        print(f"Network alias: {hostname or 'disabled'}", flush=True)
        return
    if not hostname:
        print("Network alias disabled; set LEDART_WEB_ALIAS in the project .env.", flush=True)
        return

    stopped = Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())
    try:
        publish_alias(hostname, stopped)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Network alias failed: {exc}\n")


if __name__ == "__main__":
    main()
