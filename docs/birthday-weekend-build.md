# Birthday Weekend Build

This is the shortest path to a giftable Magic Character Box. It favors the magical moment over a perfect enclosure.

## Goal

By party time, the child can:

1. Turn on the box and hear the startup chime.
2. Place a character on top.
3. Hear that character's sound, song, story, or family message.
4. Help teach at least one new character from the dashboard.

## Do Exactly This

### 1. Prepare The Pi

- Flash Raspberry Pi OS.
- Copy this repo to `/home/pi/magic-character-box`.
- Run `./scripts/install_pi.sh`.
- Enable SPI with `sudo raspi-config`.
- Reboot.

### 2. Generate The Magic Sounds

```bash
cd /home/pi/magic-character-box
./scripts/generate_system_sounds.sh
```

The box uses:

- `audio/system/startup-chime.mp3` when the service starts.
- `audio/system/unknown-tag.mp3` when it sees an unregistered tag.
- `audio/system/silence.mp3` to keep the audio path warm.

For extra magic, replace `unknown-tag.mp3` with a recorded voice saying "I found a new character."

### 3. Wire Only The Required Hardware

- Pi Zero 2 W or Zero 2 WH.
- PN532 reader in SPI mode.
- MAX98357A amp and passive speaker.
- USB power.

Use [wiring.md](wiring.md) and [pi-zero-2w-pin-map.md](pi-zero-2w-pin-map.md). Do not add buttons or battery power for the first gift version.

### 4. Make Three Characters

Start with:

- One toy character.
- One mystery token.
- One family token.

Attach NFC stickers to the underside of a toy, token, or printed base.

### 5. Teach The Characters

Open:

```text
http://<pi-hostname-or-ip>:8080
```

Use the dashboard:

1. Click `Setup scan`.
2. Go to `Teach character`.
3. Scan a tag.
4. Name it.
5. Save it.
6. Add its first sound.
7. Click `Playback`.

### 6. Box It Ugly First

Cardboard, a project box, or the printed sidecar enclosure is enough. The important part is that:

- The PN532 sits directly under the "place character here" spot.
- Wires are strain-relieved.
- The Pi and amp are not exposed to the child.
- USB power is adult-accessible.

## Done Is Better Than Perfect

For the birthday moment, skip:

- Battery power.
- Bluetooth.
- Web accounts.
- Fancy case finishing.
- More than one button.
- A big music library.

The magic is the physical ritual plus the personal recordings.
