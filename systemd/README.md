# Raspberry Pi boot services

From the project root on the Raspberry Pi 3B+, run:

```bash
sudo bash systemd/install_ledart.sh
```

Use Raspberry Pi OS with Python 3.9 or newer and an internet connection for installation. The installer creates Input if needed; an empty library is supported.

The installer installs OS prerequisites (including avahi-daemon, avahi-utils, and
iproute2), creates or reuses ledenv in the project root, installs requirements.txt
(including python-dotenv), checks package imports, validates all three systemd
units, and enables and starts:

- ledart.service: runs main.py --service led (led_backend/player.py) as root for GPIO18 PWM access.
- ledart-web.service: runs main.py --service web (web_server/app.py) through Waitress as an unprivileged user on port 8080.
- ledart-alias.service: runs main.py --service alias (web_server/network_alias.py), advertising the .env hostname through Avahi as the same unprivileged user.

All components share the root main.py entry point. The boot services use its
individual component modes to retain separate users and restart policies.
The foreground launcher, `sudo ledenv/bin/python main.py`, starts them together
without requiring systemd; see manual startup below.

All three services retry failures after 10 seconds and send Python output to the
system journal. Avahi is also enabled at boot. A blank alias makes ledart-alias
exit successfully and remain inactive. The player watches Input every second and
refreshes playback when media is added, replaced, or removed. With an empty
folder, it waits for media.

## Open the media manager

Find the Pi's address with hostname -I, then open http://<pi-ip>:8080 in a browser on the same network. The WebUI provides an image/GIF gallery, animated GIF previews, first-frame 20×15 matrix previews, multiple-file uploads, and deletion with confirmation. It refreshes its gallery every five seconds.

With the default alias configuration, http://ledmatrix.local:8080 also works. See
the .env configuration below to customize it.

Supported uploads: .bmp, .jpeg, .jpg, .png, .tif, .tiff, .webp, .gif. Each upload batch is limited to 32 MiB. Images are limited to 16 million pixels; animations to 500 frames and 100 million total frame pixels. Unsupported or corrupt uploads are rejected. Duplicate filenames receive a numbered suffix. Uploads are fully validated in temporary files before being published atomically into Input. Original media is preserved; browser previews of still images are converted to PNG. Animated WebP and multi-page TIFF play as stills in the LED player. Existing videos remain in the slideshow and are not managed by this UI.

This WebUI has no login and is intended for a trusted local network: anyone who can reach port 8080 can manage the images. Keep that port private.

## Service commands

```bash
sudo systemctl status ledart ledart-web
sudo systemctl status ledart-alias
sudo journalctl -u ledart -f
sudo journalctl -u ledart-web -f
sudo journalctl -u ledart-alias -f
sudo systemctl restart ledart-web
sudo systemctl disable --now ledart-web
```

## WebUI user and permissions

The installer selects the sudo caller as the WebUI user, falling back to the project directory's owner. If that would be root, it creates or reuses a system account named ledart-web. Override the selection with an existing unprivileged user:

```bash
sudo LEDART_WEB_USER=pi bash systemd/install_ledart.sh
```

Replace pi with your actual account name. The installer changes only the Input directory's ownership to this user and grants the owner read/write/traverse permission. It preserves ownership of existing files. The account needs read access to existing media, the project, .env, and the virtual environment, plus traverse access through the parent directories. The installer checks access to the application files and validates the alias before installing the services. A newly created .env is owned by this user with mode 0600; existing .env files and their permissions are preserved.

The WebUI uses ProtectSystem=full and ProtectHome=false: OS directories remain read-only, while Input under /home follows normal Unix permissions. The service still runs unprivileged with NoNewPrivileges, PrivateTmp, and PrivateDevices. This compatibility setting also allows writes to other locations the WebUI account owns; it does not restrict writes exclusively to Input. The LED service retains root access independently.

Before every WebUI service start, main.py --service web --check-storage creates hidden scratch files in Input and checks writing, chmod, hard-link publication, and deletion inside the actual service sandbox. A failure stops startup with the directory and error in the journal. Manual WebUI startup performs the same check. Scratch files are removed and existing media is preserved.

The installer installs /etc/systemd/system/ledart-web.service.d/zzz-ledart-storage.conf to supersede the earlier read-only storage policy and clear ReadWritePaths and BindPaths troubleshooting entries, including paths from an older checkout. Existing override files and host/port settings are preserved. Reinstalling from a fresh clone updates these service settings; recloning alone does not update /etc/systemd/system.

If startup fails, run sudo journalctl --no-pager -u ledart-web -n 40. A genuinely read-only underlying filesystem or another later override must still be corrected.

## Configure the alias, host, or port in .env

Both web_server/app.py and web_server/network_alias.py read .env from the project root, including
when started manually from a different working directory. The installer creates
it from .env.example only if it does not already exist. Existing configuration is
never overwritten. For example:

```dotenv
LEDART_WEB_ALIAS=ledmatrix
LEDART_WEB_HOST=0.0.0.0
LEDART_WEB_PORT=8080
```

Open http://ledmatrix.local:8080 from a device on the same LAN that supports mDNS.
The alias may be a hostname such as matrix or matrix.local, using 1–63 ASCII
letters, digits, or hyphens, and starting and ending with a letter or digit.
Choose a name unique on your network. An absent or blank LEDART_WEB_ALIAS disables
advertising. It does not change the Pi's system hostname or its existing address.

After editing the alias, restart its service. Restart the WebUI after changing
the bind address or port. No daemon-reload or reinstall is needed for .env edits:

```bash
sudo systemctl restart ledart-alias
sudo systemctl restart ledart-web
```

The alias service advertises the first active interface's global-scope IPv4
address, checks every 10 seconds for DHCP changes or disconnections, and replaces
or withdraws the advertisement as needed. It waits if no address is available.
Avahi reconnects if its daemon restarts. This requires IPv4 and multicast mDNS
access on the LAN; it does not publish a public internet address or a bare name
without .local. Use the Pi IP if the client or network does not support mDNS.
On a Pi connected to multiple networks, the advertised IPv4 must be reachable by
the client. If advertising fails or the name conflicts, inspect:

```bash
sudo journalctl -u ledart-alias -u avahi-daemon -n 40 --no-pager
```

The bind address defaults to 0.0.0.0 (all interfaces), port 8080. The bind address
controls where Waitress listens; it does not create a DNS name. A host of
127.0.0.1 restricts access to the Pi itself even if an alias is advertised.

Command-line --host/--port options take precedence over process environment
variables, which take precedence over .env, then application defaults. Existing
systemd host/port overrides still work and take precedence over .env. To change
them, use:

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

Host/port overrides survive rerunning the installer. Remove those overrides if
you want .env to control the same settings. The installer manages storage
restrictions separately as described above.

## Updating or moving the project

Rerun the installer after dependencies change. If you move the project, recreate ledenv at the new location and rerun the installer so all service paths are updated. The project path may contain letters, digits, spaces, underscores, hyphens, slashes, and periods.

When upgrading from the older flat layout, copy the new web_server, led_backend,
and shared packages and root main.py, then rerun the installer. Restarting old
installed units alone will still point to the removed files. Keep the existing
project-root Input folder and .env in place.

The templates are systemd/ledart.service, systemd/ledart-web.service, and
systemd/ledart-alias.service. The installer replaces @PROJECT_ROOT@ in all three
units and @WEB_USER@ in the WebUI and alias units before validating and installing
them into /etc/systemd/system.

On 32-bit Raspberry Pi OS, OpenCV may need to compile if a compatible wheel is unavailable; installation can take a long time on a Pi 3B+. The installer does not change boot/audio configuration: GPIO18 PWM must be available to the LED driver.

## Run manually through main.py

From the project root:

```bash
# Stop installed instances before running another copy against the same LEDs/port:
sudo systemctl stop ledart ledart-web ledart-alias
# Run both components and the configured alias in the foreground:
sudo ledenv/bin/python main.py
```

Ctrl+C or SIGTERM stops all children, including Avahi subprocesses. If any child
exits unexpectedly, the launcher stops its siblings and returns a nonzero status.
The launcher gives only the LED child root access; it runs the web and alias
children as LEDART_WEB_USER (process environment), the sudo caller, or the project
owner, falling back to ledart-web when that selection is root. Override this with
`sudo ledenv/bin/python main.py --web-user pi`. Run the installer first to ensure
the selected user can read .env and write Input. A new Input folder created by
the launcher is owned by the selected user; existing permissions are preserved.
The manual launcher reads .env and its own environment, not systemd overrides.

Run individual components for development or diagnostics:

```bash
ledenv/bin/python main.py --service web
# Optional local-only bind for development:
ledenv/bin/python main.py --service web --host 127.0.0.1 --port 8080
# LED hardware requires the Pi and root access:
sudo ledenv/bin/python main.py --service led
# In a separate terminal on the Pi (requires avahi-daemon, avahi-utils, iproute2):
ledenv/bin/python main.py --service alias
# Validate .env's alias without starting network advertising:
ledenv/bin/python main.py --service alias --check-config
# Check upload permissions without starting the web server:
ledenv/bin/python main.py --service web --check-storage
# Run file-operation tests without requiring LED hardware:
ledenv/bin/python -B -m unittest discover -s tests -v
```

After stopping a manual run, restore the installed services with
`sudo systemctl start ledart ledart-web ledart-alias`.
