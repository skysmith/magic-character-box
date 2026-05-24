# AGENTS.md

## Project Orientation

Magic Character Box is a Raspberry Pi Zero 2 W NFC audio-box project. Start with:

- `README.md`
- `docs/quick-build.md`
- `docs/wiring.md`
- `docs/web-admin.md`
- `docs/troubleshooting.md`

For hardware and printable work, also read:

- `docs/3d-printable-figures.md`
- `cad/README.md`
- `stl/README.md`

Story Dock commercial product work now lives in the private sibling repo:

```text
../story-dock
```

Use that repo for Story Album product planning, hosted staging, Supabase wiring,
iPhone tap-to-record loops, manufacturing, supplier outreach, inventory,
pricing, launch planning, paid social, creator outreach, landing pages,
waitlists, preorders, and other private commercial strategy.

Keep this public repo focused on the local Magic Character Box build and
open-source bridge pieces. Do not add hosted backend secrets, launch docs,
supplier docs, or private product strategy here.

## Test Command

Run tests with:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests
```

On the Pi, the project normally lives at:

```text
/home/pi/magic-character-box
```

The Pi install may be a deployed runtime copy rather than a git checkout. Before assuming `git pull` or branch state on the Pi, check the directory. For one-off field validation, it is acceptable to copy a focused module or config file over SSH/SCP, compile/import-check it there, restart the relevant service, and then fold the change back into the local repo docs/tests.

Services:

- `magic-character-box`
- `magic-character-box-admin`

There should be one parent dashboard served by `magic-character-box-admin` on local port `8080`. Tailscale Serve may proxy that same dashboard over HTTPS; do not reintroduce a separate HTTPS admin service. For remote family recordings, use a guest-only recorder/tunnel flow rather than exposing the full dashboard publicly.

## Repo Hygiene

- Do not commit private recordings, copyrighted audio, branded character art, real private NFC UID maps, local IP screenshots, or backup zip files.
- Runtime files are intentionally ignored: `config/device_state.json`, `config/guest_links.json`, `config/story_stickers.json`, `config/volume.json`, and `config/control.json`.
- Keep the admin dashboard documented as local-network-only. Do not suggest exposing the full dashboard publicly.
- For remote family recordings, use the guest-only recorder flow, not the full admin UI.

## NFC Character Base Workflow

When adding a downloaded/generated model to the RF/NFC recessed base, use the existing base as the source of truth.

Default public base:

```text
stl/nfc-character-base-flat.stl
```

Parametric source:

```text
cad/nfc-character-base.scad
```

Base specs:

- Overall puck diameter: 42 mm.
- Main puck height: 8 mm.
- Total flat-base height including top pad: 8.8 mm.
- Top figure landing pad: 32 mm diameter x 0.8 mm high.
- Sticker recess: 26.4 mm diameter x 0.8 mm deep.
- Intended sticker: 25 mm round NTAG213/215/216 NFC sticker.
- Removal notch: 7 mm diameter.
- Peg variant: 7.8 mm diameter x 3.2 mm high.
- Socket variant: 8.4 mm diameter x 3.4 mm deep.

Preferred model-placement workflow:

1. Preserve the underside sticker recess. Do not fill, flatten, or bury it.
2. Put the custom model on the flat top landing pad.
3. Scale the model so its footprint fits visually on the 42 mm puck and is stable.
4. Flatten or trim the model bottom if needed so it intersects the top pad cleanly.
5. Merge/union the model with the base in Blender, Tinkercad, a slicer, or OpenSCAD.
6. Keep the sticker on the underside of the base so it remains close to the PN532 reader.
7. Export a printable STL/3MF and check it in a slicer before printing.

When using downloaded meshes, verify the model's true up axis visually from at least two orthographic views before exporting. Some files use Y-up or have saucer/base parts in a different orientation than expected.

If a downloaded archive is a part-separated print kit and most STL parts have their own local bed coordinates, do not assume they can be concatenated into a full assembled figure. Prefer a clearly recognizable single part as the token, or stop and ask Sky before inventing a manual assembly.

When opening regenerated files in Flash Studio, start a new project or decline restore prompts first. Flash Studio may restore an old unsaved plate and make it look like the regenerated STL is still wrong.

Print orientation for the base:

- Sticker recess down on the print bed.
- Top pad up.
- Supports off.
- Preview the shallow recess bridge before printing.

If the NFC sticker is larger than 25 mm, edit `sticker_d` in `cad/nfc-character-base.scad`, regenerate the STL, and print a test puck before merging it with a character.

## Release Readiness

Before calling the project ready for publishing or gifting, use:

```text
docs/release-checklist.md
```

The remaining high-value physical checks are cold boot, real blank-sticker teach flow, reader-through-lid distance, and child-safe enclosure inspection.
