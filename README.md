# LED_Light_Project

Code for getting an LED Light strip arranged in a 20x15 matrix to work on a Raspberry Pi 3B+

The player checks the project's `Input` folder every second. Added or replaced
images, GIFs, and videos interrupt the current display and play first. Removed
files are dropped from the slideshow. Each unchanged file plays for 600 seconds
(`DISPLAY_SECONDS` in `LED_Code.py`). An empty folder waits for new files while
keeping the last displayed frame on the LEDs.

After copying updated Python code to your Raspberry Pi, restart the service once:

```bash
sudo systemctl restart ledart
```

Future changes to files in `Input` are picked up automatically, without restarting
the service or rebooting. For large uploads, copy using an unsupported extension
(such as `.tmp`), then rename to the final filename when the upload finishes.

To manually start the code:

```bash
# Command to Start LED Virtual Environment. The actual path the the env will change based on where you installed the virtual environment.
source ledenv/bin/activate

# Command to Start Script after startine Virtual Environment
sudo /home/malneyugnfl/ledenv/bin/python3 LED_Code.py
```

## Browser media manager

Install or update both startup services on the Pi:

```bash
sudo bash systemd/install_ledart.sh
```

The installer updates the WebUI storage policy even if earlier troubleshooting overrides remain on the Pi. It checks actual upload file operations inside the service before reporting success. Existing files in `Input` are preserved by the installer; keep a copy if replacing your checkout because `Input` is excluded from Git.

Open `http://<pi-ip>:8080` from a browser on your local network. The Flask WebUI lets you preview images and animated GIFs, see first-frame 20×15 matrix previews, upload multiple files, and delete files with confirmation. Changes are picked up by the player automatically. Upload batches have a 32 MiB limit; duplicate filenames are renamed rather than overwritten. Existing videos stay in the slideshow but are not managed by this image/GIF UI.

The separate `ledart-web` service uses Waitress and runs without root privileges. The WebUI has no login, so keep it on a trusted local network. See [systemd/README.md](systemd/README.md) for permissions, service commands, host/port configuration, and manual startup.
