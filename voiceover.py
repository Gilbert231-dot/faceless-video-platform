import os
import re
import tempfile
from typing import Tuple, Optional

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
    
    # LAST-LINE DEFENSE for the "ale" artifact (belt and suspenders): even if
    # a script somehow slips past clean_script_for_tts, never send a standalone
    # "ale"/"aale"/"alee" to the TTS. Word boundaries keep real words
    # ("male", "female", "scale", "tale") intact. The post-generation audio
    # mute (caption step) remains as the second layer.
    script = re.sub(r'\b(?:ale|aale|alee)\b', '', script, flags=re.IGNORECASE)
    script = re.sub(r' +', ' ', script).strip()
    
    try:
        if ELEVENLABS_V1:
            # New ElevenLabs v1.0.0+ API
            client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
            
            audio_generator = client.text_to_speech.convert(
                voice_id=voice_id,
                text=script,
                model_id="eleven_flash_v2"
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
            # Old ElevenLabs v0.x API
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
