// Reusable NFC character base for Magic Character Box figures.
//
// The default 42 mm puck has an underside recess for a 25 mm NFC sticker,
// plus a clean top landing pad where a generated or hand-modeled figure can
// be merged in Blender, Tinkercad, Bambu Studio, PrusaSlicer, or OpenSCAD.
//
// Render examples:
//   openscad -o build/nfc-character-base-flat.stl   -D 'part="flat"'   nfc-character-base.scad
//   openscad -o build/nfc-character-base-peg.stl    -D 'part="peg"'    nfc-character-base.scad
//   openscad -o build/nfc-character-base-socket.stl -D 'part="socket"' nfc-character-base.scad
//
// Units: millimeters.

$fn = 96;

part = "flat"; // "flat", "peg", "socket", or "demo"

base_d = 42;
base_h = 8;
base_bevel = 2;

top_pad_d = 32;
top_pad_h = 0.8;
top_pad_bevel = 0.4;

sticker_d = 25;
sticker_clearance = 1.4;
sticker_recess_depth = 0.8;
sticker_notch_d = 7;

connector_peg_d = 7.8;
connector_peg_h = 3.2;
connector_socket_d = 8.4;
connector_socket_depth = 3.4;

// If you want to union a model directly in OpenSCAD, put its STL path here and
// tune the scale/offset. Most workflows will instead merge the exported base
// and the figure STL in a slicer or mesh editor.
figure_file = "";
figure_scale = [1, 1, 1];
figure_offset = [0, 0, 0];

module bevelled_cylinder(d, h, bevel) {
  r = d / 2;
  b = min(bevel, min(r - 0.1, h / 2 - 0.1));
  rotate_extrude(convexity = 10)
    polygon(points = [
      [0, 0],
      [r - b, 0],
      [r, b],
      [r, h - b],
      [r - b, h],
      [0, h]
    ]);
}

module sticker_recess() {
  pocket_d = sticker_d + sticker_clearance;
  translate([0, 0, -0.05])
    cylinder(h = sticker_recess_depth + 0.1, d = pocket_d);

  // Small fingertip/notch relief so tape or a sticker edge can be lifted later.
  translate([0, -pocket_d / 2, -0.05])
    cylinder(h = sticker_recess_depth + 0.1, d = sticker_notch_d);
}

module top_landing_pad() {
  translate([0, 0, base_h - 0.01])
    bevelled_cylinder(top_pad_d, top_pad_h + 0.01, top_pad_bevel);
}

module connector_peg() {
  translate([0, 0, base_h + top_pad_h - 0.01])
    bevelled_cylinder(connector_peg_d, connector_peg_h + 0.01, 0.6);
}

module connector_socket_cut() {
  translate([0, 0, base_h + top_pad_h - connector_socket_depth])
    cylinder(h = connector_socket_depth + 0.2, d = connector_socket_d);
}

module base_solid(connector = "flat") {
  union() {
    bevelled_cylinder(base_d, base_h, base_bevel);
    top_landing_pad();
    if (connector == "peg") {
      connector_peg();
    }
  }
}

module nfc_character_base(connector = "flat") {
  difference() {
    base_solid(connector);
    sticker_recess();
    if (connector == "socket") {
      connector_socket_cut();
    }
  }
}

module imported_figure() {
  if (figure_file != "") {
    translate([figure_offset[0], figure_offset[1], base_h + top_pad_h + figure_offset[2]])
      scale(figure_scale)
        import(figure_file, convexity = 10);
  }
}

module demo_figure() {
  translate([0, 0, base_h + top_pad_h])
    union() {
      cylinder(h = 18, d = 11);
      translate([0, 0, 21])
        sphere(d = 15);
      translate([-8, -2, 11])
        rotate([0, 80, 0])
          cylinder(h = 12, d = 3.5);
      translate([8, -2, 11])
        rotate([0, -80, 0])
          cylinder(h = 12, d = 3.5);
    }
}

if (part == "peg") {
  nfc_character_base("peg");
} else if (part == "socket") {
  nfc_character_base("socket");
} else if (part == "demo") {
  union() {
    nfc_character_base("flat");
    demo_figure();
    imported_figure();
  }
} else {
  union() {
    nfc_character_base("flat");
    imported_figure();
  }
}
