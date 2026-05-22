# Hardware Profiles

Pick one profile before buying parts. The recommended path is boring on purpose: fewer variables means more magic.

## Recommended Kid Box

Best for the finished gift.

| Part | Choice |
| --- | --- |
| Computer | Raspberry Pi Zero 2 W or Zero 2 WH |
| Reader | PN532 over SPI |
| Audio | MAX98357A I2S amp plus 4-8 ohm passive speaker |
| Power | 5V USB wall charger, 2A or better |
| Enclosure | Printed sidecar, project box, wood, or cardboard prototype |

Why this profile:

- Small.
- Runs the Python app and dashboard.
- Uses local files.
- Built-in speaker path does not depend on Bluetooth pairing.

## Simplest Desktop Prototype

Best for proving the software before the speaker box works.

| Part | Choice |
| --- | --- |
| Computer | Any Raspberry Pi with Wi-Fi/Ethernet |
| Reader | Mock/file reader first, PN532 later |
| Audio | HDMI, USB speaker, or the computer's default audio |
| Power | Normal Pi power supply |

Run:

```bash
python -m magic_box.app --nfc mock --dry-run-audio
```

Then remove `--dry-run-audio` after adding MP3s and installing `mpg123`.

## Cheapest Use-What-You-Have Pi

Best when you already own an older board.

| Board | Notes |
| --- | --- |
| Raspberry Pi Zero W / Zero WH | Works for basic playback, but slower for dashboard uploads and `ffmpeg` conversion. |
| Raspberry Pi 3A+ | Good alternate, larger than Zero. |
| Raspberry Pi 3B/3B+/4B | Easy and fast, larger enclosure and higher power draw. |

Use the same PN532 and MAX98357A wiring pattern, but verify physical pin numbers before powering up.

## Contributor Porting Targets

These are plausible but not beginner-default.

| Board | Notes |
| --- | --- |
| Radxa Zero 3W | Small, capable, Linux-based. Needs board-specific SPI/I2S setup. |
| Orange Pi Zero 2W | Cheap and small. Expect OS and GPIO/I2S differences. |
| Other Linux SBCs | Fine if they expose SPI, I2S or USB audio, and can run Python 3.10+. |

To support a new board well, contribute:

- Working install notes.
- Pin map.
- SPI setup notes.
- Audio output setup.
- Any service file changes.

## Not This Software Stack

These can inspire a different project, but they are not drop-in replacements:

- Raspberry Pi Pico W.
- ESP32.
- Seeed XIAO ESP32S3.
- Arduino-class boards.

They can read NFC and trigger audio with different hardware, but this repo assumes Linux, Python, Flask, local file storage, `mpg123`, and systemd.
