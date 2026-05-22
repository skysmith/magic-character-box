# Materials List

This bill of materials is for the first public Magic Character Box build: Raspberry Pi Zero 2 W or Zero 2 WH, PN532 NFC/RFID reader, MAX98357A I2S amp, passive speaker, and a printed or simple handmade enclosure.

Exact brands do not matter. Match the part type, voltage, and interface.

## Core Electronics

| Qty | Item | Use | Notes |
| ---: | --- | --- | --- |
| 1 | Raspberry Pi Zero 2 W or Zero 2 WH | Main computer | The `WH`/with-headers version saves soldering. Zero 2 W without headers needs a 40-pin male header soldered on. |
| 1 | microSD card, 16 GB or larger | Pi OS and local audio | 32 GB gives more room for stories and songs. |
| 1 | 5V USB power supply, 2A or better | Powers the Pi and amp | Use a known-good charger. Weak power causes weird audio and boot issues. |
| 1 | PN532 NFC/RFID reader module | Reads character/tag UIDs | Use a 13.56 MHz PN532 board that supports SPI mode. AITRIP/Elechouse-style PN532 V3 boards work. |
| 3-10 | NTAG213/215/216 NFC stickers or fobs | Hidden IDs for characters | Round 25 mm stickers fit the printable figure base. |
| 1 | MAX98357A I2S DAC/amp breakout board | Drives the passive speaker | Do not connect a passive speaker directly to the Pi. |
| 1 | Passive speaker | Sound output | 4-8 ohm is ideal. A larger speaker cabinet can sound much better than a tiny bare driver. |

## Headers, Wires, And Small Connectors

Most frustrating "bad part" issues in this project are actually loose jumper wires or missing headers. For a first build, buy more jumpers than the exact count.

| Qty | Item | Use | Notes |
| ---: | --- | --- | --- |
| 1 | 40-pin male header for Pi Zero | Pi GPIO access | Not needed if you buy a Zero 2 WH or Zero WH with headers already soldered. |
| 1-2 | 0.1 inch male header strips | PN532/MAX98357A pins | Many breakout boards include unsoldered header strips. |
| 12+ | Female-to-female Dupont jumper wires, 10-20 cm | Pi-to-PN532 and Pi-to-MAX98357A wiring | This is the main type when the Pi and breakout boards both have male header pins. |
| 4-6 | Extra female-to-female jumpers | Ground sharing, SD mute, experiments | Having spares saves a trip when one wire is flaky. |
| Optional | Female-to-male Dupont jumpers | Breadboard/prototype wiring | Useful if you test through a breadboard before putting parts in the case. |
| Optional | 22-26 AWG stranded wire | Speaker leads or cleaner internal runs | Use this when the speaker needs longer or more flexible wires than Dupont jumpers. |
| Optional | 2-pin JST connector pair or screw-terminal pigtail | Removable speaker connection | Handy if you want the speaker/case to unplug cleanly. |
| Optional | Heat shrink, tape, or small zip ties | Strain relief | Keeps kid-box vibration and lid removal from tugging pins loose. |

### Wire Count For The Recommended Build

Minimum Pi-to-board jumpers:

- MAX98357A amp: `VIN`, `GND`, `BCLK`, `LRC`, `DIN`, and optional/recommended `SD` mute = 5-6 wires.
- PN532 reader: `3.3V`, `GND`, `SCK`, `MISO`, `MOSI`, `SS/CS` = 6 wires.
- Speaker: 2 wires from the MAX98357A speaker terminal to the passive speaker.

So the practical minimum is 11-12 female-to-female jumpers plus speaker wire. Buy a 20-pack or 40-pack.

## Board Options

The project is written for Raspberry Pi OS first. Other Linux boards can work, but the further you get from Raspberry Pi, the more likely you are to debug GPIO names, SPI overlays, I2S audio, and Python hardware libraries instead of building the box.

| Board | Recommendation | Notes |
| --- | --- | --- |
| Raspberry Pi Zero 2 W / Zero 2 WH | Best default | Small, cheap, Wi-Fi/Bluetooth, 40-pin GPIO, enough CPU for Flask admin, `ffmpeg`, and smooth playback. |
| Raspberry Pi Zero W / Zero WH | Works for basic playback | Same 512 MB RAM class and 40-pin header, but single-core. Good if you already own one; expect slower installs, uploads, and audio conversion. Prefer `WH` if you do not want to solder the header. |
| Raspberry Pi 3A+ | Good alternate | Faster than Zero W, built-in wireless, 40-pin GPIO, but physically larger than a Zero. |
| Used Raspberry Pi 3B/3B+/4B | Easy but bigger | Great for a bench demo and plenty fast. Larger board and higher power draw make the enclosure less cute. |
| Raspberry Pi 5 | Overkill | Works, but costs more, draws more power, and does not improve the core toy experience. |
| Radxa Zero 3W | Experimental contributor path | Similar small-board idea with 40-pin GPIO, often more RAM/CPU. Not a drop-in Raspberry Pi OS target; expect board-specific SPI/I2S setup. |
| Orange Pi Zero 2W | Experimental contributor path | Cheap and small, with community OS options such as DietPi. Treat as a porting target, not the beginner tutorial path. |
| Raspberry Pi Pico W / ESP32 | Not a drop-in replacement | Microcontrollers can do a simpler NFC/audio toy, but not this Linux/Python/Flask/`mpg123` software stack without a rewrite. |

If you use a non-Pi board, the board needs:

- Linux with Python 3.10+.
- Reliable Wi-Fi or Ethernet for the admin page.
- SPI GPIO access for the PN532.
- I2S audio support for MAX98357A, or a USB audio device/speaker instead.
- 3.3V GPIO logic for NFC signals.

## Enclosure And Figure Parts

| Qty | Item | Use | Notes |
| ---: | --- | --- | --- |
| 1 | Printed sidecar enclosure body | Electronics case | Use [`../stl/small-sidecar-enclosure-body.stl`](../stl/small-sidecar-enclosure-body.stl). |
| 1 | Printed sidecar enclosure lid | NFC reader/top cover | Use [`../stl/small-sidecar-enclosure-lid.stl`](../stl/small-sidecar-enclosure-lid.stl). |
| 4 | M2.5 x 8 mm screws | Lid screws | Small self-tapping screws also work if they fit your print. |
| 1 | Hook-and-loop tape | Mount sidecar to speaker cabinet | Use a generous patch so the module does not twist loose. |
| 3-5 | Toy figures, tokens, or printed bases | Character objects | Use generic/original figures for public tutorial photos. |
| 3-5 | Printed NFC figure bases | Holds NFC stickers | Use [`../stl/nfc-character-base-flat.stl`](../stl/nfc-character-base-flat.stl). |
| 1 roll | Thin felt, tape, or label paper | Covers sticker recess | Helps keep stickers from peeling or scraping. |

The included NFC figure base is sized for 25 mm round stickers. See [3d-printable-figures.md](3d-printable-figures.md#included-base-specs) for exact dimensions before buying larger tags.

## Tools

- Computer for flashing Raspberry Pi OS and copying the repo.
- 3D printer, or a print service.
- Small Phillips screwdriver.
- Wire cutters or flush cutters.
- Soldering iron if your Pi header, speaker connector, or amp board is not already soldered.
- Multimeter, optional but useful for checking 5V, 3.3V, and ground.
- Phone or laptop on the same network for the admin page.

## Software And Files

- Raspberry Pi OS Lite or Desktop.
- Python 3.10 or newer.
- `mpg123` for MP3 playback.
- `ffmpeg` for uploaded/recorded audio conversion.
- This repo copied to `/home/pi/magic-character-box` or another known folder.
- Local audio files you have the right to use.

The install script handles the system packages and Python environment:

```bash
./scripts/install_pi.sh
```

## Optional Upgrades

| Item | Why add it |
| --- | --- |
| Bigger passive speaker cabinet | Better sound without changing the software. |
| USB power bank | Portable prototype power, if it is sealed and kid-safe. |
| Arcade button | Future play/pause or next-track control. |
| Status LED plus resistor | Future boot/scan/play feedback. |
| Short JST or Dupont extension leads | Cleaner removable wiring inside the case. |
| Heat-set inserts | More durable screw points than plastic pilot holes. |

## Do Not Use

- A bare passive speaker connected directly to Raspberry Pi GPIO.
- Loose LiPo batteries in a kid-facing prototype.
- Metal enclosures or metal plates between the NFC tag and PN532 reader.
- Copyrighted songs, character art, or private recordings in a public repo.
- UHF RFID tags/readers. This project expects 13.56 MHz NFC tags read by a PN532-style reader.

## First Shopping Pass

If starting from nothing, buy or gather:

1. Raspberry Pi Zero 2 W/WH, microSD card, and 5V power supply.
2. PN532 NFC reader plus NTAG213/215/216 stickers.
3. MAX98357A amp plus passive speaker.
4. 20+ female-to-female jumper wires, headers, speaker wire, and small screws.
5. Printed enclosure parts, or a cardboard/project box for the ugly demo.

Then build in this order:

1. Software mock mode.
2. PN532 scan test.
3. MAX98357A audio test.
4. Register stickers.
5. Print or assemble the enclosure.

## Source Notes

- Raspberry Pi lists Zero W as a 1GHz single-core, 512 MB board with wireless and a HAT-compatible 40-pin header.
- Raspberry Pi lists Zero 2 W as a 1GHz quad-core Arm Cortex-A53, 512 MB board with wireless and a HAT-compatible 40-pin header footprint.
- Adafruit's PN532 Python guide recommends SPI on Raspberry Pi because I2C and UART are not reliable for that breakout on Pi.
- Adafruit's MAX98357A guide describes it as an I2S class-D mono amp that works with Raspberry Pi and can drive 4-8 ohm speakers from a 5V supply.
