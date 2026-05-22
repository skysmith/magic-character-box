#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
mkdir -p build
mkdir -p ../stl

openscad -o build/magic-character-box-body.stl -D 'part="body"' magic-character-box-case.scad
openscad -o build/magic-character-box-lid.stl -D 'part="lid"' magic-character-box-case.scad
openscad -o build/magic-character-box-assembly.stl -D 'part="assembly"' magic-character-box-case.scad
openscad -o build/magic-character-box-sidecar-body.stl -D 'variant="sidecar"' -D 'part="body"' magic-character-box-case.scad
openscad -o build/magic-character-box-sidecar-lid.stl -D 'variant="sidecar"' -D 'part="lid"' magic-character-box-case.scad
openscad -o build/magic-character-box-sidecar-assembly.stl -D 'variant="sidecar"' -D 'part="assembly"' magic-character-box-case.scad
openscad -o build/nfc-character-base-flat.stl -D 'part="flat"' nfc-character-base.scad
openscad -o build/nfc-character-base-peg.stl -D 'part="peg"' nfc-character-base.scad
openscad -o build/nfc-character-base-socket.stl -D 'part="socket"' nfc-character-base.scad
openscad -o build/nfc-character-base-demo.stl -D 'part="demo"' nfc-character-base.scad

cp build/magic-character-box-sidecar-body.stl ../stl/small-sidecar-enclosure-body.stl
cp build/magic-character-box-sidecar-lid.stl ../stl/small-sidecar-enclosure-lid.stl
cp build/magic-character-box-sidecar-assembly.stl ../stl/small-sidecar-enclosure-assembly.stl
cp build/nfc-character-base-flat.stl ../stl/nfc-character-base-flat.stl

echo "Wrote:"
echo "  cad/build/magic-character-box-body.stl"
echo "  cad/build/magic-character-box-lid.stl"
echo "  cad/build/magic-character-box-assembly.stl"
echo "  cad/build/magic-character-box-sidecar-body.stl"
echo "  cad/build/magic-character-box-sidecar-lid.stl"
echo "  cad/build/magic-character-box-sidecar-assembly.stl"
echo "  cad/build/nfc-character-base-flat.stl"
echo "  cad/build/nfc-character-base-peg.stl"
echo "  cad/build/nfc-character-base-socket.stl"
echo "  cad/build/nfc-character-base-demo.stl"
echo "  stl/small-sidecar-enclosure-body.stl"
echo "  stl/small-sidecar-enclosure-lid.stl"
echo "  stl/small-sidecar-enclosure-assembly.stl"
echo "  stl/nfc-character-base-flat.stl"
