import re
import json
import random
import tempfile

def whisper_json_to_srt(subtitle_path, intro_duration, srt_path):
    """Convert Whisper JSON output to SRT subtitle format."""
    with open(subtitle_path, 'r') as f:
        words = json.load(f)
    
    if not words:
        return None
    
    lines = []
    current_line = []
    current_start = None
    current_end = None
    
    for word in words:
        text = word['text'].strip()
        start = word['start'] + intro_duration
        end = word['end'] + intro_duration
        
        if not current_start:
            current_start = start
        
        current_line.append(text)
        current_end = end
        
        if len(current_line) >= 6 or any(p in text for p in ['.', '?', '!', ',', ';', ':']):
            line_text = ' '.join(current_line)
            lines.append((current_start, current_end, line_text))
            current_line = []
            current_start = None
            current_end = None
    
    if current_line:
        line_text = ' '.join(current_line)
        lines.append((current_start, current_end, line_text))
    
    with open(srt_path, 'w') as f:
        for i, (start, end, text) in enumerate(lines, 1):
            start_s = int(start)
            start_ms = int((start - start_s) * 1000)
            end_s = int(end)
            end_ms = int((end - end_s) * 1000)
            f.write(f"{i}\n")
            f.write(f"{start_s//3600:02d}:{(start_s%3600)//60:02d}:{start_s%60:02d},{start_ms:03d} --> ")
            f.write(f"{end_s//3600:02d}:{(end_s%3600)//60:02d}:{end_s%60:02d},{end_ms:03d}\n")
            f.write(f"{text}\n\n")
    
    return srt_path


def generate_fallback_srt(script, intro_duration, srt_path, audio_duration=None):
    """Generate SRT with larger phrases (5-7 words) to reduce subtitle count."""
    import re
    
    words = re.findall(r'\b\w+\b', script)
    if not words:
        return None
    
    # Group into phrases of 5-7 words
    phrase_size = 6
    phrases = []
    for i in range(0, len(words), phrase_size):
        phrase = ' '.join(words[i:i+phrase_size])
        phrases.append(phrase)
    
    # Use audio duration
    if audio_duration and audio_duration > 0:
        total_duration = audio_duration
    else:
        total_duration = len(words) / 4
        if total_duration < 30:
            total_duration = 60
    
    dur_per_phrase = total_duration / len(phrases)
    
    with open(srt_path, 'w') as f:
        for i, phrase in enumerate(phrases, 1):
            start_time = (i - 1) * dur_per_phrase + intro_duration
            end_time = i * dur_per_phrase + intro_duration
            
            start_s = int(start_time)
            start_ms = int((start_time - start_s) * 1000)
            end_s = int(end_time)
            end_ms = int((end_time - end_s) * 1000)
            
            f.write(f"{i}\n")
            f.write(f"{start_s//3600:02d}:{(start_s%3600)//60:02d}:{start_s%60:02d},{start_ms:03d} --> ")
            f.write(f"{end_s//3600:02d}:{(end_s%3600)//60:02d}:{end_s%60:02d},{end_ms:03d}\n")
            f.write(f"{phrase}\n\n")
    
    return srt_path
