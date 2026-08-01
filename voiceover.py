import os
import tempfile
from typing import Tuple, Optional

# Import the ElevenLabs client (v1.0.0+)
from elevenlabs.client import ElevenLabs
from elevenlabs import play, save
from elevenlabs.types import VoiceSettings

# API Key
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

if not ELEVENLABS_API_KEY:
    raise ValueError("ELEVENLABS_API_KEY not set in environment variables!")

# Voice IDs
MALE_VOICE_ID = "bfGb7JTLUnZebZRiFYyq"      # Adam - Distinct, Deep and Engaging
FEMALE_VOICE_ID = "S9NKLs1GeSTKzXd9D0Lf"    # Haley Maven - Social Media Bestie
DEFAULT_VOICE_ID = MALE_VOICE_ID

def generate_voiceover(script: str, voice_id: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """
    Generate voiceover using ElevenLabs v1.0.0+.
    
    Args:
        script: Text to convert to speech
        voice_id: ElevenLabs voice ID (if None, uses default)
    
    Returns:
        audio_path: Path to the generated MP3
        subtitle_path: None
    """
    if voice_id is None:
        voice_id = DEFAULT_VOICE_ID
    
    try:
        # Initialize the client
        client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
        
        print(f"   🔊 Generating with ElevenLabs (voice: {voice_id[:8]}...)")
        
        # Generate audio using the correct v1.0.0+ method
        audio_generator = client.text_to_speech.convert(
            voice_id=voice_id,
            text=script,
            model_id="eleven_flash_v2"  # Most cost-effective model
        )
        
        # Collect the audio bytes from the generator
        audio_bytes = b"".join(audio_generator)
        
        if len(audio_bytes) < 1000:
            raise Exception(f"Generated audio too small: {len(audio_bytes)} bytes")
        
        # Save to temp file
        audio_path = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False).name
        with open(audio_path, "wb") as f:
            f.write(audio_bytes)
        
        print(f"   ✅ Voiceover saved: {audio_path} ({len(audio_bytes)} bytes)")
        return audio_path, None
        
    except AttributeError as e:
        # If the method name is different, try alternative
        print(f"   ⚠️ AttributeError: {e}")
        print("   🔄 Trying alternative method...")
        
        try:
            # Try the older style (just in case)
            from elevenlabs import generate
            audio = generate(
                text=script,
                voice=voice_id,
                model="eleven_flash_v2",
                api_key=ELEVENLABS_API_KEY
            )
            
            audio_path = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False).name
            save(audio, audio_path)
            print(f"   ✅ Voiceover saved (fallback): {audio_path}")
            return audio_path, None
            
        except Exception as fallback_error:
            print(f"   ❌ Fallback also failed: {fallback_error}")
            raise Exception(f"Voiceover generation failed: {e}")
        
    except Exception as e:
        print(f"   ❌ ElevenLabs failed: {e}")
        raise Exception(f"Voiceover generation failed: {e}")
