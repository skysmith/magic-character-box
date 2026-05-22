# Troubleshooting

Start with the dashboard's `Device details` -> `Box tools` -> `Test box` button. It checks the chime, NFC readiness, audio command, service mode, and free storage.

## Dashboard Scan Button Is Disabled

Likely cause: the admin is running in mock/dev mode, or the playback service is using the PN532.

Try:

1. Open `http://<pi-hostname-or-ip>:8080`.
2. Click `Setup scan`.
3. Refresh the page.
4. Try `Scan` again.

From SSH:

```bash
systemctl status magic-character-box-admin
systemctl status magic-character-box
```

The real admin service should include `--nfc pn532`. If you see `--nfc mock`, stop the dev admin and start the real admin.

## NFC Tag Does Not Read

Check:

- PN532 switch/jumper is set to SPI mode.
- SPI is enabled in `sudo raspi-config`.
- PN532 `VCC` is on Pi `3.3V`, not 5V unless your board specifically requires 5V-tolerant power.
- `SCK`, `MISO`, `MOSI`, and `SS/CS` match [wiring.md](wiring.md).
- The tag is close to the antenna and not blocked by metal.
- The reader is not too far below the lid.

Test directly:

```bash
python scripts/scan_tag.py --nfc pn532
```

If the dashboard shows a `Last seen tag`, the reader worked at least once. Use `Use this tag` in Teach Mode to avoid copying the UID manually.

## No Audio

Check:

- Speaker is wired to the MAX98357A speaker output, not to Pi GPIO.
- MAX98357A `VIN` is on Pi 5V.
- `BCLK`, `LRC`, and `DIN` match [wiring.md](wiring.md).
- The Pi audio overlay is configured and the Pi has rebooted.
- `mpg123` is installed.
- Dashboard volume is not too low.

Quick test:

```bash
mpg123 -q -o pulse audio/system/startup-chime.mp3
```

If you hear clicks but no sound, recheck `BCLK`, `LRC`, and `DIN`; swapped clock pins are a common mistake.

## Pops Or Clicks

The recommended build uses three layers of protection:

- MAX98357A `SD` wired to `GPIO16 / physical pin 36`.
- Persistent `mpg123-remote` playback backend.
- Silent audio keeper service.

Uploaded and recorded files are also prepared with short fades and loudness normalization when `ffmpeg` is installed.

See [web-admin.md](web-admin.md#reducing-speaker-pops) and [wiring.md](wiring.md).

## Browser Recording Does Not Work

Browsers require a secure origin for microphone access.

Use one of:

- Upload an existing voice memo from the character's `Add audio` panel.
- `http://localhost` while developing on the same machine.
- A temporary HTTPS tunnel to the guest-only recorder for remote family.

The dashboard hides the browser `Record` controls when the page is not secure enough for microphone access.

See [web-admin.md](web-admin.md#guest-recording-links).

## Uploaded Audio Is Not Playable

MP3 files play directly. Other formats need `ffmpeg` conversion.

Install:

```bash
sudo apt install -y ffmpeg
```

Then upload again. The dashboard message should say the file was prepared.

## Wrong Character Plays

Open the dashboard and check the character's UID badges. If you reused a tag or accidentally registered the wrong sticker:

1. Open `Character settings`.
2. Delete the wrong character mapping.
3. Scan the tag again in Teach Mode.
4. Name it and add audio.

Deleting a character mapping does not delete its audio folder.

## Box Does Not Start After Reboot

Check services:

```bash
systemctl is-active magic-character-box
systemctl is-active magic-character-box-admin
journalctl -u magic-character-box -n 80 --no-pager
```

Common causes:

- Pi is still booting.
- Weak USB power supply.
- Missing Python virtual environment.
- SPI or I2S setup changed before reboot.
- Audio device is unavailable because PipeWire is not running.

## Running Out Of Space

The dashboard `Test box` result includes free storage. You can also run:

```bash
df -h /
du -h -d 2 audio | sort -h
```

Use `Download backup` before deleting large local clips.

## I Need To Recover A Build

1. Download a backup from the dashboard if it still opens.
2. Copy off `/home/pi/magic-character-box/config/` and `/home/pi/magic-character-box/audio/`.
3. Reinstall the repo.
4. Restore `config/characters.json` and the `audio/` folders.
5. Restart services.
