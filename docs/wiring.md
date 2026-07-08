# Wiring Notes

For a fuller GPIO reference, keep [pi-zero-2w-pin-map.md](pi-zero-2w-pin-map.md) open while wiring.

The project reserves pins for two always-installed boards:

- `MAX98357A` I2S amp.
- AITRIP PN532 NFC reader in SPI mode.

The reservation ledger is in [pi-zero-2w-pin-map.md](pi-zero-2w-pin-map.md) and [config/pin-reservations.json](../config/pin-reservations.json).

## Audio: MAX98357A To Raspberry Pi Zero 2 W

The Pi does not drive a passive speaker directly. The Pi powers the MAX98357A amp and sends I2S audio; the amp drives the speaker.

```text
MAX98357A VIN  -> Pi 5V
MAX98357A GND  -> Pi GND
MAX98357A BCLK -> GPIO18 / pin 12
MAX98357A LRC  -> GPIO19 / pin 35
MAX98357A DIN  -> GPIO21 / pin 40
MAX98357A SD   -> GPIO16 / pin 36 optional, recommended for anti-pop mute
Speaker +      -> MAX98357A speaker +
Speaker -      -> MAX98357A speaker -
```

Use a stable 5V charger with enough headroom for the Pi, PN532, and speaker amp. For this build, use 5V 2A or better.

After enabling/configuring I2S audio for the MAX98357A, reboot before testing playback.

### Anti-Pop Amp Mute Gate

For the quietest finished box, do not tie `SD` directly to 3.3V. Wire `MAX98357A SD` to `GPIO16 / physical pin 36` instead. The software can then keep the amp muted while Linux boots, enable it once the service is ready, and avoid waking the amp right before every clip.

Add this to `/boot/firmware/config.txt` so the Pi holds GPIO16 low early in boot:

```text
gpio=16=op,dl
```

The systemd services set `MAGIC_BOX_AMP_SD_GPIO=16`, so after this rewire the app and admin page will automatically use the mute gate. The player services also set `MAGIC_BOX_AMP_MUTE_BETWEEN_TRACKS=0`, leaving the amp enabled between clips because some MAX98357A boards make a click when `SD` wakes the amp. If your board still pops before Linux applies the GPIO setting, add a physical pulldown resistor from `SD` to `GND` as a hardware refinement.

The deployed Pi player service keeps `mpg123` open in remote mode through `plughw:CARD=MAX98357A,DEV=0` using `-e s16`, with `audio/system/silence.mp3` as a startup warmup file. That avoids relying on a user-session Pulse/PipeWire socket, avoids reopening the I2S path for every tap, and avoids the retired `dmix`/keeper stream that distorted some founder-card builds.

## NFC: PN532 To Raspberry Pi Zero 2 W

Use SPI for the AITRIP PN532 reader on Raspberry Pi. On this board, chip select may be labeled `SS` or `CS`.

```text
PN532 3.3V   -> Pi 3.3V
PN532 GND    -> Pi GND
PN532 SCK    -> GPIO11 / SCLK / pin 23
PN532 MISO   -> GPIO9  / MISO / pin 21
PN532 MOSI   -> GPIO10 / MOSI / pin 19
PN532 SS/CS  -> GPIO8  / CE0  / pin 24
```

Set the PN532 module's switches/jumpers to SPI mode. The exact switch positions depend on the board.

Enable SPI on the Pi:

```bash
sudo raspi-config
```

Then choose `Interface Options` -> `SPI` -> enable, and reboot.

## Physical Placement

- Put the PN532 directly under the "place character here" spot.
- Avoid metal between the tag and reader.
- Keep loose wiring strain-relieved inside the box.
- Keep the Pi accessible to adults, not freely removable by the kid.
