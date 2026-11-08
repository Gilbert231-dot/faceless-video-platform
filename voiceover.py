import os
import re
import json
import time
import tempfile
from typing import Tuple, Optional

from tts_clean import clean_for_tts
from config import (
    PAUSE_COMPRESS, PAUSE_MIN_SEC, PAUSE_KEEP_RATIO,
    PAUSE_MIN_KEPT_SEC, PAUSE_THRESHOLD,
    VOICE_LEVEL_NORMALIZE, VOICE_LUFS_TARGET, VOICE_TP_MAX, VOICE_LRA_TARGET,
    FEMALE_VOICE_BOOST_DB,
    TTS_ENGINE,
    require_automation_active,
)

# Try the new ElevenLabs import style (v1.0.0+). The paid engine is now OPTIONAL: the
# pipeline speaks with the free local engine (tts_local.py) unless TTS_ENGINE says
# otherwise, so neither this import nor the key may be a condition of importing this
# module. Before 2026-11-02 both were, which made "don't pay" impossible to even start.
try:
    from elevenlabs.client import ElevenLabs
    from elevenlabs import play, save
    from elevenlabs.types import VoiceSettings
    ELEVENLABS_V1 = True
except ImportError:
    try:
        # Fallback to old style (v0.x)
        from elevenlabs import generate, save
        ELEVENLABS_V1 = False
    except ImportError:
        ElevenLabs = None
        generate = None
        save = None
        VoiceSettings = None
        ELEVENLABS_V1 = False

# API Key (set in environment). Read when it is used, not when this module loads.
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")


def require_elevenlabs():
    """The paid engine's preconditions, raised only when it is the one being used."""
    if ElevenLabs is None and generate is None:
        raise RuntimeError(
            "the ElevenLabs SDK is not installed (`pip install elevenlabs`), and "
            "TTS_ENGINE is set to elevenlabs. Set TTS_ENGINE=chatterbox to use the "
            "free local engine instead.")
    key = os.getenv("ELEVENLABS_API_KEY") or ELEVENLABS_API_KEY
    if not key:
        raise RuntimeError(
            "ELEVENLABS_API_KEY is not set, so the paid engine cannot be used. "
            "Set TTS_ENGINE=chatterbox (the default) to speak locally for free.")
    return key

# Voice IDs
MALE_VOICE_ID = "loZFKb410q0XFUiYDx8U"      # Custom Gen Z voice
FEMALE_VOICE_ID = "CT97FgDtAHKczJP3Yl78"    # "Female yappy voice" (was Sarah)
DEFAULT_VOICE_ID = MALE_VOICE_ID

def compress_narrator_pauses(audio_path: str) -> str:
    """Shorten the narrator's silence gaps between sentences slightly.

    Runs on the RAW voiceover right after TTS, so every downstream consumer
    (footage duration, the atempo speed-up, whisper caption timing) sees the
    same compressed timeline and stays in sync automatically. Pauses shorter
    than PAUSE_MIN_SEC are left untouched; longer ones keep PAUSE_KEEP_RATIO
    of their length (floored at PAUSE_MIN_KEPT_SEC). Leading/trailing
    silence is trimmed fully. Fails SAFE: any problem logs a warning and
    returns the original audio - generation never breaks over this.
    """
    if not PAUSE_COMPRESS:
        return audio_path
    tmp_wav = None
    try:
        import numpy as np
        import subprocess
        import wave

        tmp_wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
        subprocess.run(
            ['ffmpeg', '-y', '-nostats', '-i', audio_path,
             '-ac', '1', '-ar', '44100', '-acodec', 'pcm_s16le', tmp_wav],
            check=True, capture_output=True, timeout=60)

        with wave.open(tmp_wav, 'rb') as w:
            rate = w.getframerate()
            n = w.getnframes()
            raw = w.readframes(n)
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if len(samples) < rate:  # under 1s of audio - nothing worth doing
            return audio_path

        win = max(1, int(rate * 0.005))  # 5 ms analysis windows
        n_wins = len(samples) // win
        peaks = np.abs(samples[: n_wins * win].reshape(n_wins, win)).max(axis=1)
        silent = peaks < PAUSE_THRESHOLD

        # Contiguous silent runs as (start_win, end_win) index pairs.
        runs = []
        start = None
        for i, s in enumerate(silent):
            if s and start is None:
                start = i
            elif not s and start is not None:
                runs.append((start, i))
                start = None
        if start is not None:
            runs.append((start, len(silent)))
        if not runs:
            return audio_path

        out = []
        cursor = 0
        for (s, e) in runs:
            if s == 0:
                # Leading silence: drop it entirely (lead-in, not a pause).
                cursor = e * win
                continue
            # Speech before this run: keep it verbatim.
            out.append(samples[cursor: s * win])
            # The pause itself: keep a shortened portion (first part).
            run_sec = (e - s) * win / rate
            keep_sec = run_sec if run_sec < PAUSE_MIN_SEC else \
                max(PAUSE_MIN_KEPT_SEC, run_sec * PAUSE_KEEP_RATIO)
            keep_n = int(keep_sec * rate)
            out.append(samples[s * win: s * win + keep_n])
            cursor = e * win
        # Tail after the last run (speech or trailing silence): keep speech,
        # drop trailing silence (cursor already past it if the last run hit the end).
        out.append(samples[cursor:])

        new_audio = np.concatenate(out)
        saved = (len(samples) - len(new_audio)) / rate
        if saved < 0.15:  # nothing meaningful saved - keep the original
            return audio_path

        pcm = (np.clip(new_audio, -1.0, 1.0) * 32767).astype(np.int16)
        tmp_out = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
        try:
            with wave.open(tmp_out, 'wb') as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes(pcm.tobytes())

            out_mp3 = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False).name
            subprocess.run(
                ['ffmpeg', '-y', '-nostats', '-i', tmp_out,
                 '-ac', '1', '-acodec', 'mp3', '-b:a', '192k', out_mp3],
                check=True, capture_output=True, timeout=60)
            print(f"   ⏱️ Narrator pauses compressed: "
                  f"{len(samples)/rate:.1f}s -> {len(new_audio)/rate:.1f}s "
                  f"(saved {saved:.2f}s)")
            return out_mp3
        finally:
            if os.path.exists(tmp_out):
                os.unlink(tmp_out)
    except Exception as e:
        print(f"   ⚠️ Pause compression skipped ({e}) - using original voiceover")
        return audio_path
    finally:
        if tmp_wav and os.path.exists(tmp_wav):
            try:
                os.unlink(tmp_wav)
            except Exception:
                pass


def measure_loudness(media_path: str) -> Tuple[Optional[float], Optional[float]]:
    """Return (integrated_LUFS, true_peak_dBFS), or (None, None) on failure."""
    try:
        import subprocess
        result = subprocess.run(
            ['ffmpeg', '-y', '-nostats', '-i', media_path,
             '-af', 'ebur128=peak=true', '-f', 'null', '-'],
            capture_output=True, text=True, timeout=180)
        out = result.stdout + result.stderr
        summary = out.split("Summary:")[-1]
        m_i = re.search(r"I:\s+(-?[\d.]+) LUFS", summary)
        m_tp = re.search(r"Peak:\s+(-?[\d.]+) dBFS", out)
        return (float(m_i.group(1)) if m_i else None,
                float(m_tp.group(1)) if m_tp else None)
    except Exception:
        return None, None


def _loudnorm_measure(src: str, target: float):
    """Pass 1 of two-pass loudnorm: ffmpeg's own measurement of the material."""
    try:
        import subprocess
        result = subprocess.run(
            ['ffmpeg', '-y', '-nostats', '-i', src, '-af',
             f"loudnorm=I={target}:TP={VOICE_TP_MAX}:LRA={VOICE_LRA_TARGET}"
             ":print_format=json", '-f', 'null', '-'],
            capture_output=True, text=True, timeout=900)
        out = result.stdout + result.stderr
        m = re.search(r'\{[^{}]*"input_i"[^{}]*\}', out, re.DOTALL)
        return json.loads(m.group(0)) if m else None
    except Exception:
        return None


def normalize_voice_loudness(audio_path: str,
                             voice_id: Optional[str] = None) -> str:
    """Bring a narration to VOICE_LUFS_TARGET so every voice matches.

    Why not just a gain: the true-peak ceiling caps a gain, and a cloned
    voice can have a crest factor wide enough that the cap binds long before
    the target is reached. loudnorm lifts the average and limits the peaks
    TOGETHER, which is the only way a quiet clone reaches the male's level;
    the result is then measured and given the small remaining correction.

    Runs AFTER pause compression on purpose: the pause detector compares
    against an absolute threshold, so changing the level first would change
    which gaps it finds.

    Fails SAFE: any problem logs a warning and returns the original audio.
    """
    if not VOICE_LEVEL_NORMALIZE:
        return audio_path
    try:
        import subprocess
        target = VOICE_LUFS_TARGET
        if voice_id == FEMALE_VOICE_ID:
            target += FEMALE_VOICE_BOOST_DB

        before_i, before_tp = measure_loudness(audio_path)
        if before_i is None:
            print("   \u26a0\ufe0f Loudness measurement failed - narration kept as-is")
            return audio_path

        def _apply(src, dst, af):
            subprocess.run(
                ['ffmpeg', '-y', '-nostats', '-loglevel', 'error', '-i', src,
                 '-af', af, '-ac', '1', '-acodec', 'mp3', '-b:a', '192k', dst],
                check=True, capture_output=True, timeout=900)

        # TWO-PASS loudnorm. The single-pass form only ESTIMATES the material as
        # it streams and undershot by ~3 dB on the real clone (measured), which
        # the gain correction below then cannot make up because the peak ceiling
        # binds first. Pass 1 measures, pass 2 applies using the measurements.
        work = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False).name
        stats = _loudnorm_measure(audio_path, target)
        af = f"loudnorm=I={target}:TP={VOICE_TP_MAX}:LRA={VOICE_LRA_TARGET}"
        if stats:
            af += (":linear=false"
                   f":measured_I={stats['input_i']}"
                   f":measured_TP={stats['input_tp']}"
                   f":measured_LRA={stats['input_lra']}"
                   f":measured_thresh={stats['input_thresh']}"
                   f":offset={stats['target_offset']}")
        _apply(audio_path, work, af)
        after_i, after_tp = measure_loudness(work)

        # Small measured correction, still honouring the peak ceiling. Never
        # allowed to make the voice quieter AND clip: the min() keeps the peak.
        if after_i is not None:
            gain = target - after_i
            if after_tp is not None:
                gain = min(gain, VOICE_TP_MAX - after_tp)
            if abs(gain) >= 0.1:
                corrected = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False).name
                try:
                    _apply(work, corrected, f"volume={gain:.2f}dB")
                except Exception:
                    corrected = None
                if corrected:
                    try:
                        os.unlink(work)
                    except Exception:
                        pass
                    work = corrected
                    after_i, after_tp = measure_loudness(work)

        print(f"   \U0001f399\ufe0f Narration level: {before_i:.1f} LUFS "
              f"(peak {before_tp if before_tp is not None else 0:.1f}) -> "
              f"{after_i if after_i is not None else target:.1f} LUFS "
              f"(target {target:.1f})")
        return work
    except Exception as e:
        print(f"   \u26a0\ufe0f Loudness normalization skipped ({e}) - using original voiceover")
        return audio_path


def generate_voiceover(script: str, voice_id: Optional[str] = None,
                       engine: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """
    Generate the narration, with whichever engine TTS_ENGINE names.

    Args:
        script: Text to convert to speech
        voice_id: voice ID (if None, uses default). With the local engine this picks
                  which reference clip is cloned; with ElevenLabs it is its own id.
        engine: override TTS_ENGINE for one call ("chatterbox" | "elevenlabs")

    Returns:
        audio_path: Path to the generated MP3
        subtitle_path: None
    """
    if voice_id is None:
        voice_id = DEFAULT_VOICE_ID

    engine = (engine or TTS_ENGINE or "chatterbox").strip().lower()
    if engine in ("elevenlabs", "eleven", "paid"):
        spoken = "elevenlabs"
    elif engine in ("local", "chatterbox", "free"):
        spoken = "chatterbox"
    else:
        raise ValueError("TTS_ENGINE=%r is not an engine; use chatterbox or elevenlabs"
                         % (engine,))

    # THE LAST GATE BEFORE SYNTHESIS: this is the one function that actually speaks,
    # so it honours the pause switch itself rather than trusting every caller to have
    # checked. Nothing above this line has spent anything, so the failure costs nothing.
    require_automation_active()

    # LAST-LINE DEFENSE (belt and suspenders): re-run the full tts_clean pass
    # even if a script somehow slipped past clean_script_for_tts — "ale" -> "",
    # markdown/brackets stripped, acronyms expanded, "I'm" -> "I am". The
    # post-generation audio mute (caption step) remains as the second layer.
    script = clean_for_tts(script)

    # Ship the EXACT text the narrator was asked to say (output/tts_script_*.json), and
    # which engine said it, so the run's artifact proves it — if a glitch is ever heard,
    # we can tell instantly whether it came from the text or the voice model.
    try:
        out_dir = os.environ.get("OUTPUT_DIR", "output")
        os.makedirs(out_dir, exist_ok=True)
        script_path = os.path.join(out_dir, f"tts_script_{int(time.time())}.json")
        with open(script_path, "w", encoding="utf-8") as f:
            json.dump({"engine": spoken, "voice_id": voice_id, "script": script}, f,
                      indent=2, ensure_ascii=False)
    except Exception:
        pass

    if spoken == "chatterbox":
        return _generate_local(script, voice_id)
    return _generate_elevenlabs(script, voice_id)


def _generate_local(script: str, voice_id: str) -> Tuple[str, Optional[str]]:
    """The free engine: Chatterbox on this machine. No key, no account, no credits.

    The same two things happen around it as around the paid one - the pause compressor
    runs on the raw audio, then the loudness normaliser, because the rest of the
    pipeline (footage length, the atempo speed-up, whisper's caption timing) was tuned
    against that shape and does not care who spoke.
    """
    import tts_local

    print("\U0001f3a4 Narrating with the local engine (chatterbox, %s, %s)"
          % (tts_local.config.TTS_MODEL, tts_local.config.TTS_DEVICE))
    clip = tts_local.synthesize(script, voice_id=voice_id)
    return normalize_voice_loudness(compress_narrator_pauses(clip), voice_id), None


def _generate_elevenlabs(script: str, voice_id: str) -> Tuple[str, Optional[str]]:
    """The paid engine, unchanged - kept so a voice can still be auditioned there."""
    key = require_elevenlabs()
    
    try:
        if ELEVENLABS_V1:
            # New ElevenLabs v1.0.0+ API
            client = ElevenLabs(api_key=key)
            
            # VoiceSettings: stability 0.65 (up from the ~0.5 default) is the
            # lever against the glottal-stop artifact that inserts the "ale"
            # syllable after "I'm" — higher stability = fewer vocal-fry breaks.
            audio_generator = client.text_to_speech.convert(
                voice_id=voice_id,
                text=script,
                model_id="eleven_flash_v2",
                voice_settings=VoiceSettings(
                    stability=0.65,
                    similarity_boost=0.85,
                    style=0.0,
                    use_speaker_boost=True,
                ),
            )
            
            # Convert generator to bytes
            audio_bytes = b"".join(audio_generator)
            
            # Save to temp file
            audio_path = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False).name
            with open(audio_path, "wb") as f:
                f.write(audio_bytes)
            
            print(f"   ✅ Voiceover saved: {audio_path}")
            return normalize_voice_loudness(
                compress_narrator_pauses(audio_path), voice_id), None
            
        else:
            # Old ElevenLabs v0.x API (kept for local fallback — the runner
            # uses v1, where VoiceSettings are applied above).
            audio = generate(
                text=script,
                voice=voice_id,
                model="eleven_flash_v2",
                api_key=key
            )
            
            audio_path = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False).name
            save(audio, audio_path)
            
            print(f"   ✅ Voiceover saved: {audio_path}")
            return normalize_voice_loudness(
                compress_narrator_pauses(audio_path), voice_id), None
            
    except Exception as e:
        print(f"   ❌ ElevenLabs failed: {e}")
        raise Exception(f"Voiceover generation failed: {e}")
