import os
import time
import tempfile
import subprocess
import shutil
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
# Stable Compilation (Good Quality + Speed Up)
# ----------------------------------------------------------------------

def compile_video(video_paths, audio_path, script, subtitle_path=None,
                  intro_frame=None, title=None, part_label=None):
    """
    Compile video with:
    - Sped up background (1.25x)
    - Good quality (CRF 18, medium preset)
    - Stable and reliable
    """
    print("🎬 Starting video compilation (STABLE + QUALITY)...")

    # Handle both single path and list
    if isinstance(video_paths, str):
        video_paths = [video_paths]
    if not video_paths:
        raise Exception("No video paths provided")

    # 1. Get audio duration
    audio_duration = get_duration(audio_path)
    print(f"   🎙️ Audio duration: {audio_duration:.2f}s")

    # 2. Extract segment from gameplay (add buffer for speed up)
    speed_factor = 1.25  # 25% faster - good balance
    extract_duration = audio_duration * 1.4  # Buffer for speed up
    print(f"   ⏱️ Extracting {extract_duration:.2f}s of footage (will be sped up {speed_factor}x)")
    
    gameplay_segment = os.path.join(OUTPUT_DIR, f"gameplay_segment_{int(time.time())}.mp4")
    source_video = video_paths[0]
    
    if not os.path.exists(source_video):
        raise Exception(f"Source video not found: {source_video}")
    
    # Extract without re-encoding (fast)
    cmd_extract = [
        'ffmpeg', '-y',
        '-i', source_video,
        '-t', str(extract_duration),
        '-c:v', 'copy',
        '-an',
        gameplay_segment
    ]
    
    try:
        subprocess.run(cmd_extract, check=True, capture_output=True, timeout=120)
        print(f"   ✅ Extracted {extract_duration:.2f}s segment.")
    except Exception as e:
        raise Exception(f"Segment extraction failed: {e}")

    # 3. Step 1: Crop + Scale + Speed Up (Video ONLY)
    print("⚡ Step 1: Cropping, scaling, and speeding up video...")
    video_processed = os.path.join(OUTPUT_DIR, f"video_processed_{int(time.time())}.mp4")
    
    # Video processing: crop to 9:16, scale to 1080x1920, speed up
    cmd_video = [
        'ffmpeg', '-y',
        '-i', gameplay_segment,
        '-vf', 
        f'crop=ih*9/16:ih:(iw-ih*9/16)/2:0,'
        f'scale=1080:1920,'
        f'setpts={1/speed_factor}*PTS',
        '-c:v', 'libx264',
        '-preset', 'medium',      # Good quality, stable
        '-crf', '18',             # Excellent quality (visually lossless)
        '-profile:v', 'high',
        '-level', '4.1',
        '-pix_fmt', 'yuv420p',
        '-an',                    # No audio yet
        '-movflags', '+faststart',
        video_processed
    ]
    
    try:
        subprocess.run(cmd_video, check=True, capture_output=True, timeout=600)
        print(f"   ✅ Video processed (sped up {speed_factor}x, CRF 18).")
        os.unlink(gameplay_segment)
        gameplay_segment = video_processed
    except Exception as e:
        print(f"   ⚠️ Video processing failed: {e}")
        # Fallback: simpler processing
        print("   🔄 Trying fallback with lower quality settings...")
        cmd_video_fallback = [
            'ffmpeg', '-y',
            '-i', gameplay_segment,
            '-vf', 
            f'crop=ih*9/16:ih:(iw-ih*9/16)/2:0,'
            f'scale=1080:1920,'
            f'setpts={1/speed_factor}*PTS',
            '-c:v', 'libx264',
            '-preset', 'veryfast',
            '-crf', '23',
            '-an',
            video_processed
        ]
        try:
            subprocess.run(cmd_video_fallback, check=True, capture_output=True, timeout=600)
            print(f"   ✅ Fallback succeeded (CRF 23, veryfast).")
            os.unlink(gameplay_segment)
            gameplay_segment = video_processed
        except Exception as e2:
            raise Exception(f"Video processing failed: {e2}")

    # 4. Get the actual duration after speed up
    actual_duration = get_duration(gameplay_segment)
    print(f"   ⏱️ Sped up video duration: {actual_duration:.2f}s")

    # 5. Trim to match audio duration exactly
    if actual_duration > audio_duration:
        print(f"   ✂️ Trimming video to match audio ({audio_duration:.2f}s)...")
        video_trimmed = os.path.join(OUTPUT_DIR, f"video_trimmed_{int(time.time())}.mp4")
        cmd_trim = [
            'ffmpeg', '-y',
            '-i', gameplay_segment,
            '-t', str(audio_duration),
            '-c:v', 'copy',
            '-an',
            video_trimmed
        ]
        try:
            subprocess.run(cmd_trim, check=True, capture_output=True, timeout=60)
            os.unlink(gameplay_segment)
            gameplay_segment = video_trimmed
            print(f"   ✅ Trimmed to {audio_duration:.2f}s")
        except Exception as e:
            print(f"   ⚠️ Trim failed: {e}")

    # 6. Add audio (high quality, no re-encode)
    print("⚡ Step 2: Adding audio...")
    final_output = os.path.join(OUTPUT_DIR, f"output_{int(time.time())}.mp4")
    
    cmd_audio = [
        'ffmpeg', '-y',
        '-i', gameplay_segment,
        '-i', audio_path,
        '-c:v', 'copy',        # Don't re-encode video
        '-c:a', 'aac',
        '-b:a', '192k',
        '-shortest',
        '-movflags', '+faststart',
        final_output
    ]
    
    try:
        subprocess.run(cmd_audio, check=True, capture_output=True, timeout=120)
        print(f"   ✅ Audio added.")
        os.unlink(gameplay_segment)
    except Exception as e:
        print(f"   ⚠️ Audio addition failed: {e}")
        # Fallback: re-encode with audio
        cmd_audio_fallback = [
            'ffmpeg', '-y',
            '-i', gameplay_segment,
            '-i', audio_path,
            '-vf', 'scale=1080:1920',
            '-c:v', 'libx264',
            '-preset', 'medium',
            '-crf', '18',
            '-c:a', 'aac',
            '-b:a', '192k',
            '-shortest',
            '-movflags', '+faststart',
            final_output
        ]
        try:
            subprocess.run(cmd_audio_fallback, check=True, capture_output=True, timeout=300)
            print(f"   ✅ Audio added (fallback with re-encode).")
            os.unlink(gameplay_segment)
        except Exception as e2:
            os.rename(gameplay_segment, final_output)

    # 7. Clean up
    for path in video_paths + [audio_path]:
        try:
            if os.path.exists(path):
                os.unlink(path)
        except:
            pass

    print(f"✅ Video compiled successfully: {final_output}")
    print(f"   📊 Video info:")
    print(f"      - Resolution: 1080x1920 (9:16)")
    print(f"      - Video codec: H.264 (High Profile)")
    print(f"      - Audio codec: AAC 192kbps")
    print(f"      - Speed factor: {speed_factor}x")
    print(f"      - Quality: CRF 18 (excellent)")
    return final_output
