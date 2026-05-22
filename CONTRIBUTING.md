# Contributing

Thanks for helping make Magic Character Box better.

This project is meant to stay tutorial-friendly: small parts, local files, beginner-readable Python, and hardware choices that a parent, teacher, or makerspace can realistically assemble.

## Good Contributions

- Clear setup fixes for Raspberry Pi OS.
- Safer wiring notes.
- Better error messages.
- Small, testable Python improvements.
- Docs for common PN532 or MAX98357A board variants.
- Parent-friendly features such as volume limits, quiet hours, or easier character registration.

## Media Rules

- Do not add copyrighted songs, audiobooks, sound effects, or voice clips to the repo.
- Only add photos or media that you have the right to publish.
- Do not add private family recordings, real private NFC UID mappings, local IP screenshots, or backup zip files.

## Safety Rules

- Do not recommend connecting a passive speaker directly to a Raspberry Pi GPIO pin.
- Do not recommend loose LiPo batteries for kid-facing builds.
- Keep mains power, exposed wiring, and removable electronics out of child reach.
- Prefer USB wall power or a sealed USB power bank for early builds.

## Development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=src python3 -m unittest discover -s tests
```

Use mock NFC and dry-run audio for local changes:

```bash
PYTHONPATH=src python3 -m magic_box.app --nfc mock --dry-run-audio
```

Before a release or polished public post, use `docs/release-checklist.md`.
