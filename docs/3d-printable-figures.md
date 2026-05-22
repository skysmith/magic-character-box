# Custom 3D Printable Figures

You can use generic toys, wooden tokens, peg dolls, printed tokens, or custom 3D printed figures. The goal is not a perfect collectible; it is a sturdy shape that a child can place on the box.

## Good Figure Constraints

- Chunky silhouette.
- Flat base.
- No thin arms, antennas, tails, horns, or spikes.
- No unsupported dramatic overhangs.
- Big enough to hold an NFC sticker under the base.
- Easy to recognize from across a room.

## NFC Base

For common round NFC stickers:

- Base diameter: 35-45 mm.
- Underside recess: 25-30 mm wide.
- Recess depth: about 0.5-1.0 mm, or enough to hide the sticker under felt/tape.
- Keep metal away from the tag.

If you do not want to model a recess yet, print a flat base and stick the tag under it with tape or felt.

This repo includes a reusable base at `cad/nfc-character-base.scad`. It renders a 42 mm rounded puck with a 25 mm sticker recess on the underside and a flat top pad for attaching custom figures. A ready-to-slice flat base is also available at [`../stl/nfc-character-base-flat.stl`](../stl/nfc-character-base-flat.stl).

### Included Base Specs

Source file: [`../cad/nfc-character-base.scad`](../cad/nfc-character-base.scad)

Units are millimeters.

| Feature | Spec |
| --- | ---: |
| Overall puck diameter | 42 mm |
| Main puck height | 8 mm |
| Total flat-base height including top pad | 8.8 mm |
| Outer edge bevel | 2 mm |
| Top figure landing pad diameter | 32 mm |
| Top figure landing pad height | 0.8 mm |
| Sticker target diameter | 25 mm |
| Sticker pocket diameter | 26.4 mm |
| Sticker pocket depth | 0.8 mm |
| Sticker pocket clearance | 1.4 mm total diameter clearance |
| Fingertip/notch relief | 7 mm diameter |
| Peg variant connector | 7.8 mm diameter x 3.2 mm high |
| Socket variant connector | 8.4 mm diameter x 3.4 mm deep |

The flat base is the public default. Use `peg` or `socket` only when you are designing both halves of the character connection.

If your NFC stickers are larger than 25 mm, edit `sticker_d` in `nfc-character-base.scad`, regenerate the STL, and print one test puck before merging it with a character.

Print orientation:

- Put the sticker recess on the print bed.
- Keep supports off.
- Check the slicer preview around the shallow pocket ceiling.
- After printing, place the sticker into the underside recess and cover it with thin felt, tape, or label paper.

Use the flat base for most generated figures:

```bash
cd cad
openscad -o build/nfc-character-base-flat.stl -D 'part="flat"' nfc-character-base.scad
```

Workflow:

1. Generate or model the character as a separate STL/3MF.
2. Make the character's feet/base flat.
3. Place it on top of `nfc-character-base-flat.stl` in Blender, Tinkercad, Bambu Studio, PrusaSlicer, or Cura.
4. Merge/group the meshes or let the slicer treat overlapping bodies as one print.
5. Print the combined figure.
6. Stick the NFC sticker into the underside recess.

There are also peg/socket variants:

- `nfc-character-base-peg.stl`: for figures modeled with a matching bottom socket.
- `nfc-character-base-socket.stl`: for figures modeled with a matching bottom peg.

The sticker belongs on the underside of the base, not buried high inside the figure. That keeps the tag close to the PN532 reader and makes scans more reliable.

## AI 3D Tool Workflow

Tools such as Tripo, Meshy, Blender add-ons, and other AI 3D generators can help make original toy-like figures. For printing, prefer STL or 3MF export when available.

Recommended flow:

1. Generate a simple character in Tripo, Meshy, or another AI 3D tool.
2. Export STL or 3MF for printing.
3. Open the file in Blender, Tinkercad, Bambu Studio, PrusaSlicer, or Cura.
4. Add or verify a flat base.
5. Add an underside NFC recess if you want one.
6. Check the slicer preview for fragile parts and unsupported areas.
7. Print in PLA.
8. Stick the NFC tag under the base.
9. Register the UID in the Magic Character Box admin UI.

If your tool offers export options such as flattening the bottom or centering the model pivot on the build plate, use them. Every character needs a stable base.

## Prompt Template

Use prompts that describe physical printability, not just visual style:

```text
A small friendly [character] mascot toy for a child's NFC story box.
Simple rounded shapes, chunky silhouette, flat circular base, no thin parts,
no fragile antennas, single solid object, designed for FDM 3D printing,
easy to recognize.
```

Examples:

```text
A small friendly rocket mascot toy for a child's NFC story box.
Simple rounded shapes, chunky silhouette, flat circular base, no thin parts,
single solid object, designed for FDM 3D printing.
```

```text
A small gentle dinosaur mascot toy for a child's NFC story box.
Chunky body, short legs, rounded head, flat oval base, no sharp teeth,
single solid object, designed for FDM 3D printing.
```

## Cleanup Checklist

Before printing:

- Model sits flat on the build plate.
- No tiny floating pieces.
- No ultra-thin features.
- Slicer preview shows closed geometry.
- The base is wide enough for the tag.
- The figure is large enough not to be a choking hazard for the intended child.
- You have the right to share any model files or photos you publish.

## Public Tutorial Photos

Use photos that make the build easier to understand: bases, stickers, reader placement, wiring, and finished figures.

## Useful References

Check your chosen 3D tool's export documentation before relying on a generated model. The stable project requirement is simple: end with a printable STL or 3MF that has a flat bottom and a base wide enough for the NFC sticker.
