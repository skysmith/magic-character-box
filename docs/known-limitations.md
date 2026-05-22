# Known Limitations

This project is intentionally small and local-first. These limits are part of the v0.1 design.

## Local Network Only

The admin dashboard has no login. Keep it on a trusted home network. Do not expose the full dashboard to the public internet.

For remote family voice messages, use a temporary guest-only recorder link as described in [web-admin.md](web-admin.md#guest-recording-links).

## Raspberry Pi Linux Stack

This repo assumes:

- Linux
- Python
- Flask
- local file storage
- `mpg123`
- `ffmpeg`
- systemd services

Raspberry Pi Pico W, ESP32, Seeed XIAO, and Arduino-class boards are not drop-in replacements. They could inspire a smaller firmware-only fork, but they cannot run this dashboard/software stack as-is.

## No Streaming Music Included

The project plays local audio files. It does not include Spotify, Apple Music, commercial radio, or copyrighted songs.

Builders should add family recordings, original audio, public-domain audio, Creative Commons audio with compatible terms, or legally usable personal files.

## Bluetooth Is Experimental

The dashboard includes an experimental Bluetooth output panel for contributors. The recommended kid box uses the wired MAX98357A amp and passive speaker because it boots predictably and does not depend on pairing.

Bluetooth receiver mode, where a phone streams into the box like a Bluetooth speaker, is a future roadmap item.

## No Tag Writing In The MVP

The box maps NFC tag UIDs to local folders. It does not write data onto NFC stickers.

That is simpler and harder to break. If tag writing is added later, it should be a separate advanced tool.

## Not A Finished Toy Product

This is an educational maker project. A child-facing build still needs adult judgment:

- Enclose electronics.
- Strain-relieve wires.
- Avoid loose LiPo batteries.
- Avoid sharp edges.
- Keep small removable parts appropriate for the child's age.
