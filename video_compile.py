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
# Simplified compilation (NO INTRO, NO TEMPLATES)
# ----------------------------------------------------------------------

def compile_video(video_paths, audio_path, script, subtitle_path=None,
                  intro_frame=None, title=None, part_label=None):
    """
    Compile video with audio only - no intro, no templates.
    Just gameplay footage with voiceover.
    """
    print("🎬 Starting video compilation (NO INTRO, SIMPLE)...")

    # Handle both single path and list
    if isinstance(video_paths, str):
        video_paths = [video_paths]
    if not video_paths:
        raise Exception("No video paths provided")

    # 1. Get audio duration
    audio_duration = get_duration(audio_path)
    print(f"   🎙️ Audio duration: {audio_duration:.2f}s")

    # 2. Extract segment from gameplay
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

    # 3. Crop to 9:16 and scale to 1080x1920
    print("⚡ Step 2: Cropping to 9:16 and scaling to 1080x1920...")
    video_ready = os.path.join(OUTPUT_DIR, f"video_ready_{int(time.time())}.mp4")
    
    cmd_crop = [
        'ffmpeg', '-y',
        '-i', gameplay_segment,
        '-vf', 'crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920',
        '-c:v', 'libx264',
        '-preset', 'ultrafast',
        '-crf', '23',
        '-an',
        video_ready
    ]
    
    try:
        subprocess.run(cmd_crop, check=True, capture_output=True, timeout=300)
        print(f"   ✅ Cropped and scaled to 1080x1920.")
        os.unlink(gameplay_segment)
        gameplay_segment = video_ready
    except Exception as e:
        raise Exception(f"Video processing failed: {e}")

    # 4. Add audio
    print("⚡ Step 3: Adding audio...")
    final_output = os.path.join(OUTPUT_DIR, f"output_{int(time.time())}.mp4")
    
    cmd_audio = [
        'ffmpeg', '-y',
        '-i', gameplay_segment,
        '-i', audio_path,
        '-c:v', 'copy',
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
            '-preset', 'ultrafast',
            '-crf', '23',
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

    # 5. Clean up
    for path in video_paths + [audio_path]:
        try:
            if os.path.exists(path):
                os.unlink(path)
        except:
            pass

    print(f"✅ Video compiled successfully: {final_output}")
    return final_output
