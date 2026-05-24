# NFC / RF Programming Tool

The admin UI includes an "RF program tool" for registering characters.

In this project, programming does **not** mean writing data to the NFC tag. The MVP only reads the tag UID and stores that UID in `config/characters.json`.

That is intentional:

- It works with cheap blank NFC stickers and fobs.
- It avoids tag-writing mistakes.
- The box can be reset by editing one JSON file.
- The tag contains no private data.

## Mental Model

```text
NFC tag UID -> config/characters.json -> audio folder -> MP3 playback
```

Example:

```json
{
  "04-A1-22-9B": {
    "name": "Dinosaur",
    "folder": "audio/dinosaur",
    "mode": "shuffle"
  }
}
```

## Register A Tag In The Web UI

1. Start the admin UI:

   ```bash
   python -m magic_box.admin --nfc pn532 --host 0.0.0.0 --port 8080
   ```

2. Open `http://<pi-ip-address>:8080`.
3. Go to "Add tag".
4. Place the tag or character on the PN532 reader.
5. Tap "Scan". The button should change to `Scanning...`; hold the tag flat on the reader until a UID appears or the scan times out.
6. Enter a character name, such as `Dinosaur`.
7. Pick a mode:

   - `first`: always play the first MP3 in the folder.
   - `shuffle`: pick a random MP3.
   - `sequence`: advance through files in filename order.

8. Tap "Save character".
9. Upload or record audio for that character. Audio uploads begin automatically after files are selected.

The web UI creates the audio folder automatically from the character name. For duplicate names, it appends a number, such as `audio/dinosaur-2`.

## Register A Tag From The Terminal

```bash
python scripts/register_character.py --nfc pn532 --name Dinosaur --mode shuffle
```

The terminal helper uses the same automatic folder naming as the web UI.

## Manual / Mock UIDs

For development, use simple fake UIDs:

```text
DINOSAUR
ROCKET
DAD
```

In web admin mock mode, type the UID manually. Browser scan is disabled because the mock reader reads from terminal stdin.
If the browser Scan button is disabled, check that `magic-character-box-admin` is running and `magic-character-box-admin-dev` is stopped.

## When To Write Data To Tags

Do not write data to tags for the MVP.

Possible future reasons to write tags:

- Cross-box portability.
- Character metadata stored on the figure.
- Pairing tags with a mobile app.
- Story Dock phone-tap flow, where a commercial Story Sticker has a prewritten `/story/<token>` URL.

If that becomes necessary, add a separate `write_tag.py` tool with a dry-run mode and clear warnings. Keep UID-based registration as the default because it is simpler and harder to break.

## Story Dock URL Tags

For the commercial Story Dock direction, the sticker/card can carry two identities:

- NFC UID: what the dock reads for playback.
- NDEF URL: what the phone opens for recording.

The dashboard can now create the URL side first under `Story stickers`. That creates a `/story/<token>` link and stores it in `config/story_stickers.json`.

The open-source dashboard can also generate a QR SVG for that same `/story/<token>` URL. That is the free fallback path: print the QR on a backing card, photo sleeve, prompt card, or setup sheet while still using the PN532/NFC UID for dock playback when available. NFC remains the preferred magic interaction, but QR lets a phone open the same recording page without writing an NFC URL tag.

For a future factory-encoded tag batch, the supplier should return a manifest mapping:

```text
printed support code -> NFC UID -> encoded URL token
```

That lets the phone recording flow and the dock playback flow meet without asking a normal user to write NFC tags at home.

## Troubleshooting

- If scan times out, check PN532 power, SPI wiring, and board switch/jumper mode.
- If the Pi cannot initialize PN532, make sure SPI is enabled with `sudo raspi-config`, then reboot.
- If tags scan intermittently, move metal away from the reader and keep the tag close to the marked top spot.
- If the Scan button is disabled, the admin page is probably running with the mock backend. Stop `magic-character-box-admin-dev` and start `magic-character-box-admin`.
- If two services are using the reader, stop one of them before scanning. During tag setup, stop `magic-character-box` and run `magic-character-box-admin`; during kid playback, stop the admin and run `magic-character-box`.
