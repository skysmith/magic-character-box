// Magic Character Box enclosure
//
// Print parts:
//   openscad -o build/magic-character-box-body.stl -D 'part="body"' magic-character-box-case.scad
//   openscad -o build/magic-character-box-lid.stl  -D 'part="lid"'  magic-character-box-case.scad
//   openscad -o build/magic-character-box-sidecar-body.stl -D 'variant="sidecar"' -D 'part="body"' magic-character-box-case.scad
//
// Units: millimeters.

$fn = 40;

part = "assembly"; // "assembly", "body", or "lid"
variant = "standalone"; // "standalone" or "sidecar"
sidecar = variant == "sidecar";

case_w = sidecar ? 112 : 124;
case_d = sidecar ? 84 : 96;
body_h = sidecar ? 48 : 58;
lid_t = 7;
wall = 3;
corner_r = sidecar ? 8 : 10;
fit_clearance = 0.55;

screw_offset = sidecar ? 10 : 12;
screw_post_d = 10;
screw_pilot_d = 2.4;      // pilot for M2.5-ish self tapping screws
screw_lid_clearance_d = 3.2;
screw_counterbore_d = 6.4;
screw_counterbore_h = 2.0;

lid_lip_depth = 3.2;
lid_lip_wall = 2.1;

pi_board = [65, 30];
pi_origin = sidecar ? [16, 14] : [18, 12];
pi_hole_offset = 3.5;
pi_standoff_h = sidecar ? 8 : 5;

pn532_board = [43, 41];
pn532_tray_clearance = 1.8;
pn532_recess = [58, 56];
pn532_recess_skin = 1.6;
pn532_recess_r = 3;

amp_pad = [25, 22];
amp_pad_origin = sidecar ? [70, 50] : [86, 14];

speaker_center_z = 30;
speaker_center_y = case_d / 2;
speaker_hole_d = 4.2;
speaker_hole_spacing = 8;
speaker_pattern_d = 43;

velcro_pad_w = 76;
velcro_pad_h = 30;
velcro_pad_t = 1.2;
velcro_pad_z = 12;

strap_slot_w = 6;
strap_slot_h = 26;
strap_slot_spacing = 46;

speaker_wire_exit = [wall + 4, 18, 8];

cable_window_z = sidecar ? 7 : 10;
cable_window_h = sidecar ? 22 : 16;

module rounded_box(size, r) {
  translate([r, r, r])
    minkowski() {
      cube([size[0] - 2 * r, size[1] - 2 * r, size[2] - 2 * r]);
      sphere(r = r);
    }
}

module rounded_prism(size, r) {
  translate([r, r, 0])
    linear_extrude(height = size[2])
      offset(r = r)
        square([size[0] - 2 * r, size[1] - 2 * r]);
}

module screw_positions() {
  for (p = [
    [screw_offset, screw_offset],
    [case_w - screw_offset, screw_offset],
    [screw_offset, case_d - screw_offset],
    [case_w - screw_offset, case_d - screw_offset]
  ]) {
    translate([p[0], p[1], 0]) children();
  }
}

module standoff(x, y, h = 5, d = 6, hole_d = 2.2) {
  translate([x, y, wall - 0.2])
    difference() {
      cylinder(h = h + 0.2, d = d);
      translate([0, 0, -0.2])
        cylinder(h = h + 0.6, d = hole_d);
    }
}

module pi_standoffs() {
  for (x = [pi_hole_offset, pi_board[0] - pi_hole_offset])
    for (y = [pi_hole_offset, pi_board[1] - pi_hole_offset])
      standoff(pi_origin[0] + x, pi_origin[1] + y, pi_standoff_h, 6.2, 2.2);
}

module amp_pad_mount() {
  translate([amp_pad_origin[0], amp_pad_origin[1], wall - 0.2])
    difference() {
      union() {
        cube([amp_pad[0], amp_pad[1], 1.6]);
        translate([2, 2, 1.4]) cube([amp_pad[0] - 4, 2, 2.2]);
        translate([2, amp_pad[1] - 4, 1.4]) cube([amp_pad[0] - 4, 2, 2.2]);
      }
      translate([amp_pad[0] / 2 - 1.2, -1, 1.8])
        cube([2.4, amp_pad[1] + 2, 1.6]);
    }
}

module body_shell() {
  difference() {
    rounded_box([case_w, case_d, body_h], corner_r);
    translate([wall, wall, wall])
      rounded_box([case_w - 2 * wall, case_d - 2 * wall, body_h + wall], corner_r - wall);
  }
}

module sidecar_velcro_pad() {
  if (sidecar)
    translate([(case_w - velcro_pad_w) / 2, case_d - 0.15, velcro_pad_z])
      cube([velcro_pad_w, velcro_pad_t, velcro_pad_h]);
}

module screw_posts() {
  screw_positions()
    translate([0, 0, wall - 0.2])
      difference() {
        cylinder(h = body_h - wall - 1.0, d = screw_post_d);
        translate([0, 0, -0.2])
          cylinder(h = body_h - wall + 0.4, d = screw_pilot_d);
      }
}

module speaker_holes() {
  for (dy = [-16 : speaker_hole_spacing : 16])
    for (dz = [-16 : speaker_hole_spacing : 16])
      if (sqrt(dy * dy + dz * dz) <= speaker_pattern_d / 2)
        translate([case_w / 2, speaker_center_y + dy, speaker_center_z + dz])
          rotate([0, 90, 0])
            cylinder(h = case_w + 6, d = speaker_hole_d, center = true);
}

module cable_window() {
  // Broad front window for USB power/data cable access during prototyping.
  translate([26, -1, cable_window_z])
    cube([72, wall + 2, cable_window_h]);
}

module bottom_air_slots() {
  for (x = [38 : 10 : 88])
    translate([x, case_d - 18, -1])
      cube([4, 30, wall + 2]);
}

module rear_rounded_slot_cut(x, z, w, h, cut_depth = wall + velcro_pad_t + 4) {
  translate([x - w / 2, case_d - wall - 1, z - h / 2 + w / 2])
    cube([w, cut_depth, h - w]);
  for (dz = [-h / 2 + w / 2, h / 2 - w / 2])
    translate([x, case_d + velcro_pad_t / 2, z + dz])
      rotate([90, 0, 0])
        cylinder(h = cut_depth, d = w, center = true);
}

module rear_round_dimple_cut(x, z, d, cut_depth = velcro_pad_t + 0.8) {
  translate([x, case_d + velcro_pad_t / 2, z])
    rotate([90, 0, 0])
      cylinder(h = cut_depth, d = d, center = true);
}

module sidecar_face_cuts() {
  if (sidecar) {
    // The two strap slots double as friendly rounded eyes.
    for (x = [case_w / 2 - strap_slot_spacing / 2, case_w / 2 + strap_slot_spacing / 2])
      rear_rounded_slot_cut(x, body_h / 2, strap_slot_w + 1.5, strap_slot_h);

    // A dotted smile engraved into the raised rear pad.
    for (a = [-52 : 13 : 52])
      rear_round_dimple_cut(
        case_w / 2 + 18 * sin(a),
        16.5 + 5.5 * (1 - cos(a)),
        3.0
      );

    // Small cheek dimples. They stay shallow so the pad still has plenty of
    // surface for tape/Velcro if this side is mounted against a speaker.
    for (x = [case_w / 2 - 28, case_w / 2 + 28])
      rear_round_dimple_cut(x, 19, 4.2);
  }
}

module sidecar_speaker_wire_exit() {
  if (sidecar)
    translate([case_w - wall - 1, case_d / 2 - speaker_wire_exit[1] / 2, 9])
      cube(speaker_wire_exit);
}

module body() {
  difference() {
    union() {
      body_shell();
      sidecar_velcro_pad();
      screw_posts();
      pi_standoffs();
      amp_pad_mount();
    }
    if (!sidecar)
      speaker_holes();
    cable_window();
    bottom_air_slots();
    sidecar_face_cuts();
    sidecar_speaker_wire_exit();
  }
}

module lid_lip() {
  lip_w = case_w - 2 * wall - fit_clearance;
  lip_d = case_d - 2 * wall - fit_clearance;
  translate([wall + fit_clearance / 2, wall + fit_clearance / 2, -lid_lip_depth + 0.2])
    difference() {
      rounded_prism([lip_w, lip_d, lid_lip_depth], corner_r - wall - 0.5);
      translate([lid_lip_wall, lid_lip_wall, -0.2])
        rounded_prism([
          lip_w - 2 * lid_lip_wall,
          lip_d - 2 * lid_lip_wall,
          lid_lip_depth + 0.4
        ], max(1.5, corner_r - wall - lid_lip_wall - 0.5));
    }
}

module nfc_target_ring() {
  translate([case_w / 2, case_d / 2, lid_t - 0.1])
    difference() {
      cylinder(h = 0.8, d = 54);
      translate([0, 0, -0.1])
        cylinder(h = 1.0, d = 44);
    }
}

module pn532_lid_tray() {
  tray_w = pn532_board[0] + 2 * pn532_tray_clearance;
  tray_d = pn532_board[1] + 2 * pn532_tray_clearance;
  rail_h = 2.2;
  rail_w = 2.2;
  x0 = case_w / 2 - tray_w / 2;
  y0 = case_d / 2 - tray_d / 2;
  z0 = -rail_h + 0.2;

  // Four low rails: hold the PN532 board close to the top without covering the antenna.
  translate([x0, y0, z0]) cube([tray_w, rail_w, rail_h]);
  translate([x0, y0 + tray_d - rail_w, z0]) cube([tray_w, rail_w, rail_h]);
  translate([x0, y0, z0]) cube([rail_w, tray_d, rail_h]);
  translate([x0 + tray_w - rail_w, y0, z0]) cube([rail_w, tray_d, rail_h]);
}

module pn532_lid_recess_cut() {
  // Sidecar build: maximize NFC range by letting the PN532 sit almost flush
  // against a thin plastic window. Glue/tape the board into the pocket.
  translate([
    case_w / 2 - pn532_recess[0] / 2,
    case_d / 2 - pn532_recess[1] / 2,
    -0.05
  ])
    rounded_prism([
      pn532_recess[0],
      pn532_recess[1],
      lid_t - pn532_recess_skin + 0.1
    ], pn532_recess_r);
}

module lid_screw_holes() {
  screw_positions()
    translate([0, 0, -lid_lip_depth - 0.5])
      union() {
        cylinder(h = lid_t + lid_lip_depth + 1.5, d = screw_lid_clearance_d);
        translate([0, 0, lid_lip_depth + lid_t - screw_counterbore_h])
          cylinder(h = screw_counterbore_h + 1, d = screw_counterbore_d);
      }
}

module lid_model(show_target_ring = true) {
  difference() {
    union() {
      rounded_prism([case_w, case_d, lid_t], corner_r);
      lid_lip();
      if (show_target_ring)
        nfc_target_ring();
      if (!sidecar)
        pn532_lid_tray();
    }
    lid_screw_holes();
    if (sidecar)
      pn532_lid_recess_cut();
  }
}

module lid_print_flat_top() {
  // Print with the visible top face on the bed. The sidecar lid omits the
  // raised NFC target ring so this exported STL has a single flat bed face.
  translate([0, case_d, lid_t])
    rotate([180, 0, 0])
      lid_model(show_target_ring = false);
}

module assembly() {
  body();
  translate([0, 0, body_h + 1.5])
    lid_model(show_target_ring = !sidecar);
}

if (part == "body") {
  body();
} else if (part == "lid") {
  if (sidecar)
    lid_print_flat_top();
  else
    lid_model(show_target_ring = true);
} else {
  assembly();
}
