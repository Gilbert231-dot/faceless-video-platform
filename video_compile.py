#!/usr/bin/env python3
"""
video_compile.py - Simplified FFmpeg pipeline for faceless TikTok videos
"""

import subprocess
import os
import shutil
import tempfile
from pathlib import Path

# ----------------------------------------------------------------------
# Helper: Check if an audio file is valid and has content
# ----------------------------------------------------------------------
def is_valid_audio(filepath: str) -> bool:
    """Return True if file exists, has size > 0, and ffprobe can read it."""
    if not os.path.exists(filepath):
        return False
    if os.path.getsize(filepath) == 0:
        return False
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        return True
    cmd = [
        ffprobe, '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        filepath
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            float(result.stdout.strip())
            return True
    except Exception:
        pass
    return False

# ----------------------------------------------------------------------
# Helper: Decode MP3 to WAV (with validation)
# ----------------------------------------------------------------------
def prepare_audio(mp3_path: str, wav_path: str, sample_rate: int = 48000) -> str:
    """Convert MP3 to WAV after validating the input file."""
    mp3_path = str(mp3_path)
    wav_path = str(wav_path)

    if not is_valid_audio(mp3_path):
        raise RuntimeError(f"Input audio file is invalid or empty: {mp3_path}")

    # Try decoders in order
    ffmpeg = shutil.which('ffmpeg')
    if ffmpeg:
        cmd = [
            ffmpeg, '-y',
            '-err_detect', 'ignore_err',
            '-i', mp3_path,
            '-acodec', 'pcm_s16le',
            '-ar', str(sample_rate),
            '-ac', '2',
            wav_path
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            return wav_path
        except subprocess.CalledProcessError:
            pass

    # Fallback: use the MP3 directly if decoding fails (some environments)
    # We'll just copy it and hope ffmpeg can handle it later
    print(f"⚠️ FFmpeg decode failed, using original MP3: {mp3_path}")
    shutil.copy2(mp3_path, wav_path)
    return wav_path

# ----------------------------------------------------------------------
# Helper: Get duration (seconds)
# ----------------------------------------------------------------------
def get_duration(media_path: str) -> float:
    cmd = [
        'ffprobe', '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        media_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())

# ----------------------------------------------------------------------
# Main compilation function (SIMPLIFIED)
# ----------------------------------------------------------------------
def compile_video(
    video_path: str,
    audio_mp3: str,
    output_path: str,
    subtitle_ass: str,
    background_music: str = None,
    voice_volume: float = 2.0,
    bg_volume: float = 0.3,
    target_width: int = 1080,
    target_height: int = 1920,
    crf: int = 18,
    temp_dir: str = None,
) -> str:
    """
    Compose final video. Uses -c:a copy to avoid audio re-encoding issues.
    """
    # Create temp dir
    if temp_dir is None:
        temp_dir = tempfile.mkdtemp(prefix='video_compile_')
    else:
        os.makedirs(temp_dir, exist_ok=True)

    # 1. Get audio duration
    audio_duration = get_duration(audio_mp3)
    print(f"🎙️ Audio duration: {audio_duration:.2f}s")

    # 2. Extract required segment from video (if needed)
    video_segment = os.path.join(temp_dir, 'video_segment.mp4')
    cmd_extract = [
        'ffmpeg', '-y',
        '-i', video_path,
        '-t', str(audio_duration),
        '-c:v', 'copy',
        '-an',
        video_segment
    ]
    subprocess.run(cmd_extract, check=True, capture_output=True)

    # 3. Prepare voiceover audio (decode to WAV or use as-is)
    voice_wav = os.path.join(temp_dir, 'voice.wav')
    prepare_audio(audio_mp3, voice_wav)

    # 4. Combine video + audio
    cmd_combine = [
        'ffmpeg', '-y',
        '-i', video_segment,
        '-i', voice_wav,
        '-vf', f'crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale={target_width}:{target_height}',
        '-c:v', 'libx264',
        '-preset', 'medium',
        '-crf', str(crf),
        '-c:a', 'copy',
        '-shortest',
        output_path
    ]
    subprocess.run(cmd_combine, check=True, capture_output=True)

    # 5. Burn subtitles (using ASS file)
    if subtitle_ass and os.path.exists(subtitle_ass):
        print("⚡ Burning subtitles...")
        temp_captioned = os.path.join(temp_dir, 'captioned.mp4')
        cmd_burn = [
            'ffmpeg', '-y',
            '-i', output_path,
            '-vf', f'subtitles={subtitle_ass}:force_style="Fontsize=50,Bold=1,Alignment=10,OutlineColour=&H80000000"',
            '-c:a', 'copy',
            temp_captioned
        ]
        try:
            subprocess.run(cmd_burn, check=True, capture_output=True)
            shutil.move(temp_captioned, output_path)
            print("✅ Subtitles burned.")
        except Exception as e:
            print(f"⚠️ Subtitle burn failed: {e}")

    # Clean up
    shutil.rmtree(temp_dir, ignore_errors=True)

    print(f"✅ Video compiled: {output_path}")
    return output_path

# ----------------------------------------------------------------------
# Example usage (for direct testing)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 5:
        print("Usage: python video_compile.py <video.mp4> <audio.mp3> <output.mp4> <subtitles.ass>")
        sys.exit(1)
    video = sys.argv[1]
    audio = sys.argv[2]
    output = sys.argv[3]
    subs = sys.argv[4]
    compile_video(video, audio, output, subs)
