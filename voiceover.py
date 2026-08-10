import os
import re
import json
import time
import tempfile
from typing import Tuple, Optional

from tts_clean import clean_for_tts

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
            return audio_path, None
            
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
            return audio_path, None
            
    except Exception as e:
        print(f"   ❌ ElevenLabs failed: {e}")
        raise Exception(f"Voiceover generation failed: {e}")
