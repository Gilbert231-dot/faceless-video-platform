#!/usr/bin/env python
"""Cut a reference clip for the local voice engine out of audio you already have.

    python make_voice_ref.py clip.mp3 --out assets/voices/male.wav
    python make_voice_ref.py clip.mp3 --start 1:20 --seconds 14 --out assets/voices/female.wav
    python make_voice_ref.py --list          # what the pipeline will pick up

WHY A CLIP AND NOT A RECORDING
------------------------------
The local engine (tts_local.py, Chatterbox) clones a voice from a few seconds of
speech: it encodes that clip once and speaks the whole narration with the same
timbre. The cheapest source is audio the pipeline has already paid for - a past
ElevenLabs narration, or the uploaded video itself - so no new recording is needed.
The engine reads at most the first 10-15 seconds of whatever it is given, which is
what this tool produces.

WHAT IT DOES TO THE CLIP
------------------------
  * takes one window (default: the first 14 seconds after the leading silence)
  * trims leading/trailing silence, so the model does not spend its reference on a
    room tone
  * mono, 24 kHz - the engine's own rate, so nothing is resampled twice
  * loudness-normalised to -18 LUFS, so every reference produces the same level
  * checked for content once written: a clip with no broadband speech in it - a
    tone, a hum, a stuck test signal - is refused and deleted. Duration and level
    checks cannot catch that; they happily passed a nine-second drone once.

It refuses a clip that has less than 5 seconds of speech in it, because the engine
asserts on that and a run would otherwise die 20 minutes into a render.

If no reference exists, nothing breaks: the engine speaks in the voice that ships
with the model and tts_local.py says so once.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

import config  # noqa: E402

DEFAULT_SECONDS = 14.0
MIN_SPEECH = 5.0          # the engine asserts on references shorter than 5s
SPEECHY_FRAME = 0.05      # a frame this flat (spectrally) is broadband, i.e. voice
MIN_FLATNESS_MEAN = 0.030  # a pure tone sits around 0.003, speech around 0.05-0.08
MIN_SPEECHY_FRACTION = 0.10  # speech: 30-55% of frames; a drone: 0%


def ffmpeg():
    exe = shutil.which("ffmpeg")
    if not exe:
        raise SystemExit("ffmpeg is not on PATH - it is what cuts the clip")
    return exe


def probe_seconds(path):
    """The file's duration, via ffprobe (or None when it cannot be read)."""
    exe = shutil.which("ffprobe")
    if not exe:
        return None
    out = subprocess.run([exe, "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", path],
                         capture_output=True).stdout.decode("utf-8", "replace").strip()
    try:
        return float(out)
    except ValueError:
        return None


def flatness_profile(path):
    """Per-frame spectral flatness of a file, via ffmpeg. (mean, speechy_fraction) or None.

    A steady tone - a mains hum, a stuck test tone, a struck bell - is a narrow peak
    in every frame, so its flatness sits near 0.003 and no frame is broadband. Speech
    is broadband and flickers frame to frame, so most frames climb well above that.
    This is the only check here that looks at *content*: a drone is the right length,
    the right format and the right level, and it clones nothing.
    """
    exe = shutil.which("ffmpeg")
    if not exe:
        return None
    cmd = [exe, "-hide_banner", "-nostats", "-i", path, "-vn", "-af",
           "aspectralstats=measure=flatness,"
           "ametadata=mode=print:key=lavfi.aspectralstats.1.flatness:file=-",
           "-f", "null", "-"]
    out = subprocess.run(cmd, capture_output=True).stdout.decode("utf-8", "replace")
    values = [float(m) for m in re.findall(r"flatness=([0-9.eE+-]+)", out)]
    if not values:
        return None
    mean = sum(values) / len(values)
    speechy = sum(1 for v in values if v >= SPEECHY_FRAME) / len(values)
    return mean, speechy


def speechy_verdict(mean, speechy):
    """(is_speech, one-line why) from a flatness profile."""
    if mean < MIN_FLATNESS_MEAN or speechy < MIN_SPEECHY_FRACTION:
        return False, ("a steady tone, not speech (flatness mean %.3f, only %.0f%% of "
                       "frames broadband)" % (mean, speechy * 100))
    return True, ("speech-like (flatness mean %.3f, %.0f%% of frames broadband)"
                  % (mean, speechy * 100))


def mmss(value):
    """'1:20' / '80' / '1:02:03' as seconds."""
    if value is None:
        return None
    parts = str(value).strip().split(":")
    try:
        parts = [float(p) for p in parts]
    except ValueError:
        raise SystemExit("--start wants seconds or m:ss, not %r" % value)
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def cut(src, out, start=None, seconds=DEFAULT_SECONDS, verbose=True):
    """Write the reference clip and return (path, speech_seconds)."""
    if not os.path.isfile(src):
        raise SystemExit("no such file: %s" % src)
    start = mmss(start)
    if start is None:
        # Without a start, skip the first 0.5s: a clip very often opens on a room
        # tone or an intake of breath, and the model would learn that instead.
        start = 0.5

    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    # silenceremove trims the silence this window happens to contain at its edges;
    # loudnorm then puts every reference at the same level; -ac 1 -ar 24000 is the
    # engine's own shape.
    af = ("silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05,"
          "areverse,silenceremove=start_periods=1:start_threshold=-45dB:"
          "start_silence=0.05,areverse,"
          "loudnorm=I=-18:TP=-2:LRA=11")
    cmd = [ffmpeg(), "-y", "-loglevel", "error",
           "-ss", "%.2f" % start, "-t", "%.2f" % seconds, "-i", src,
           "-vn", "-ac", "1", "-ar", "24000", "-af", af, out]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0 or not os.path.isfile(out):
        raise SystemExit("ffmpeg could not cut the clip: %s"
                         % result.stderr.decode("utf-8", "replace").strip()[:400])

    speech = probe_seconds(out)
    if verbose:
        source = probe_seconds(src)
        print("  source  : %s%s" % (src, (" (%.0fs)" % source) if source else ""))
        print("  window  : %.1fs from %.1fs" % (seconds, start))
        print("  clip    : %s%s" % (out, (" (%.1fs of speech)" % speech) if speech else ""))
    profile = flatness_profile(out)
    if profile is not None:
        is_speech, why = speechy_verdict(*profile)
        if verbose:
            print("  content : %s" % why)
        if not is_speech:
            print("  REFUSED: the clip is %s. Re-cut it from something with speech in "
                  "it - a finished render, a past narration, or a vibe_media story."
                  % why)
            try:
                os.remove(out)
            except OSError:
                pass
            return None, speech

    if speech is not None and speech < MIN_SPEECH:
        print("  REFUSED: only %.1fs of speech in the clip and the engine needs more "
              "than %.0fs. Try a --start further into the file, or a longer --seconds."
              % (speech, MIN_SPEECH))
        try:
            os.remove(out)
        except OSError:
            pass
        return None, speech
    return out, speech


def show_list():
    """Which references the pipeline will use, and whether they exist."""
    rows = [("male  ", config.TTS_REF_MALE), ("female", config.TTS_REF_FEMALE)]
    engine = config.TTS_ENGINE
    print("  engine: %s   model: %s   device: %s"
          % (engine, config.TTS_MODEL, config.TTS_DEVICE))
    for label, path in rows:
        if os.path.isfile(path):
            seconds = probe_seconds(path)
            print("  %s : %s%s" % (label, path,
                                   ("  (%.1fs)" % seconds) if seconds else ""))
        else:
            print("  %s : %s  MISSING - the model's own voice will be used" % (label, path))
    if engine != "chatterbox":
        print("  note: the reference clips are only read by the local engine")
    return 0


def check():
    """Read the configured clips back and report whether they hold speech."""
    rows = [("male  ", config.TTS_REF_MALE), ("female", config.TTS_REF_FEMALE)]
    bad = 0
    for label, path in rows:
        if not path or not os.path.isfile(path):
            print("  %s : %s  MISSING - the model's own voice will be used"
                  % (label, path))
            continue
        seconds = probe_seconds(path)
        profile = flatness_profile(path)
        if profile is None:
            print("  %s : %s  could not be profiled" % (label, path))
            bad += 1
            continue
        is_speech, why = speechy_verdict(*profile)
        print("  %s : %s  (%s)  %s  %s"
              % (label, path, ("%.1fs" % seconds) if seconds else "?",
                 "OK  " if is_speech else "BAD ", why))
        if not is_speech:
            bad += 1
    return 0 if not bad else 1


def selftest():
    """The parts that do not need audio: parsing, paths, and the refusal rule."""
    checks = []
    checks.append(("m:ss parses", mmss("1:20") == 80.0))
    checks.append(("h:mm:ss parses", mmss("1:02:03") == 3723.0))
    checks.append(("plain seconds parse", mmss("14") == 14.0))
    checks.append(("a missing file is refused, not crashed on",
                   _refused(lambda: cut("definitely-not-here.mp3", "unused.wav",
                                        verbose=False))))
    checks.append(("the default clip is longer than the engine's minimum",
                   DEFAULT_SECONDS > MIN_SPEECH))
    checks.append(("a drone is refused by the content gate",
                   speechy_verdict(0.003, 0.0)[0] is False))
    checks.append(("a clean hum barely above the floor is still refused",
                   speechy_verdict(0.012, 0.04)[0] is False))
    checks.append(("real speech passes the content gate",
                   speechy_verdict(0.047, 0.31)[0] is True))
    checks.append(("a very clean voice clip still passes",
                   speechy_verdict(0.031, 0.12)[0] is True))
    checks.append(("both reference paths are configured",
                   bool(config.TTS_REF_MALE and config.TTS_REF_FEMALE)))
    for name, ok in checks:
        print("  %s  %s" % ("PASS" if ok else "FAIL", name))
    bad = [n for n, ok in checks if not ok]
    print("\n  %d checks, %d fail" % (len(checks), len(bad)))
    return 0 if not bad else 1


def _refused(fn):
    try:
        fn()
        return False
    except SystemExit:
        return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("source", nargs="?", help="the audio to cut the clip out of")
    ap.add_argument("--out", help="where to write the clip (default: the male slot)")
    ap.add_argument("--start", help="seconds or m:ss to start at (default: 0.5s)")
    ap.add_argument("--seconds", type=float, default=DEFAULT_SECONDS,
                    help="how long the clip is (default: %(default)s)")
    ap.add_argument("--list", action="store_true", help="show the configured clips")
    ap.add_argument("--check", action="store_true",
                    help="verify the configured clips actually contain speech")
    ap.add_argument("--selftest", action="store_true", help="checks that need no audio")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.list:
        return show_list()
    if args.check:
        return check()
    if not args.source:
        ap.error("give a source file, or --list, or --selftest")
    out = args.out or config.TTS_REF_MALE
    path, speech = cut(args.source, out, start=args.start, seconds=args.seconds)
    if not path:
        return 1
    print("\n  the local engine will clone this voice. To let the pipeline use it, "
          "point %s at it (or pass --out for the other slot)."
          % ("TTS_REF_MALE" if out == config.TTS_REF_MALE else "TTS_REF_FEMALE"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
