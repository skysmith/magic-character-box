# Open Source Notes

Magic Character Box is an educational maker project: a beginner-friendly Raspberry Pi box where NFC-tagged toys trigger songs, stories, sounds, and family voice messages.

## Positioning

Use this public framing:

> Build a magic NFC story box for kids with Raspberry Pi.

## License

The software and documentation are licensed under the Apache License 2.0. See `LICENSE`.

Media files are not included. Builders should add their own legally usable audio:

- Family recordings.
- Self-made music or sound effects.
- Public-domain recordings.
- Creative Commons audio with compatible terms and proper attribution.
- Legally owned personal files for private use, where allowed.

## Repo Hygiene

- Keep sample config generic.
- Keep committed audio folders empty except for README files and `.gitkeep`.
- Do not commit private family recordings.
- Do not commit runtime files such as `config/device_state.json`, `config/guest_links.json`, `config/volume.json`, or backup zip files.
- Do not commit API keys, Wi-Fi credentials, SSH keys, or `.env` files.
- Prefer clear diagrams, electronics closeups, and build photos.
- Strip image metadata before committing photos.
- Avoid photos that show children, home addresses, mail, Wi-Fi labels, serial numbers, or private family details.
- For custom figure docs, link to tool documentation rather than redistributing generated model files you do not have rights to share.

## Public Assets

It is okay to include:

- Generated STL files for the printable enclosure and NFC bases.
- CAD source files used to generate those STLs.
- Bench photos of the Pi, amp, NFC reader, and wiring.
- Diagrams, pin maps, and screenshots of the local admin UI with fake UIDs.

Do not include:

- Media you do not have rights to publish.
- Real family voice messages.
- Real private NFC UID mappings unless the builder intentionally wants to publish them.
- Local IP addresses, hostnames tied to a private network, or SSH details.

Run through [release-checklist.md](release-checklist.md) before publishing a tag or sharing a polished build post.

## Tutorial Voice

Keep the docs friendly, concrete, and build-first. The best first impression is not a perfect enclosure; it is the moment a character touches the box and the right sound plays.
