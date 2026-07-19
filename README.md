# Magic Character Box

Build a magic NFC story box for kids with Raspberry Pi.

Magic Character Box is a local-first maker project where NFC-tagged toys, tokens, or figures trigger songs, stories, sound effects, and family voice messages. Put a character on top, read its NFC UID, map that UID to an audio folder, and play the matching files.

![Smaller sidecar enclosure preview](cad/build/magic-character-box-sidecar-preview.png)

The finished kid box should use:

```text
5V charger -> Raspberry Pi Zero 2 W -> MAX98357A I2S amp -> passive speaker
```

The software is intentionally testable before the hardware is finished. By default it uses a keyboard-backed mock NFC reader, so you can type `DINOSAUR`, `ROCKET`, or `DAD` and verify the app loop on a Mac/Linux machine.

## Start Here

- Quick build path: [docs/quick-build.md](docs/quick-build.md)
- Build guide: [docs/assembly.md](docs/assembly.md)
- Birthday weekend path: [docs/birthday-weekend-build.md](docs/birthday-weekend-build.md)
- Materials list: [docs/materials.md](docs/materials.md)
- Wiring and pin map: [docs/wiring.md](docs/wiring.md) and [docs/pi-zero-2w-pin-map.md](docs/pi-zero-2w-pin-map.md)
- NFC registration: [docs/rf-programming.md](docs/rf-programming.md)
- Web admin: [docs/web-admin.md](docs/web-admin.md)
- Troubleshooting: [docs/troubleshooting.md](docs/troubleshooting.md)
- v0.1 release checklist: [docs/release-checklist.md](docs/release-checklist.md)
- Known limitations: [docs/known-limitations.md](docs/known-limitations.md)
- System sounds: [docs/system-sounds.md](docs/system-sounds.md)
- Hardware profiles: [docs/hardware-profiles.md](docs/hardware-profiles.md)
- Optional transactional player-load bridge: [docs/transactional-player-load.md](docs/transactional-player-load.md)
- Printable assets: [docs/printable-assets.md](docs/printable-assets.md)
- Printable STL files: [stl/](stl/README.md)
- Parametric CAD source: [cad/](cad/README.md)

## Build It Now

For the recommended first build:

1. Buy or gather the parts in [materials.md](docs/materials.md).
2. Wire the PN532 and MAX98357A from [wiring.md](docs/wiring.md).
3. Run `./scripts/install_pi.sh` on the Pi.
4. Open the dashboard at `http://<pi-hostname-or-ip>:8080`.
5. Click `Setup scan`, scan a blank sticker, name it, and add its first sound.
6. Click `Playback`, place the character on the box, and listen.

The shortest copy-and-build version is [docs/quick-build.md](docs/quick-build.md).

## Why Build This

- Screen-free, physical interaction for kids.
- Beginner-friendly Raspberry Pi, NFC, Python, and audio practice.
- Easy to personalize with a Dad Token, Grandma Token, dinosaur facts, bedtime stories, or secret missions.
- Can also become a Story Dock / Story Album: NFC-tagged printed photos that play family memories and voice notes.
- Expandable from an ugly desk demo into a kid-usable enclosure and local parent admin page.

## Story Dock Boundary

Story Dock commercial product work lives in the private sibling `story-dock`
repo. This public repo stays focused on the local Magic Character Box build and
may keep open-source bridge pieces for local photo-story and guest-recorder
flows, but not hosted backend secrets, launch plans, supplier docs, or private
customer/product strategy.

## Current Behavior

- Reads NFC UIDs from either a mock keyboard reader or a PN532 over SPI.
- Offers an explicit `pn532-ndef` hosted mode that identifies a Story Sticker
  from its verified `https://tap.getstorydock.com/s/<token>` NDEF URL. This mode
  derives an opaque `sdpk1_...` config key and never falls back to the tag UID.
- Normalizes UIDs such as `04:a1:22:9b` to `04-A1-22-9B`.
- Looks up characters in `config/characters.json`.
- Stops current playback when a different known character is scanned.
- Plays the first, shuffled, or next sequenced `.mp3` file in the mapped folder.
- Ignores repeat scans of the same tag identity for a cooldown window.
- Continues playing when a tag is removed. In the MVP, only a new known tag changes playback.
- Plays a startup chime when the service starts.
- Plays a friendly discovery cue for unknown/unregistered tags.
- Shares a tiny `config/device_state.json` file with the dashboard for the last-seen tag and recent plain-language events.

Normal maker mode hot-reloads valid `characters.json` edits and preserves its
last in-memory mapping when a rewrite is invalid. Coordinated installers may
instead opt into the local, credential-free transactional player-load bridge;
it is off by default and ordinary maker installs are unchanged. See
[docs/transactional-player-load.md](docs/transactional-player-load.md).

## Quick Start: Dev Mode

```bash
cd magic-character-box
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m magic_box.app --nfc mock --dry-run-audio
```

At the prompt, type:

```text
DINOSAUR
ROCKET
DAD
q
```

Dry-run audio logs what it would do without launching `mpg123`. To hear sound, add legally usable MP3 files under:

```text
audio/dinosaur/
audio/rocket/
audio/dad/
```

Then install `mpg123` and run without `--dry-run-audio`:

```bash
python -m magic_box.app --nfc mock
```

## Pi Setup

On the Raspberry Pi:

```bash
cd /home/pi/magic-character-box
./scripts/install_pi.sh
```

The install script also runs `scripts/generate_system_sounds.sh`, which creates the startup chime, unknown-tag cue, success/error cues, and silent warmup MP3.

Then enable SPI:

```bash
sudo raspi-config
```

Choose `Interface Options` -> `SPI` -> enable, then reboot.

For the MAX98357A speaker, follow `docs/wiring.md`; the Pi service files pin playback to the direct `plughw:CARD=MAX98357A,DEV=0` ALSA path so the built-in speaker behaves consistently across cards.

For a wiring cheat sheet and reserved-pin ledger, use [docs/pi-zero-2w-pin-map.md](docs/pi-zero-2w-pin-map.md). The same reservations are available as JSON in [config/pin-reservations.json](config/pin-reservations.json).

Service deployment notes are in [docs/pi-deployment.md](docs/pi-deployment.md).

## Run With Real PN532

```bash
python -m magic_box.app --nfc pn532
```

You can also use environment variables:

```bash
MAGIC_BOX_NFC=pn532 \
MAGIC_BOX_AUDIO_BACKEND=continuous-pcm \
MAGIC_BOX_AUDIO_CMD="mpg123 -q -s --rate 48000 --stereo -e s16" \
MAGIC_BOX_AUDIO_SINK_CMD="aplay -q -D plughw:CARD=MAX98357A,DEV=0 --file-type raw --format S16_LE --rate 48000 --channels 2 --buffer-time=100000 --period-time=20000" \
python -m magic_box.app
```

Ordinary `pn532` mode keeps the public maker workflow and uses the physical tag
UID. A hosted deployment can opt in to URL identity instead:

```bash
python -m magic_box.app --nfc pn532-ndef
```

In `pn532-ndef` mode, the tag must contain exactly one well-formed NDEF HTTPS
URI record at `https://tap.getstorydock.com/s/<token>`. The player reads that
URL directly from the NTAG, derives a one-way `sdpk1_...` lookup key, and never
uses the physical UID as a fallback. URLs and tokens are not written to logs.

## Scan A Tag

```bash
python scripts/scan_tag.py --nfc pn532
```

For a laptop/dev placeholder:

```bash
python scripts/scan_tag.py --nfc mock
```

## Test Without The Reader

Before the PN532 and stickers arrive, run the real app loop with the file-backed fake reader:

```bash
MAGIC_BOX_NFC=file python -m magic_box.app --nfc file
```

In another terminal, queue pretend tag IDs:

```bash
python scripts/fake_tag.py DINOSAUR
python scripts/fake_tag.py ROCKET
python scripts/fake_tag.py DAD
```

The app consumes one queued UID at a time from `/tmp/magic-character-box-tags.txt`. This is the easiest way to test the Pi service, config lookup, cooldown behavior, and audio switching without touching NFC hardware.

The admin UI also has a `Simulate scan` button for each character. When the app service is running with `MAGIC_BOX_NFC=file`, that button queues the same fake UID and the background app plays it as if a toy had been placed on the reader.

## Register A Character

```bash
python scripts/register_character.py --nfc pn532 --name Dinosaur --mode shuffle
```

This reads one tag UID, creates a folder such as `audio/dinosaur`, and updates `config/characters.json`.
If a folder with that name already exists, the helper appends a number, such as `audio/dinosaur-2`.
Use `--folder audio/custom-name --create-folder` only when you want to override the automatic folder name.

## Device Admin UI

Run the local web admin when you want a phone/laptop interface for the box:

```bash
python -m magic_box.admin --nfc pn532 --host 0.0.0.0 --port 8080
```

Open `http://<your-pi-hostname-or-ip>:8080` on the same network.

If the Pi is on your Tailscale tailnet, Tailscale Serve can expose the same dashboard over trusted tailnet HTTPS without creating a second admin service. For example, a Tailscale URL can look like `https://your-box.your-tailnet.ts.net/`.

The admin UI can:

- Upload audio to a character immediately after files are selected.
- Upload a family voice memo from a phone or laptop.
- Record in the browser only when the page is running from a secure origin, such as localhost or a guest-only HTTPS tunnel.
- Create an upload-only guest recording link for a remote family member.
- Register an NFC tag UID without editing JSON, creating the audio folder automatically from the character name.
- Create a photo story in one pass: scan a sticker, name the memory, and generate a phone recorder link.
- Create Story Sticker URLs for the newer Story Dock flow: preassign a phone-tap link, bind the NFC UID, record from a phone, and expose the story in `/api/dock/manifest`.
- Download QR SVG fallbacks for Story Sticker links and guest recorder links. This is the free/open-source print option for people who do not have prewritten NFC URL stickers yet.
- Serve a first mobile API for future native iPhone/App Clip work at `/api/mobile/story-stickers/<token>`.
- Point Story Sticker links at a hosted/tunneled URL with `MAGIC_BOX_PUBLIC_STORY_BASE_URL` during staging.
- Switch between setup scanning and kid playback mode from the dashboard.
- Check Wi-Fi status, scan nearby networks, and connect through the local NetworkManager controls on the Pi.
- Use the last-seen tag to teach a new character without copying a UID.
- Run a one-click box test from the hidden tools drawer.
- Download a backup zip of `config/` and `audio/`.
- Shut down the Pi cleanly from the local dashboard.
- Leave room for experimental Bluetooth speaker work without making it part of the main build.
- Show guidance for custom 3D printable figures.

Browser recordings are converted to MP3 when `ffmpeg` is installed. `scripts/install_pi.sh` installs `ffmpeg` on the Pi.

For Grandma-style remote messages, create a guest recording link from the dashboard or command line:

```bash
python scripts/create_guest_link.py --name "Dad Token" --label "Grandma birthday message" --base-url https://your-temporary-tunnel.example
```

The guest page is upload-only. For a temporary public tunnel, run the app in `--guest-only` mode on a separate local port so the tunnel cannot serve the dashboard, scan controls, file deletion, Bluetooth controls, or system mode buttons. That guest recorder is not a second dashboard; the single parent dashboard stays on `http://<pi-hostname-or-ip>:8080`.

When using the real PN532 Scan button, run the real admin service and stop the mock/dev admin service. If the Scan button is disabled and the page says browser scan is unavailable, the admin is running in mock mode.

See [docs/web-admin.md](docs/web-admin.md), [docs/rf-programming.md](docs/rf-programming.md), and [docs/3d-printable-figures.md](docs/3d-printable-figures.md).

Bluetooth controls are intentionally treated as an experimental socket for future contributors. The finished kid box defaults to the wired MAX98357A amp path because it boots predictably and does not depend on a paired accessory.

## Install Services

After copying the repo to `/home/pi/magic-character-box` and running `scripts/install_pi.sh`:

```bash
sudo cp systemd/magic-character-box.service /etc/systemd/system/
sudo systemctl daemon-reload
```

To install the web admin as a separate service:

```bash
sudo cp systemd/magic-character-box-admin.service /etc/systemd/system/
sudo systemctl daemon-reload
```

The MVP app and admin page are separate PN532 readers. Use either setup mode or kid playback mode:

```bash
# Setup mode: register tags and upload/record audio
sudo systemctl disable --now magic-character-box
sudo systemctl disable --now magic-character-box-admin-dev
sudo systemctl enable --now magic-character-box-admin

# Kid playback mode
sudo systemctl disable --now magic-character-box-admin
sudo systemctl enable --now magic-character-box
```

Watch logs with `journalctl -u magic-character-box -f` or `journalctl -u magic-character-box-admin -f`.

Adjust `WorkingDirectory`, `User`, and `ExecStart` in the service file if you install somewhere other than `/home/pi/magic-character-box`.

## Tutorial Stages

Follow [docs/tutorial-roadmap.md](docs/tutorial-roadmap.md):

1. The ugly magic demo.
2. Make it kid-usable.
3. The birthday box.
4. Parent-friendly admin.

The first printable case model is in [cad/](cad/README.md). It has rounded dice-like edges, a screw top, side speaker holes, and internal spaces for the Pi, PN532 reader, MAX98357A amp, and a small passive speaker.

The smaller current prototype enclosure is the sidecar version. It mounts the electronics beside an existing passive speaker cabinet. Ready-to-slice STL files are in [stl/](stl/README.md):

- `small-sidecar-enclosure-body.stl`
- `small-sidecar-enclosure-lid.stl`
- `nfc-character-base-flat.stl`

## Open Source And Media

This repo is licensed under Apache License 2.0. See [LICENSE](LICENSE).

The repo intentionally does not include music, stories, character art, or private voice recordings. Add your own audio locally. Good sources include family recordings, original audio, public-domain media, and Creative Commons audio with compatible terms.

See [docs/open-source-notes.md](docs/open-source-notes.md), [docs/audio-sources.md](docs/audio-sources.md), and [CONTRIBUTING.md](CONTRIBUTING.md).

## Project Screenshots And Photos

Useful open-source presentation assets are included under `docs/assets/`:

- Dashboard screenshot: [docs/assets/screenshots/software-admin-library.png](docs/assets/screenshots/software-admin-library.png)
- Bluetooth/future socket screenshot: [docs/assets/screenshots/software-bluetooth-control.png](docs/assets/screenshots/software-bluetooth-control.png)
- Pi header photo: [docs/assets/photos/pi-zero-2w-header.jpg](docs/assets/photos/pi-zero-2w-header.jpg)
- Amp wiring photo: [docs/assets/photos/max98357a-wired-to-pi.jpg](docs/assets/photos/max98357a-wired-to-pi.jpg)

Before publishing new photos, use the checklist in [docs/release-checklist.md](docs/release-checklist.md).

## Notes

- NFC UIDs are fine toy identifiers, not security credentials.
- Do not connect a passive speaker directly to the Pi. The MAX98357A drives the speaker; the Pi only powers the amp and sends I2S audio.
- Keep audio local for the birthday MVP. Streaming services can wait.
