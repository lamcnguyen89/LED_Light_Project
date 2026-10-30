"""Start the LED player and web server, or run one component for systemd."""
import argparse
import os
from pathlib import Path
import pwd
import signal
import subprocess
import sys
from threading import Event

PROJECT_ROOT = Path(__file__).resolve().parent


def web_credentials(username=None):
    """Keep web and alias subprocesses unprivileged when the launcher is root."""
    if os.geteuid() != 0:
        if username:
            raise ValueError("--web-user requires running the launcher as root.")
        return {}
    username = username or os.environ.get("LEDART_WEB_USER") or os.environ.get("SUDO_USER")
    if not username:
        username = pwd.getpwuid(PROJECT_ROOT.stat().st_uid).pw_name
    if username == "root":
        username = "ledart-web"
    try:
        account = pwd.getpwnam(username)
    except KeyError as exc:
        raise ValueError(
            f"Web user {username!r} does not exist. Run systemd/install_ledart.sh "
            "or supply --web-user with an existing unprivileged account."
        ) from exc
    if account.pw_uid == 0:
        raise ValueError("The web server must run as an unprivileged user.")
    return {
        "user": account.pw_uid,
        "group": account.pw_gid,
        "extra_groups": os.getgrouplist(account.pw_name, account.pw_gid),
    }


def stop_children(children):
    # Each child has its own process group, including any Avahi publisher it starts.
    # Signal every group before waiting, so all components stop together.
    for child in children:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for child in children:
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=5)


def run_all(web_arguments, username=None):
    from shared.config import INPUT_FOLDER, read_environment
    from web_server.network_alias import normalize_alias

    if os.geteuid() != 0:
        raise ValueError(
            "LED playback requires root for GPIO18. Use sudo ledenv/bin/python main.py, "
            "or run only the web server with --service web."
        )
    credentials = web_credentials(username)
    alias = normalize_alias(read_environment().get("LEDART_WEB_ALIAS", ""))

    # For manual startup before installation, give a newly created media folder
    # to the web user. Existing media and permissions are left to the installer.
    try:
        INPUT_FOLDER.mkdir()
    except FileExistsError:
        pass
    else:
        os.chown(INPUT_FOLDER, credentials["user"], credentials["group"])

    services = [("web", web_arguments, credentials), ("led", [], {})]
    if alias:
        services.append(("alias", [], credentials))
    children = []
    stopped = Event()
    previous_handlers = {}
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, lambda *_: stopped.set())
        for service, arguments, identity in services:
            command = [
                sys.executable, "-u", str(PROJECT_ROOT / "main.py"),
                "--service", service, *arguments,
            ]
            child = subprocess.Popen(
                command, cwd=PROJECT_ROOT, start_new_session=True, **identity,
            )
            children.append((service, child))
            print(f"Started {service} (PID {child.pid}).", flush=True)
        while not stopped.wait(0.2):
            for service, child in children:
                status = child.poll()
                if status is not None:
                    print(f"{service} exited with status {status}; stopping all components.", file=sys.stderr)
                    return status if status > 0 else 1
        return 0
    finally:
        try:
            stop_children([child for _, child in children])
        finally:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--service", choices=("all", "web", "led", "alias"), default="all",
        help="Component to run (default: all; includes the alias when configured).",
    )
    parser.add_argument("--host", help="Override the web server bind address.")
    parser.add_argument("--port", type=int, help="Override the web server port.")
    parser.add_argument("--web-user", help="Unprivileged account for web/alias children in all mode.")
    parser.add_argument("--check-storage", action="store_true", help="Check web storage and exit (--service web).")
    parser.add_argument("--check-config", action="store_true", help="Check alias configuration and exit (--service alias).")
    args = parser.parse_args(argv)
    if args.check_storage and args.service != "web":
        parser.error("--check-storage requires --service web")
    if args.check_config and args.service != "alias":
        parser.error("--check-config requires --service alias")
    if (args.host is not None or args.port is not None) and args.service not in ("all", "web"):
        parser.error("--host and --port apply only to --service web or all")
    if args.web_user and args.service != "all":
        parser.error("--web-user applies only to --service all")
    web_arguments = []
    if args.host is not None:
        web_arguments.extend(["--host", args.host])
    if args.port is not None:
        web_arguments.extend(["--port", str(args.port)])
    try:
        # Lazy imports let --help and web-only startup work without GPIO libraries.
        if args.service == "web":
            from web_server.app import main as run_web
            if args.check_storage:
                web_arguments.append("--check-storage")
            run_web(web_arguments)
        elif args.service == "led":
            from led_backend.player import main as run_led
            run_led()
        elif args.service == "alias":
            from web_server.network_alias import main as run_alias
            run_alias(["--check-config"] if args.check_config else [])
        else:
            return run_all(web_arguments, args.web_user)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Startup failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
