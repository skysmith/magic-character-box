# Case CAD

This folder contains a first printable enclosure for the Magic Character Box.

The default model is a two-piece screw-top box. That is the recommended first print because you can open the box, move the NFC reader, fix wiring, replace the speaker, and iterate without destroying the case.

There is also a `sidecar` variant for the current prototype direction: keep an existing passive speaker as the speaker cabinet and Velcro a smaller electronics module to its side or back.

## Files

- `magic-character-box-case.scad`: parametric OpenSCAD source.
- `nfc-character-base.scad`: reusable character puck with an underside NFC sticker recess.
- `build_case.sh`: renders STL files when OpenSCAD is installed.
- `build/`: generated STL output.
- `../stl/`: small public STL set for the recommended sidecar enclosure and NFC base.

## Design

- Rounded/blunted dice-like edges.
- Removable screw top.
- Side-face speaker perforations on both left and right sides.
- Raised top target ring for NFC character placement on the standalone box.
- Underside lid tray sized for a PN532 V3-style reader on the standalone box.
- Bottom standoffs for Raspberry Pi Zero 2 W.
- Small amp pad for a MAX98357A board.
- Broad front cable window for USB power/data during prototyping.

## Sidecar Variant

The sidecar variant is for mounting the Pi/NFC/amp electronics to an existing passive speaker cabinet with hook-and-loop tape.

Differences from the standalone box:

- Slightly smaller footprint and height.
- No printed speaker grille/perforations.
- Flat rear Velcro pad.
- Two rounded vertical strap slots as a backup to adhesive Velcro. They also make the rear face look intentionally character-like.
- Shallow smile and cheek details on the rear pad.
- Small side exit for speaker wires running to the speaker terminals.
- The PN532 mounts in a shallow recessed pocket under the lid instead of a printed tray. This keeps the antenna close to the NFC tag and gives clone PN532 boards extra clearance.
- The ready-to-slice sidecar lid omits the raised NFC target ring and is exported with the flat visible face on the print bed, so it can print without support material. Mark the target area later with paint, vinyl, or a sticker.

Keep the PN532/NFC target several inches away from the speaker magnet and metal hardware when choosing the exact Velcro location.

Ready-to-slice sidecar files are copied to [`../stl/`](../stl/README.md):

- `small-sidecar-enclosure-body.stl`
- `small-sidecar-enclosure-lid.stl`
- `small-sidecar-enclosure-assembly.stl` for visual/reference use

## Hardware Assumptions

- Raspberry Pi Zero 2 W: 65 mm x 30 mm board footprint.
- PN532 V3-style NFC reader: about 42.7 mm x 40.4 mm.
- MAX98357A amp breakout: mounted with tape/zip tie on the small internal amp pad.
- Speaker: small passive speaker mounted behind either perforated side face with foam tape, hot glue, or a printed bracket in a later revision.
- Sidecar Pi standoffs: raised to 8 mm so the Pi Zero 2 W USB ports line up better with the cable access window.

Measure your exact parts before a final gift print. Clone boards can vary.

## Render STLs

```bash
cd cad
./build_case.sh
```

Or render one part:

```bash
openscad -o build/magic-character-box-body.stl -D 'part="body"' magic-character-box-case.scad
openscad -o build/magic-character-box-lid.stl -D 'part="lid"' magic-character-box-case.scad
```

Render the smaller sidecar parts:

```bash
openscad -o build/magic-character-box-sidecar-body.stl -D 'variant="sidecar"' -D 'part="body"' magic-character-box-case.scad
openscad -o build/magic-character-box-sidecar-lid.stl  -D 'variant="sidecar"' -D 'part="lid"'  magic-character-box-case.scad
```

Render only the reusable NFC figure bases:

```bash
openscad -o build/nfc-character-base-flat.stl   -D 'part="flat"'   nfc-character-base.scad
openscad -o build/nfc-character-base-peg.stl    -D 'part="peg"'    nfc-character-base.scad
openscad -o build/nfc-character-base-socket.stl -D 'part="socket"' nfc-character-base.scad
```

## Reusable Character Base

The character base is a 42 mm rounded puck sized for common 25 mm NTAG213/215/216 stickers.

- `nfc-character-base-flat.stl`: default base. Merge a figure STL onto the flat top pad.
- `nfc-character-base-peg.stl`: adds a small center peg for figures designed with a matching hole.
- `nfc-character-base-socket.stl`: adds a center socket for figures designed with a matching peg.
- `nfc-character-base-demo.stl`: quick visual reference with a simple placeholder figure.

The NFC sticker goes in the underside recess. Keep the sticker on the bottom face so it sits close to the reader in the box lid. Cover it with thin felt or tape if the figure will slide around.

Base source-of-truth dimensions:

| Feature | Value |
| --- | ---: |
| Base diameter | 42 mm |
| Main base height | 8 mm |
| Flat variant total height | 8.8 mm |
| Top landing pad | 32 mm diameter x 0.8 mm high |
| Sticker recess | 26.4 mm diameter x 0.8 mm deep |
| Intended sticker | 25 mm round NTAG213/215/216 |
| Removal notch | 7 mm diameter |
| Peg variant | 7.8 mm diameter x 3.2 mm high |
| Socket variant | 8.4 mm diameter x 3.4 mm deep |

More figure workflow notes are in [`../docs/3d-printable-figures.md`](../docs/3d-printable-figures.md).

## Print Notes

Suggested first settings:

- Material: PLA or PETG.
- Layer height: 0.2 mm.
- Walls: 3.
- Top/bottom layers: 4.
- Infill: 15-20%.
- Supports: off for the sidecar body and lid. Check your slicer preview for the standalone lid lip and PN532 tray.
- Screws: start with M2.5 or small self-tapping screws. The lid uses clearance holes and the body has pilot holes.

Print the body open-side up. The sidecar lid STL is already oriented flat-side down; print it as-is with the internal lip and PN532 recess facing upward.

For the sidecar, put adhesive Velcro on the flat rear pad. If the tape alone feels weak, pass a thin Velcro strap or zip tie through the two rounded vertical eye slots. Route the speaker wire through the small side exit.

Print the NFC character base flat-side down with supports off. The underside sticker recess is shallow enough to bridge cleanly on most printers, but check the slicer preview.

For the full public build flow, see [`../docs/assembly.md`](../docs/assembly.md).

## Assembly Order

1. Print the body and lid.
2. Test fit screws before installing electronics.
3. Mount the Pi Zero 2 W on the floor standoffs.
4. Mount the MAX98357A amp on the internal amp pad.
5. For the sidecar lid, hot-glue, foam-tape, or VHB-tape the PN532 reader into the recessed pocket, antenna facing the thin lid window. For the standalone lid, mount the PN532 reader under the lid in the tray.
6. For the standalone box, mount the speaker behind a side perforation field. For the sidecar, route speaker wires out to the speaker terminals.
7. Wire everything with the Pi powered off.
8. Run NFC/audio tests before closing the lid.

## Paused-Print Variant

A mid-print component capture is possible, but it is not the recommended first version. It makes repairs and debugging much harder, and the print bed/nozzle environment is not friendly to electronics.

If you still want a captured-electronics print later, use this screw-top model as the dimensional prototype first. Once board positions are confirmed, a later model can remove the lid seam and add a slicer pause just before the top bridge closes.

## Sources For Dimensions

- Raspberry Pi Zero 2 W mechanical drawing: https://datasheets.raspberrypi.com/rpizero2/raspberry-pi-zero-2-w-mechanical-drawing.pdf
- Raspberry Pi Zero 2 W hardware docs: https://www.raspberrypi.com/documentation/hardware/raspberry-pi-zero-2/
- PN532 V3 guide dimensions commonly list the board around 42.7 mm x 40.4 mm.
