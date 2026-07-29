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
# Main compilation function (No captions, maximum quality)
# ----------------------------------------------------------------------
def compile_video(video_paths, audio_path, script, subtitle_path=None,
                  intro_frame=None, title=None, part_label=None):
    """Compile video with fast cropping and high quality."""
    print("🎬 Starting video compilation (fast crop, high quality)...")

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

    # 3. Crop and resize to 1080p (FAST preset)
    print("⚡ Step 2: Cropping and resizing to 1080p (veryfast)...")
    video_cropped = os.path.join(OUTPUT_DIR, f"video_cropped_{int(time.time())}.mp4")
    
    cmd_crop = [
        'ffmpeg', '-y',
        '-i', gameplay_segment,
        '-vf', 'crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920',
        '-c:v', 'libx264',
        '-preset', 'veryfast',   # FAST preset to avoid timeout
        '-crf', '18',
        '-an',
        video_cropped
    ]
    
    try:
        subprocess.run(cmd_crop, check=True, capture_output=True, timeout=900)  # 15 minutes timeout
        print(f"   ✅ Cropped and resized to 1080p.")
        os.unlink(gameplay_segment)
        gameplay_segment = video_cropped
    except subprocess.TimeoutExpired:
        raise Exception("Video cropping timed out even with veryfast preset. Try reducing resolution or using a smaller source.")
    except Exception as e:
        raise Exception(f"Video cropping failed: {e}")

    # 4. Combine video + audio (no re-encode, just copy)
    print("⚡ Step 3: Combining video + audio...")
    final_output = os.path.join(OUTPUT_DIR, f"output_{int(time.time())}.mp4")
    
    cmd_combine = [
        'ffmpeg', '-y',
        '-i', gameplay_segment,
        '-i', audio_path,
        '-c:v', 'copy',        # Use already encoded video
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

    # 5. Clean up
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

    print(f"✅ Video compiled successfully: {final_output}")
    return final_output
