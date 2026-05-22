# System Audio

Generated system sounds live here on the Pi.

Run:

```bash
./scripts/generate_system_sounds.sh
```

That creates:

- `startup-chime.mp3`: short boot/wake sound.
- `unknown-tag.mp3`: friendly discovery cue for unregistered tags.
- `success.mp3`: small positive UI/setup cue.
- `error.mp3`: small low cue for failures.
- `silence.mp3`: silent warmup file for the persistent audio path.

For the most magical unknown-tag behavior, replace `unknown-tag.mp3` with a family-recorded voice clip that says something like "I found a new character."
