import os
import json
import tempfile
import asyncio
import edge_tts
import whisper

def generate_voiceover(script):
    """Generate voiceover using Edge TTS and transcribe with Whisper."""
    voice = "en-US-JennyNeural"
    audio_file = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3')
    subtitle_file = tempfile.NamedTemporaryFile(delete=False, suffix='.json')
    
    print("🔊 Generating voiceover with Edge TTS (female)...")
    
    async def generate():
        communicate = edge_tts.Communicate(script, voice, rate="-5%")
        await communicate.save(audio_file.name)
    
    asyncio.run(generate())
    print(f"   ✅ Voiceover saved: {audio_file.name}")
    
    print("🧠 Transcribing with Whisper...")
    try:
        model = whisper.load_model("tiny")
        result = model.transcribe(audio_file.name, word_timestamps=True)
        words = []
        for segment in result['segments']:
            for word in segment['words']:
                words.append({
                    'text': word['word'].strip(),
                    'start': word['start'],
                    'end': word['end']
                })
        with open(subtitle_file.name, 'w') as f:
            json.dump(words, f)
        print(f"   ✅ Subtitle file saved: {subtitle_file.name}")
        print(f"   📝 Found {len(words)} words with timestamps")
    except Exception as e:
        print(f"   ⚠️ Whisper failed: {e}. Using fallback.")
        subtitle_file = None
    
    return audio_file.name, subtitle_file.name if subtitle_file else None