import os
import re
import time
import json
import shutil
import tempfile
import subprocess
from pathlib import Path
from config import PROJECT_ROOT, FAST_MODE, OUTPUT_DIR
from captions import whisper_json_to_srt, generate_fallback_srt

#!/usr/bin/env python3
"""
video_compile.py - FFmpeg pipeline for faceless TikTok videos
"""

# ----------------------------------------------------------------------
# Helper: decode MP3 to WAV with multiple tools
# ----------------------------------------------------------------------
def prepare_audio(mp3_path: str, wav_path: str, sample_rate: int = 48000) -> str:
    """
    Convert MP3 to 16‑bit PCM WAV using the first available tool that works.
    Tries: sox, lame, mpg123, then ffmpeg with error-ignoring flags.
    """
    mp3_path = str(mp3_path)
    wav_path = str(wav_path)

    # ---- 1) sox (most reliable) ----
    sox = shutil.which('sox')
    if sox:
        cmd = [sox, mp3_path, '-r', str(sample_rate), '-c', '2', '-b', '16', wav_path]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            return wav_path
        except subprocess.CalledProcessError as e:
            print(f"sox failed: {e.stderr}")

    # ---- 2) lame --decode ----
    lame = shutil.which('lame')
    if lame:
        cmd = [lame, '--decode', mp3_path, wav_path]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            return wav_path
        except subprocess.CalledProcessError as e:
            print(f"lame failed: {e.stderr}")

    # ---- 3) mpg123 ----
    mpg123 = shutil.which('mpg123')
    if mpg123:
        cmd = [mpg123, '-w', wav_path, '-r', str(sample_rate), '-2', mp3_path]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            return wav_path
        except subprocess.CalledProcessError as e:
            print(f"mpg123 failed: {e.stderr}")

    # ---- 4) ffmpeg with error-ignoring ----
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
        except subprocess.CalledProcessError as e:
            print(f"ffmpeg failed: {e.stderr}")
            raise RuntimeError(f"All decoders failed for {mp3_path}")

    raise RuntimeError("No audio decoder found (sox, lame, mpg123, or ffmpeg)")

# ----------------------------------------------------------------------
# (rest of video_compile.py remains the same as previously given)
# ----------------------------------------------------------------------


# ----------------------------------------------------------------------
# Helper: Get video/audio duration (seconds)
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
# Main compilation function
# ----------------------------------------------------------------------
def compile_video(
    video_path: str,
    audio_mp3: str,
    output_path: str,
    subtitle_ass: str,               # path to .ass file
    background_music: str = None,    # optional .mp3
    voice_volume: float = 2.0,       # boost voice (default 2x)
    bg_volume: float = 0.3,          # background volume (30%)
    fade_duration: float = 3.0,      # fade in/out for bg music (seconds)
    target_width: int = 1080,
    target_height: int = 1920,
    crf: int = 18,
    audio_bitrate: str = '192k',
    temp_dir: str = None,
) -> str:
    """
    Compose final video with audio processing and subtitles.

    Returns path to output file.
    """
    # Create temporary directory for decoded WAVs
    if temp_dir is None:
        temp_dir = tempfile.mkdtemp(prefix='video_compile_')
    else:
        os.makedirs(temp_dir, exist_ok=True)

    # 1. Decode voiceover MP3 -> WAV
    voice_wav = os.path.join(temp_dir, 'voice_decoded.wav')
    prepare_audio(audio_mp3, voice_wav)

    # 2. Get video duration (for fades and trimming)
    video_duration = get_duration(video_path)
    # Voice duration should be similar; we'll trim to video duration.

    # 3. Build filter_complex parts
    filter_parts = []

    # ---- Video filter chain ----
    # Crop to 9:16 aspect ratio (center crop) then scale to target
    # Assuming source is landscape (16:9) - adjust if needed.
    video_filter = (
        f"[0:v]crop=iw:ih*9/16,scale={target_width}:{target_height},"
        f"subtitles={subtitle_ass}:force_style='FontName=Arial,Bold,FontSize=40,"
        f"Outline=2,Shadow=1,Alignment=10,MarginV=100'"
    )
    filter_parts.append(f"{video_filter}[vout]")

    # ---- Audio filter chain ----
    # Voice: trim to video duration, boost volume
    voice_filter = (
        f"[1:a]atrim=0:{video_duration},asetpts=PTS-STARTPTS,"
        f"volume={voice_volume}[voice]"
    )
    filter_parts.append(voice_filter)

    # If background music is provided, decode it and mix
    if background_music:
        bg_wav = os.path.join(temp_dir, 'bg_decoded.wav')
        prepare_audio(background_music, bg_wav)

        # Fade in/out and adjust volume
        bg_filter = (
            f"[2:a]volume={bg_volume},"
            f"afade=t=in:st=0:d={fade_duration},"
            f"afade=t=out:st={video_duration - fade_duration}:d={fade_duration}[bg]"
        )
        filter_parts.append(bg_filter)

        # Mix voice and bg (keep first duration, i.e. voice length)
        mix_filter = (
            f"[bg][voice]amix=inputs=2:duration=first:dropout_transition={fade_duration}[mixed]"
        )
        filter_parts.append(mix_filter)

        # Apply loudness normalization to the mixed audio
        norm_filter = "[mixed]loudnorm=I=-16:LRA=11:TP=-1.5[aout]"
        filter_parts.append(norm_filter)
        audio_map = '[aout]'
        audio_inputs = ['-i', voice_wav, '-i', bg_wav]
    else:
        # Only voice, apply loudnorm directly
        norm_filter = "[voice]loudnorm=I=-16:LRA=11:TP=-1.5[aout]"
        filter_parts.append(norm_filter)
        audio_map = '[aout]'
        audio_inputs = ['-i', voice_wav]

    # Combine all filter parts
    full_filter = '; '.join(filter_parts)

    # 4. Build the FFmpeg command
    cmd = [
        'ffmpeg', '-y',
        '-i', video_path,
        *audio_inputs,               # voice_wav, optionally bg_wav
        '-filter_complex', full_filter,
        '-map', '[vout]',
        '-map', audio_map,
        '-c:v', 'libx264',
        '-crf', str(crf),
        '-preset', 'medium',
        '-c:a', 'aac',
        '-b:a', audio_bitrate,
        '-shortest',                 # stop when shortest stream ends
        output_path
    ]

    # 5. Execute
    print(f"Running FFmpeg command:\n{' '.join(cmd)}")
    subprocess.run(cmd, check=True, capture_output=False)  # show progress

    # 6. Cleanup temp files? (optional – commented out for debugging)
    # shutil.rmtree(temp_dir)

    return output_path


# ----------------------------------------------------------------------
# Example usage (for testing or direct invocation)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # When run directly, compile a sample video (modify paths as needed)
    import sys

    if len(sys.argv) < 5:
        print("Usage: python video_compile.py <video.mp4> <audio.mp3> <output.mp4> <subtitles.ass> [background.mp3]")
        sys.exit(1)

    video = sys.argv[1]
    audio = sys.argv[2]
    output = sys.argv[3]
    subs = sys.argv[4]
    bg = sys.argv[5] if len(sys.argv) > 5 else None

    compile_video(
        video_path=video,
        audio_mp3=audio,
        output_path=output,
        subtitle_ass=subs,
        background_music=bg,
        voice_volume=2.0,
        bg_volume=0.3,
        fade_duration=3.0,
    )
