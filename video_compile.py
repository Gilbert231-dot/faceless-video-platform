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
    """Compile video with intro overlay, captions, and correct aspect ratio."""
    print("🎬 Starting video compilation (FIXED)...")

    # Handle both single path and list
    if isinstance(video_paths, str):
        video_paths = [video_paths]
    if not video_paths:
        raise Exception("No video paths provided")

    # 1. Get audio duration
    audio_duration = get_duration(audio_path)
    print(f"   🎙️ Audio duration: {audio_duration:.2f}s")

    # 2. Determine intro duration
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

    # 4. Crop and resize to 1080x1920 (FIXED ASPECT RATIO)
    print("⚡ Step 2: Cropping and resizing to 1080x1920...")
    video_cropped = os.path.join(OUTPUT_DIR, f"video_cropped_{int(time.time())}.mp4")
    
    # FIX: Force 9:16 aspect ratio
    cmd_crop = [
        'ffmpeg', '-y',
        '-i', gameplay_segment,
        '-vf', 'scale=1080:1920:force_original_aspect_ratio=decrease,crop=1080:1920',
        '-c:v', 'libx264',
        '-preset', 'veryfast',
        '-crf', '18',
        '-an',
        video_cropped
    ]
    
    try:
        subprocess.run(cmd_crop, check=True, capture_output=True, timeout=900)
        print(f"   ✅ Cropped and resized to 1080x1920.")
        os.unlink(gameplay_segment)
        gameplay_segment = video_cropped
    except Exception as e:
        raise Exception(f"Video cropping failed: {e}")

    # 5. Build final video with intro and captions
    print("⚡ Step 3: Building final video...")
    final_output = os.path.join(OUTPUT_DIR, f"output_{int(time.time())}.mp4")

    # Step 3a: Create intro video from title card
    if intro_frame and os.path.exists(intro_frame):
        print(f"   🖼️ Adding intro frame: {intro_frame}")
        intro_video = os.path.join(OUTPUT_DIR, f"intro_video_{int(time.time())}.mp4")
        cmd_intro = [
            'ffmpeg', '-y',
            '-loop', '1',
            '-i', intro_frame,
            '-t', str(intro_duration),
            '-c:v', 'libx264',
            '-preset', 'veryfast',
            '-crf', '18',
            '-pix_fmt', 'yuv420p',
            intro_video
        ]
        try:
            subprocess.run(cmd_intro, check=True, capture_output=True, timeout=60)
            print(f"   ✅ Intro video created.")
        except Exception as e:
            print(f"   ⚠️ Intro creation failed: {e}")
            intro_video = None
    else:
        intro_video = None
        print("   ℹ️ No intro frame provided.")

    # Step 3b: Combine intro + gameplay
    temp_no_audio = os.path.join(OUTPUT_DIR, f"temp_no_audio_{int(time.time())}.mp4")
    
    if intro_video and os.path.exists(intro_video):
        concat_file = os.path.join(OUTPUT_DIR, f"concat_list_{int(time.time())}.txt")
        with open(concat_file, 'w') as f:
            f.write(f"file '{intro_video}'\n")
            f.write(f"file '{gameplay_segment}'\n")
        
        cmd_concat = [
            'ffmpeg', '-y',
            '-f', 'concat',
            '-safe', '0',
            '-i', concat_file,
            '-c:v', 'copy',
            '-an',
            temp_no_audio
        ]
        try:
            subprocess.run(cmd_concat, check=True, capture_output=True, timeout=120)
            print(f"   ✅ Video concatenated with intro.")
            os.unlink(concat_file)
            os.unlink(intro_video)
        except Exception as e:
            print(f"   ⚠️ Concatenation failed: {e}. Using gameplay only.")
            import shutil
            shutil.copy(gameplay_segment, temp_no_audio)
    else:
        import shutil
        shutil.copy(gameplay_segment, temp_no_audio)

    # Step 3c: Add audio
    temp_with_audio = os.path.join(OUTPUT_DIR, f"temp_audio_{int(time.time())}.mp4")
    cmd_audio = [
        'ffmpeg', '-y',
        '-i', temp_no_audio,
        '-i', audio_path,
        '-c:v', 'copy',
        '-c:a', 'aac',
        '-b:a', '192k',
        '-shortest',
        '-movflags', '+faststart',
        temp_with_audio
    ]
    try:
        subprocess.run(cmd_audio, check=True, capture_output=True, timeout=120)
        print(f"   ✅ Audio added.")
        os.unlink(temp_no_audio)
    except Exception as e:
        print(f"   ⚠️ Audio addition failed: {e}")
        os.rename(temp_no_audio, temp_with_audio)

    # Step 3d: Burn captions
    if subtitle_path and os.path.exists(subtitle_path):
        print("⚡ Step 4: Burning captions...")
        final_output = os.path.join(OUTPUT_DIR, f"output_{int(time.time())}.mp4")
        cmd_burn = [
            'ffmpeg', '-y',
            '-i', temp_with_audio,
            '-vf', f"subtitles={subtitle_path}:force_style='Fontsize=48, Bold=1, Alignment=10, OutlineColour=&H80000000'",
            '-c:a', 'copy',
            final_output
        ]
        try:
            subprocess.run(cmd_burn, check=True, capture_output=True, timeout=180)
            print(f"   ✅ Captions burned.")
            os.unlink(temp_with_audio)
        except Exception as e:
            print(f"   ⚠️ Caption burn failed: {e}. Using video without captions.")
            os.rename(temp_with_audio, final_output)
    else:
        final_output = temp_with_audio
        print("   ℹ️ No captions to burn.")

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
