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
    """Compile video with background music and volume boost."""
    print("🎬 Starting video compilation (with music + volume boost)...")

    # 1. Get audio duration
    audio_duration = float(subprocess.check_output(
        ['ffprobe', '-i', audio_path, '-show_entries', 'format=duration',
         '-v', 'quiet', '-of', 'csv=%s' % ("p=0")]
    ).decode().strip())

    # 2. Generate SRT
    srt_path = None
    if subtitle_path and os.path.exists(subtitle_path):
        srt_path = subtitle_path
        print(f"   ✅ Using provided SRT: {srt_path}")
    else:
        try:
            srt_path = tempfile.NamedTemporaryFile(delete=False, suffix='.srt').name
            generate_fallback_srt(script, 0, srt_path, audio_duration)
            print(f"   ✅ SRT subtitles created: {srt_path}")
        except Exception as e:
            print(f"   ⚠️ SRT generation failed: {e}")
            srt_path = None

    # 3. Concatenate video clips
    print("⚡ Step 1: Concatenating video clips...")
    video_concat_file = os.path.join(OUTPUT_DIR, f"video_concat_{int(time.time())}.txt")
    with open(video_concat_file, 'w') as f:
        for path in video_paths:
            f.write(f"file '{path}'\n")
    
    video_concat_output = os.path.join(OUTPUT_DIR, f"video_{int(time.time())}.mp4")
    cmd_concat = [
        'ffmpeg', '-y',
        '-f', 'concat',
        '-safe', '0',
        '-i', video_concat_file,
        '-c:v', 'copy',
        video_concat_output
    ]
    
    try:
        subprocess.run(cmd_concat, check=True, capture_output=True, timeout=120)
        print("   ✅ Video concatenated.")
    except Exception as e:
        raise Exception(f"Video concat failed: {e}")

    # 4. Download or locate background music
    music_path = download_background_music()
    
    # 5. Audio mixing (voice + music)
    print("⚡ Step 2: Mixing voiceover + music...")
    audio_mixed_output = os.path.join(OUTPUT_DIR, f"audio_mixed_{int(time.time())}.mp3")
    
    if music_path and os.path.exists(music_path):
        # Mix voice (volume=2) + music (volume=0.15)
        cmd_mix = [
            'ffmpeg', '-y',
            '-i', audio_path,
            '-i', music_path,
            '-filter_complex', 
            f'[0:a]volume=2[voice];[1:a]volume=0.15,atrim=duration={audio_duration}[music];[voice][music]amix=inputs=2:duration=longest',
            '-c:a', 'aac',
            '-b:a', '192k',
            audio_mixed_output
        ]
        try:
            subprocess.run(cmd_mix, check=True, capture_output=True, timeout=120)
            print("   ✅ Audio mixed (voice + music).")
            mixed_audio = audio_mixed_output
        except Exception as e:
            print(f"   ⚠️ Audio mixing failed: {e}. Using voice only with volume boost.")
            # Fallback: just boost voice volume
            cmd_voice = [
                'ffmpeg', '-y',
                '-i', audio_path,
                '-af', 'volume=2',
                '-c:a', 'aac',
                '-b:a', '192k',
                audio_mixed_output
            ]
            subprocess.run(cmd_voice, check=True, capture_output=True, timeout=60)
            mixed_audio = audio_mixed_output
    else:
        print("   ⚠️ No music available. Using voice only with volume boost.")
        cmd_voice = [
            'ffmpeg', '-y',
            '-i', audio_path,
            '-af', 'volume=2',
            '-c:a', 'aac',
            '-b:a', '192k',
            audio_mixed_output
        ]
        subprocess.run(cmd_voice, check=True, capture_output=True, timeout=60)
        mixed_audio = audio_mixed_output

    # 6. Combine video + mixed audio
    print("⚡ Step 3: Combining video + audio...")
    final_output = os.path.join(OUTPUT_DIR, f"output_{int(time.time())}.mp4")
    
    cmd_combine = [
        'ffmpeg', '-y',
        '-i', video_concat_output,
        '-i', mixed_audio,
        '-vf', 'crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920',
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
        subprocess.run(cmd_combine, check=True, capture_output=True, timeout=600)
        print("   ✅ Video combined.")
    except Exception as e:
        raise Exception(f"Video combine failed: {e}")

    # 7. Burn captions
    if srt_path and os.path.exists(srt_path):
        print("⚡ Step 4: Burning captions...")
        temp_captioned = os.path.join(OUTPUT_DIR, f"temp_captioned_{int(time.time())}.mp4")
        cmd_burn = [
            'ffmpeg', '-y',
            '-i', final_output,
            '-vf', f"subtitles={srt_path}:force_style='Fontsize=45, Bold=1, Alignment=10, OutlineColour=&H80000000'",
            '-c:a', 'copy',
            temp_captioned
        ]
        try:
            subprocess.run(cmd_burn, check=True, capture_output=True, timeout=180)
            os.replace(temp_captioned, final_output)
            print("   ✅ Captions burned.")
        except Exception as e:
            print(f"   ⚠️ Caption burn failed: {e}.")
            if os.path.exists(temp_captioned):
                os.unlink(temp_captioned)

    # 8. Clean up
    for path in [video_concat_output, video_concat_file, mixed_audio]:
        try:
            os.unlink(path)
        except:
            pass
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
    if srt_path and srt_path != subtitle_path:
        try:
            os.unlink(srt_path)
        except:
            pass

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
