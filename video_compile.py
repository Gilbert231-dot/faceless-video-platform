import os
import time
import tempfile
import json
import subprocess
from config import PROJECT_ROOT, FAST_MODE, OUTPUT_DIR
from captions import whisper_json_to_srt, generate_fallback_srt

def create_reddit_frame(title, video_size, part_label=None):
    """Stub: No longer used. Returns None to skip intro frame."""
    return None

def compile_video(video_paths, audio_path, script, subtitle_path=None, intro_frame=None, title=None, part_label=None):
    print("🎬 VERSION 3 - UPDATED COMPILE_VIDEO WITH STYLING AND SPEED")
    """Compile video using FFmpeg with:
    - Normal video speed (no speed-up)
    - Narrator speed kept (already natural)
    - Captions: smaller, bold, centered
    - Narrator volume: boosted to TikTok standard
    - Background music: action/cinematic track at 15% volume
    """
    print("🎬 Starting FFmpeg compilation...")

    # 1. Get audio duration
    audio_duration = float(subprocess.check_output(
        ['ffprobe', '-i', audio_path, '-show_entries', 'format=duration',
         '-v', 'quiet', '-of', 'csv=%s' % ("p=0")]
    ).decode().strip())

    # 2. Intro duration (set to 0 since we removed the intro frame)
    intro_duration = 0

    # 3. Generate SRT from Whisper JSON OR fallback
    srt_path = None
    if subtitle_path and os.path.exists(subtitle_path):
        try:
            srt_path = tempfile.NamedTemporaryFile(delete=False, suffix='.srt').name
            whisper_json_to_srt(subtitle_path, intro_duration, srt_path)
            print(f"   ✅ SRT subtitles created from Whisper: {srt_path}")
        except Exception as e:
            print(f"   ⚠️ Whisper SRT generation failed: {e}")
            srt_path = None

    with open(srt_path, 'r') as f:
        print(f"   📝 SRT preview: {f.read()[:500]}")
    
    # If Whisper failed, generate fallback SRT from script
    if not srt_path and script:
        try:
            srt_path = tempfile.NamedTemporaryFile(delete=False, suffix='.srt').name
            generate_fallback_srt(script, intro_duration, srt_path)
            print(f"   ✅ SRT subtitles created from fallback: {srt_path}")
        except Exception as e:
            print(f"   ⚠️ Fallback SRT generation failed: {e}")
            srt_path = None

    # 4. Build input list
    inputs = []
    for p in video_paths:
        inputs.extend(['-i', p])
    audio_index = len(video_paths)
    inputs.extend(['-i', audio_path])

    # --- 4.5 DOWNLOAD BACKGROUND MUSIC ---
    music_path = download_background_music()
    if music_path and os.path.exists(music_path):
        inputs.extend(['-i', music_path])
        music_index = len(video_paths) + 1  # after all video inputs and audio
        print(f"   ✅ Background music added: {music_path}")
    else:
        music_index = None
        print("   ⚠️ No background music available. Proceeding with voiceover only.")

    # 5. Build filter graph
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
    
    # 6. Captions: simple, reliable styling
    if srt_path and os.path.exists(srt_path):
        style = 'Fontsize=22, Alignment=10, OutlineColour=&H80000000'
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

    # 7. Audio: boost narrator volume (2x = ~6dB boost) + mix with background music
    if music_index is not None:
        filters.append(
            f'[{audio_index}:a]atrim=duration={audio_duration},'
            f'atempo=1.3,volume=2[voice]'
            )
        # Music: low volume (15%), trimmed to audio duration
        filters.append(
            f'[{music_index}:a]atrim=duration={audio_duration},'
            f'volume=0.15[music]'
        )
        # Mix voice + music
        filters.append(
            f'[voice][music]amix=inputs=2:duration=longest[outa]'
        )
    else:
        # Voice only (no music)
        filters.append(
            f'[{audio_index}:a]atrim=duration={audio_duration},'
            f'volume=2[outa]'
        )

    filter_graph = ';'.join(filters)

    # 8. Output path
    output_path = os.path.join(OUTPUT_DIR, f"output_{int(time.time())}.mp4")
    cmd = (
        ['ffmpeg', '-y'] + inputs +
        ['-filter_complex', filter_graph,
        '-map', '[outv]', '-map', '[outa]',
        '-c:v', 'libx264', '-preset', 'veryfast',
        '-crf', '23',
        '-c:a', 'aac', '-b:a', '128k',
        '-shortest',
        '-movflags', '+faststart',
        output_path]
    )

    print("⚡ Running FFmpeg (this may take a few minutes)...")
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

    # Clean up temp files
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
    """Download a royalty-free action/cinematic background music track."""
    import requests
    import tempfile
    
    # Pixabay royalty-free music (action/cinematic)
    # You can replace this URL with any royalty-free track
    music_url = "https://cdn.pixabay.com/download/audio/2022/03/10/audio_c8c8f7c7a6.mp3"
    
    try:
        print("   🎵 Downloading background music...")
        response = requests.get(music_url, timeout=30)
        if response.status_code == 200:
            temp_music = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3')
            temp_music.write(response.content)
            temp_music.close()
            print(f"   ✅ Background music downloaded: {temp_music.name}")
            return temp_music.name
        else:
            print(f"   ⚠️ Music download failed (HTTP {response.status_code})")
            return None
    except Exception as e:
        print(f"   ⚠️ Music download error: {e}")
        return None
