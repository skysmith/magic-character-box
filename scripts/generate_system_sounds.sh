#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

out_dir="${1:-audio/system}"
mkdir -p "$out_dir"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg is required to generate system sounds." >&2
  exit 1
fi

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

fade_start() {
  awk -v duration="$1" 'BEGIN { value = duration - 0.035; if (value < 0) value = 0; printf "%.3f", value }'
}

tone() {
  local path="$1"
  local frequency="$2"
  local duration="$3"
  local volume="${4:-0.18}"
  local end
  end="$(fade_start "$duration")"
  ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i "sine=frequency=${frequency}:duration=${duration}:sample_rate=44100" \
    -af "volume=${volume},afade=t=in:st=0:d=0.01,afade=t=out:st=${end}:d=0.035" \
    "$path"
}

pause() {
  local path="$1"
  local duration="$2"
  ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i "anullsrc=channel_layout=mono:sample_rate=44100" \
    -t "$duration" \
    "$path"
}

concat_mp3() {
  local output="$1"
  shift
  local list="$tmp_dir/list.txt"
  : > "$list"
  for item in "$@"; do
    printf "file '%s'\n" "$item" >> "$list"
  done
  ffmpeg -hide_banner -loglevel error -y \
    -f concat -safe 0 -i "$list" \
    -af "loudnorm=I=-20:LRA=7:TP=-2,afade=t=in:st=0:d=0.012" \
    -codec:a libmp3lame -q:a 4 \
    "$output"
}

make_startup() {
  local parts=()
  local notes=(392.00 523.25 659.25 783.99 1046.50 987.77 783.99)
  local durations=(0.085 0.085 0.105 0.105 0.080 0.095 0.180)
  for index in "${!notes[@]}"; do
    local part="$tmp_dir/startup-${index}.wav"
    tone "$part" "${notes[$index]}" "${durations[$index]}" 0.16
    parts+=("$part")
  done
  concat_mp3 "$out_dir/startup-chime.mp3" "${parts[@]}"
}

make_unknown() {
  local a="$tmp_dir/unknown-a.wav"
  local b="$tmp_dir/unknown-b.wav"
  local c="$tmp_dir/unknown-c.wav"
  local d="$tmp_dir/unknown-d.wav"
  local e="$tmp_dir/unknown-e.wav"
  tone "$a" 523.25 0.13 0.15
  tone "$b" 659.25 0.13 0.15
  tone "$c" 587.33 0.18 0.13
  pause "$d" 0.045
  tone "$e" 783.99 0.28 0.12
  concat_mp3 "$out_dir/unknown-tag.mp3" "$a" "$b" "$c" "$d" "$e"
}

make_success() {
  local a="$tmp_dir/success-a.wav"
  local b="$tmp_dir/success-b.wav"
  local c="$tmp_dir/success-c.wav"
  tone "$a" 523.25 0.10 0.14
  tone "$b" 659.25 0.10 0.14
  tone "$c" 880.00 0.18 0.13
  concat_mp3 "$out_dir/success.mp3" "$a" "$b" "$c"
}

make_error() {
  local a="$tmp_dir/error-a.wav"
  local b="$tmp_dir/error-b.wav"
  tone "$a" 293.66 0.16 0.12
  tone "$b" 220.00 0.22 0.11
  concat_mp3 "$out_dir/error.mp3" "$a" "$b"
}

make_silence() {
  ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i "anullsrc=channel_layout=mono:sample_rate=44100" \
    -t 1.0 \
    -codec:a libmp3lame -q:a 9 \
    "$out_dir/silence.mp3"
}

make_startup
make_unknown
make_success
make_error
make_silence

cat <<MSG
Generated system sounds in $out_dir:
- startup-chime.mp3
- unknown-tag.mp3
- success.mp3
- error.mp3
- silence.mp3

Tip: replace unknown-tag.mp3 with a recorded "I found a new character" voice
clip if you want the box to speak instead of using the discovery cue.
MSG
