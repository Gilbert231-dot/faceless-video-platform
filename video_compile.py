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
    """Compile video with intro overlay, fade transition, NO captions."""
    print("🎬 Starting video compilation (WITH INTRO, NO CAPTIONS)...")

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

    # 4. Crop to 9:16
    print("⚡ Step 2: Cropping to 9:16...")
    video_cropped = os.path.join(OUTPUT_DIR, f"video_cropped_{int(time.time())}.mp4")
    
    cmd_crop = [
        'ffmpeg', '-y',
        '-i', gameplay_segment,
        '-vf', 'crop=ih*9/16:ih:(iw-ih*9/16)/2:0',
        '-c:v', 'libx264',
        '-preset', 'veryfast',
        '-crf', '18',
        '-an',
        video_cropped
    ]
    
    try:
        subprocess.run(cmd_crop, check=True, capture_output=True, timeout=900)
        print(f"   ✅ Cropped to 9:16.")
        os.unlink(gameplay_segment)
        gameplay_segment = video_cropped
    except Exception as e:
        raise Exception(f"Video cropping failed: {e}")

    # 5. Build final video with intro
    print("⚡ Step 3: Building final video...")
    
    # Step 3a: Create intro video from title card
    intro_video = None
    if intro_frame and os.path.exists(intro_frame):
        print(f"   🖼️ Creating intro video from: {intro_frame}")
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
        print("   ℹ️ No intro frame provided.")

    # Step 3b: Combine intro + gameplay (with fade if intro exists)
    temp_no_audio = os.path.join(OUTPUT_DIR, f"temp_no_audio_{int(time.time())}.mp4")
    
    if intro_video and os.path.exists(intro_video):
        # Use crossfade for smooth transition
        fade_offset = intro_duration - 0.3
        if fade_offset < 0:
            fade_offset = 0
        
        cmd_concat = [
            'ffmpeg', '-y',
            '-i', intro_video,
            '-i', gameplay_segment,
            '-filter_complex', f'xfade=transition=fade:duration=0.3:offset={fade_offset}',
            '-c:v', 'libx264',
            '-preset', 'veryfast',
            '-crf', '18',
            '-an',
            temp_no_audio
        ]
        try:
            subprocess.run(cmd_concat, check=True, capture_output=True, timeout=120)
            print(f"   ✅ Video concatenated with fade transition.")
            # Clean up intro video
            os.unlink(intro_video)
        except Exception as e:
            print(f"   ⚠️ Fade failed: {e}. Using simple concat.")
            # Fallback: simple concat
            concat_file = os.path.join(OUTPUT_DIR, f"concat_list_{int(time.time())}.txt")
            with open(concat_file, 'w') as f:
                f.write(f"file '{intro_video}'\n")
                f.write(f"file '{gameplay_segment}'\n")
            cmd_concat_simple = [
                'ffmpeg', '-y',
                '-f', 'concat',
                '-safe', '0',
                '-i', concat_file,
                '-c:v', 'copy',
                '-an',
                temp_no_audio
            ]
            try:
                subprocess.run(cmd_concat_simple, check=True, capture_output=True, timeout=120)
                print(f"   ✅ Video concatenated (simple concat).")
                os.unlink(concat_file)
                os.unlink(intro_video)
            except Exception as e2:
                print(f"   ⚠️ Concat failed: {e2}. Using gameplay only.")
                import shutil
                shutil.copy(gameplay_segment, temp_no_audio)
    else:
        # No intro: just copy gameplay
        import shutil
        shutil.copy(gameplay_segment, temp_no_audio)

    # Step 3c: Add audio AND scale to 1080x1920
    print("⚡ Step 4: Adding audio and scaling to 1080x1920...")
    final_output = os.path.join(OUTPUT_DIR, f"output_{int(time.time())}.mp4")
    
    cmd_audio = [
        'ffmpeg', '-y',
        '-i', temp_no_audio,
        '-i', audio_path,
        '-vf', 'scale=1080:1920',
        '-c:v', 'libx264',
        '-preset', 'veryfast',
        '-crf', '18',
        '-c:a', 'aac',
        '-b:a', '192k',
        '-shortest',
        '-movflags', '+faststart',
        final_output
    ]
    try:
        subprocess.run(cmd_audio, check=True, capture_output=True, timeout=300)
        print(f"   ✅ Audio added and scaled to 1080x1920.")
        os.unlink(temp_no_audio)
    except Exception as e:
        print(f"   ⚠️ Audio addition failed: {e}")
        os.rename(temp_no_audio, final_output)

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
