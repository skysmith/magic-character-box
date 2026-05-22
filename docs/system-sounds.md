# System Sounds

System sounds make the box feel alive before any character audio is added.

Generate the sample pack:

```bash
./scripts/generate_system_sounds.sh
```

Files:

| File | Used for |
| --- | --- |
| `audio/system/startup-chime.mp3` | Played once when the main app starts. |
| `audio/system/unknown-tag.mp3` | Played when the box sees an unregistered NFC tag. |
| `audio/system/success.mp3` | Small setup/UI success cue for future workflows. |
| `audio/system/error.mp3` | Small setup/UI error cue for future workflows. |
| `audio/system/silence.mp3` | Silent warmup file for the persistent audio backend. |

The default sounds are generated with `ffmpeg` sine waves, so they are safe to regenerate and ship as examples.

## Replace The Unknown Tag Cue With Voice

For the birthday build, a voice clip is better than a beep:

```text
I found a new character.
```

Record that line from the dashboard or phone, convert it to MP3, and save it as:

```text
audio/system/unknown-tag.mp3
```

Keep it short. The app ignores the same still-present unknown tag until it is lifted, so the message will not stutter.

## Disable System Sounds

The app accepts empty paths to disable either sound:

```bash
python -m magic_box.app --startup-sound "" --unknown-sound ""
```

Or with environment variables:

```bash
MAGIC_BOX_STARTUP_SOUND="" MAGIC_BOX_UNKNOWN_SOUND="" python -m magic_box.app
```
