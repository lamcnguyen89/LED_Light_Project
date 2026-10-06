# Raspberry Pi boot service

From the project root on the Raspberry Pi 3B+, run:

```bash
sudo bash systemd/install_ledart.sh
```

Use Raspberry Pi OS with Python 3.9 or newer and an internet connection for installation. Put supported images, GIFs, or videos in the project's `Input` directory before running the installer.

The installer installs OS prerequisites, creates `ledenv` in the project root (or reuses it), installs `requirements.txt`, checks package imports, validates and installs `/etc/systemd/system/ledart.service`, and enables and starts it. The service runs `LED_Code.py` using the environment's Python as root for GPIO18 PWM access. Failures are retried after 10 seconds; Python output goes to the system journal. With an empty `Input` directory, the player exits and retries until media is added.

```bash
sudo systemctl status ledart
sudo journalctl -u ledart -f
sudo systemctl restart ledart
sudo systemctl disable --now ledart
```

Rerun the installer after requirements change. If you move the project, recreate `ledenv` at the new location and rerun the installer so the service paths are updated. The project path may contain letters, digits, spaces, underscores, hyphens, slashes, and periods.

On 32-bit Raspberry Pi OS, OpenCV may need to compile if a compatible wheel is unavailable; installation can take a long time on a Pi 3B+. The installer does not change boot/audio configuration: GPIO18 PWM must be available to the LED driver.

The service definition lives in `systemd/ledart.service`. Edit that file to customize the service, then rerun the installer. The installer replaces `@PROJECT_ROOT@` with the project's absolute path in a temporary copy before validating and installing it.
