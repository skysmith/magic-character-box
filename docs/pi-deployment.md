# Pi Service Deployment

Use this after the manual tests in [assembly.md](assembly.md) pass.

These instructions assume the project has been copied to:

```text
/home/pi/magic-character-box
```

If you install somewhere else, update the `WorkingDirectory` and `ExecStart` paths in the systemd unit files.

## Install Dependencies

On the Pi:

```bash
cd /home/pi/magic-character-box
./scripts/install_pi.sh
```

Enable SPI for the PN532 reader:

```bash
sudo raspi-config
```

Choose `Interface Options` -> `SPI` -> enable, then reboot.

## Audio Notes

For the MAX98357A I2S amp, configure Raspberry Pi OS to route audio to the I2S device. A typical `/boot/firmware/config.txt` setup is:

```text
dtparam=i2s=on
dtoverlay=max98357a,no-sdmode
```

If you wire the MAX98357A `SD` shutdown pin to `GPIO16 / physical pin 36`, also add:

```text
gpio=16=op,dl
```

That keeps the amp muted early in boot. See [wiring.md](wiring.md) for the full anti-pop explanation.

The included service files default to the built-in MAX98357A ALSA output through the direct `plughw` path that has been the cleanest on the founder docks:

```text
MAGIC_BOX_AUDIO_BACKEND=continuous-pcm
MAGIC_BOX_DEFAULT_VOLUME=50
MAGIC_BOX_MAX_OUTPUT_VOLUME=75
MAGIC_BOX_AUDIO_CMD=mpg123 -q -s --rate 48000 --stereo -e s16
MAGIC_BOX_AUDIO_SINK_CMD=aplay -q -D plughw:CARD=MAX98357A,DEV=0 --file-type raw --format S16_LE --rate 48000 --channels 2 --buffer-time=100000 --period-time=20000
```

Main playback keeps one direct `aplay` ALSA sink open and continuously feeds it fixed-format PCM. Idle time is zero PCM, while `mpg123 -s` decodes an active clip into that same stream. The sink receives 200 ms of silence before the amp is enabled, and idle silence is not reported as audible playback. Story paths are passed to the decoder through inherited file descriptors rather than exposed in its command line. On service stop, systemd signals the Python process first so it can cancel playback, mute the amp, terminate and reap the sink, and then release the GPIO. Volume buttons use the decoder's software volume, with `MAGIC_BOX_MAX_OUTPUT_VOLUME` acting as a small-speaker output ceiling. Install `alsa-utils` as well as `mpg123`; `aplay` is a required runtime dependency. The old PipeWire/`dmix` keeper path remains retired because it adds a second client and previously caused distortion or silence.

Bluetooth speaker support is an experimental socket, not part of the recommended deployment path. The panel uses `bluetoothctl` for pairing and `pactl` to select a matching `bluez_output` sink when possible, but the default service files stay on the wired MAX98357A speaker. Install the optional tools only if you are intentionally experimenting:

```bash
sudo apt install -y bluez pulseaudio-utils
sudo systemctl enable --now bluetooth
```

The finished kid box should use the MAX98357A speaker path as the default built-in output.

## Install The Main Box Service

Install this for kid/playback mode.

```bash
cd /home/pi/magic-character-box
sudo cp systemd/magic-character-box.service /etc/systemd/system/
sudo systemctl daemon-reload
```

Watch logs:

```bash
journalctl -u magic-character-box -f
```

## Install The Admin UI

Install this for setup mode: registering tags, uploading audio, recording messages, and testing playback from a phone or laptop.

```bash
cd /home/pi/magic-character-box
sudo cp systemd/magic-character-box-admin.service /etc/systemd/system/
sudo systemctl daemon-reload
```

Open the admin page on the same trusted network:

```text
http://<pi-hostname-or-ip>:8080
```

There is one parent dashboard. The admin page has no login, so keep it on the trusted local network and do not expose it to the public internet.

For the MVP, the main box app and real PN532 admin should not scan the reader at the same time. The commands above install the services; use one of these modes to decide what is actually running:

The admin dashboard includes top-level mode buttons for the standard service install:

- `Setup scan` stops `magic-character-box` and frees the PN532 for the browser Scan button.
- `Playback` starts `magic-character-box` again for normal child-facing playback.

Manual equivalent:

```bash
# Setup mode: real browser Scan button
sudo systemctl disable --now magic-character-box
sudo systemctl disable --now magic-character-box-admin-dev
sudo systemctl enable --now magic-character-box-admin

# Kid playback mode
sudo systemctl disable --now magic-character-box-admin
sudo systemctl enable --now magic-character-box
```

If the browser Scan button is disabled and the page says browser scan is unavailable, the mock/dev admin is running. Stop `magic-character-box-admin-dev` and start `magic-character-box-admin`.

## Temporary Remote Recording Links

For a remote family member, do not expose the full admin dashboard as the thing you send around. Create a guest recording link instead. It is tokenized, upload-only, and saves into one selected character.

Run a guest-only server on a separate local port:

```bash
cd /home/pi/magic-character-box
.venv/bin/python -m magic_box.admin \
  --guest-only \
  --config /home/pi/magic-character-box/config/characters.json \
  --host 127.0.0.1 \
  --port 8090
```

In another terminal, start the temporary tunnel:

```bash
cloudflared tunnel --url http://localhost:8090
```

Then create the link from SSH using the generated tunnel origin:

```bash
cd /home/pi/magic-character-box
.venv/bin/python scripts/create_guest_link.py \
  --name "Dad Token" \
  --label "Grandma birthday message" \
  --days 14 \
  --base-url https://random-words.trycloudflare.com
```

The [Cloudflare Quick Tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/) points at the guest-only service, not the full dashboard:

```bash
cloudflared tunnel --url http://localhost:8090
```

Send only the guest recorder URL, and stop `cloudflared` plus the guest-only server after the recording is received. Quick tunnels are for short-lived testing and sharing, not permanent public hosting.

The parent dashboard also has an optional `Guest access` field when creating a guest link. It defaults to the current HTTPS dashboard origin or to `MAGIC_BOX_PREFERRED_GUEST_BASE_URL` when that environment variable is set. Paste a temporary public tunnel origin there only when you want a non-Tailscale guest to use the link.

## Fake-Tag Dev Services

If you want to test the service path before wiring NFC, use the fake-tag service:

```bash
sudo cp systemd/magic-character-box-dev.service /etc/systemd/system/
sudo cp systemd/magic-character-box-admin-dev.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now magic-character-box-dev
sudo systemctl enable --now magic-character-box-admin-dev
```

The dev admin uses `--nfc mock`, so its browser Scan button is disabled. It is for fake UID and simulate-scan testing only. Do not leave the dev admin enabled once the PN532 is installed, because it can occupy port `8080` and make the real admin look broken.

Queue a fake scan:

```bash
cd /home/pi/magic-character-box
.venv/bin/python scripts/fake_tag.py DINOSAUR
.venv/bin/python scripts/fake_tag.py ROCKET
.venv/bin/python scripts/fake_tag.py DAD
```

The dev service consumes one fake UID per line from:

```text
/tmp/magic-character-box-tags.txt
```

When the PN532 is wired and working, stop the dev services and start the real services:

```bash
sudo systemctl disable --now magic-character-box-dev
sudo systemctl disable --now magic-character-box-admin-dev
sudo systemctl enable --now magic-character-box-admin
```

After registration is done, switch back to kid playback mode:

```bash
sudo systemctl disable --now magic-character-box-admin
sudo systemctl enable --now magic-character-box
```

## Smoke Tests

Mock app loop:

```bash
cd /home/pi/magic-character-box
. .venv/bin/activate
printf "DINOSAUR\nROCKET\nDAD\nq\n" | python -m magic_box.app --nfc mock --dry-run-audio
```

PN532 scan:

```bash
python scripts/scan_tag.py --nfc pn532
```

Real app with dry-run audio:

```bash
python -m magic_box.app --nfc pn532 --dry-run-audio
```

Real app with audio:

```bash
python -m magic_box.app --nfc pn532
```

## Security Checklist

- Change the Pi password if password login is enabled.
- Prefer SSH keys.
- Keep the admin UI on a trusted local network.
- Remember the dashboard includes backup download and clean shutdown controls.
- Do not commit real Wi-Fi credentials, SSH keys, private recordings, or local `.env` files.
