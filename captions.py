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
    """Generate SRT subtitles with natural pauses and bigger text."""
    import re
    
    # Split script into words
    words = re.findall(r'\b\w+\b', script)
    
    if not words:
        return None
    
    # --- Use audio duration if provided ---
    if audio_duration and audio_duration > 0:
        total_duration = audio_duration
    else:
        total_duration = len(words) / 4
        if total_duration < 30:
            total_duration = 60
    
    # --- Calculate base duration per word ---
    base_dur_per_word = total_duration / len(words)
    
    # --- Add micro-pauses for natural rhythm ---
    # Each word gets a random duration between 80% and 140% of the base duration
    # This creates natural pauses without breaking sync
    durations = []
    for i, word in enumerate(words):
        # Add longer pauses at punctuation marks (if any)
        # We'll simulate pauses by giving certain words longer durations
        if word in ['.', '!', '?', ',', ';', ':'] or word in ['and', 'but', 'or', 'so', 'for']:
            # Pause slightly longer at punctuation and conjunctions
            dur = base_dur_per_word * random.uniform(1.2, 1.8)
        else:
            dur = base_dur_per_word * random.uniform(0.7, 1.3)
        durations.append(dur)
    
    # Normalize durations so total = audio_duration
    total_dur_sum = sum(durations)
    scale_factor = total_duration / total_dur_sum
    durations = [d * scale_factor for d in durations]
    
    # --- Generate SRT ---
    with open(srt_path, 'w') as f:
        current_time = intro_duration
        for i, word in enumerate(words, 1):
            start_time = current_time
            end_time = current_time + durations[i-1]
            current_time = end_time
            
            start_s = int(start_time)
            start_ms = int((start_time - start_s) * 1000)
            end_s = int(end_time)
            end_ms = int((end_time - end_s) * 1000)
            
            f.write(f"{i}\n")
            f.write(f"{start_s//3600:02d}:{(start_s%3600)//60:02d}:{start_s%60:02d},{start_ms:03d} --> ")
            f.write(f"{end_s//3600:02d}:{(end_s%3600)//60:02d}:{end_s%60:02d},{end_ms:03d}\n")
            f.write(f"{word}\n\n")
    
    return srt_path
