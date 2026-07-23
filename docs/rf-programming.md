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
- Story Dock phone-tap flow, where a commercial Story Sticker has a prewritten
  versioned Story Dock URL.

If that becomes necessary, add a separate `write_tag.py` tool with a dry-run mode and clear warnings. Keep UID-based registration as the default because it is simpler and harder to break.

## Story Dock URL Tags

For the commercial Story Dock direction, the sticker/card carries an NDEF URL
that both the phone and a hosted-mode dock can use:

- The phone opens the NDEF URL for recording.
- A dock started with `--nfc pn532-ndef` either verifies a complete legacy URL
  or reads the public suffix of a Luis-suffix URL from absolute page 19. The
  suffix must resolve through authenticated hosted config to an opaque
  `sdpk1_...` playback key.

The open-source dashboard's `Story stickers` tool remains a separate local
maker flow. It creates a `/story/<token>` link and stores it in
`config/story_stickers.json`; that local route is intentionally not accepted by
the strict hosted reader.

The open-source dashboard can also generate a QR SVG for that same `/story/<token>` URL. That is the free fallback path: print the QR on a backing card, photo sleeve, prompt card, or setup sheet while still using the PN532/NFC UID for dock playback when available. NFC remains the preferred magic interaction, but QR lets a phone open the same recording page without writing an NFC URL tag.

Ordinary `--nfc pn532` maker mode is unchanged and still uses the physical UID.
Hosted `pn532-ndef` mode only accepts a single canonical NDEF URI record:
legacy `https://tap.getstorydock.com/s/<token>` or versioned
`https://tap.getstorydock.com/s/<T32>/<SDxx-xxxx>`. It never treats UID, the
suffix, four digits, or a filename as identity, and it never logs the URL,
token, or raw UID. A learned hashed UID cache may skip later RF reads only
after URL or active-alias verification and is invalidated when its playback key
is no longer configured.

For a factory-encoded tag batch, the supplier may still return a manufacturing
manifest for QA and traceability:

```text
printed Sticker ID -> exact encoded URL -> optional NFC UID diagnostic
```

The runtime playback identity remains the token-derived hosted playback key;
the suffix only lets the dock reach that key with one reliable short read. The
phone recording flow and dock playback flow meet without asking a normal user
to write NFC tags or manually bind a UID at home.

## Troubleshooting

- If scan times out, check PN532 power, SPI wiring, and board switch/jumper mode.
- If the Pi cannot initialize PN532, make sure SPI is enabled with `sudo raspi-config`, then reboot.
- If tags scan intermittently, move metal away from the reader and keep the tag close to the marked top spot.
- If the Scan button is disabled, the admin page is probably running with the mock backend. Stop `magic-character-box-admin-dev` and start `magic-character-box-admin`.
- If two services are using the reader, stop one of them before scanning. During tag setup, stop `magic-character-box` and run `magic-character-box-admin`; during kid playback, stop the admin and run `magic-character-box`.
