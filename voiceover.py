import os
import tempfile
from typing import Tuple, Optional
from elevenlabs import generate, save

# ElevenLabs API Key (set this in your environment or .env file)
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

if not ELEVENLABS_API_KEY:
    raise ValueError("ELEVENLABS_API_KEY not set in environment variables!")

# Voice IDs (Brian for male, Sarah for female)
MALE_VOICE_ID = "nPczCjzI2devNBz1zQrb"      # Brian
FEMALE_VOICE_ID = "EXAVITQu4vr4xnSDxMaL"    # Sarah
DEFAULT_VOICE_ID = MALE_VOICE_ID

def generate_voiceover(script: str, voice_id: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """
    Generate voiceover using ElevenLabs.
    
    Args:
        script: Text to convert to speech
        voice_id: ElevenLabs voice ID (if None, uses male voice)
    
    Returns:
        audio_path: Path to the generated MP3
        subtitle_path: None
    """
    if voice_id is None:
        voice_id = DEFAULT_VOICE_ID
    
    try:
        # Generate audio
        audio = generate(
            text=script,
            voice=voice_id,
            model="eleven_flash_v2",
            api_key=ELEVENLABS_API_KEY
        )
        
        # Save to temp file
        audio_path = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False).name
        save(audio, audio_path)
        
        return audio_path, None
        
    except Exception as e:
        raise Exception(f"ElevenLabs voiceover failed: {e}")
