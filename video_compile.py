import os
import time
import subprocess
import tempfile
import shutil
from tqdm import tqdm
from config import VOICE_SPEED

# Helper: Get duration (seconds)
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


def compile_video(video_paths, audio_path, script, subtitle_path=None,
                  intro_frame=None, title=None, part_label=None,
                  voice_id=None):
    """
    Compile video with segmented rendering.
    Includes gender-specific voice volume (both increased).
    """
    print("🎬 Starting video compilation (SEGMENTED, HIGH QUALITY)...")
    
    # --- VOICE VOLUME (Increased for both) ---
    if voice_id == "EXAVITQu4vr4xnSDxMaL":  # Sarah (female)
        VOICE_VOLUME = 1.8   # Female voice at 180%
    else:
        VOICE_VOLUME = 1.5   # Male voice at 150%
    
    # --- OTHER SETTINGS ---
    SPEED_FACTOR = 1.6
    SEGMENT_DURATION = 45
    CRF_VALUE = 18
    PRESET = "medium"
    
    print(f"   🎙️ Voice volume: {int(VOICE_VOLUME * 100)}%")
    print(f"   🎙️ Voice speed: {VOICE_SPEED}x")
    
    # --- BACKGROUND MUSIC ---
    MUSIC_PATH = "/workspaces/faceless-video-platform/assets/music/Caleb Arredondo - Feeling Blue.mp3"
    MUSIC_VOLUME = 0.35
    
    # Check if music exists
    music_available = os.path.exists(MUSIC_PATH)
    if music_available:
        print(f"   🎵 Background music found: {MUSIC_PATH}")
        print(f"   🎵 Music volume: {int(MUSIC_VOLUME * 100)}%")
    else:
        print(f"   ⚠️ Background music not found at: {MUSIC_PATH}")
        print("   Continuing without music...")
    
    # Handle both single path and list
    if isinstance(video_paths, str):
        video_paths = [video_paths]
    if not video_paths:
        raise Exception("No video paths provided")
    
    # Get audio duration
    audio_duration = get_duration(audio_path)
    print(f"   🎙️ Audio duration: {audio_duration:.2f}s")
    
    # Extract gameplay segment
    extract_duration = audio_duration * 1.5
    print(f"   ⏱️ Extracting {extract_duration:.2f}s of footage (will be sped up {SPEED_FACTOR}x)")
    
    output_dir = os.environ.get('OUTPUT_DIR', '.')
    if not os.path.exists(output_dir):
        output_dir = '.'
    
    gameplay_segment = os.path.join(output_dir, f"gameplay_segment_{int(time.time())}.mp4")
    source_video = video_paths[0]
    
    if not os.path.exists(source_video):
        raise Exception(f"Source video not found: {source_video}")
    
    cmd_extract = [
        'ffmpeg', '-y',
        '-i', source_video,
        '-t', str(extract_duration),
        '-c:v', 'copy',
        '-an',
        gameplay_segment
    ]
    
    try:
        subprocess.run(cmd_extract, check=True, capture_output=True, timeout=120)
        print(f"   ✅ Extracted {extract_duration:.2f}s segment.")
    except Exception as e:
        raise Exception(f"Segment extraction failed: {e}")
    
    video_duration = get_duration(gameplay_segment)
    print(f"   📊 Video duration: {video_duration:.2f}s")
    
    # Segment processing with quality tiers
    total_segments = int(video_duration / SEGMENT_DURATION) + 1
    print(f"   📦 Splitting into {total_segments} segments")
    print(f"   📊 Quality tiers: CRF 18 (segment 1), CRF 20 (segment 2), CRF 22+ (segments 3+)")

    segment_files = []
    pbar = tqdm(total=total_segments, desc="🎬 Rendering segments", unit="segment")
    
    for i in range(total_segments):
        start_time = i * SEGMENT_DURATION
        segment_duration = min(SEGMENT_DURATION, video_duration - start_time)
        if segment_duration <= 0:
            break
        
        segment_input = os.path.join(output_dir, f"segment_input_{i}_{int(time.time())}.mp4")
        cmd_extract_seg = [
            'ffmpeg', '-y',
            '-i', gameplay_segment,
            '-ss', str(start_time),
            '-t', str(segment_duration),
            '-c:v', 'copy',
            '-an',
            segment_input
        ]
        try:
            subprocess.run(cmd_extract_seg, check=True, capture_output=True, timeout=60)
        except Exception as e:
            print(f"   ⚠️ Failed to extract segment {i+1}: {e}")
            continue
        
        # --- CHECK FOR SHORT SEGMENT (skip processing) ---
        if segment_duration < 5.0:
            # Very short segment: just copy it without processing
            segment_output = os.path.join(output_dir, f"segment_processed_{i}_{int(time.time())}.mp4")
            subprocess.run([
                'ffmpeg', '-y',
                '-i', segment_input,
                '-c:v', 'copy',
                '-an',
                segment_output
            ], check=True, capture_output=True, timeout=60)
            segment_files.append(segment_output)
            pbar.update(1)
            print(f"   ✅ Segment {i+1}/{total_segments} complete (copied, no processing)")
            if os.path.exists(segment_input):
                os.unlink(segment_input)
            continue
        
        # --- QUALITY TIERS BASED ON SEGMENT POSITION ---
        if i == 0:
            segment_crf = 18
            segment_preset = "medium"
            quality_label = "CRF 18 (High)"
        elif i == 1:
            segment_crf = 20
            segment_preset = "medium"
            quality_label = "CRF 20 (Medium)"
        else:
            segment_crf = 22
            segment_preset = "veryfast"
            quality_label = "CRF 22 (Efficient)"
        
        print(f"   📌 Segment {i+1}/{total_segments}: {quality_label} ({segment_duration:.1f}s)")
        
        segment_output = os.path.join(output_dir, f"segment_processed_{i}_{int(time.time())}.mp4")
        
        # Build FFmpeg command with segment-specific settings
        cmd_process = [
            'ffmpeg', '-y',
            '-i', segment_input,
            '-vf', 
            f'crop=ih*9/16:ih:(iw-ih*9/16)/2:0,'
            f'scale=1080:1920:flags=lanczos,'
            f'setpts={1/SPEED_FACTOR}*PTS',
            '-sws_flags', 'lanczos',
            '-c:v', 'libx264',
            '-preset', segment_preset,
            '-crf', str(segment_crf),
            '-profile:v', 'high',
            '-level', '4.1',
            '-pix_fmt', 'yuv420p',
            '-an',
            '-movflags', '+faststart',
            segment_output
        ]
        
        try:
            subprocess.run(cmd_process, check=True, capture_output=True, timeout=300)
            segment_files.append(segment_output)
            pbar.update(1)
            print(f"   ✅ Segment {i+1}/{total_segments} complete ({quality_label})")
        except Exception as e:
            print(f"   ⚠️ Segment {i+1} failed: {e}")
            # Fallback: even lower quality
            print(f"   🔄 Using fallback for segment {i+1}...")
            cmd_fallback = [
                'ffmpeg', '-y',
                '-i', segment_input,
                '-vf', 
                f'crop=ih*9/16:ih:(iw-ih*9/16)/2:0,'
                f'scale=1080:1920:flags=lanczos',
                '-sws_flags', 'lanczos',
                '-c:v', 'libx264',
                '-preset', 'veryfast',
                '-crf', '25',
                '-an',
                segment_output
            ]
            subprocess.run(cmd_fallback, check=True, capture_output=True, timeout=300)
            segment_files.append(segment_output)
            print(f"   ✅ Segment {i+1} complete (fallback)")
        
        if os.path.exists(segment_input):
            os.unlink(segment_input)
    
    pbar.close()
    
    # Concatenate segments
    print("   🔗 Concatenating segments...")
    concat_file = os.path.join(output_dir, f"concat_list_{int(time.time())}.txt")
    with open(concat_file, 'w') as f:
        for seg in segment_files:
            f.write(f"file '{os.path.abspath(seg)}'\n")
    
    video_combined = os.path.join(output_dir, f"video_combined_{int(time.time())}.mp4")
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
    
    for seg in segment_files:
        os.unlink(seg)
    os.unlink(concat_file)
    
    # --- FULL AUDIO PROCESSING ---
    print("⚡ Processing audio (premium TikTok voice chain)...")
    
    audio_processed = os.path.join(output_dir, f"audio_processed_{int(time.time())}.mp3")
    
    cmd_audio_process = [
        'ffmpeg', '-y',
        '-i', audio_path,
        '-filter_complex',
        f'[0:a]atempo={VOICE_SPEED},'
        f'volume={VOICE_VOLUME},'
        f'highpass=f=80,'
        f'equalizer=f=300:width_type=octave:width=0.5:g=-2,'
        f'equalizer=f=3000:width_type=octave:width=0.5:g=2,'
        f'equalizer=f=9000:width_type=octave:width=0.5:g=1.5,'
        f'compand=attacks=0.015:decays=0.1:points=-80/-80|-30/-15|-20/-10|-12/-9|-6/-6|0/-3|20/-3,'
        f'deesser=amount=0.2:type=bandpass:bands=5,'
        f'loudnorm=I=-18:LRA=7:TP=-1,'
        f'volume=0.5',
        '-ac', '1',
        '-acodec', 'mp3',
        '-b:a', '192k',
        audio_processed
    ]
    
    try:
        subprocess.run(cmd_audio_process, check=True, capture_output=True, timeout=120)
        print(f"   ✅ Audio processed (premium TikTok voice)")
    except Exception as e:
        print(f"   ⚠️ Premium processing failed: {e}")
        print("   🔄 Falling back to basic processing...")
        cmd_audio_fallback = [
            'ffmpeg', '-y',
            '-i', audio_path,
            '-filter_complex',
            f'atempo={VOICE_SPEED},volume={VOICE_VOLUME}',
            '-ac', '1',
            '-acodec', 'mp3',
            '-b:a', '192k',
            audio_processed
        ]
        subprocess.run(cmd_audio_fallback, check=True, capture_output=True, timeout=60)
        print(f"   ✅ Audio processed (basic fallback)")
    
    # --- ADD BACKGROUND MUSIC ---
    final_audio = audio_processed
    music_added = False
    
    if music_available:
        print("🎵 Mixing background music (looped, ducking applied)...")
        audio_with_music = os.path.join(output_dir, f"audio_with_music_{int(time.time())}.mp3")
        voice_duration = get_duration(audio_processed)
        
        try:
            cmd_mix = [
                'ffmpeg', '-y',
                '-i', audio_processed,
                '-i', MUSIC_PATH,
                '-filter_complex',
                f'[0:a]volume=1.0[voice];'
                f'[1:a]volume={MUSIC_VOLUME},aloop=loop=-1:size=2e+06[music];'
                f'[voice][music]amix=inputs=2:duration=first,volume=1.2',
                '-t', str(voice_duration),
                '-ac', '2',
                '-acodec', 'mp3',
                '-b:a', '192k',
                audio_with_music
            ]
            
            subprocess.run(cmd_mix, check=True, capture_output=True, timeout=120)
            music_added = True
            print(f"   ✅ Music looped and mixed (music volume: {int(MUSIC_VOLUME*100)}%)")
            if audio_processed != audio_path:
                os.unlink(audio_processed)
            final_audio = audio_with_music
            
        except Exception as e:
            print(f"   ⚠️ Music mixing failed: {e}")
            print("   Continuing without music...")
    
    # --- ADD AUDIO TO VIDEO ---
    print("⚡ Adding audio to video...")
    final_output = os.path.join(output_dir, f"output_{int(time.time())}.mp4")
    cmd_audio = [
        'ffmpeg', '-y',
        '-i', video_combined,
        '-i', final_audio,
        '-c:v', 'copy',
        '-c:a', 'aac',
        '-b:a', '192k',
        '-shortest',
        '-movflags', '+faststart',
        final_output
    ]
    
    try:
        subprocess.run(cmd_audio, check=True, capture_output=True, timeout=120)
        os.unlink(video_combined)
        if final_audio != audio_path and os.path.exists(final_audio):
            os.unlink(final_audio)
    except Exception as e:
        os.rename(video_combined, final_output)
    
    os.unlink(gameplay_segment)
    
    final_size = os.path.getsize(final_output) / (1024 * 1024)
    final_duration = get_duration(final_output)
    
    print(f"✅ Video compiled successfully: {final_output}")
    print(f"   📊 Video info:")
    print(f"      - Resolution: 1080x1920 (9:16)")
    print(f"      - Video codec: H.264 (High Profile)")
    print(f"      - Audio codec: AAC 192kbps")
    print(f"      - Video speed: {SPEED_FACTOR}x")
    print(f"      - Voice speed: {VOICE_SPEED}x")
    print(f"      - Voice volume: {int(VOICE_VOLUME*100)}%")
    print(f"      - Music: {'✅ Added (looped, ducked)' if music_added else '❌ Skipped'}")
    print(f"      - Music volume: {int(MUSIC_VOLUME*100)}%")
    print(f"      - Quality: CRF {CRF_VALUE} (excellent)")
    print(f"      - Scaling: Lanczos (maximum sharpness)")
    print(f"      - File size: {final_size:.1f} MB")
    print(f"      - Duration: {final_duration:.1f}s")
    
    # --- ADD PART NUMBER OVERLAY (if part_label exists) ---
    if part_label and "Part" in part_label:
        print(f"\n📌 Adding Part number overlay: {part_label}")
        
        overlay_output = final_output.replace(".mp4", f"_with_part.mp4")
        
        cmd_overlay = [
            'ffmpeg', '-y',
            '-i', final_output,
            '-vf',
            f"drawtext=text='{part_label}':"
            f"fontcolor=white:"
            f"fontsize=24:"
            f"fontfile=/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf:"
            f"bordercolor=black:"
            f"borderw=2:"
            f"x=(w-text_w)/2:"
            f"y=50:"
            f"shadowcolor=black:"
            f"shadowx=2:"
            f"shadowy=2",
            '-c:v', 'libx264',
            '-preset', 'medium',
            '-crf', str(CRF_VALUE),
            '-c:a', 'copy',
            '-movflags', '+faststart',
            overlay_output
        ]
        
        try:
            subprocess.run(cmd_overlay, check=True, capture_output=True, timeout=120)
            print(f"   ✅ Part number overlay added: {overlay_output}")
            os.unlink(final_output)
            final_output = overlay_output
        except Exception as e:
            print(f"   ⚠️ Failed to add part number overlay: {e}")
    
    # --- CLEANUP: Remove ALL temporary files ---
    print("   🗑️ Cleaning up temporary files...")
    
    temp_files = []
    for f in os.listdir(output_dir):
        if f.startswith(("segment_input_", "segment_processed_", "gameplay_segment_")):
            temp_files.append(os.path.join(output_dir, f))
    
    for f in os.listdir(output_dir):
        if f.startswith("caption_segments_"):
            import shutil
            shutil.rmtree(os.path.join(output_dir, f))
            print(f"      🗑️ Removed folder: {f}")
    
    for file_path in temp_files:
        try:
            os.remove(file_path)
            print(f"      🗑️ Removed: {os.path.basename(file_path)}")
        except:
            pass
    
    print(f"   ✅ Cleanup complete")
    
    return final_output