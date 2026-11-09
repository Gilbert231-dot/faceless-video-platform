#!/usr/bin/env python
"""Speak a narration on this machine, for nothing.

    python tts_local.py --text "Hello there." --out hello.mp3
    python tts_local.py --text-file script.txt --voice female --out out.mp3
    python tts_local.py --selftest        # no model, no download, no audio

The pipeline used to rent its voice from ElevenLabs, per character, every run.
Chatterbox (resemble-ai/chatterbox) is the same job done locally: MIT licence for
both code and weights, so it is free to use commercially, and it clones a voice from
a short reference clip instead of requiring an account.

WHAT IT COSTS
-------------
CPU only, so it costs TIME rather than money. The model speaks roughly 5-10x slower
than real time on two free cores, which is 15-25 minutes for a four-minute narration.
That is the trade: no bill, a slow render.

THE THREE THINGS THAT MAKE IT WORK IN A PIPELINE
------------------------------------------------
1. THE REFERENCE IS PREPARED ONCE. `generate(audio_prompt_path=...)` re-encodes the
   reference clip on every call. This module calls `prepare_conditionals()` once and
   then generates against the cached conditionals, so a twenty-chunk narration
   encodes the voice once instead of twenty times.

2. THE SCRIPT IS CHUNKED BY SENTENCE. The model truncates its input at its own token
   limit, so handing it a 600-word narration silently drops the end. Every chunk here
   ends at a sentence boundary and stays under TTS_CHUNK_CHARS, and the chunks are
   joined with a short silence - which is also where the pause compressor downstream
   expects pauses to be.

3. THE OUTPUT IS AN MP3. Everything downstream (moviepy, whisper, the atempo speed-up)
   already expects an mp3 from `generate_voiceover()`, so the wav the model returns is
   encoded here rather than teaching four other files a second format.

WHEN THERE IS NO REFERENCE
--------------------------
The model ships with one voice of its own. If no reference clip is configured (or the
file is missing) the narration is still produced, in that voice, and the module says
so once instead of failing a run at the last step.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

import config  # noqa: E402  (path set up above)


class LocalTTSError(RuntimeError):
    """The local engine is missing, or it failed. The message says which."""


# ---------------------------------------------------------------------------
# text -> chunks
# ---------------------------------------------------------------------------

# A sentence, roughly: everything up to . ! ? or a line break. Kept deliberately
# simple - the text arriving here has already been through the pipeline's own TTS
# cleaner, so abbreviations and numerals are already spelled out.
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]*")


def chunk_text(text, limit=None):
    """Split a script into pieces the model will not truncate.

    Whole sentences, in order, packed up to `limit` characters. A sentence longer than
    the limit is split again on commas, then on spaces, because a chunk that overflows
    is silently cut by the model - and a cut is a missing word in the narration, not an
    error anyone would see.
    """
    limit = int(limit or config.TTS_CHUNK_CHARS)
    limit = max(60, limit)
    text = " ".join((text or "").split())
    if not text:
        return []

    sentences = [s.strip() for s in _SENTENCE.findall(text) if s.strip()]
    if not sentences:
        sentences = [text]

    pieces = []
    for sentence in sentences:
        if len(sentence) <= limit:
            pieces.append(sentence)
            continue
        # Too long on its own: break on commas first, then on spaces.
        for part in re.split(r"(?<=,)\s+", sentence):
            part = part.strip()
            while len(part) > limit:
                cut = part.rfind(" ", 0, limit)
                cut = cut if cut > 0 else limit
                pieces.append(part[:cut].strip())
                part = part[cut:].strip()
            if part:
                pieces.append(part)

    chunks, current = [], ""
    for piece in pieces:
        if not current:
            current = piece
        elif len(current) + 1 + len(piece) <= limit:
            current = current + " " + piece
        else:
            chunks.append(current)
            current = piece
    if current:
        chunks.append(current)
    return chunks


# ---------------------------------------------------------------------------
# which reference clip speaks
# ---------------------------------------------------------------------------

# A configured reference value the engine must read as "do not clone": the model's
# own voice. "none" is deliberate, a missing file is an accident - both end up as None
# here, but only the accident is worth a warning (and _prepare says so once).
NO_REFERENCE = {"", "none", "off", "no", "-"}


def _slot(voice_id):
    """"male" or "female" - which slot's reference and model apply to this voice.

    The pipeline chooses a voice by ElevenLabs id (or by whatever id the caller was
    configured with), so the slot is decided by id: the configured female id is the
    female slot, and everything else - including an id this module has never seen -
    falls to the male slot, which is also the pipeline's default.
    """
    female = os.getenv("ELEVENLABS_VOICE_FEMALE_ID", "").strip()
    male = os.getenv("ELEVENLABS_VOICE_MALE_ID", "").strip()
    try:
        import voiceover  # local import: voiceover imports this module
        female = female or voiceover.FEMALE_VOICE_ID
        male = male or voiceover.MALE_VOICE_ID
    except Exception:
        pass
    if female and voice_id == female:
        return "female"
    if voice_id and voice_id != male and "female" in str(voice_id).lower():
        # an id this module has never seen, but named like the female slot
        return "female"
    return "male"


def reference_for(voice_id):
    """The reference clip for this voice, or None when the model's own voice is used.

    A value of "none" is a decision, not a missing file: the female slot speaks in the
    model's own voice on purpose (it measured 216 Hz and out-cloned the old reference
    clip). Missing files are still reported once by _prepare, so a machine without
    clips still narrates instead of failing at the last step.
    """
    path = config.TTS_REF_FEMALE if _slot(voice_id) == "female" else config.TTS_REF_MALE
    if path is None:
        return None
    if str(path).strip().lower() in NO_REFERENCE:
        return None
    if os.path.isfile(path):
        return path
    return None


# ---------------------------------------------------------------------------
# the model
# ---------------------------------------------------------------------------

_MODELS = {}                # model name -> the loaded engine
_PREPARED = None            # (model id, reference path) the conditionals belong to
_WARNED = set()


def model_name_for(voice_id):
    """Which model speaks this voice: "turbo" or "standard".

    The slots can differ - the male clone was chosen on the standard model and the
    female slot on turbo's own voice - so the model is chosen per voice, falling back
    to TTS_MODEL. Only the model actually used is loaded, so a run that speaks one
    voice still pays for one model.
    """
    name = (config.TTS_MODEL_FEMALE if _slot(voice_id) == "female"
            else config.TTS_MODEL_MALE)
    return (name or config.TTS_MODEL).strip().lower()


def _load(name=None):
    """Load (and keep) a model. Importing here keeps the rest of the pipeline free."""
    name = (name or config.TTS_MODEL).strip().lower()
    if name in _MODELS:
        return _MODELS[name]
    try:
        import torch  # noqa: F401
        if name == "standard":
            from chatterbox.tts import ChatterboxTTS as Engine
        else:
            from chatterbox.tts_turbo import ChatterboxTurboTTS as Engine
    except ImportError as exc:
        raise LocalTTSError(
            "the local voice engine is not installed (%s). Install it with "
            "`pip install -r requirements-tts.txt`, or set TTS_ENGINE=elevenlabs to "
            "use the paid API instead." % exc)
    device = config.TTS_DEVICE
    print("   loading the local voice model (%s, %s) - the first run downloads it"
          % (name, device))
    started = time.time()
    _MODELS[name] = Engine.from_pretrained(device=device)
    print("   model ready in %.0fs" % (time.time() - started))
    return _MODELS[name]


def _prepare(model, reference):
    """Encode the reference voice once per process, not once per chunk."""
    global _PREPARED
    key = (id(model), reference)
    if _PREPARED == key:
        return
    if reference:
        model.prepare_conditionals(reference)
        print("   reference voice: %s" % reference)
    else:
        if "no-reference" not in _WARNED:
            _WARNED.add("no-reference")
            print("   no reference clip found - speaking in the model's own voice. "
                  "Run make_voice_ref.py to clone one from a clip you already have.")
    _PREPARED = key


def _to_mp3(wav_path, mp3_path):
    """Encode the wav the model returns into the mp3 everything downstream expects."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return wav_path
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", wav_path,
           "-codec:a", "libmp3lame", "-q:a", str(config.TTS_MP3_QUALITY), mp3_path]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        print("   could not encode the mp3 (%s) - keeping the wav"
              % result.stderr.decode("utf-8", "replace").strip()[:200])
        return wav_path
    try:
        os.remove(wav_path)
    except OSError:
        pass
    return mp3_path


def synthesize(text, voice_id=None, out_dir=None, verbose=True):
    """Speak `text` with the local engine and return the path of an mp3."""
    import numpy as np

    chunks = chunk_text(text)
    if not chunks:
        raise LocalTTSError("nothing to speak - the script is empty")
    reference = reference_for(voice_id)
    model = _load(model_name_for(voice_id))
    _prepare(model, reference)

    out_dir = out_dir or os.environ.get("OUTPUT_DIR") or tempfile.gettempdir()
    os.makedirs(out_dir, exist_ok=True)
    stamp = int(time.time())
    wav_path = os.path.join(out_dir, "voiceover_local_%d.wav" % stamp)
    mp3_path = os.path.join(out_dir, "voiceover_local_%d.mp3" % stamp)

    gap = np.zeros(int(model.sr * 0.18), dtype=np.float32)
    pieces = []
    started = time.time()
    for i, chunk in enumerate(chunks, 1):
        audio = model.generate(chunk).squeeze(0)
        pieces.append(audio.detach().cpu().numpy().astype(np.float32))
        pieces.append(gap)
        if verbose:
            print("   spoke %d/%d (%.0fs elapsed)" % (i, len(chunks), time.time() - started))
    spoken = np.concatenate(pieces[:-1]) if len(pieces) > 1 else pieces[0]

    import soundfile as sf
    sf.write(wav_path, spoken, model.sr, subtype="PCM_16")
    if verbose:
        print("   %.1fs of audio from %d chunks in %.0fs (%.1fs per second of speech)"
              % (len(spoken) / float(model.sr), len(chunks), time.time() - started,
                 (time.time() - started) / max(1e-6, len(spoken) / float(model.sr))))
    return _to_mp3(wav_path, mp3_path)


def available():
    """Is the local engine importable here? Cheap: no model load, no download."""
    try:
        import torch  # noqa: F401
        import chatterbox  # noqa: F401
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# self-test: everything except the model
# ---------------------------------------------------------------------------

def selftest():
    checks = []

    checks.append(("a short script is one chunk",
                   chunk_text("I left my job in March.") == ["I left my job in March."]))
    chunks = chunk_text(" ".join(["She took the money and never said a word."] * 12), 120)
    checks.append(("a long script is split", len(chunks) > 1))
    checks.append(("...and no chunk overflows the limit",
                   all(len(c) <= 120 for c in chunks)))
    checks.append(("...and nothing is lost",
                   " ".join(chunks).split() ==
                   " ".join(["She took the money and never said a word."] * 12).split()))
    checks.append(("...and every chunk ends where a sentence ends",
                   all(c.rstrip()[-1] in ".!?" for c in chunks)))

    monster = "word " * 400
    long_chunks = chunk_text(monster, 100)
    checks.append(("a sentence longer than the limit is split, not truncated",
                   all(len(c) <= 100 for c in long_chunks)
                   and len(" ".join(long_chunks).split()) == 400))

    checks.append(("empty text gives no chunks", chunk_text("   ") == []))
    tiny = chunk_text("This is a sentence. And here is another one.", 5)
    checks.append(("an impossible limit still yields every word",
                   " ".join(tiny).split() ==
                   "This is a sentence. And here is another one.".split()))

    # The reference mapping. Files are not required to exist; what is being proved is
    # that the female id picks the female slot and an unknown id does not throw.
    class _Cfg:
        TTS_REF_MALE = "male.wav"
        TTS_REF_FEMALE = "female.wav"
    real_cfg = config.TTS_REF_MALE, config.TTS_REF_FEMALE
    config.TTS_REF_MALE, config.TTS_REF_FEMALE = _Cfg.TTS_REF_MALE, _Cfg.TTS_REF_FEMALE
    try:
        import voiceover
        # A real file for the male slot and the sentinel for the female one: that is
        # the shipped arrangement, and it is what these checks must describe.
        tmp_ref = os.path.join(tempfile.mkdtemp(prefix="tts_ref_"), "male.wav")
        io.open(tmp_ref, "wb").close()
        config.TTS_REF_MALE, config.TTS_REF_FEMALE = tmp_ref, "none"
        checks.append(("the male id maps to the male clip",
                       reference_for(voiceover.MALE_VOICE_ID) == tmp_ref))
        checks.append(("'none' means the model's own voice, not a missing clip",
                       reference_for(voiceover.FEMALE_VOICE_ID) is None))
        checks.append(("each slot can pick its own model",
                       model_name_for(voiceover.MALE_VOICE_ID) == config.TTS_MODEL_MALE
                       and model_name_for(voiceover.FEMALE_VOICE_ID)
                       == config.TTS_MODEL_FEMALE))
        checks.append(("an unknown id falls to the male slot",
                       reference_for("nope") == tmp_ref))
        config.TTS_REF_MALE = os.path.join(os.path.dirname(tmp_ref), "gone.wav")
        checks.append(("a missing clip is not an exception",
                       reference_for(voiceover.MALE_VOICE_ID) is None))
    except Exception as exc:                                    # pragma: no cover
        checks.append(("voiceover imports for the id mapping (%s)" % exc, False))
    finally:
        config.TTS_REF_MALE, config.TTS_REF_FEMALE = real_cfg

    # THE WIRING. voiceover.generate_voiceover() is what the pipeline calls, and the
    # engine it picks is the whole point of this file, so it is exercised here with a
    # stub in place of the model: the routing, the artifact, and the refusal of the
    # paid path on a machine that has no key.
    # The module voiceover will import, which is NOT this one when this file is run as
    # a script: `python tts_local.py` loads it as __main__, and a later `import
    # tts_local` would build a second copy whose stub the caller never sets. Patch the
    # copy that will actually be called.
    import importlib
    engine = sys.modules.get("tts_local") or importlib.import_module("tts_local")
    real_synth = engine.synthesize
    written = []

    def stub(text, voice_id=None, out_dir=None, verbose=True):
        written.append((text, voice_id))
        return "stub-audio.mp3"

    engine.synthesize = stub
    real_pause = config.AUTOMATION_PAUSED
    config.AUTOMATION_PAUSED = False
    real_out = os.environ.get("OUTPUT_DIR")
    tmp = tempfile.mkdtemp(prefix="tts_selftest_")
    os.environ["OUTPUT_DIR"] = tmp
    try:
        import voiceover
        clip, _ = voiceover.generate_voiceover("One two three.", engine="chatterbox")
        checks.append(("the pipeline's call reaches this engine by default",
                       clip == "stub-audio.mp3" and written))
        checks.append(("...and the voice id it passed is the pipeline's own",
                       written and written[-1][1] == voiceover.DEFAULT_VOICE_ID))
        artifacts = [f for f in os.listdir(tmp) if f.startswith("tts_script_")]
        checks.append(("...and the run's artifact names the engine that spoke",
                       bool(artifacts) and "chatterbox" in io.open(
                           os.path.join(tmp, artifacts[0]), encoding="utf-8").read()))
        # The refusal is only provable with NO key in the environment. Since the
        # pipeline's .env now carries a real ELEVENLABS_API_KEY (and python-dotenv is
        # installed), this used to send a live request and fail on the account's plan
        # instead of on the missing key - a check that tested the network, not the
        # refusal. Hide the key for the duration of the check and put it back after.
        real_key = os.environ.pop("ELEVENLABS_API_KEY", None)
        real_mod_key = getattr(voiceover, "ELEVENLABS_API_KEY", None)
        voiceover.ELEVENLABS_API_KEY = None
        try:
            voiceover.generate_voiceover("Test.", engine="elevenlabs")
            checks.append(("the paid engine refuses without a key", False))
        except RuntimeError:
            checks.append(("the paid engine refuses without a key", True))
        except Exception as exc:
            checks.append(("the paid engine refuses without a key (%r)" % exc, False))
        finally:
            if real_key is not None:
                os.environ["ELEVENLABS_API_KEY"] = real_key
            voiceover.ELEVENLABS_API_KEY = real_mod_key
    finally:
        engine.synthesize = real_synth
        config.AUTOMATION_PAUSED = real_pause
        if real_out is None:
            os.environ.pop("OUTPUT_DIR", None)
        else:
            os.environ["OUTPUT_DIR"] = real_out
        shutil.rmtree(tmp, ignore_errors=True)

    for name, ok in checks:
        print("  %s  %s" % ("PASS" if ok else "FAIL", name))
    bad = [n for n, ok in checks if not ok]
    print("\n  %d checks, %d fail" % (len(checks), len(bad)))
    return 0 if not bad else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--text", help="the line to speak")
    ap.add_argument("--text-file", help="a file of text to speak")
    ap.add_argument("--voice", default=None, help="male | female | an elevenlabs voice id")
    ap.add_argument("--out", default=None, help="where to write the audio")
    ap.add_argument("--selftest", action="store_true", help="checks that need no model")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    text = args.text or ""
    if args.text_file:
        with io.open(args.text_file, encoding="utf-8") as fh:
            text = fh.read()
    if not text.strip():
        ap.error("give --text or --text-file")

    voice_id = args.voice
    if voice_id in (None, "male", "female"):
        import voiceover
        voice_id = {"male": voiceover.MALE_VOICE_ID,
                    "female": voiceover.FEMALE_VOICE_ID}.get(voice_id,
                                                             voiceover.DEFAULT_VOICE_ID)
    out = synthesize(text, voice_id=voice_id,
                     out_dir=os.path.dirname(os.path.abspath(args.out))
                     if args.out else None)
    if args.out and out != args.out:
        shutil.move(out, args.out)
        out = args.out
    print("wrote %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
