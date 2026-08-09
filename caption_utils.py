import os
import json
import subprocess
import shutil
import tempfile
import time
import re
from typing import Optional

# Minimum inter-word gap (in sped-audio seconds) after "I'm" that indicates
# the ElevenLabs "ale" artifact is present. Normal speech gaps are ~30-90ms;
# the voice's inserted "ale" syllable (~100-150ms) stretches the gap to
# ~150ms+. A gap this large is otherwise near-silence, so muting it is
# inaudible except exactly where the artifact sits.
ALE_GAP_MIN_SEC = 0.15

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

def _is_im(word: str) -> bool:
    """True for "I'm" / "I\u2019m" / "Im" / "i m" (apostrophe variants)."""
    return re.sub(r"[^a-z]", "", word.lower()) == "im"


def _im_gap_silences(words):
    """Find "ale" artifacts whisper did NOT transcribe as a word.

    The ElevenLabs voice inserts an "ale" syllable after "I'm" (e.g. "I'm ale
    about to") even when the text sent to the TTS is clean, and whisper often
    does not transcribe the syllable at all. When that happens the artifact
    lands inside the gap between whisper's "I'm" and the next word \u2014 a gap
    of ~150ms+ instead of the usual <90ms. Returns (start, end) mute ranges
    (in sped-audio seconds) for those gaps. `words` is an iterable of
    (text, raw_start, raw_end) tuples from whisper's word timestamps.
    """
    ranges = []
    items = list(words)
    for i in range(len(items) - 1):
        text, s, e = items[i]
        _, ns, _ = items[i + 1]
        gap = ns - e
        if _is_im(text) and gap > ALE_GAP_MIN_SEC:
            # small pads so we never clip the "m" of "I'm" or the next word
            ranges.append((e + 0.01, ns - 0.01))
    return ranges


def generate_srt_from_audio(
    audio_path: str,
    output_srt_path: Optional[str] = None,
    model_size: str = "tiny",
    speed_factor: float = 1.0
) -> str:
    import whisper
    
    print(f"   🎤 Transcribing audio with Whisper (model: {model_size})...")
    # NOTE: atempo is a linear time-stretch, so dividing whisper's timestamps
    # by speed_factor maps them EXACTLY onto the sped-up audio timeline that
    # gets muxed into the final video (no approximation, no drift).
    model = whisper.load_model(model_size)
    result = model.transcribe(audio_path, word_timestamps=True)
    
    if output_srt_path is None:
        output_srt_path = audio_path.replace(".mp3", ".srt")
    
    # Time ranges (in SPED-audio seconds) where the TTS voice inserts an
    # "ale" syllable after "I'm" (e.g. "I'm ale about to"). The voice model
    # produces it even though the text is clean, whisper hears it, and we
    # mute those exact moments when the audio is muxed into the video.
    silence_ranges = []
    # Raw (un-anchored) whisper word boundaries for ALL segments, in sped-audio
    # seconds — used ONLY for artifact-gap detection (see _im_gap_silences).
    words_flat = []
    
    # Monotonicity is enforced GLOBALLY (across segments too) so the SRT
    # never contains overlapping cues, which make ffmpeg's subtitles filter
    # drop cues. Whisper segments are contiguous, so this clamp is a safety
    # net rather than the timing mechanism (the segment anchor is).
    prev_end = None
    with open(output_srt_path, 'w', encoding='utf-8') as f:
        index = 1
        for segment in result['segments']:
            words = segment.get('words', [])
            seg_start_raw = segment['start']
            seg_end_raw = segment['end']
            seg_raw_dur = seg_end_raw - seg_start_raw
            if seg_raw_dur <= 0:
                seg_raw_dur = 1e-6
            
            if not words:
                # Segment-level fallback (no word timestamps)
                start = seg_start_raw / speed_factor
                end = seg_end_raw / speed_factor
                text = segment['text'].strip()
                if text:
                    f.write(f"{index}\n")
                    f.write(f"{_format_time(start)} --> {_format_time(end)}\n")
                    f.write(f"{text}\n\n")
                    index += 1
                continue
            
            # FIXED (sync): word-by-word captions, one word per cue, with
            # timestamps ANCHORED to the whisper SEGMENT boundaries instead of
            # raw word timestamps. Whisper's segment times are accurate; its
            # word times jitter and overlap, and the old forward-only clamp
            # (start = max(word_start, prev_end)) turned every overlap into a
            # small delay that ACCUMULATED — captions fell seconds behind by
            # the end of a 3-minute video (noticeable from ~30s in).
            #
            # Design: each word keeps its RELATIVE position inside the segment
            # (preserving whisper's within-phrase pacing), and its cue spans
            # [this word's anchor, next word's anchor) — so the whole segment
            # covers exactly [seg_start, seg_end] and no drift can accumulate,
            # ever. Cues are monotonic by construction (no overlaps for the
            # ffmpeg subtitles filter).
            seg_start = seg_start_raw / speed_factor
            seg_end = seg_end_raw / speed_factor
            n = len(words)
            
            def _anchor_of(w, fallback_rel):
                rel = (w.get('start', seg_start_raw + fallback_rel * seg_raw_dur) - seg_start_raw) / seg_raw_dur
                return seg_start + min(max(rel, 0.0), 1.0) * (seg_end - seg_start)
            
            for j, w in enumerate(words):
                start = _anchor_of(w, j / max(n, 1))
                # safety net for degenerate timestamps (global monotonicity)
                if prev_end is not None and start < prev_end:
                    start = prev_end + 0.01
                if j < n - 1:
                    end = _anchor_of(words[j + 1], (j + 1) / max(n, 1))
                    if end <= start:
                        end = start + 0.15  # whisper duplicated a timestamp
                else:
                    end = seg_end
                prev_end = end
                
                text = w.get('word', '').strip()
                if not text:
                    continue
                
                # Raw whisper boundaries (NOT the anchored SRT times) — the
                # artifact-gap detection needs the real audio gaps, and the
                # anchored cues are contiguous by design (gap = 0).
                w_start_raw = (w.get('start') or seg_start_raw) / speed_factor
                w_end_raw = (w.get('end') or seg_end_raw) / speed_factor
                
                # "ale" artifact: silence it in the audio and never show it in
                # the captions (the narrator should just be silent there).
                if text.lower() in ('ale', 'aale', 'alee'):
                    silence_ranges.append((max(start - 0.02, 0.0), min(end, seg_end) + 0.03))
                    continue
                
                words_flat.append((text, w_start_raw, w_end_raw))
                
                f.write(f"{index}\n")
                f.write(f"{_format_time(start)} --> {_format_time(end)}\n")
                f.write(f"{text}\n\n")
                index += 1
    
    # FIXED (still-audible "ale"): whisper often does NOT transcribe the
    # artifact as a word (that's why it never showed in the captions nor
    # triggered the old mute). Detect it from the elongated gap after "I'm"
    # instead — this catches every instance, transcribed or not.
    silence_ranges.extend(_im_gap_silences(words_flat))
    
    # Clean up common Whisper errors (also strips any "ale" that slipped into
    # multi-word segment text)
    _clean_srt_text(output_srt_path)
    
    # Persist the silence ranges next to the SRT so the mux step can mute them.
    silences_path = output_srt_path + ".silences.json"
    with open(silences_path, 'w', encoding='utf-8') as f:
        json.dump(silence_ranges, f)
    if silence_ranges:
        print(f"   🔇 Muting {len(silence_ranges)} 'ale' artifact(s) in the voiceover audio")
    else:
        print(f"   ℹ️ No 'ale' artifact detected (analyzed {len(words_flat)} words)")
    
    print(f"   ✅ SRT file created (scaled by {speed_factor}x): {output_srt_path}")
    return output_srt_path


def _clean_srt_text(srt_path: str):
    corrections = {
        # REMOVED the bare "ale" -> "about" rule: it corrupted whole words in
        # the captions ("male" -> "mabout", "scale" -> "scabout").
        "alright": "all right",
        "gonna": "going to",
        "wanna": "want to",
        "kinda": "kind of",
        "sorta": "sort of",
    }
    with open(srt_path, 'r', encoding='utf-8') as f:
        content = f.read()
    for wrong, correct in corrections.items():
        content = content.replace(wrong, correct)
    # Standalone "ale"/"aale"/"alee" filler words (word boundaries keep
    # real words like "male", "female", "scale", "tale" intact).
    content = re.sub(r'\b(?:ale|aale|alee)\b', '', content, flags=re.IGNORECASE)
    content = re.sub(r' +', ' ', content)  # collapse double spaces
    with open(srt_path, 'w', encoding='utf-8') as f:
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
    audio_path: Optional[str] = None,      # voiceover used for whisper transcription
    mux_audio_path: Optional[str] = None,  # exact final audio (sped + music) to mux
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
    print(f"      Quality: uniform CRF {os.environ.get('CAPTION_CRF', '18')}, "
          f"preset {os.environ.get('CAPTION_PRESET', 'slow')} (every segment)")
    
    temp_dir = os.path.join(os.path.dirname(video_path), f"caption_segments_{int(time.time())}")
    os.makedirs(temp_dir, exist_ok=True)
    
    # Prefer the exact final audio track (voice sped up + music) when given —
    # that's the audio actually inside the video, so the captions will line up
    # with it. Falls back to the raw voiceover, then to extracting from video.
    if mux_audio_path and os.path.exists(mux_audio_path):
        audio_file = mux_audio_path
        print(f"      ✅ Using final audio for muxing: {audio_file}")
    elif audio_path and os.path.exists(audio_path):
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
        
        # Create shifted SRT for this segment
        shifted_srt = os.path.join(temp_dir, f"shifted_{i:04d}.srt")
        _shift_srt_timestamps(abs_srt, start_time, shifted_srt)
        
        # Burn subtitles on segment.
        # FIXED (sync): seek straight from the source video with -ss BEFORE -i
        # and re-encode — the old "extract with -c:v copy" step snapped to the
        # nearest keyframe (up to ~8s off), so every 30s segment's captions
        # were offset from the voice. Input seeking + re-encode starts the
        # segment at EXACTLY start_time.
        seg_output = os.path.join(temp_dir, f"seg_captioned_{i:04d}.mp4")
        
        # CAPTION burn quality is SEPARATE from the background render
        # (video_compile.py keeps CRF 15 + veryslow). FIXED: burning at
        # veryslow + CRF 15 timed out on every 30s segment of a 2-core
        # GitHub runner (>600s each), which fell back to caption-less copies
        # and ultimately failed the whole caption pass. CRF 18 + slow keeps
        # the burned text crisp while finishing each segment in ~2 min.
        seg_crf = int(os.environ.get("CAPTION_CRF", "18"))
        seg_preset = os.environ.get("CAPTION_PRESET", "slow")
        quality_label = f"CRF {seg_crf} ({seg_preset})"
        
        cmd_burn = [
            'ffmpeg', '-y',
            '-ss', str(start_time),
            '-t', str(seg_duration),
            '-i', video_path,
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
            # Generous timeout: veryslow + CRF 15 needs ~4-6 min per 30s
            # segment on a 2-core runner.
            subprocess.run(cmd_burn, check=True, capture_output=True, timeout=600)
            segment_files.append(seg_output)
        except Exception as e:
            print(f"      ⚠️ Segment {i+1} failed: {e}")
            # FIXED (Errno 39 bug): a timed-out/killed ffmpeg leaves a PARTIAL
            # seg_captioned file behind. It used to stay in temp_dir, so the
            # final os.rmdir() raised "Directory not empty" and the ENTIRE
            # caption pass was reported as failed — every veryslow run lost
            # its captions this way. Remove the partial before falling back.
            if os.path.exists(seg_output):
                try:
                    os.unlink(seg_output)
                except OSError:
                    pass
            # Fallback: copy without captions (keyframe-snapped is fine here —
            # better a caption-less sliver than a dead video)
            fallback_output = os.path.join(temp_dir, f"seg_fallback_{i:04d}.mp4")
            subprocess.run([
                'ffmpeg', '-y',
                '-ss', str(start_time),
                '-t', str(seg_duration),
                '-i', video_path,
                '-c:v', 'copy',
                '-an',
                fallback_output
            ], check=True, capture_output=True, timeout=60)
            segment_files.append(fallback_output)
        
        # Clean shifted SRT
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
        ]
        # Mute the "ale" artifacts (detected by whisper during transcription)
        # so the narrator is SILENT there instead of saying "I'm ale about to".
        # The ranges are already in sped-audio seconds, matching this track.
        silence_ranges = []
        silences_path = srt_path + ".silences.json"
        if os.path.exists(silences_path):
            try:
                with open(silences_path, 'r', encoding='utf-8') as f:
                    silence_ranges = json.load(f)
            except Exception as e:
                print(f"      ⚠️ Could not read silence ranges: {e}")
        if silence_ranges:
            gates = ",".join(
                f"volume=enable='between(t,{s:.3f},{e:.3f})':volume=0"
                for s, e in silence_ranges
            )
            cmd_mux += ['-af', gates, output_path]
            print(f"      🔇 Applied {len(silence_ranges)} silence gate(s) to audio")
        else:
            cmd_mux.append(output_path)
        subprocess.run(cmd_mux, check=True, capture_output=True, timeout=120)
    else:
        # No audio, just move video
        os.rename(video_combined, output_path)
    
    # Clean up temp directory (never delete the caller's mux_audio_path —
    # tasks.py owns that file's lifecycle). FIXED: rmtree instead of rmdir so
    # a stray partial file can never fail the whole caption pass again.
    if (audio_file and audio_file != audio_path and audio_file != mux_audio_path
            and os.path.exists(audio_file)):
        os.unlink(audio_file)
    if os.path.exists(video_combined):
        os.unlink(video_combined)
    shutil.rmtree(temp_dir, ignore_errors=True)
    
    print(f"   ✅ Captions burned successfully (segmented, shifted)")
    return output_path


# ============================================================
# PART 3: MAIN ENTRY POINT
# ============================================================

def add_subtitles_to_video(
    video_path: str,
    output_path: Optional[str] = None,
    audio_path: Optional[str] = None,
    mux_audio_path: Optional[str] = None,  # exact final audio track from compile_video
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
        audio_path=audio_path,          # used for whisper transcription
        mux_audio_path=mux_audio_path,  # exact audio track from compile_video
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
