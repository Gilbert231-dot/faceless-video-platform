import os
import time
import subprocess
import tempfile
import shutil
from tqdm import tqdm
from config import VOICE_SPEED

# --- ENCODING CONSTANTS (module-level so tasks.py can stay in sync) ---
# Background footage playback speed. Lower = calmer/slower movement.
SPEED_FACTOR = 1.35
SEGMENT_DURATION = 45
# How much footage to grab relative to the narration: the background plays at
# SPEED_FACTOR x and the voice is sped to VOICE_SPEED x, so to cover the whole
# narration (with 10% slack) we need:
#   footage = audio_duration * SPEED_FACTOR / VOICE_SPEED * 1.1
# This replaces the old hardcoded 1.5x which (a) rendered ~25% more footage
# than needed and (b) combined with a 1x supply from get_next_segment, made
# the sped-up video end BEFORE the narration so -shortest cut stories short.
EXTRACT_FACTOR = round((SPEED_FACTOR / VOICE_SPEED) * 1.1, 3)

# Helper: Get duration (seconds)
def get_duration(media_path: str) -> float:
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
    Now outputs YouTube-compatible format (yuv420p, faststart, aac audio).
    """
    print("🎬 Starting video compilation (SEGMENTED, HIGH QUALITY)...")
    
    # --- VOICE VOLUME ---
    if voice_id == "EXAVITQu4vr4xnSDxMaL":
        VOICE_VOLUME = 1.8
    else:
        VOICE_VOLUME = 1.5
    
    # --- OTHER SETTINGS ---
    # Uniform CRF for the WHOLE background video (was 18/20/22 per segment).
    # CRF controls quality; the preset controls encode speed. CRF 15 is
    # near-visually-lossless and preset=veryslow gives the best motion
    # estimation — videos upload straight to YouTube, so the slower encode
    # and bigger files are fine.
    CRF_VALUE = int(os.environ.get("VIDEO_CRF", "15"))
    PRESET = os.environ.get("VIDEO_PRESET", "veryslow")
    
    print(f"   🎙️ Voice volume: {int(VOICE_VOLUME * 100)}%")
    print(f"   🎙️ Voice speed: {VOICE_SPEED}x")
    
    # --- BACKGROUND MUSIC ---
    MUSIC_PATH = "assets/music/Caleb Arredondo - Feeling Blue.mp3"
    MUSIC_VOLUME = 0.35
    
    music_available = os.path.exists(MUSIC_PATH)
    if music_available:
        print(f"   🎵 Background music found: {MUSIC_PATH}")
        print(f"   🎵 Music volume: {int(MUSIC_VOLUME * 100)}%")
    else:
        print(f"   ⚠️ Background music not found at: {MUSIC_PATH}")
        print("   Continuing without music...")
    
    if isinstance(video_paths, str):
        video_paths = [video_paths]
    if not video_paths:
        raise Exception("No video paths provided")
    
    audio_duration = get_duration(audio_path)
    print(f"   🎙️ Audio duration: {audio_duration:.2f}s")
    
    extract_duration = audio_duration * EXTRACT_FACTOR
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
    
    total_segments = int(video_duration / SEGMENT_DURATION) + 1
    print(f"   📦 Splitting into {total_segments} segments")
    print(f"   📊 Quality: uniform CRF {CRF_VALUE}, preset {PRESET} (same quality for every segment)")
    
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
        
        # --- SHORT SEGMENT HANDLING ---
        # FIXED: a failed -c:v copy used to kill the whole video. Now it falls
        # back to a re-encode, and only skips the sliver as a last resort.
        # Timeouts are generous because veryslow + CRF 15 can take ~5-7 min
        # per 45s segment on a 2-core runner (the 300s cap was hit in prod).
        if segment_duration < 5.0:
            segment_output = os.path.join(output_dir, f"segment_processed_{i}_{int(time.time())}.mp4")
            try:
                subprocess.run([
                    'ffmpeg', '-y',
                    '-i', segment_input,
                    '-c:v', 'copy',
                    '-an',
                    segment_output
                ], check=True, capture_output=True, timeout=60)
            except Exception as e:
                print(f"   ⚠️ Short segment {i+1} copy failed ({e}); re-encoding...")
                try:
                    subprocess.run([
                        'ffmpeg', '-y',
                        '-i', segment_input,
                        '-c:v', 'libx264',
                        '-preset', PRESET,
                        '-crf', str(CRF_VALUE),
                        '-pix_fmt', 'yuv420p',
                        '-an',
                        segment_output
                    ], check=True, capture_output=True, timeout=300)
                except Exception as e2:
                    print(f"   ⚠️ Short segment {i+1} still failed ({e2}); skipping it")
                    if os.path.exists(segment_input):
                        os.unlink(segment_input)
                    pbar.update(1)
                    continue
            segment_files.append(segment_output)
            pbar.update(1)
            print(f"   ✅ Segment {i+1}/{total_segments} complete (copied, no processing)")
            if os.path.exists(segment_input):
                os.unlink(segment_input)
            continue
        
        # Uniform CRF for every segment (removed the 18/20/22 quality tiers).
        segment_crf = CRF_VALUE
        segment_preset = PRESET
        quality_label = f"CRF {CRF_VALUE} ({PRESET})"
        
        print(f"   📌 Segment {i+1}/{total_segments}: {quality_label} ({segment_duration:.1f}s)")
        
        segment_output = os.path.join(output_dir, f"segment_processed_{i}_{int(time.time())}.mp4")
        
        # YouTube-compatible format
        cmd_process = [
            'ffmpeg', '-y',
            '-i', segment_input,
            '-vf', 
            f'crop=ih*9/16:ih:(iw-ih*9/16)/2:0,'
            f'scale=1080:1920:flags=lanczos,'
            f'setpts={1/SPEED_FACTOR}*PTS,'
            f'format=yuv420p',
            '-sws_flags', 'lanczos',
            '-c:v', 'libx264',
            '-preset', segment_preset,
            '-crf', str(segment_crf),
            '-profile:v', 'high',
            '-level', '4.0',
            '-an',
            '-movflags', '+faststart',
            segment_output
        ]
        
        try:
            subprocess.run(cmd_process, check=True, capture_output=True, timeout=900)
            segment_files.append(segment_output)
            pbar.update(1)
            print(f"   ✅ Segment {i+1}/{total_segments} complete ({quality_label})")
        except Exception as e:
            print(f"   ⚠️ Segment {i+1} failed: {e}")
            print(f"   🔄 Using fallback for segment {i+1}...")
            cmd_fallback = [
                'ffmpeg', '-y',
                '-i', segment_input,
                '-vf', 
                f'crop=ih*9/16:ih:(iw-ih*9/16)/2:0,'
                f'scale=1080:1920:flags=lanczos,'
                f'format=yuv420p',
                '-sws_flags', 'lanczos',
                '-c:v', 'libx264',
                '-preset', PRESET,
                '-crf', str(CRF_VALUE),
                '-profile:v', 'high',
                '-level', '4.0',
                '-an',
                '-movflags', '+faststart',
                segment_output
            ]
            subprocess.run(cmd_fallback, check=True, capture_output=True, timeout=900)
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
    
    # --- AUDIO PROCESSING ---
    print("⚡ Processing audio...")
    audio_processed = os.path.join(output_dir, f"audio_processed_{int(time.time())}.mp3")
    
    cmd_audio_process = [
        'ffmpeg', '-y',
        '-i', audio_path,
        '-filter_complex',
        f'atempo={VOICE_SPEED},volume={VOICE_VOLUME}',
        '-ac', '1',
        '-acodec', 'mp3',
        '-b:a', '192k',
        audio_processed
    ]
    
    try:
        subprocess.run(cmd_audio_process, check=True, capture_output=True, timeout=120)
        print(f"   ✅ Audio processed")
    except Exception as e:
        print(f"   ⚠️ Audio processing failed: {e}")
        audio_processed = audio_path
    
    # --- BACKGROUND MUSIC ---
    final_audio = audio_processed
    music_added = False
    
    if music_available:
        print("🎵 Mixing background music...")
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
            print(f"   ✅ Music looped and mixed")
            if audio_processed != audio_path:
                os.unlink(audio_processed)
            final_audio = audio_with_music
        except Exception as e:
            print(f"   ⚠️ Music mixing failed: {e}")
            print("   Continuing without music...")
    
    # --- FINAL VIDEO COMPILATION (YouTube-Compatible) ---
    print("⚡ Adding audio to video...")
    final_output = os.path.join(output_dir, f"output_{int(time.time())}.mp4")
    # FIXED: mux with -c:v copy instead of re-encoding. The segments were
    # already encoded at CRF 18, so this second full encode of the whole video
    # was pure wasted CPU and a major cause of the GitHub Actions timeouts.
    cmd_audio = [
        'ffmpeg', '-y',
        '-i', video_combined,
        '-i', final_audio,
        '-c:v', 'copy',
        '-c:a', 'aac',
        '-b:a', '192k',
        '-movflags', '+faststart',
        '-shortest',
        final_output
    ]
    
    try:
        subprocess.run(cmd_audio, check=True, capture_output=True, timeout=120)
        os.unlink(video_combined)
        # NOTE: final_audio is intentionally KEPT here — the caption step
        # reuses it so the burned-in captions are in sync with the exact
        # audio track that's inside the video (sped up + music).
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
    print(f"      - Music: {'✅ Added' if music_added else '❌ Skipped'}")
    print(f"      - Quality: CRF {CRF_VALUE}")
    print(f"      - Scaling: Lanczos")
    print(f"      - File size: {final_size:.1f} MB")
    print(f"      - Duration: {final_duration:.1f}s")
    print(f"   ✅ YouTube-compatible format (yuv420p, faststart, AAC)")
    
    # --- PART NUMBER OVERLAY ---
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
            '-preset', PRESET,
            '-crf', str(CRF_VALUE),
            '-profile:v', 'high',
            '-level', '4.0',
            '-pix_fmt', 'yuv420p',
            '-c:a', 'copy',
            '-movflags', '+faststart',
            overlay_output
        ]
        try:
            subprocess.run(cmd_overlay, check=True, capture_output=True, timeout=120)
            os.unlink(final_output)
            final_output = overlay_output
        except Exception as e:
            print(f"   ⚠️ Failed to add part number overlay: {e}")
    
    # --- CLEANUP ---
    print("   🗑️ Cleaning up temporary files...")
    temp_files = []
    for f in os.listdir(output_dir):
        if f.startswith(("segment_input_", "segment_processed_", "gameplay_segment_")):
            temp_files.append(os.path.join(output_dir, f))
    for f in os.listdir(output_dir):
        if f.startswith("caption_segments_"):
            shutil.rmtree(os.path.join(output_dir, f))
            print(f"      🗑️ Removed folder: {f}")
    for file_path in temp_files:
        try:
            os.remove(file_path)
            print(f"      🗑️ Removed: {os.path.basename(file_path)}")
        except:
            pass
    print(f"   ✅ Cleanup complete")
    
    # Return the video AND the exact audio track inside it, so the caption
    # step can burn subtitles that stay in sync with the narrator's voice.
    return final_output, final_audio
