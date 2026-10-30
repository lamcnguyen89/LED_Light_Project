# LED_Light_Project

An LED light strip arranged in a 20×15 matrix on a Raspberry Pi 3B+, with a Flask
media manager and a Python playback backend.

## Project layout

```text
main.py                    # Common entry point for all components
web_server/
    app.py                 # Flask routes and Waitress server
    network_alias.py       # Avahi .local hostname advertising
    templates/             # HTML
    static/                # CSS and JavaScript
led_backend/
    player.py              # GPIO configuration and playback loop
    media_player.py        # GIF and video playback
shared/
    config.py              # Project paths and .env loading
    settings.py            # Persistent brightness, duration, and pause settings
Input/                     # Media and .led-settings.json (excluded from Git)
.env                       # Local configuration (excluded from Git)
systemd/                   # Installer and boot services
tests/
```

Both components use the project-root `Input/` folder. The web server writes
media and settings there; the LED backend reads them without an HTTP connection
between the two processes.

## Start the application

Install dependencies and enable startup services on the Pi:

```bash
sudo bash systemd/install_ledart.sh
```

Rerun the installer after updating from the older flat file layout. It updates
the service commands to use `main.py`, preserving `.env` and existing media.

For manual startup, stop any installed instances first, then use the single entry
point to run both the web server and LED backend in the foreground:

```bash
sudo systemctl stop ledart ledart-web ledart-alias
sudo ledenv/bin/python main.py
```

The launcher also starts alias advertising when `LEDART_WEB_ALIAS` is set.
Ctrl+C stops all child processes. If a component exits unexpectedly, the launcher
stops the other components and exits with an error. The LED player runs as root
for GPIO18 access; web and alias processes run as the sudo caller. When starting
from a root login, use `--web-user pi` to select an existing unprivileged account,
or let the launcher use the project owner / installer-created `ledart-web` user.

Run individual components through the same entry point:

```bash
ledenv/bin/python main.py --service web
sudo ledenv/bin/python main.py --service led
ledenv/bin/python main.py --service alias
```

For automatic startup, the three systemd units call these same component modes,
retain their separate users, and restart failures independently. To switch back
after a manual run, stop the launcher, then run
`sudo systemctl start ledart ledart-web ledart-alias`.

## Playback

The player checks the project's `Input` folder every second. Added or replaced
images, GIFs, and videos interrupt the current display and play first. Removed
files are dropped from the slideshow. Each unchanged file plays for 600 seconds
by default (`DISPLAY_SECONDS` in `led_backend/player.py`). Adjust animation duration above
brightness in the WebUI (1 to 86400 seconds). The saved duration persists across
restarts and applies when the next image, GIF, or video starts. An empty folder
clears the panel and waits for new files.

After copying updated Python code to your Raspberry Pi, restart the service once:

```bash
sudo systemctl restart ledart
```

Future changes to files in `Input` are picked up automatically, without restarting
the service or rebooting. For large uploads, copy using an unsupported extension
(such as `.tmp`), then rename to the final filename when the upload finishes.

## Browser media manager

The installer updates the WebUI storage policy even if earlier troubleshooting overrides remain on the Pi. It checks actual upload file operations inside the service before reporting success. Existing files in `Input` are preserved by the installer; keep a copy if replacing your checkout because `Input` is excluded from Git.

Open `http://<pi-ip>:8080` from a browser on your local network. The Flask WebUI lets you preview images and animated GIFs, see first-frame 20×15 matrix previews, upload multiple files, and delete files with confirmation. Each card has a Pause/Unpause button: paused items remain in the library with their previews, but are skipped on the LED panel. Pause state survives restarts. Changes reach the player within about one second; if every item is paused, the panel clears. Upload batches have a 32 MiB limit; duplicate filenames are renamed rather than overwritten. Existing videos also appear with pause and delete controls; browser video previews depend on codec support. Video uploads are not supported by this UI.

The installer creates a project-root `.env` from `.env.example` if none exists. With the
default `LEDART_WEB_ALIAS=ledmatrix`, you can also open `http://ledmatrix.local:8080`
on the same local network using a device that supports mDNS. Change the alias in
`.env`, then run `sudo systemctl restart ledart-alias`. This adds an address
without renaming the Pi. Leave the alias blank to disable it. Existing `.env`
files are preserved; add `LEDART_WEB_ALIAS=ledmatrix` yourself if needed.

The separate `ledart-web` service uses Waitress and runs without root privileges.
For a password-only popup, set a password in the project-root `.env`:

```dotenv
LEDART_WEB_PASSWORD="your chosen password"
```

Restart with `sudo systemctl restart ledart-web`, or restart the foreground
launcher. Enter only your password; the app uses the admin account implicitly.
The library, previews, and control APIs require an authenticated session; CSRF
checks still apply to changes. Use **Lock** to end the session. Sessions expire
after 12 hours or when the web server restarts. A missing or blank password
disables sign-in. Password changes take effect after restarting the web server.

HTTP sends the password and session cookie without encryption, so
keep this on a trusted local network. See [systemd/README.md](systemd/README.md)
for permissions, service commands, configuration, and manual startup.
