#!/usr/bin/env python3
"""
video_compile.py - FFmpeg pipeline for faceless TikTok videos
With robust MP3 validation and multiple decoder fallbacks.
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
    # Use ffprobe to verify it's a valid audio file
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        # fallback: try to read with ffmpeg (quick)
        return True  # assume valid if we can't check
    cmd = [
        ffprobe, '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        filepath
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        # If we get a numeric duration, it's valid
        if result.returncode == 0 and result.stdout.strip():
            float(result.stdout.strip())  # should succeed
            return True
    except Exception:
        pass
    return False

# ----------------------------------------------------------------------
# Helper: Decode MP3 to WAV (with validation)
# ----------------------------------------------------------------------
def prepare_audio(mp3_path: str, wav_path: str, sample_rate: int = 48000) -> str:
    """
    Convert MP3 to WAV after validating the input file.
    Raises RuntimeError if input is invalid or all decoders fail.
    """
    mp3_path = str(mp3_path)
    wav_path = str(wav_path)

    # Validate input
    if not is_valid_audio(mp3_path):
        raise RuntimeError(f"Input audio file is invalid or empty: {mp3_path}")

    # Try decoders in order: sox, lame, mpg123, ffmpeg
    decoders = []

    sox = shutil.which('sox')
    if sox:
        decoders.append(('sox', [sox, mp3_path, '-r', str(sample_rate), '-c', '2', '-b', '16', wav_path]))

    lame = shutil.which('lame')
    if lame:
        decoders.append(('lame', [lame, '--decode', mp3_path, wav_path]))

    mpg123 = shutil.which('mpg123')
    if mpg123:
        decoders.append(('mpg123', [mpg123, '-w', wav_path, '-r', str(sample_rate), '-2', mp3_path]))

    ffmpeg = shutil.which('ffmpeg')
    if ffmpeg:
        decoders.append(('ffmpeg', [
            ffmpeg, '-y',
            '-err_detect', 'ignore_err',
            '-i', mp3_path,
            '-acodec', 'pcm_s16le',
            '-ar', str(sample_rate),
            '-ac', '2',
            wav_path
        ]))

    if not decoders:
        raise RuntimeError("No audio decoder found (sox, lame, mpg123, or ffmpeg)")

    for name, cmd in decoders:
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            return wav_path
        except subprocess.CalledProcessError as e:
            print(f"{name} failed: {e.stderr}")

    raise RuntimeError(f"All decoders failed for {mp3_path}")

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
# Main compilation function
# ----------------------------------------------------------------------
def compile_video(
    video_path: str,
    audio_mp3: str,
    output_path: str,
    subtitle_ass: str,
    background_music: str = None,
    voice_volume: float = 2.0,
    bg_volume: float = 0.3,
    fade_duration: float = 3.0,
    target_width: int = 1080,
    target_height: int = 1920,
    crf: int = 18,
    audio_bitrate: str = '192k',
    temp_dir: str = None,
) -> str:
    """
    Compose final video with audio processing and subtitles.
    """
    # Create temp dir
    if temp_dir is None:
        temp_dir = tempfile.mkdtemp(prefix='video_compile_')
    else:
        os.makedirs(temp_dir, exist_ok=True)

    # 1. Validate and decode voiceover
    if not is_valid_audio(audio_mp3):
        raise RuntimeError(f"Voiceover audio is invalid: {audio_mp3}")
    voice_wav = os.path.join(temp_dir, 'voice_decoded.wav')
    prepare_audio(audio_mp3, voice_wav)

    # 2. Get video duration
    video_duration = get_duration(video_path)

    # 3. Build filter_complex
    filter_parts = []

    # ---- Video filter ----
    video_filter = (
        f"[0:v]crop=iw:ih*9/16,scale={target_width}:{target_height},"
        f"subtitles={subtitle_ass}:force_style='FontName=Arial,Bold,FontSize=40,"
        f"Outline=2,Shadow=1,Alignment=10,MarginV=100'"
    )
    filter_parts.append(f"{video_filter}[vout]")

    # ---- Voice filter ----
    voice_filter = (
        f"[1:a]atrim=0:{video_duration},asetpts=PTS-STARTPTS,"
        f"volume={voice_volume}[voice]"
    )
    filter_parts.append(voice_filter)

    # ---- Background music (only if valid) ----
    use_bg = False
    if background_music and is_valid_audio(background_music):
        bg_wav = os.path.join(temp_dir, 'bg_decoded.wav')
        prepare_audio(background_music, bg_wav)
        use_bg = True
        bg_filter = (
            f"[2:a]volume={bg_volume},"
            f"afade=t=in:st=0:d={fade_duration},"
            f"afade=t=out:st={video_duration - fade_duration}:d={fade_duration}[bg]"
        )
        filter_parts.append(bg_filter)
        mix_filter = (
            f"[bg][voice]amix=inputs=2:duration=first:dropout_transition={fade_duration}[mixed]"
        )
        filter_parts.append(mix_filter)
        norm_filter = "[mixed]loudnorm=I=-16:LRA=11:TP=-1.5[aout]"
        filter_parts.append(norm_filter)
        audio_map = '[aout]'
        audio_inputs = ['-i', voice_wav, '-i', bg_wav]
    else:
        # Only voice
        norm_filter = "[voice]loudnorm=I=-16:LRA=11:TP=-1.5[aout]"
        filter_parts.append(norm_filter)
        audio_map = '[aout]'
        audio_inputs = ['-i', voice_wav]
        if background_music:
            print(f"⚠️ Background music skipped: {background_music} is invalid or missing")

    full_filter = '; '.join(filter_parts)

    # 4. Build FFmpeg command
    cmd = [
        'ffmpeg', '-y',
        '-i', video_path,
        *audio_inputs,
        '-filter_complex', full_filter,
        '-map', '[vout]',
        '-map', audio_map,
        '-c:v', 'libx264',
        '-crf', str(crf),
        '-preset', 'medium',
        '-c:a', 'aac',
        '-b:a', audio_bitrate,
        '-shortest',
        output_path
    ]

    print(f"Running FFmpeg command:\n{' '.join(cmd)}")
    subprocess.run(cmd, check=True, capture_output=False)

    # Cleanup temp? (optional)
    # shutil.rmtree(temp_dir)

    return output_path

# ----------------------------------------------------------------------
# Example usage (for direct testing)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 5:
        print("Usage: python video_compile.py <video.mp4> <audio.mp3> <output.mp4> <subtitles.srt/ass> [background.mp3]")
        sys.exit(1)
    video = sys.argv[1]
    audio = sys.argv[2]
    output = sys.argv[3]
    subs = sys.argv[4]
    bg = sys.argv[5] if len(sys.argv) > 5 else None
    compile_video(video, audio, output, subs, bg)
