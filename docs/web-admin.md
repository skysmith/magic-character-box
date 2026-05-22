# Device Admin UI

The device admin UI is a local web page served by the Raspberry Pi. It is meant for parent/setup use on the home network, not for public internet exposure.

## Start It

For setup on the Pi:

```bash
python -m magic_box.admin --nfc pn532 --host 0.0.0.0 --port 8080
```

Then open:

```text
http://<your-pi-hostname-or-ip>:8080
```

There is one parent dashboard. Keep it on `http://<your-pi-hostname-or-ip>:8080` on your trusted home network. Do not expose the full dashboard to the public internet.

If the Pi is joined to your Tailscale tailnet, you can also proxy the same dashboard through Tailscale Serve:

```bash
tailscale serve --bg --yes 8080
```

That gives trusted HTTPS inside your tailnet without a separate admin service. A private Tailscale URL usually looks like:

```text
https://your-box.your-tailnet.ts.net/
```

Browser microphone recording requires a secure origin. The dashboard hides browser recording when the browser will block it. Use one of these paths:

- Upload an existing voice memo from the same character panel.
- Create a guest recorder link and open that guest-only page through a temporary HTTPS tunnel.
- Run the dashboard on `localhost` during desktop development.

For laptop/dev testing:

```bash
python -m magic_box.admin --nfc mock --host 127.0.0.1 --port 8080 --dry-run-audio
```

In mock mode, browser scanning is disabled; type the UID manually.
If the Scan button is disabled and the page says "Browser scan is unavailable here," the admin is running in mock mode, not PN532 mode.

## What It Does

- Shows registered characters.
- Uploads audio files into a character folder as soon as files are selected.
- Uploads phone voice memos for family messages.
- Shows browser recording controls only when the page is running from a secure origin, such as localhost or a guest-only HTTPS tunnel.
- Converts browser recordings to MP3 when `ffmpeg` is installed.
- Creates tokenized guest recording links for remote family voice messages.
- Registers NFC tag UIDs into `config/characters.json` and creates the audio folder automatically from the character name.
- Shows the last tag seen by either the dashboard or the playback service.
- Shows a small recent event log in a drawer.
- Offers a hidden `Box tools` drawer with a one-click box test, backup download, and clean shutdown.
- Tests playback through the same audio command used by the box app.
- Stops device playback by sending a local request to the main playback service.
- Sets shared software volume in `config/volume.json`.
- Exposes an experimental Bluetooth control socket for future prototype playback work.
- Gives quick guidance for custom 3D printed figures.
- Switches between setup scanning and child playback mode from the top of the page on the Pi.

## Teach Mode

The `Teach character` section is meant to feel like teaching the box, not editing a config file:

1. `Scan a new character`.
2. `Name it`.
3. `Add its first sound`.

After a new character is saved, the dashboard opens that character's `Add first sound` panel when it has no playable audio yet.

The `Last seen tag` panel updates from `config/device_state.json`. If the kid-facing playback service notices a new/unregistered tag, the dashboard can show that UID and offer `Use this tag` without making you copy it from logs.

## Scanning Tags

The Scan button works only when the admin is running with the real PN532 backend and the playback service is not using the reader:

```bash
python -m magic_box.admin --nfc pn532 --host 0.0.0.0 --port 8080
```

On the Pi service install, use the mode controls at the top of the dashboard:

- `Setup scan`: stops `magic-character-box` so the browser Scan button can use the PN532.
- `Playback`: starts `magic-character-box` again so the kid-facing box listens for character tags.

Clicking Scan changes the button to `Scanning...` for up to 20 seconds. Hold the tag flat against the reader during that window. If no tag is found, the status text changes to a timeout message.

For the MVP, the main box app and the admin page are separate processes. Do not let both processes scan the PN532 at the same time. The top mode controls handle this for the standard service install. The manual version is:

```bash
sudo systemctl stop magic-character-box magic-character-box-admin-dev
sudo systemctl start magic-character-box-admin
```

If port `8080` is already in use, a mock admin may still be running. Stop the dev admin services and refresh the page.

## Audio Uploads

MP3 files are immediately playable by the default `mpg123` command.

In the web UI, click Upload to open the file picker. Selecting one or more files starts the upload immediately. The row shows a spinner, progress bar, and status text while the upload is running. There is no separate Save or Update button for audio files.

The admin accepts other common audio formats such as `.wav`, `.m4a`, `.ogg`, `.flac`, `.webm`, and `.mp4`. When `ffmpeg` is installed, those files are converted to MP3 on upload. If `ffmpeg` is missing, the original file is saved but marked as not playable by `mpg123` yet.

`scripts/install_pi.sh` installs both `mpg123` and `ffmpeg`.

When `ffmpeg` is available, uploaded and recorded audio is re-encoded as mono MP3 with:

- short fade-in and fade-out edges to reduce clicks
- loudness normalization so family clips and songs land closer to the same perceived volume

## Guest Recording Links

Guest links are for remote family messages. A guest link opens a single upload-only page at:

```text
/guest/<token>
```

The guest page can:

- Upload an existing phone voice memo or audio file.
- Record from the browser microphone when opened from a secure HTTPS link.
- Save the message into one selected character folder.

The guest page cannot delete files, scan NFC tags, change playback mode, pair Bluetooth devices, or see the admin dashboard.

Create links from the dashboard under `Guest links`, or from SSH:

```bash
python scripts/create_guest_link.py \
  --name "Dad Token" \
  --label "Grandma birthday message" \
  --days 14 \
  --base-url https://your-temporary-tunnel.example
```

Guest link metadata is stored in:

```text
config/guest_links.json
```

For someone outside your home network, run a guest-only local server and put a temporary HTTPS tunnel in front of that, not the full admin dashboard.

Terminal 1:

```bash
python -m magic_box.admin \
  --guest-only \
  --config config/characters.json \
  --host 127.0.0.1 \
  --port 8090
```

Terminal 2: [Cloudflare documents Quick Tunnels](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/) for development/testing with:

```bash
cloudflared tunnel --url http://localhost:8090
```

That command prints a random `trycloudflare.com` URL.

Terminal 3:

```bash
python scripts/create_guest_link.py \
  --name "Dad Token" \
  --label "Grandma birthday message" \
  --days 14 \
  --base-url https://random-words.trycloudflare.com
```

Send the printed guest URL. Stop the guest-only server and tunnel after the recording is received.

The dashboard's `Guest links` form has an optional `Guest access` field. The dashboard fills it with the best secure origin it can see:

- If opened through Tailscale HTTPS, it uses the Tailscale dashboard origin.
- If `MAGIC_BOX_PREFERRED_GUEST_BASE_URL` is set, it uses that as the default secure origin.
- If you paste a public guest-only tunnel URL, the guest link uses that tunnel instead.

The guest link list labels each link as private Tailscale HTTPS, public/secure, or local Wi-Fi so you know who can use it.

## Stop Audio

The `Stop audio` button stops any clip launched from the dashboard and also writes a one-time request to `config/control.json` for the main `magic-character-box` playback service.

If a character is still sitting on the reader, the service stops the current clip and keeps ignoring that same tag until it is lifted and placed again. That keeps the button from immediately retriggering the same song.

## Box Tools

The `Box tools` drawer is intentionally tucked under `Device details` so the day-to-day dashboard stays simple.

- `Test box` plays the startup chime, checks NFC scan readiness, reports audio command status, checks playback/setup mode, and shows free storage.
- `Download backup` returns a zip with the character config, guest links, volume/control/state files when present, and everything under `audio/`.
- `Shutdown box` asks the Pi to shut down cleanly with `sudo -n shutdown -h now`.

The shutdown command can be overridden for unusual installs:

```bash
MAGIC_BOX_SHUTDOWN_COMMAND="sudo -n shutdown -h now"
```

Recent events are stored in:

```text
config/device_state.json
```

That file is runtime state, not a hand-edited config. It is useful for troubleshooting and for the `Last seen tag` panel.

## Volume

The MAX98357A I2S amp usually does not expose a hardware mixer to `amixer`. On the no-pop Pi setup, the `+` and `-` buttons save a shared volume percentage in `config/volume.json` and apply it to the PipeWire default sink with `wpctl set-volume`.

That keeps the silent keeper stream running while still changing the loudness of real clips. If PipeWire is unavailable, the app falls back to per-player software volume.

## Experimental Bluetooth Socket

The Pi Zero 2 W can play to Bluetooth speakers, but Bluetooth is not part of the recommended main build. Treat it as an experimental socket for contributors and prototypes. The finished kid box should keep the MAX98357A plus passive speaker as the reliable built-in path.

The admin page includes a deliberately secondary Bluetooth panel with:

- Adapter status and current default audio output.
- Power on/off.
- Scan for nearby devices.
- Pair, connect, disconnect, and `Use for audio` buttons.

The panel shells out to `bluetoothctl` for Bluetooth actions. `Use for audio` also tries to make the matching `bluez_output` Pulse/PipeWire sink the default output using `pactl`. The Pi services already use:

```text
MAGIC_BOX_AUDIO_CMD=mpg123 -q -o pulse
```

That means playback follows the Pi's default Pulse/PipeWire sink. If a Bluetooth speaker is connected and selected as the default sink, character audio should play through it without changing character folders or NFC behavior.

Install support tools on the Pi if they are missing:

```bash
sudo apt install -y bluez pulseaudio-utils
sudo systemctl enable --now bluetooth
```

If you are intentionally experimenting with Bluetooth, open the admin UI, put the speaker in pairing mode, and use:

1. `Scan for speakers`.
2. `Pair` if the speaker is new.
3. `Use for audio`.
4. Test playback from any character row.

If the speaker connects but audio still comes out of the wired speaker, refresh the Bluetooth panel and check `Default output`. You can also inspect sinks from SSH:

```bash
pactl list short sinks
wpctl status
```

For the tutorial and birthday MVP, skip this section. Wired audio is simpler to explain, easier to enclose, and more predictable for a child-facing object.

## Reducing Speaker Pops

The admin page prepares newly uploaded or recorded audio with short fades and loudness normalization so clips do not begin or end with hard waveform edges and do not vary wildly in volume.

For hardware boot/start pops, wire the MAX98357A `SD` shutdown pin to `GPIO16 / physical pin 36` instead of 3.3V, then enable the boot-time low setting described in [wiring.md](wiring.md). The app uses that pin as a boot mute and then leaves the amp enabled between clips, because waking the amp for every clip can create its own pop.

If clip starts still pop, use the persistent audio backend:

```bash
MAGIC_BOX_AUDIO_BACKEND=mpg123-remote \
MAGIC_BOX_AUDIO_CMD="mpg123 -q -o pulse" \
MAGIC_BOX_AUDIO_WARMUP_FILE=/home/pi/magic-character-box/audio/system/silence.mp3 \
XDG_RUNTIME_DIR=/run/user/1000 \
python -m magic_box.app --nfc pn532
```

The systemd files included in this repo use that backend for the Pi build, plus `magic-character-box-audio-keeper.service`, which plays silent samples through PipeWire so the I2S audio path stays awake between clips.

## Voice Recording

Browser recording uses the browser's microphone APIs. Most browsers produce WebM, Ogg, or MP4 audio, not MP3. The Pi converts that recording to MP3 with `ffmpeg` so the regular box app can play it.

If recording fails, check:

- The browser has microphone permission.
- The page is loaded from `localhost`, `127.0.0.1`, or a guest-only HTTPS tunnel.
- `ffmpeg` is installed on the Pi.
- The target character has a writable audio folder.

## Service Install

The repo includes a separate systemd service:

```bash
sudo cp systemd/magic-character-box-admin.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable magic-character-box-admin
sudo systemctl start magic-character-box-admin
journalctl -u magic-character-box-admin -f
```

## Running Beside The Main Box App

The MVP app and admin UI are separate processes. Avoid having both processes scan the PN532 at the same time. During setup, the simplest flow is:

1. Open the dashboard.
2. Click `Setup scan`.
3. Register tags and upload or record audio.
4. Click `Playback`.

Manual equivalent:

```bash
sudo systemctl stop magic-character-box magic-character-box-admin-dev
sudo systemctl start magic-character-box-admin
```

Register tags, upload/record audio, then restart the main box app:

```bash
sudo systemctl stop magic-character-box-admin
sudo systemctl start magic-character-box
```

Later, the project can merge playback, scanning, and admin into one long-running service.

## Security

This admin UI has no login and includes maintenance actions such as backup download and Pi shutdown. Keep it on a trusted home network. Do not expose it to the public internet.
