import os
import time
import tempfile
import subprocess
from config import OUTPUT_DIR

# ----------------------------------------------------------------------
# Helper: Get duration (seconds)
# ----------------------------------------------------------------------
def get_duration(media_path: str) -> float:
    """Return duration in seconds using ffprobe."""
    cmd = [
        'ffprobe', '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        media_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())

# ----------------------------------------------------------------------
# Generate SRT with 2-word chunks (2-by-2 captions)
# ----------------------------------------------------------------------
def generate_fallback_srt(script, intro_duration, srt_path, audio_duration=None):
    """Generate SRT subtitles with 2 words per caption chunk."""
    import re
    
    words = re.findall(r'\b\w+\b', script)
    if not words:
        return None
    
    # Group into 2-word chunks
    chunk_size = 2
    chunks = []
    for i in range(0, len(words), chunk_size):
        chunk = words[i:i+chunk_size]
        chunks.append(' '.join(chunk))
    
    if not chunks:
        return None
    
    if audio_duration and audio_duration > 0:
        total_duration = audio_duration
    else:
        total_duration = len(words) / 3
        if total_duration < 30:
            total_duration = 60
    
    dur_per_chunk = total_duration / len(chunks)
    
    # UPPERCASE the entire script
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
            f.write(f"{chunk.upper()}\n\n")  # <-- UPPERCASE
    
    return srt_path

# ----------------------------------------------------------------------
# Main compilation function
# ----------------------------------------------------------------------
def compile_video(video_paths, audio_path, script, subtitle_path=None,
                  intro_frame=None, title=None, part_label=None):
    """Compile video with 1080p quality, 2-by-2 UPPERCASE captions, and narrator speed."""
    print("🎬 Starting video compilation (1080p + UPPERCASE captions + speed)...")

    # Handle both single path and list
    if isinstance(video_paths, str):
        video_paths = [video_paths]
    if not video_paths:
        raise Exception("No video paths provided")

    # 1. Get audio duration
    audio_duration = get_duration(audio_path)
    print(f"   🎙️ Audio duration: {audio_duration:.2f}s")

    # 2. Generate SRT (2-word chunks, UPPERCASE)
    srt_path = None
    try:
        srt_path = tempfile.NamedTemporaryFile(delete=False, suffix='.srt').name
        generate_fallback_srt(script, 0, srt_path, audio_duration)
        print(f"   ✅ 2-by-2 UPPERCASE SRT subtitles created: {srt_path}")
    except Exception as e:
        print(f"   ⚠️ SRT generation failed: {e}")
        srt_path = None

    # 3. Extract segment from gameplay
    print("⚡ Step 1: Extracting segment from gameplay...")
    gameplay_segment = os.path.join(OUTPUT_DIR, f"gameplay_segment_{int(time.time())}.mp4")
    source_video = video_paths[0]
    
    if not os.path.exists(source_video):
        raise Exception(f"Source video not found: {source_video}")
    
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

    # 4. Crop and resize to 1080p
    print("⚡ Step 2: Cropping and resizing to 1080p...")
    video_cropped = os.path.join(OUTPUT_DIR, f"video_cropped_{int(time.time())}.mp4")
    
    cmd_crop = [
        'ffmpeg', '-y',
        '-i', gameplay_segment,
        '-vf', 'crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920',
        '-c:v', 'libx264',
        '-preset', 'veryfast',
        '-crf', '18',
        '-an',
        video_cropped
    ]
    
    try:
        subprocess.run(cmd_crop, check=True, capture_output=True, timeout=600)
        print(f"   ✅ Cropped and resized to 1080p.")
        os.unlink(gameplay_segment)
        gameplay_segment = video_cropped
    except subprocess.TimeoutExpired:
        print("   ⚠️ 1080p crop timed out. Trying 720p fallback...")
        cmd_crop_fallback = [
            'ffmpeg', '-y',
            '-i', gameplay_segment,
            '-vf', 'crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=720:1280',
            '-c:v', 'libx264',
            '-preset', 'veryfast',
            '-crf', '18',
            '-an',
            video_cropped
        ]
        try:
            subprocess.run(cmd_crop_fallback, check=True, capture_output=True, timeout=300)
            print(f"   ✅ Cropped and resized to 720p (fallback).")
            os.unlink(gameplay_segment)
            gameplay_segment = video_cropped
        except Exception as e2:
            raise Exception(f"Video cropping failed: {e2}")
    except Exception as e:
        raise Exception(f"Video cropping failed: {e}")

    # 5. Mix audio: speed up narrator (1.15x) + keep original quality
    print("⚡ Step 3: Processing audio (narrator speed 1.15x)...")
    audio_processed = os.path.join(OUTPUT_DIR, f"audio_processed_{int(time.time())}.mp3")
    
    # Use atempo to speed up voice, keep same sample rate
    cmd_audio = [
        'ffmpeg', '-y',
        '-i', audio_path,
        '-filter_complex', 'atempo=1.15',
        '-c:a', 'aac',
        '-b:a', '192k',
        audio_processed
    ]
    
    try:
        subprocess.run(cmd_audio, check=True, capture_output=True, timeout=60)
        print("   ✅ Audio processed (1.15x speed).")
        audio_to_use = audio_processed
    except Exception as e:
        print(f"   ⚠️ Audio processing failed: {e}. Using original audio.")
        audio_to_use = audio_path

    # 6. Combine video + processed audio
    print("⚡ Step 4: Combining video + audio...")
    final_output = os.path.join(OUTPUT_DIR, f"output_{int(time.time())}.mp4")
    
    cmd_combine = [
        'ffmpeg', '-y',
        '-i', gameplay_segment,
        '-i', audio_to_use,
        '-c:v', 'copy',
        '-c:a', 'aac',
        '-b:a', '192k',
        '-shortest',
        '-movflags', '+faststart',
        final_output
    ]
    
    try:
        subprocess.run(cmd_combine, check=True, capture_output=True, timeout=120)
        print("   ✅ Video combined.")
    except Exception as e:
        raise Exception(f"Video combine failed: {e}")

    # 7. Burn captions (smaller, bold, centered, UPPERCASE)
    if srt_path and os.path.exists(srt_path):
        print("⚡ Step 5: Burning captions (smaller, bold, centered, UPPERCASE)...")
        temp_captioned = os.path.join(OUTPUT_DIR, f"temp_captioned_{int(time.time())}.mp4")
        
        # Smaller font size (35), bold, centered
        cmd_burn = [
            'ffmpeg', '-y',
            '-i', final_output,
            '-vf', f"subtitles={srt_path}:force_style='Fontsize=35, Bold=1, Alignment=10, OutlineColour=&H80000000, MarginV=50'",
            '-c:a', 'copy',
            temp_captioned
        ]
        
        try:
            subprocess.run(cmd_burn, check=True, capture_output=True, timeout=600)
            os.replace(temp_captioned, final_output)
            print("   ✅ 2-by-2 UPPERCASE captions burned.")
        except Exception as e:
            print(f"   ⚠️ Caption burn failed: {e}.")
            if os.path.exists(temp_captioned):
                os.unlink(temp_captioned)
    else:
        print("   ⚠️ No SRT available. Skipping captions.")

    # 8. Clean up
    for path in [gameplay_segment, source_video, audio_processed]:
        try:
            os.unlink(path)
        except:
            pass
    for path in video_paths + [audio_path]:
        try:
            os.unlink(path)
        except:
            pass
    if srt_path:
        try:
            os.unlink(srt_path)
        except:
            pass

    print(f"✅ Video compiled successfully: {final_output}")
    return final_output
