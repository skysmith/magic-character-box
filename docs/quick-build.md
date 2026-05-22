# Quick Build Path

This is the shortest path from parts on the table to a working Magic Character Box.

For deeper explanations, use [assembly.md](assembly.md), [wiring.md](wiring.md), and [web-admin.md](web-admin.md).

## 1. Buy Or Gather Parts

Minimum recommended build:

- Raspberry Pi Zero 2 W or Zero 2 WH.
- microSD card, 16 GB or larger.
- 5V USB power supply, 2A or better.
- PN532 NFC reader that supports SPI mode.
- NTAG213/215/216 NFC stickers or fobs.
- MAX98357A I2S amp board.
- 4-8 ohm passive speaker.
- 20+ female-to-female jumper wires.
- Printed sidecar enclosure or a cardboard/project box.

Use [materials.md](materials.md) as the shopping checklist.

## 2. Wire The Boards

Wire the Pi while it is powered off.

PN532 in SPI mode:

```text
PN532 VCC/3.3V -> Pi physical pin 1 or 17
PN532 GND      -> Pi GND
PN532 SCK      -> GPIO11 / physical pin 23
PN532 MISO     -> GPIO9  / physical pin 21
PN532 MOSI     -> GPIO10 / physical pin 19
PN532 SS/CS    -> GPIO8  / physical pin 24
```

MAX98357A:

```text
MAX98357A VIN  -> Pi 5V / physical pin 2 or 4
MAX98357A GND  -> Pi GND
MAX98357A BCLK -> GPIO18 / physical pin 12
MAX98357A LRC  -> GPIO19 / physical pin 35
MAX98357A DIN  -> GPIO21 / physical pin 40
MAX98357A SD   -> GPIO16 / physical pin 36
```

Full wiring notes are in [wiring.md](wiring.md).

## 3. Install The Software

On the Pi:

```bash
cd /home/pi/magic-character-box
./scripts/install_pi.sh
sudo raspi-config
```

Enable SPI in `raspi-config`, then reboot.

If using the MAX98357A, apply the audio setup from [wiring.md](wiring.md) and reboot again.

## 4. Prove Each Piece

Run these before closing the box:

```bash
python scripts/scan_tag.py --nfc pn532
```

```bash
python -m magic_box.app --nfc mock
```

The first proves NFC. The second proves audio playback.

## 5. Start The Dashboard

Install/start the services:

```bash
sudo cp systemd/magic-character-box.service /etc/systemd/system/
sudo cp systemd/magic-character-box-admin.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now magic-character-box magic-character-box-admin
```

Open the dashboard from another device on the same Wi-Fi:

```text
http://<pi-hostname-or-ip>:8080
```

## 6. Teach The First Tag

1. Click `Setup scan`.
2. Put a blank NFC sticker on the reader.
3. Click `Scan`.
4. Name the character.
5. Click `Create character`.
6. Upload or record the first sound.
7. Click `Playback`.
8. Place the character on the box.

## 7. Before Handing It To A Kid

- Unplug and replug the Pi for a cold boot test.
- Confirm the box starts without SSH.
- Confirm the tags play the right sounds.
- Confirm the reader works through the lid.
- Confirm the Pi and wiring are not exposed.
- Keep the dashboard on your trusted home network only.
