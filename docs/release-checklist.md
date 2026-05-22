# v0.1 Release Checklist

Use this before publishing the repo or calling a birthday build done.

## Physical Box Tests

- [ ] Cold boot: unplug the Pi, plug it back in, wait for services, then place a known character.
- [ ] Dad Token plays the expected clip.
- [ ] Dinosaur or another toy token plays the expected clip.
- [ ] Unknown blank tag plays the friendly discovery sound.
- [ ] Reader detects stickers through the lid or top cover.
- [ ] Swapping characters stops current audio and starts the new character.
- [ ] `Stop audio` works from the dashboard.
- [ ] Speaker volume is comfortable for a child.
- [ ] No sharp edges, loose wires, exposed electronics, or loose battery cells.

## Real Setup Flow

- [ ] Open the dashboard on the home network.
- [ ] Click `Setup scan`.
- [ ] Scan a blank sticker.
- [ ] Confirm `Last seen tag` updates.
- [ ] Use the scanned UID to create a new character.
- [ ] Upload or record the first sound.
- [ ] Click `Playback`.
- [ ] Place the new character and hear the new sound.
- [ ] Delete an old audio file from the dashboard.
- [ ] Download a backup zip.

## Open-Source Hygiene

- [ ] `LICENSE` is Apache License 2.0.
- [ ] `NOTICE` names the project and contributors.
- [ ] README links the build guide, materials, wiring, admin UI, troubleshooting, and printable files.
- [ ] No private family recordings are committed.
- [ ] No copyrighted songs, audiobooks, character art, or branded model files are committed.
- [ ] No Wi-Fi credentials, SSH keys, local `.env` files, or private UIDs are committed.
- [ ] Photos/screenshots do not reveal children, home addresses, network labels, serial numbers, or private browser tabs.
- [ ] `config/characters.json` contains only fake/demo UIDs.
- [ ] Runtime files such as `config/device_state.json`, `config/guest_links.json`, and backups are ignored.

## Docs Smoke Test

- [ ] A beginner can find the shortest path in [quick-build.md](quick-build.md).
- [ ] A builder can buy parts from [materials.md](materials.md).
- [ ] A builder can wire from [wiring.md](wiring.md).
- [ ] A builder can register tags from [rf-programming.md](rf-programming.md).
- [ ] A builder can debug common problems from [troubleshooting.md](troubleshooting.md).
- [ ] NFC base dimensions are documented in [3d-printable-figures.md](3d-printable-figures.md).
- [ ] STL files listed in [../stl/README.md](../stl/README.md) exist.

## Software Checks

Run locally:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
```

Run on the Pi:

```bash
cd /home/pi/magic-character-box
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
systemctl is-active magic-character-box magic-character-box-admin
```

## Suggested v0.1 Tag

Call it `v0.1.0` when:

- the cold boot test passes
- the real blank-sticker setup flow passes
- the repo hygiene checklist is clean
- the README and troubleshooting docs are enough for a new builder to start
