#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

sudo apt update
sudo apt install -y python3 python3-pip python3-venv mpg123 ffmpeg git bluez

python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

./scripts/generate_system_sounds.sh

if [[ -f scripts/magic-character-box-wifi-control ]]; then
  sudo install -m 0755 scripts/magic-character-box-wifi-control /usr/local/bin/magic-character-box-wifi-control
fi

if [[ -f sudoers/magic-character-box-wifi-control && -d /etc/sudoers.d ]]; then
  sudo install -m 0440 sudoers/magic-character-box-wifi-control /etc/sudoers.d/magic-character-box-wifi-control
  sudo visudo -cf /etc/sudoers.d/magic-character-box-wifi-control >/dev/null
fi

if [[ -f polkit/49-magic-character-box-networkmanager.rules && -d /etc/polkit-1/rules.d ]]; then
  sudo install -m 0644 polkit/49-magic-character-box-networkmanager.rules /etc/polkit-1/rules.d/49-magic-character-box-networkmanager.rules
fi

cat <<'MSG'

Software installed.

Next Pi setup steps:
1. Enable SPI with: sudo raspi-config
2. Reboot.
3. Wire the PN532 and MAX98357A using docs/wiring.md.
4. Test NFC: python scripts/scan_tag.py --nfc pn532
5. Test app: python -m magic_box.app --nfc pn532 --dry-run-audio
6. Start admin UI: python -m magic_box.admin --nfc pn532 --host 0.0.0.0 --port 8080

For MAX98357A audio, configure Raspberry Pi OS to route audio to the I2S amp,
then reboot before expecting mpg123 playback through the speaker.

For Bluetooth speaker experiments, put the speaker in pairing mode and use the
Bluetooth panel in the admin UI. The default finished-box audio path stays on
the wired MAX98357A ALSA speaker; install pulseaudio-utils separately only if
you intentionally route playback through Pulse/PipeWire.

For the anti-pop amp mute gate, wire MAX98357A SD to GPIO16 / physical pin 36
and add this to /boot/firmware/config.txt:

gpio=16=op,dl
MSG
