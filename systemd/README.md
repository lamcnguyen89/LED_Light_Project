# Raspberry Pi boot services

From the project root on the Raspberry Pi 3B+, run:

```bash
sudo bash systemd/install_ledart.sh
```

Use Raspberry Pi OS with Python 3.9 or newer and an internet connection for installation. The installer creates Input if needed; an empty library is supported.

The installer installs OS prerequisites, creates or reuses ledenv in the project root, installs requirements.txt, checks package imports, validates both systemd units, and enables and starts:

- ledart.service: runs LED_Code.py as root for GPIO18 PWM access.
- ledart-web.service: runs web_ui.py through Waitress as an unprivileged user on port 8080.

Both services retry failures after 10 seconds and send Python output to the system journal. The player watches Input every second and refreshes playback when media is added, replaced, or removed. With an empty folder, it waits for media.

## Open the media manager

Find the Pi's address with hostname -I, then open http://<pi-ip>:8080 in a browser on the same network. The WebUI provides an image/GIF gallery, animated GIF previews, first-frame 20×15 matrix previews, multiple-file uploads, and deletion with confirmation. It refreshes its gallery every five seconds.

Supported uploads: .bmp, .jpeg, .jpg, .png, .tif, .tiff, .webp, .gif. Each upload batch is limited to 32 MiB. Images are limited to 16 million pixels; animations to 500 frames and 100 million total frame pixels. Unsupported or corrupt uploads are rejected. Duplicate filenames receive a numbered suffix. Uploads are fully validated in temporary files before being published atomically into Input. Original media is preserved; browser previews of still images are converted to PNG. Animated WebP and multi-page TIFF play as stills in the LED player. Existing videos remain in the slideshow and are not managed by this UI.

This WebUI has no login and is intended for a trusted local network: anyone who can reach port 8080 can manage the images. Keep that port private.

## Service commands

```bash
sudo systemctl status ledart ledart-web
sudo journalctl -u ledart -f
sudo journalctl -u ledart-web -f
sudo systemctl restart ledart-web
sudo systemctl disable --now ledart-web
```

## WebUI user and permissions

The installer selects the sudo caller as the WebUI user, falling back to the project directory's owner. If that would be root, it creates or reuses a system account named ledart-web. Override the selection with an existing unprivileged user:

```bash
sudo LEDART_WEB_USER=pi bash systemd/install_ledart.sh
```

Replace pi with your actual account name. The installer changes only the Input directory's ownership to this user and grants the owner read/write/traverse permission. It preserves ownership of existing files. The account needs read access to existing media, the project, and the virtual environment, plus traverse access through the parent directories. The installer checks access to the application files before installing either service.

The WebUI uses ProtectSystem=full and ProtectHome=false: OS directories remain read-only, while Input under /home follows normal Unix permissions. The service still runs unprivileged with NoNewPrivileges, PrivateTmp, and PrivateDevices. This compatibility setting also allows writes to other locations the WebUI account owns; it does not restrict writes exclusively to Input. The LED service retains root access independently.

Before every service start, web_ui.py --check-storage creates hidden scratch files in Input and checks writing, chmod, hard-link publication, and deletion inside the actual service sandbox. A failure stops startup with the directory and error in the journal. Manual web_ui.py startup performs the same check. Scratch files are removed and existing media is preserved.

The installer installs /etc/systemd/system/ledart-web.service.d/zzz-ledart-storage.conf to supersede the earlier read-only storage policy and clear ReadWritePaths and BindPaths troubleshooting entries, including paths from an older checkout. Existing override files and host/port settings are preserved. Reinstalling from a fresh clone updates these service settings; recloning alone does not update /etc/systemd/system.

If startup fails, run sudo journalctl --no-pager -u ledart-web -n 40. A genuinely read-only underlying filesystem or another later override must still be corrected.

## Change host or port

The default bind address is 0.0.0.0 (all interfaces), port 8080. Use a systemd override to change it:

```bash
sudo systemctl edit ledart-web
```

```ini
[Service]
Environment=LEDART_WEB_HOST=0.0.0.0
Environment=LEDART_WEB_PORT=8081
```

```bash
sudo systemctl daemon-reload
sudo systemctl restart ledart-web
```

Host/port overrides survive rerunning the installer. The installer manages storage restrictions separately as described above. A host of 127.0.0.1 restricts access to the Pi itself.

## Updating or moving the project

Rerun the installer after dependencies change. If you move the project, recreate ledenv at the new location and rerun the installer so both service paths are updated. The project path may contain letters, digits, spaces, underscores, hyphens, slashes, and periods.

The templates are systemd/ledart.service and systemd/ledart-web.service. The installer replaces @PROJECT_ROOT@ in both units and @WEB_USER@ in the WebUI unit before validating and installing them into /etc/systemd/system.

On 32-bit Raspberry Pi OS, OpenCV may need to compile if a compatible wheel is unavailable; installation can take a long time on a Pi 3B+. The installer does not change boot/audio configuration: GPIO18 PWM must be available to the LED driver.

## Run the WebUI without systemd

From the project root:

```bash
ledenv/bin/python web_ui.py
# Optional local-only bind for development:
ledenv/bin/python web_ui.py --host 127.0.0.1 --port 8080
# Run file-operation tests without requiring LED hardware:
ledenv/bin/python -B -m unittest discover -s tests -v
```
