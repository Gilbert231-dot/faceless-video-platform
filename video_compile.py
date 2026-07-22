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

def compile_video(video_paths, audio_path, script, subtitle_path=None,
                  intro_frame=None, title=None, part_label=None):
    print("🎬 Starting FFmpeg compilation...")

    # 1. Get audio duration
    audio_duration = float(subprocess.check_output(
        ['ffprobe', '-i', audio_path, '-show_entries', 'format=duration',
         '-v', 'quiet', '-of', 'csv=%s' % ("p=0")]
    ).decode().strip())

    intro_duration = 0

    # 2. Generate SRT – ALWAYS use fallback with actual audio duration
    srt_path = None
    try:
        srt_path = tempfile.NamedTemporaryFile(delete=False, suffix='.srt').name
        generate_fallback_srt(script, intro_duration, srt_path, audio_duration)
        print(f"   ✅ SRT subtitles created (word-by-word, synced to audio): {srt_path}")
    except Exception as e:
        print(f"   ⚠️ Fallback SRT generation failed: {e}")
        srt_path = None

    # If fallback failed, try Whisper (but we want fallback's word-by-word)
    if not srt_path and subtitle_path and os.path.exists(subtitle_path):
        try:
            srt_path = tempfile.NamedTemporaryFile(delete=False, suffix='.srt').name
            whisper_json_to_srt(subtitle_path, intro_duration, srt_path)
            print(f"   ✅ SRT subtitles created from Whisper: {srt_path}")
        except Exception as e:
            print(f"   ⚠️ Whisper SRT generation failed: {e}")
            srt_path = None

    # 3. Build input list
    inputs = []
    for p in video_paths:
        inputs.extend(['-i', p])
    audio_index = len(video_paths)
    inputs.extend(['-i', audio_path])

    # --- Background music (optional) ---
    music_path = download_background_music()
    if music_path and os.path.exists(music_path):
        inputs.extend(['-i', music_path])
        music_index = len(video_paths) + 1
        print(f"   ✅ Background music added: {music_path}")
    else:
        music_index = None
        print("   ⚠️ No background music available. Proceeding with voiceover only.")

    # 4. Build filter graph
    filters = []
    for i in range(len(video_paths)):
        filters.append(
            f'[{i}:v]crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920,'
            f'setsar=1[v{i}]'
        )
    concat_inputs = ''.join([f'[v{i}]' for i in range(len(video_paths))])
    filters.append(
        f'{concat_inputs}concat=n={len(video_paths)}:v=1:a=0[bgv]'
    )
    filters.append(f'[bgv]copy[outv0]')
    outv_stream = '[outv0]'
    
    # 5. Captions: use subtitles filter with style
    if srt_path and os.path.exists(srt_path):
        # Style: bold, centered, large font, black outline
        style = 'Fontsize=50, Bold=1, Alignment=10, OutlineColour=&H80000000, Outline=3'
        filters.append(
            f'{outv_stream}trim=duration={audio_duration},'
            f'subtitles={srt_path}:force_style=\'{style}\','
            f'format=yuv420p[outv]'
        )
    else:
        filters.append(
            f'{outv_stream}trim=duration={audio_duration},'
            f'format=yuv420p[outv]'
        )
    # 6. Audio: simple mixing using concat (more reliable)
    if music_index is not None:
        filters.append(
            f'[{audio_index}:a]atrim=duration={audio_duration},'
            f'atempo=1.3,volume=2[voice]'
        )
        filters.append(
            f'[{music_index}:a]atrim=duration={audio_duration},'
            f'volume=0.15[music]'
        )
        filters.append(
            f'[voice][music]concat=n=2:v=0:a=1[outa]'
        )
    else:
        filters.append(
            f'[{audio_index}:a]atrim=duration={audio_duration},'
            f'atempo=1.3,volume=2[outa]'
        )
        
    filter_graph = ';'.join(filters)
    print(f"   🧪 Filter graph: {filter_graph}")

    # 7. Output path
    output_path = os.path.join(OUTPUT_DIR, f"output_{int(time.time())}.mp4")
    cmd = (
    ['ffmpeg', '-y'] + inputs +
    ['-filter_complex', filter_graph,
     '-map', '[outv]', '-map', '[outa]',
     '-c:v', 'libx264',
     '-preset', 'slow',          # <-- Better quality
     '-crf', '18',               # <-- Near-lossless
     '-c:a', 'aac',
     '-b:a', '192k',             # <-- Clearer audio
     '-shortest',
     '-movflags', '+faststart',
     output_path]
    )
    print("⚡ Running FFmpeg...")
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

    print("   ✅ FFmpeg completed successfully.")

    # Clean up
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

    print(f"✅ Video compiled successfully: {output_path}")
    return output_path

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
