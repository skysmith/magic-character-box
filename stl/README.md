# Printable STL Files

This folder contains ready-to-slice STL files for the first public build.

The OpenSCAD source lives in [`../cad/`](../cad/README.md). If you change dimensions, regenerate these files with:

```bash
./cad/build_case.sh
```

## Smaller Sidecar Enclosure

Use these when mounting the electronics module to the side or back of an existing passive speaker cabinet:

- [`small-sidecar-enclosure-body.stl`](small-sidecar-enclosure-body.stl)
- [`small-sidecar-enclosure-lid.stl`](small-sidecar-enclosure-lid.stl)
- [`small-sidecar-enclosure-assembly.stl`](small-sidecar-enclosure-assembly.stl) for visual/reference use

Print the body open-side up. The lid STL is already oriented with its flat visible face on the print bed; print it as-is with supports off.

The sidecar lid has a recessed PN532 pocket on the inside. Hot-glue, foam-tape, or VHB-tape the reader into that pocket with the antenna facing the thin lid window.

The sidecar body has rounded rear strap slots that double as a cute face, plus shallow smile and cheek details on the rear pad. The Pi standoffs are raised so the Pi Zero 2 W USB ports line up better with the access window.

## Character Base

- [`nfc-character-base-flat.stl`](nfc-character-base-flat.stl)

This is a reusable 42 mm base with an underside recess for a common 25 mm NFC sticker. Drop or merge a custom figure model onto the flat top pad, then put the NFC sticker in the bottom recess.

Specs:

| Feature | Value |
| --- | ---: |
| Overall diameter | 42 mm |
| Total flat-base height | 8.8 mm |
| Top landing pad | 32 mm diameter |
| Sticker recess | 26.4 mm diameter x 0.8 mm deep |
| Intended sticker | 25 mm round NFC sticker |
| Recess notch | 7 mm diameter |

Print with the sticker recess on the bed and supports off. If your stickers are larger than 25 mm, edit [`../cad/nfc-character-base.scad`](../cad/nfc-character-base.scad) and regenerate the STL.

## Starting Print Settings

- Material: PLA or PETG.
- Layer height: 0.2 mm.
- Walls: 3.
- Top/bottom layers: 4.
- Infill: 15-20%.
- Supports: off for the body, lid, and character base.
- Screws: start with M2.5 x 8 mm or similar small self-tapping screws.

Measure your exact boards before a long final print. Clone PN532 and MAX98357A boards vary.
