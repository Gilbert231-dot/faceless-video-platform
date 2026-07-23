import os
import time
import tempfile
import json
import subprocess
import re
from config import PROJECT_ROOT, FAST_MODE, OUTPUT_DIR
from captions import whisper_json_to_srt, generate_fallback_srt

def create_reddit_frame(title, video_size, part_label=None):
    return None

def convert_srt_to_ass(srt_path, ass_path, audio_duration):
    """Convert SRT to ASS format with proper styling."""
    with open(srt_path, 'r') as f:
        content = f.read()
    
    # ASS header
    ass_content = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
Timer: 100.0000

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,50,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,3,0,10,10,10,10,0

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    
    # Parse SRT and convert to ASS events
    blocks = content.strip().split('\n\n')
    for block in blocks:
        lines = block.split('\n')
        if len(lines) >= 3:
            # Timecode line
            timecode = lines[1]
            # Text
            text = ' '.join(lines[2:])
            # Convert time format
            start_str, end_str = timecode.split(' --> ')
            start = start_str.replace(',', '.')
            end = end_str.replace(',', '.')
            # Clean text
            text = text.replace('\n', ' ').strip()
            ass_content += f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}\n"
    
    with open(ass_path, 'w') as f:
        f.write(ass_content)
    
    return ass_path

def compile_video(video_paths, audio_path, script, subtitle_path=None,
                  intro_frame=None, title=None, part_label=None):
    """Compile video using FFmpeg + caption burn in second pass."""
    print("🎬 Starting video compilation...")

    # 1. Get audio duration
    audio_duration = float(subprocess.check_output(
        ['ffprobe', '-i', audio_path, '-show_entries', 'format=duration',
         '-v', 'quiet', '-of', 'csv=%s' % ("p=0")]
    ).decode().strip())

    # 2. Generate SRT subtitles (fallback from script)
    srt_path = None
    try:
        srt_path = tempfile.NamedTemporaryFile(delete=False, suffix='.srt').name
        generate_fallback_srt(script, 0, srt_path, audio_duration)
        print(f"   ✅ SRT subtitles created: {srt_path}")
    except Exception as e:
        print(f"   ⚠️ SRT generation failed: {e}")
        srt_path = None

    # 3. Build input list and filter graph (NO CAPTIONS)
    inputs = []
    filter_parts = []
    
    for i, path in enumerate(video_paths):
        inputs.extend(['-i', path])
        filter_parts.append(
            f'[{i}:v]crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920,setsar=1[v{i}]'
        )
    
    concat_inputs = ''.join([f'[v{i}]' for i in range(len(video_paths))])
    filter_parts.append(
        f'{concat_inputs}concat=n={len(video_paths)}:v=1:a=0[outv]'
    )
    
    # Audio
    inputs.extend(['-i', audio_path])
    audio_index = len(video_paths)
    
    # Background music
    music_path = download_background_music()
    if music_path and os.path.exists(music_path):
        inputs.extend(['-i', music_path])
        music_index = len(video_paths) + 1
        print(f"   ✅ Background music added: {music_path}")
    else:
        music_index = None
        print("   ⚠️ No background music available.")

    filters = []
    filters.extend(filter_parts)
    filters.append(f'[outv]trim=duration={audio_duration},format=yuv420p[outv]')

    if music_index is not None:
        filters.append(
            f'[{audio_index}:a]atrim=duration={audio_duration},volume=2[voice]'
        )
        filters.append(
            f'[{music_index}:a]atrim=duration={audio_duration},volume=0.15[music]'
        )
        filters.append(
            f'[voice][music]amix=inputs=2:duration=longest[outa]'
        )
    else:
        filters.append(
            f'[{audio_index}:a]atrim=duration={audio_duration},volume=2[outa]'
        )

    filter_graph = ';'.join(filters)

    # 4. Render raw video (NO CAPTIONS)
    temp_output = os.path.join(OUTPUT_DIR, f"temp_{int(time.time())}.mp4")
    cmd = (
        ['ffmpeg', '-y'] + inputs +
        ['-filter_complex', filter_graph,
         '-map', '[outv]', '-map', '[outa]',
         '-c:v', 'libx264', '-preset', 'slow',
         '-crf', '18',
         '-c:a', 'aac', '-b:a', '192k',
         '-shortest',
         '-movflags', '+faststart',
         temp_output]
    )

    print("⚡ Running FFmpeg (video + audio, no captions)...")
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        bufsize=1
    )

    for line in process.stdout:
        if 'frame=' in line or 'size=' in line:
            print(f"   {line.strip()}")
    process.wait()

    if process.returncode != 0:
        raise Exception(f"FFmpeg failed with exit code {process.returncode}")

    print("   ✅ FFmpeg completed.")

    # 5. Burn captions (Plan A: Simple FFmpeg subtitle burn)
    final_output = os.path.join(OUTPUT_DIR, f"output_{int(time.time())}.mp4")
    
    if srt_path and os.path.exists(srt_path):
        print("⚡ Burning captions with FFmpeg (Plan A)...")
        cmd_burn = [
            'ffmpeg', '-y',
            '-i', temp_output,
            '-vf', f"subtitles={srt_path}:force_style='Fontsize=45, Bold=1, Alignment=10, OutlineColour=&H80000000'",
            '-c:a', 'copy',
            '-preset', 'slow',
            '-crf', '18',
            final_output
        ]
        
        process_burn = subprocess.Popen(
            cmd_burn,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1
        )

        for line in process_burn.stdout:
            if 'frame=' in line or 'size=' in line:
                print(f"   {line.strip()}")
        process_burn.wait()

        if process_burn.returncode == 0:
            print("   ✅ Captions burned successfully (Plan A).")
            os.unlink(temp_output)
        else:
            print(f"   ⚠️ Plan A failed (exit code {process_burn.returncode}). Trying Plan B...")
            # Fallback to Plan B: VideoCaptioner CLI
            os.rename(temp_output, final_output)
            try:
                print("⚡ Burning captions with VideoCaptioner CLI (Plan B)...")
                style_file = os.path.join(PROJECT_ROOT, "my_style.ass")
                cmd_vc = [
                    'videocaptioner', 'process', final_output,
                    '--style-ass', style_file,
                    '--quality', 'ultra',
                    '--target-language', 'en',
                    '--output', final_output
                ]
                subprocess.run(cmd_vc, check=True, capture_output=True)
                print("   ✅ Captions burned successfully (Plan B).")
            except Exception as e:
                print(f"   ⚠️ Plan B failed: {e}. Proceeding without captions.")
    else:
        os.rename(temp_output, final_output)
        print("   ℹ️ No captions available. Using raw video.")

    # 6. Clean up
    for path in video_paths + [audio_path]:
        try:
            os.unlink(path)
        except:
            pass
    if music_path and os.path.exists(music_path):
        try:
            os.unlink(music_path)
        except:
            pass
    if srt_path and os.path.exists(srt_path):
        os.unlink(srt_path)

    print(f"✅ Video compiled successfully: {final_output}")
    return final_output


def download_background_music():
    """Use a local music file from the repository."""
    from config import PROJECT_ROOT
    local_music_path = os.path.join(PROJECT_ROOT, "assets/music/my_action_track.mp3")
    if os.path.exists(local_music_path):
        print(f"   ✅ Using local background music: {local_music_path}")
        return local_music_path
    else:
        print("   ⚠️ Local music not found. Proceeding without background music.")
        return None
