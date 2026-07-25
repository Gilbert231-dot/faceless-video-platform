import os
import time
import tempfile
import subprocess
from config import OUTPUT_DIR

def generate_fallback_srt(script, intro_duration, srt_path, audio_duration=None):
    """Generate SRT subtitles from script (fallback when Whisper fails)."""
    import re
    
    words = re.findall(r'\b\w+\b', script)
    if not words:
        return None
    
    # Group into phrases of 4-6 words
    phrase_size = 5
    phrases = []
    for i in range(0, len(words), phrase_size):
        phrase = ' '.join(words[i:i+phrase_size])
        phrases.append(phrase)
    
    if audio_duration and audio_duration > 0:
        total_duration = audio_duration
    else:
        total_duration = len(words) / 3
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


def compile_video(video_paths, audio_path, script, subtitle_path=None,
                  intro_frame=None, title=None, part_label=None):
    """Compile video with clean audio and bold captions (no music)."""
    print("🎬 Starting video compilation (clean version)...")

    # 1. Get audio duration
    audio_duration = float(subprocess.check_output(
        ['ffprobe', '-i', audio_path, '-show_entries', 'format=duration',
         '-v', 'quiet', '-of', 'csv=%s' % ("p=0")]
    ).decode().strip())
    print(f"   🎙️ Audio duration: {audio_duration:.2f}s")

    # 2. Generate SRT (if not provided)
    srt_path = None
    if subtitle_path and os.path.exists(subtitle_path):
        srt_path = subtitle_path
        print(f"   ✅ Using provided SRT: {srt_path}")
    else:
        try:
            srt_path = tempfile.NamedTemporaryFile(delete=False, suffix='.srt').name
            generate_fallback_srt(script, 0, srt_path, audio_duration)
            print(f"   ✅ SRT subtitles created: {srt_path}")
        except Exception as e:
            print(f"   ⚠️ SRT generation failed: {e}")
            srt_path = None

    # 3. Extract required segment from gameplay footage
    print("⚡ Step 1: Extracting segment from gameplay...")
    gameplay_segment = os.path.join(OUTPUT_DIR, f"gameplay_segment_{int(time.time())}.mp4")
    
    source_video = video_paths[0]
    cmd_extract = [
        'ffmpeg', '-y',
        '-i', source_video,
        '-t', str(audio_duration),
        '-c:v', 'copy',
        '-an',
        gameplay_segment
    ]
    
    try:
        subprocess.run(cmd_extract, check=True, capture_output=True, timeout=120)
        print(f"   ✅ Extracted {audio_duration:.2f}s segment.")
    except Exception as e:
        raise Exception(f"Segment extraction failed: {e}")

    # 4. Combine gameplay + voiceover audio (NO AUDIO PROCESSING)
    print("⚡ Step 2: Combining video + audio...")
    final_output = os.path.join(OUTPUT_DIR, f"output_{int(time.time())}.mp4")
    
    cmd_combine = [
        'ffmpeg', '-y',
        '-i', gameplay_segment,
        '-i', audio_path,
        '-vf', 'crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920',
        '-c:v', 'libx264',
        '-preset', 'medium',
        '-crf', '18',
        '-c:a', 'copy',        # <-- CRITICAL: copy audio WITHOUT processing
        '-shortest',
        '-movflags', '+faststart',
        final_output
    ]
    
    try:
        subprocess.run(cmd_combine, check=True, capture_output=True, timeout=600)
        print("   ✅ Video combined.")
    except Exception as e:
        raise Exception(f"Video combine failed: {e}")

    # 5. Burn captions (bold, centered, big)
    if srt_path and os.path.exists(srt_path):
        print("⚡ Step 3: Burning captions...")
        temp_captioned = os.path.join(OUTPUT_DIR, f"temp_captioned_{int(time.time())}.mp4")
        cmd_burn = [
            'ffmpeg', '-y',
            '-i', final_output,
            '-vf', f"subtitles={srt_path}:force_style='Fontsize=55, Bold=1, Alignment=10, OutlineColour=&H80000000'",
            '-c:a', 'copy',
            temp_captioned
        ]
        try:
            subprocess.run(cmd_burn, check=True, capture_output=True, timeout=180)
            os.replace(temp_captioned, final_output)
            print("   ✅ Captions burned.")
        except Exception as e:
            print(f"   ⚠️ Caption burn failed: {e}.")
            if os.path.exists(temp_captioned):
                os.unlink(temp_captioned)

    # 6. Clean up
    for path in [gameplay_segment, source_video]:
        try:
            os.unlink(path)
        except:
            pass
    for path in video_paths + [audio_path]:
        try:
            os.unlink(path)
        except:
            pass
    if srt_path and srt_path != subtitle_path:
        try:
            os.unlink(srt_path)
        except:
            pass

    print(f"✅ Video compiled successfully: {final_output}")
    return final_output
