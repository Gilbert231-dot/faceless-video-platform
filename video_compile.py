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
    """Compile video with intro frame overlay."""
    print("🎬 Starting video compilation (with intro overlay)...")

    # Handle both single path and list
    if isinstance(video_paths, str):
        video_paths = [video_paths]
    if not video_paths:
        raise Exception("No video paths provided")

    # 1. Get audio duration
    audio_duration = get_duration(audio_path)
    print(f"   🎙️ Audio duration: {audio_duration:.2f}s")

    # 2. Determine intro duration (3-5 seconds based on title length)
    intro_duration = 3.5
    if title:
        word_count = len(title.split())
        intro_duration = max(2.5, min(5.0, word_count * 0.3))
    intro_duration = min(intro_duration, audio_duration)
    print(f"   🖼️ Intro duration: {intro_duration:.2f}s")

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
        subprocess.run(cmd_crop, check=True, capture_output=True, timeout=900)
        print(f"   ✅ Cropped and resized to 1080p.")
        os.unlink(gameplay_segment)
        gameplay_segment = video_cropped
    except Exception as e:
        raise Exception(f"Video cropping failed: {e}")

    # 5. Build FFmpeg command with or without intro frame
    print("⚡ Step 3: Combining video + audio with intro overlay...")
    final_output = os.path.join(OUTPUT_DIR, f"output_{int(time.time())}.mp4")

    if intro_frame and os.path.exists(intro_frame):
        # --- WITH INTRO FRAME (Title Card) ---
        print(f"   🖼️ Overlaying intro frame: {intro_frame}")
        
        cmd_combine = [
            'ffmpeg', '-y',
            '-i', gameplay_segment,
            '-i', audio_path,
            '-i', intro_frame,
            '-filter_complex',
            f'[0:v]trim=0:{audio_duration},setpts=PTS-STARTPTS[video];'
            f'[2:v]scale=1080:1920,loop=-1:size=1,trim=0:{intro_duration},setpts=PTS-STARTPTS[intro];'
            f'[intro][video]overlay=0:0:enable=\'between(t,0,{intro_duration})\'[outv]',
            '-map', '[outv]',
            '-map', '1:a',
            '-c:v', 'libx264',
            '-preset', 'medium',
            '-crf', '18',
            '-c:a', 'aac',
            '-b:a', '192k',
            '-shortest',
            '-movflags', '+faststart',
            final_output
        ]
    else:
        # --- WITHOUT INTRO FRAME ---
        print("   ℹ️ No intro frame provided. Using basic combine.")
        cmd_combine = [
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
        subprocess.run(cmd_combine, check=True, capture_output=True, timeout=300)
        print("   ✅ Video combined.")
    except Exception as e:
        raise Exception(f"Video combine failed: {e}")

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

    print(f"✅ Video compiled successfully: {final_output}")
    return final_output
