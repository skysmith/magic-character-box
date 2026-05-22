# Tutorial Roadmap

Build the box in stages. Each stage should produce a visible win before adding complexity.

## Part 1: The Ugly Magic Demo

Goal: prove the magic on a desk.

- Raspberry Pi Zero 2 W boots.
- Python app runs manually.
- Mock NFC mode works from the keyboard.
- PN532 can scan a tag.
- Local MP3 files play through any available audio output.
- Three starter identities work: Dinosaur, Rocket, and Dad Token.

Done when typing or scanning a tag changes the selected audio folder.

## Part 2: Make It Kid-Usable

Goal: make the box predictable after power-on.

- App starts on boot with systemd.
- Unknown tags produce a friendly sound or log entry.
- Repeat scans do not stutter.
- New tags can be registered with `scripts/register_character.py`.
- MAX98357A I2S audio is configured and tested.
- Volume is set to a safe default.

Done when a non-technical adult can power the box and swap characters without using SSH.

## Part 3: The Birthday Box

Goal: make the prototype feel like a gift.

- Follow [birthday-weekend-build.md](birthday-weekend-build.md) if time is short.
- Follow the assembly flow in [assembly.md](assembly.md).
- NFC reader is hidden under the top surface.
- Speaker is mounted behind a grille or holes.
- USB power remains adult-accessible.
- Wires are strain-relieved.
- Characters have hidden NFC stickers.
- Mission cards explain the game.
- Printable labels and mission-card copy are in [printable-assets.md](printable-assets.md).

Done when the child can place a character on the box and hear the right sound without seeing loose electronics.

## Part 4: Parent-Friendly Admin

Goal: make updates easy from another device on the home network.

- Local web page lists known characters.
- Parent can scan a new tag.
- Parent can name a character.
- Parent can upload or assign local audio.
- Parent can trigger test playback.
- Parent can use the last-seen tag to add a new character without copying a UID.
- Parent can download a backup of config and audio.
- Parent can read a simple event log instead of raw service logs.
- Optional voice-message flow supports family recordings.
- Printable-figure guidance helps builders make custom NFC characters.

Done when adding a new character no longer requires editing JSON by hand.

## Future: Bluetooth Receiver Mode

Goal: let a parent stream audio from a phone or laptop to the Magic Character Box like a Bluetooth speaker, while keeping NFC playback as the main child-facing experience.

- Add a dashboard control to make the Pi discoverable for a short window, such as 3 minutes.
- Pair a phone/laptop to the Pi as an audio source.
- Route incoming Bluetooth audio through the Pi's normal PipeWire/Pulse output, then through the MAX98357A speaker.
- Show connected source device status in the admin page.
- Add a `Disconnect` button for the current source.
- Decide how NFC playback behaves while Bluetooth audio is streaming: pause/stop NFC playback, or make Bluetooth receiver mode a separate parent-controlled mode.
- Keep the Pi non-discoverable by default.

This is distinct from the existing experimental Bluetooth panel, which is for sending Pi audio out to an external Bluetooth speaker. Receiver mode is the reverse path: phone or laptop audio into the box.
