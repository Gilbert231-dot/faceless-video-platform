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


def generate_fallback_srt(script, intro_duration, srt_path):
    """Generate SRT subtitles with word-by-word chunks (1 word per subtitle)."""
    import re
    
    # Split script into words
    words = re.findall(r'\b\w+\b', script)
    
    if not words:
        return None
    
    # --- 1 WORD PER CAPTION (word-by-word) ---
    chunk_size = 1  # <-- Changed from 2-4 to 1
    chunks = []
    for i in range(0, len(words), chunk_size):
        chunk = words[i:i+chunk_size]
        chunks.append(' '.join(chunk))
    
    if not chunks:
        chunks = words  # fallback: each word individually
    
    # --- ESTIMATE DURATION FOR SPED-UP SPEECH ---
    # Normal speech: 3 words/second. At 1.3x speed: ~4 words/second.
    total_words = len(words)
    estimated_duration = total_words / 4  # 4 words per second (for 1.3x speed)
    
    # Minimum duration
    if estimated_duration < 30:
        estimated_duration = 60
    
    # Calculate duration per chunk (1 word per chunk)
    dur_per_chunk = estimated_duration / len(chunks)
    
    # If duration per word is too short, set a minimum
    if dur_per_chunk < 0.25:
        dur_per_chunk = 0.25  # Minimum 0.25 seconds per word
    
    with open(srt_path, 'w') as f:
        for i, chunk in enumerate(chunks, 1):
            start_time = (i - 1) * dur_per_chunk + intro_duration
            end_time = i * dur_per_chunk + intro_duration
            
            start_s = int(start_time)
            start_ms = int((start_time - start_s) * 1000)
            end_s = int(end_time)
            end_ms = int((end_time - end_s) * 1000)
            
            f.write(f"{i}\n")
            f.write(f"{start_s//3600:02d}:{(start_s%3600)//60:02d}:{start_s%60:02d},{start_ms:03d} --> ")
            f.write(f"{end_s//3600:02d}:{(end_s%3600)//60:02d}:{end_s%60:02d},{end_ms:03d}\n")
            f.write(f"{chunk}\n\n")
    
    return srt_path
