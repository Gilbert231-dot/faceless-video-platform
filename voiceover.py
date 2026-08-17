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
)

# Try the new ElevenLabs import style (v1.0.0+)
try:
    from elevenlabs.client import ElevenLabs
    from elevenlabs import play, save
    from elevenlabs.types import VoiceSettings
    ELEVENLABS_V1 = True
except ImportError:
    # Fallback to old style (v0.x)
    from elevenlabs import generate, save
    ELEVENLABS_V1 = False

# API Key (set in environment)
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

if not ELEVENLABS_API_KEY:
    raise ValueError("ELEVENLABS_API_KEY not set in environment variables!")

# Voice IDs
MALE_VOICE_ID = "loZFKb410q0XFUiYDx8U"      # Custom Gen Z voice
FEMALE_VOICE_ID = "EXAVITQu4vr4xnSDxMaL"    # Sarah
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


def generate_voiceover(script: str, voice_id: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """
    Generate voiceover using ElevenLabs.
    
    Args:
        script: Text to convert to speech
        voice_id: ElevenLabs voice ID (if None, uses default)
    
    Returns:
        audio_path: Path to the generated MP3
        subtitle_path: None
    """
    if voice_id is None:
        voice_id = DEFAULT_VOICE_ID
    
    # LAST-LINE DEFENSE (belt and suspenders): re-run the full tts_clean pass
    # even if a script somehow slipped past clean_script_for_tts — "ale" -> "",
    # markdown/brackets stripped, acronyms expanded, "I'm" -> "I am". The
    # post-generation audio mute (caption step) remains as the second layer.
    script = clean_for_tts(script)
    
    # Ship the EXACT text sent to ElevenLabs (output/tts_script_*.json) so
    # the run's artifact proves what the narrator was asked to say — if a
    # glitch is ever heard, we can tell instantly whether it came from the
    # text or the voice model.
    try:
        out_dir = os.environ.get("OUTPUT_DIR", "output")
        os.makedirs(out_dir, exist_ok=True)
        script_path = os.path.join(out_dir, f"tts_script_{int(time.time())}.json")
        with open(script_path, "w", encoding="utf-8") as f:
            json.dump({"voice_id": voice_id, "script": script}, f,
                      indent=2, ensure_ascii=False)
    except Exception:
        pass
    
    try:
        if ELEVENLABS_V1:
            # New ElevenLabs v1.0.0+ API
            client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
            
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
            return compress_narrator_pauses(audio_path), None
            
        else:
            # Old ElevenLabs v0.x API (kept for local fallback — the runner
            # uses v1, where VoiceSettings are applied above).
            audio = generate(
                text=script,
                voice=voice_id,
                model="eleven_flash_v2",
                api_key=ELEVENLABS_API_KEY
            )
            
            audio_path = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False).name
            save(audio, audio_path)
            
            print(f"   ✅ Voiceover saved: {audio_path}")
            return compress_narrator_pauses(audio_path), None
            
    except Exception as e:
        print(f"   ❌ ElevenLabs failed: {e}")
        raise Exception(f"Voiceover generation failed: {e}")
