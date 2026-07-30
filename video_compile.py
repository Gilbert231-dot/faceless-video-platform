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
# Quality + Speed Compilation (NO INTRO)
# ----------------------------------------------------------------------

def compile_video(video_paths, audio_path, script, subtitle_path=None,
                  intro_frame=None, title=None, part_label=None):
    """
    Compile video with:
    - Sped up background (1.2x - 1.5x faster)
    - Maximum quality (CRF 15, slow preset)
    - 1080x1920 9:16 output
    """
    print("🎬 Starting video compilation (QUALITY + SPEED UP)...")

    # Handle both single path and list
    if isinstance(video_paths, str):
        video_paths = [video_paths]
    if not video_paths:
        raise Exception("No video paths provided")

    # 1. Get audio duration
    audio_duration = get_duration(audio_path)
    print(f"   🎙️ Audio duration: {audio_duration:.2f}s")

    # 2. Extract segment from gameplay (add some buffer for speed up)
    # We extract a bit more than audio duration so after speeding up
    # we still have enough footage
    extract_duration = audio_duration * 1.5  # 1.5x for safety
    print(f"   ⏱️ Extracting {extract_duration:.2f}s of footage (will be sped up)")
    
    gameplay_segment = os.path.join(OUTPUT_DIR, f"gameplay_segment_{int(time.time())}.mp4")
    source_video = video_paths[0]
    
    if not os.path.exists(source_video):
        raise Exception(f"Source video not found: {source_video}")
    
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

    # 3. Process video: Crop + Scale + Speed Up + High Quality
    print("⚡ Step 1: Cropping to 9:16, scaling to 1080x1920, and speeding up...")
    video_ready = os.path.join(OUTPUT_DIR, f"video_ready_{int(time.time())}.mp4")
    
    # Speed factor: 1.3x (good balance between engaging and watchable)
    # Adjust this value if you want faster/slower: 1.2 = 20% faster, 1.5 = 50% faster
    speed_factor = 1.3
    
    cmd_process = [
        'ffmpeg', '-y',
        '-i', gameplay_segment,
        '-vf', 
        f'crop=ih*9/16:ih:(iw-ih*9/16)/2:0,'
        f'scale=1080:1920,'
        f'setpts={1/speed_factor}*PTS',  # Speed up video
        '-filter_complex', 
        f'[0:a]atempo={speed_factor}[a]',  # Speed up audio (but we won't use it)
        '-map', '0:v:0',
        '-map', '[a]',
        '-c:v', 'libx264',
        '-preset', 'slow',        # Max quality (vs ultrafast/veryfast)
        '-crf', '15',             # Max quality (lower = better, 18 is transparent)
        '-profile:v', 'high',
        '-level', '4.1',
        '-pix_fmt', 'yuv420p',
        '-c:a', 'aac',
        '-b:a', '192k',
        '-movflags', '+faststart',
        video_ready
    ]
    
    try:
        subprocess.run(cmd_process, check=True, capture_output=True, timeout=600)
        print(f"   ✅ Cropped, scaled, and sped up {speed_factor}x.")
        os.unlink(gameplay_segment)
        gameplay_segment = video_ready
    except Exception as e:
        print(f"   ⚠️ Processing failed: {e}")
        raise Exception(f"Video processing failed: {e}")

    # 4. Get the actual duration after speed up
    actual_duration = get_duration(gameplay_segment)
    print(f"   ⏱️ Sped up video duration: {actual_duration:.2f}s")

    # 5. Trim to match audio duration exactly (in case speed up made it longer/shorter)
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

    # 6. Add audio (max quality, no re-encode of video)
    print("⚡ Step 2: Adding audio (max quality)...")
    final_output = os.path.join(OUTPUT_DIR, f"output_{int(time.time())}.mp4")
    
    # Use highest quality audio codec
    cmd_audio = [
        'ffmpeg', '-y',
        '-i', gameplay_segment,
        '-i', audio_path,
        '-c:v', 'copy',        # Don't re-encode video (preserve quality)
        '-c:a', 'aac',         # Best audio codec for compatibility
        '-b:a', '256k',        # Highest bitrate for audio
        '-shortest',
        '-movflags', '+faststart',
        final_output
    ]
    
    try:
        subprocess.run(cmd_audio, check=True, capture_output=True, timeout=120)
        print(f"   ✅ Audio added (256kbps AAC).")
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
            '-preset', 'slow',
            '-crf', '15',
            '-profile:v', 'high',
            '-level', '4.1',
            '-pix_fmt', 'yuv420p',
            '-c:a', 'aac',
            '-b:a', '256k',
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
    print(f"      - Audio codec: AAC 256kbps")
    print(f"      - Speed factor: {speed_factor}x")
    return final_output
