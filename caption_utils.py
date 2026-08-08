import os
import subprocess
import tempfile
import time
import re
from typing import Optional

# ============================================================
# HELPER: GET DURATION
# ============================================================

def get_duration(media_path: str) -> float:
    cmd = [
        'ffprobe', '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        media_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())

# ============================================================
# PART 1: TRANSCRIBE WITH WHISPER → SRT
# ============================================================

def generate_srt_from_audio(
    audio_path: str,
    output_srt_path: Optional[str] = None,
    model_size: str = "tiny",
    speed_factor: float = 1.0
) -> str:
    import whisper
    
    print(f"   🎤 Transcribing audio with Whisper (model: {model_size})...")
    model = whisper.load_model(model_size)
    result = model.transcribe(audio_path, word_timestamps=True)
    
    if output_srt_path is None:
        output_srt_path = audio_path.replace(".mp3", ".srt")
    
    with open(output_srt_path, 'w') as f:
        index = 1
        for segment in result['segments']:
            words = segment.get('words', [])
            if not words:
                start = segment['start'] / speed_factor
                end = segment['end'] / speed_factor
                text = segment['text'].strip()
                if text:
                    f.write(f"{index}\n")
                    f.write(f"{_format_time(start)} --> {_format_time(end)}\n")
                    f.write(f"{text}\n\n")
                    index += 1
                continue
            
            chunk_size = 3
            for i in range(0, len(words), chunk_size):
                chunk = words[i:i+chunk_size]
                if chunk:
                    start_time = chunk[0].get('start', 0) / speed_factor
                    end_time = chunk[-1].get('end', start_time + 0.5) / speed_factor
                    text = ' '.join([w.get('word', '').strip() for w in chunk]).strip()
                    if text:
                        f.write(f"{index}\n")
                        f.write(f"{_format_time(start_time)} --> {_format_time(end_time)}\n")
                        f.write(f"{text}\n\n")
                        index += 1
    
    # Clean up common Whisper errors
    _clean_srt_text(output_srt_path)
    
    print(f"   ✅ SRT file created (scaled by {speed_factor}x): {output_srt_path}")
    return output_srt_path


def _clean_srt_text(srt_path: str):
    corrections = {
        "I'm ale": "I'm",
        "I'm aale": "I'm",
        "ale": "about",
        "alright": "all right",
        "gonna": "going to",
        "wanna": "want to",
        "kinda": "kind of",
        "sorta": "sort of",
    }
    with open(srt_path, 'r') as f:
        content = f.read()
    for wrong, correct in corrections.items():
        content = content.replace(wrong, correct)
    with open(srt_path, 'w') as f:
        f.write(content)


def _shift_srt_timestamps(srt_path: str, shift_seconds: float, output_path: str):
    """Shift all timestamps in an SRT file by -shift_seconds."""
    with open(srt_path, 'r') as f:
        lines = f.readlines()
    
    shifted_lines = []
    time_pattern = re.compile(r'(\d{2}):(\d{2}):(\d{2}),(\d{3})')
    
    for line in lines:
        if '-->' in line:
            parts = line.split('-->')
            new_parts = []
            for part in parts:
                part = part.strip()
                match = time_pattern.match(part)
                if match:
                    h, m, s, ms = map(int, match.groups())
                    total_sec = h*3600 + m*60 + s + ms/1000.0
                    new_sec = total_sec - shift_seconds
                    if new_sec < 0:
                        new_sec = 0
                    new_h = int(new_sec // 3600)
                    new_m = int((new_sec % 3600) // 60)
                    new_s = int(new_sec % 60)
                    new_ms = int((new_sec % 1) * 1000)
                    new_part = f"{new_h:02d}:{new_m:02d}:{new_s:02d},{new_ms:03d}"
                    new_parts.append(new_part)
                else:
                    new_parts.append(part)
            shifted_lines.append(f"{new_parts[0]} --> {new_parts[1]}\n")
        else:
            shifted_lines.append(line)
    
    with open(output_path, 'w') as f:
        f.writelines(shifted_lines)


# ============================================================
# PART 2: BURN SUBTITLES ON SEGMENTS (WITH TIMESTAMP SHIFT)
# ============================================================

def burn_subtitles_segmented(
    video_path: str,
    srt_path: str,
    output_path: str,
    audio_path: Optional[str] = None,   # <-- NEW: pass the original audio
    font_size: int = 20,
    segment_duration: int = 30,
) -> str:
    print(f"   🎬 Burning subtitles (segmented, shifted)...")
    print(f"      Video: {video_path}")
    print(f"      SRT: {srt_path}")
    
    duration = get_duration(video_path)
    print(f"      Duration: {duration:.1f}s")
    
    total_segments = int(duration / segment_duration) + 1
    print(f"      Splitting into {total_segments} segments of {segment_duration}s each")
    print(f"      Quality tiers: CRF 20 (seg 1), CRF 22 (seg 2), CRF 25+ (seg 3+)")
    
    temp_dir = os.path.join(os.path.dirname(video_path), f"caption_segments_{int(time.time())}")
    os.makedirs(temp_dir, exist_ok=True)
    
    # Use provided audio file if available, otherwise try to extract
    if audio_path and os.path.exists(audio_path):
        audio_file = audio_path
        print(f"      ✅ Using provided audio: {audio_file}")
    else:
        # Fallback: extract audio from video (this may fail)
        audio_file = os.path.join(temp_dir, "audio.aac")
        cmd_extract_audio = [
            'ffmpeg', '-y',
            '-i', video_path,
            '-vn',
            '-acodec', 'copy',
            audio_file
        ]
        try:
            subprocess.run(cmd_extract_audio, check=True, capture_output=True, timeout=60)
            print(f"      ✅ Audio extracted")
        except Exception as e:
            print(f"      ⚠️ Audio extraction failed: {e}")
            audio_file = None
    
    segment_files = []
    abs_srt = os.path.abspath(srt_path)
    
    for i in range(total_segments):
        start_time = i * segment_duration
        seg_duration = min(segment_duration, duration - start_time)
        if seg_duration <= 0:
            break
        
        # Extract video segment
        seg_input = os.path.join(temp_dir, f"seg_input_{i:04d}.mp4")
        cmd_extract = [
            'ffmpeg', '-y',
            '-i', video_path,
            '-ss', str(start_time),
            '-t', str(seg_duration),
            '-c:v', 'copy',
            '-an',
            seg_input
        ]
        subprocess.run(cmd_extract, check=True, capture_output=True, timeout=60)
        
        # Create shifted SRT for this segment
        shifted_srt = os.path.join(temp_dir, f"shifted_{i:04d}.srt")
        _shift_srt_timestamps(abs_srt, start_time, shifted_srt)
        
        # Burn subtitles on segment
        seg_output = os.path.join(temp_dir, f"seg_captioned_{i:04d}.mp4")
        
        # --- QUALITY TIERS ---
        if i == 0:
            seg_crf = 20
            seg_preset = "veryfast"
            quality_label = "CRF 20 (Fast)"
        elif i == 1:
            seg_crf = 22
            seg_preset = "veryfast"
            quality_label = "CRF 22 (Fast)"
        else:
            seg_crf = 25
            seg_preset = "ultrafast"
            quality_label = "CRF 25 (Ultra Fast)"
        
        cmd_burn = [
            'ffmpeg', '-y',
            '-i', seg_input,
            '-vf',
            f"subtitles='{shifted_srt}':force_style='FontName=Arial,FontSize={font_size},Bold=1,Alignment=10,MarginV=90,Outline=2,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000'",
            '-c:v', 'libx264',
            '-preset', seg_preset,
            '-crf', str(seg_crf),
            '-an',
            '-movflags', '+faststart',
            seg_output
        ]
        print(f"      Burning segment {i+1}/{total_segments} ({seg_duration:.1f}s) with {quality_label}...")
        try:
            subprocess.run(cmd_burn, check=True, capture_output=True, timeout=180)
            segment_files.append(seg_output)
        except Exception as e:
            print(f"      ⚠️ Segment {i+1} failed: {e}")
            # Fallback: copy without captions
            fallback_output = os.path.join(temp_dir, f"seg_fallback_{i:04d}.mp4")
            subprocess.run([
                'ffmpeg', '-y',
                '-i', seg_input,
                '-c:v', 'copy',
                '-an',
                fallback_output
            ], check=True, capture_output=True, timeout=60)
            segment_files.append(fallback_output)
        
        # Clean input and shifted SRT
        os.unlink(seg_input)
        os.unlink(shifted_srt)
    
    # Concatenate segments
    print(f"   🔗 Concatenating {len(segment_files)} segments...")
    concat_file = os.path.join(temp_dir, "concat.txt")
    with open(concat_file, 'w') as f:
        for seg in segment_files:
            f.write(f"file '{os.path.abspath(seg)}'\n")
    
    video_combined = os.path.join(temp_dir, "video_combined.mp4")
    cmd_concat = [
        'ffmpeg', '-y',
        '-f', 'concat',
        '-safe', '0',
        '-i', concat_file,
        '-c:v', 'copy',
        '-an',
        video_combined
    ]
    subprocess.run(cmd_concat, check=True, capture_output=True, timeout=120)
    
    # Clean up segment files
    for seg in segment_files:
        os.unlink(seg)
    os.unlink(concat_file)
    
    # Mux audio
    if audio_file and os.path.exists(audio_file):
        print(f"   🔗 Muxing audio...")
        cmd_mux = [
            'ffmpeg', '-y',
            '-i', video_combined,
            '-i', audio_file,
            '-c:v', 'copy',
            '-c:a', 'aac',
            '-b:a', '192k',
            '-shortest',
            '-movflags', '+faststart',
            output_path
        ]
        subprocess.run(cmd_mux, check=True, capture_output=True, timeout=120)
    else:
        # No audio, just move video
        os.rename(video_combined, output_path)
    
    # Clean up temp directory
    if audio_file and audio_file != audio_path and os.path.exists(audio_file):
        os.unlink(audio_file)
    os.unlink(video_combined)
    os.rmdir(temp_dir)
    
    print(f"   ✅ Captions burned successfully (segmented, shifted)")
    return output_path


# ============================================================
# PART 3: MAIN ENTRY POINT
# ============================================================

def add_subtitles_to_video(
    video_path: str,
    output_path: Optional[str] = None,
    audio_path: Optional[str] = None,
    whisper_model: str = "tiny",
    font_size: int = 20,
    speed_factor: float = 1.0,
    font_name: str = "Arial",
    bold: bool = True,
    alignment: int = 10,
    margin_v: int = 90
) -> str:
    print(f"\n🎬 ADDING CAPTIONS TO VIDEO...")
    print(f"   Input video: {video_path}")
    
    if not os.path.exists(video_path):
        raise Exception(f"Input video not found: {video_path}")
    
    # Use provided audio file; if none, try to extract
    if audio_path is None or not os.path.exists(audio_path):
        audio_path = video_path.replace(".mp4", "_extracted.mp3")
        if not os.path.exists(audio_path):
            print(f"   🔄 Extracting audio from video...")
            cmd = [
                'ffmpeg', '-y',
                '-i', video_path,
                '-vn',
                '-acodec', 'mp3',
                '-b:a', '192k',
                audio_path
            ]
            try:
                subprocess.run(cmd, check=True, capture_output=True, timeout=60)
                print(f"   ✅ Audio extracted: {audio_path}")
            except Exception as e:
                print(f"   ❌ Audio extraction failed: {e}")
                raise Exception("Audio extraction failed")
    else:
        print(f"   ✅ Using provided audio: {audio_path}")
    
    # Generate SRT from audio
    srt_path = audio_path.replace(".mp3", ".srt")
    if not os.path.exists(srt_path):
        try:
            srt_path = generate_srt_from_audio(
                audio_path=audio_path,
                output_srt_path=srt_path,
                model_size=whisper_model,
                speed_factor=speed_factor
            )
        except Exception as e:
            print(f"   ❌ Transcription failed: {e}")
            raise Exception("Transcription failed")
    
    # Burn subtitles using segmented method
    if output_path is None:
        timestamp = int(time.time())
        output_path = video_path.replace(".mp4", f"_captioned_{timestamp}.mp4")
    
    print(f"   📝 Output path: {output_path}")
    
    final_path = burn_subtitles_segmented(
        video_path=video_path,
        srt_path=srt_path,
        output_path=output_path,
        audio_path=audio_path,  # Pass the audio file directly
        font_size=font_size
    )
    
    if os.path.exists(final_path) and os.path.getsize(final_path) > 1000000:
        print(f"   ✅ Captioned video verified: {final_path} ({os.path.getsize(final_path) / (1024*1024):.1f} MB)")
        return final_path
    else:
        raise Exception("Captioning produced invalid file")


# ============================================================
# HELPER: FORMAT TIME
# ============================================================

def _format_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
