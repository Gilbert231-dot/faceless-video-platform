import os
import re
import glob
import json
import time
import datetime
import tempfile
import subprocess
from celery import Celery
from voiceover import generate_voiceover
from gender_detector import GenderDetector
from drive_clip_manager import get_next_segment
from broll_fetcher import fetch_gameplay_footage
from caption_utils import add_subtitles_to_video
from generate_reddit_frame import generate_frame
from tts_clean import clean_for_tts
from reddit_story_loader import RedditStoryLoader
from video_compile import compile_video, get_duration, EXTRACT_FACTOR
from reddit_fetcher import get_reddit_story_with_fallback
from script_gen import generate_story_script, adapt_reddit_story
from config import FAST_MODE, DEBUG_MODE, USE_CAPTIONS, VOICE_SPEED, platform_tags

# ElevenLabs Voice IDs
MALE_VOICE_ID = "loZFKb410q0XFUiYDx8U"  # Custom Gen Z voice
FEMALE_VOICE_ID = "EXAVITQu4vr4xnSDxMaL"  # Sarah
DEFAULT_VOICE_ID = MALE_VOICE_ID

# Channel name shown on the animated reddit frame (the account name).
FRAME_USERNAME = "StoryLab"

# When unused stories fall to this many, the run warns loudly so the user
# refills the bank (fetch_stories.py on the laptop, or its weekly schedule).
STORY_REFILL_THRESHOLD = 20

# Initialize gender detector
gender_detector = GenderDetector(default_voice="male")

# Celery setup
app = Celery('tasks', broker='redis://localhost:6379/0', backend='redis://localhost:6379/0')

# Initialize the story loader
STORY_DATA_PATH = "reddit_stories"
# Use the environment variable if set, otherwise fallback to config
_debug_mode = os.environ.get('DEBUG_MODE', 'False').lower() == 'true'
story_loader = RedditStoryLoader(STORY_DATA_PATH, debug_mode=_debug_mode)


def save_metadata(video_path, title, subreddit_name, score=0, author="unknown",
                  thumbnail=None):
    """Save story metadata alongside the video for YouTube upload."""
    # NOTE: the Kevin MacLeod CC BY attribution is intentionally NOT added
    # to the description anymore (user request). CC BY technically asks for
    # credit, so keep this in mind if you ever re-enable it.
    metadata = {
        "title": title,
        "subreddit": subreddit_name,
        "score": score,
        "author": author,
        "description": f"Story from r/{subreddit_name}\n\n{title}\n\nSubscribe for more Reddit stories! 🔔",
        # YouTube tags: plain words (no '#') — the exact list the API needs.
        "tags": platform_tags("youtube", subreddit_name),
        # Facebook hashtags for the description (build_description reads this).
        "facebook_tags": platform_tags("facebook", subreddit_name),
        # TikTok hashtags for the caption — keyed tiktok_tags so the manual
        # uploader knows which list to paste on tiktok.com.
        "tiktok_tags": platform_tags("tiktok", subreddit_name),
        "video_path": video_path,
        # Custom thumbnail (1280x720 JPEG) matching the reddit-card intro;
        # the YouTube uploader calls thumbnails().set with it when present.
        "thumbnail": thumbnail,
    }
    metadata_path = video_path.replace(".mp4", "_metadata.json")
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2)
    print(f"   📝 Metadata saved: {metadata_path}")

    # TikTok manual-upload companion file (ships in the artifact zip): when
    # the user uploads the video by hand on tiktok.com, this JSON gives the
    # exact caption + settings to use — same caption the API would post
    # (kept in sync with tiktok_uploader.build_caption).
    hashtags = platform_tags("tiktok", subreddit_name)
    suggested_slot = _next_tiktok_slot()
    tiktok_meta = {
        "video_file": os.path.basename(video_path),
        "title": title,
        "subreddit": subreddit_name,
        "score": score,
        "author": author,
        # Copy this whole caption into TikTok's "Caption" box.
        "caption": f"{title}\n\n{' '.join(hashtags)}",
        "hashtags": hashtags,
        # Same list under the key you asked for, so it's easy to spot in the
        # artifact zip (tiktok_tags) alongside the YouTube-style metadata.
        "tiktok_tags": hashtags,
        "upload_settings": {
            "visibility": "Only me (private) — switch to Public after you review",
            "ai_generated_content_label": "ON — required (AI voiceover)",
            "allow_comments": True,
            "allow_duet": False,
            "allow_stitch": False,
        },
        "schedule": {
            "cadence": "2 posts per day",
            "suggested_post_time_utc": suggested_slot.strftime("%Y-%m-%d %H:%M UTC"),
            "slots_utc": _tiktok_slot_times(),
            "max_ahead_days": 10,
            "note": ("Times are UTC — TikTok Studio shows your LOCAL time, so convert "
                     "before scheduling. Max 10 days ahead. If two files suggest "
                     "the same slot, push the second to the next slot."),
        },
    }
    tiktok_path = video_path.replace(".mp4", "_tiktok.json")
    with open(tiktok_path, "w", encoding="utf-8") as f:
        json.dump(tiktok_meta, f, indent=2, ensure_ascii=False)
    print(f"   📝 TikTok metadata saved: {tiktok_path} (suggested slot: {tiktok_meta['schedule']['suggested_post_time_utc']})")
    return metadata_path


# ============================================================
# TIKTOK SCHEDULE SUGGESTIONS (2 posts/day, tracked across runs)
# ============================================================

# State file (committed like used_story_ids.json) so every new video gets the
# NEXT free slot of a twice-a-day cadence instead of colliding with earlier
# videos. The runner is on UTC, so slots are UTC — TikTok Studio shows local
# time, and the JSON says to convert.
TIKTOK_SCHEDULE_STATE = "tiktok_schedule_state.json"


def _tiktok_slot_times():
    """The two post slots per day (UTC). Override with TIKTOK_SCHEDULE_TIMES,
    e.g. "10:00,20:00" in the workflow env."""
    raw = os.environ.get("TIKTOK_SCHEDULE_TIMES", "12:00,18:00")
    slots = [t.strip() for t in raw.split(",") if t.strip()]
    return slots or ["12:00", "18:00"]


def _next_tiktok_slot():
    """Return the next suggested publish datetime (UTC) for a new video."""
    slots = _tiktok_slot_times()
    idx = 0
    try:
        if os.path.exists(TIKTOK_SCHEDULE_STATE):
            with open(TIKTOK_SCHEDULE_STATE, encoding="utf-8") as f:
                idx = int(json.load(f).get("next_index", 0))
    except Exception:
        pass
    now = datetime.datetime.now(datetime.timezone.utc)
    idx0 = idx
    candidate = None
    while idx < idx0 + 60:
        slot = slots[idx % len(slots)]
        try:
            hh, mm = slot.split(":")
            day = now.date() + datetime.timedelta(days=idx // len(slots))
            candidate = datetime.datetime(day.year, day.month, day.day,
                                          int(hh), int(mm), tzinfo=datetime.timezone.utc)
        except ValueError:
            candidate = now + datetime.timedelta(days=1)
        if candidate > now + datetime.timedelta(hours=1):
            break
        idx += 1
    try:
        with open(TIKTOK_SCHEDULE_STATE, "w", encoding="utf-8") as f:
            json.dump({"next_index": idx + 1}, f)
    except Exception:
        pass
    return candidate or (now + datetime.timedelta(days=1))

def clean_script_for_tts(text):
    """Clean the script before sending to ElevenLabs.

    Delegates to tts_clean.clean_for_tts (the single source of truth):
      * "ale" -> "" (narrator says nothing wherever it appears)
      * casual speech normalized to natural phrases ("gonna" -> "going to")
      * Reddit acronyms spoken out ("AITAH" -> "Am I the jerk")
      * markdown/"[TEST]"-style artifacts stripped (no "asterisk asterisk")
      * "I'm" -> "I am" (deterministic glottal-stop "ale" fix)
    Also applied inside generate_voiceover as the last line before synthesis.
    """
    return clean_for_tts(text)

# ============================================================
# HELPER: CLEANUP INTERMEDIATE FILES
# ============================================================

def cleanup_intermediate_files(base_path):
    """Delete intermediate files created during captioning."""
    print(f"   🗑️ Cleaning up intermediate files for: {os.path.basename(base_path)}")
    
    patterns = [
        f"{base_path}.mp4",                    # Original video (no captions)
        f"{base_path}_extracted.mp3",          # Extracted audio
        f"{base_path}_extracted.srt",          # SRT file
        f"{base_path}_captioned_*.mp4.tmp",    # Temp files
    ]
    
    deleted_count = 0
    for pattern in patterns:
        for f in glob.glob(pattern):
            if os.path.exists(f) and os.path.isfile(f):
                try:
                    os.remove(f)
                    print(f"      🗑️ Deleted: {os.path.basename(f)}")
                    deleted_count += 1
                except Exception as e:
                    print(f"      ⚠️ Could not delete: {os.path.basename(f)} - {e}")
    
    if deleted_count == 0:
        print("      ℹ️ No intermediate files found to clean up")


def final_cleanup(video_path):
    """Delete all extracted and temporary files associated with the final video."""
    base = video_path.replace(".mp4", "")
    patterns = [
        f"{base}_extracted.mp3",
        f"{base}_extracted.srt",
        f"{base}_captioned_*.mp4.tmp",
    ]
    for pattern in patterns:
        for f in glob.glob(pattern):
            if os.path.exists(f) and os.path.isfile(f):
                try:
                    os.remove(f)
                except:
                    pass


# ============================================================
# HELPER: CONCATENATE CLIPS
# ============================================================

def concat_clips(clip_paths, output_path):
    """Concatenate multiple MP4 clips using FFmpeg's concat demuxer."""
    if len(clip_paths) == 1:
        subprocess.run(['ffmpeg', '-y', '-i', clip_paths[0], '-c', 'copy', output_path], check=True)
        return output_path

    list_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
    for clip in clip_paths:
        list_file.write(f"file '{os.path.abspath(clip)}'\n")
    list_file.close()

    cmd = [
        'ffmpeg', '-y',
        '-f', 'concat',
        '-safe', '0',
        '-i', list_file.name,
        '-c', 'copy',
        output_path
    ]
    subprocess.run(cmd, check=True)
    os.unlink(list_file.name)
    return output_path


# ============================================================
# TASK: generate_single_video
# ============================================================

def generate_single_video(title, script, part_label=None, topic=None,
                          include_title_in_script=True, subreddit=None,
                          voice_id=None, intro_frame=None):
    """Generate a single video with proper Part 1/Part 2 voiceover handling."""
    
    # Build the script with proper Part handling
    if part_label == "Part 2":
        full_script = f"{part_label}. {title}. {script}"
        print(f"   📝 Part 2 script: '{part_label}. {title}'")
    elif include_title_in_script:
        full_script = f"{title}. {script}"
        print(f"   📝 Prepended title to script: '{title}'")
    else:
        full_script = script
    
    # --- CLEAN THE SCRIPT FOR TTS (fix "I'm ale" and other glitches) ---
    # clean_script_for_tts expands "I'm" -> "I am" (deterministic "ale" fix)
    # and strips stray "ale" fillers; the caption step merges the whispered
    # "I am" cues back to "I'm" so on-screen captions look unchanged.
    full_script = clean_script_for_tts(full_script)

    audio_path, _ = generate_voiceover(full_script, voice_id=voice_id)
    print(f"🎙️ Voiceover saved to: {audio_path}")

    audio_duration = get_duration(audio_path)
    # FIXED: compile_video() extracts EXTRACT_FACTOR x the audio length of
    # footage. Supplying only 1x meant the sped-up video ended BEFORE the
    # narration and -shortest cut the last ~30% of every story. Supply the
    # exact amount the compiler needs (same constant, always in sync).
    segment_path = get_next_segment(audio_duration * EXTRACT_FACTOR)
    print(f"🎬 Using segment: {segment_path}")

    final_video_path, final_audio_path = compile_video(
        video_paths=[segment_path],
        audio_path=audio_path,
        script=full_script,
        subtitle_path=None,
        intro_frame=intro_frame,
        title=title,
        part_label=part_label
    )
    
    # Returns (video, raw_voiceover, final_audio_track). The final audio is
    # the sped-up + music track actually inside the video — the caption step
    # uses it so the burned-in captions stay in sync with the narrator.
    return final_video_path, audio_path, final_audio_path


# ============================================================
# TASK: generate_video_from_reddit
# ============================================================

@app.task
def generate_video_from_reddit(subreddit=None, mark_used=True, force_real=False):
    """Generate a video using real Reddit stories from your local data."""
    try:
        if DEBUG_MODE:
            print("\n🔬 DEBUG MODE ACTIVE")
            print("   ⚠️ Stories will NOT be marked as used")
            print("   🏷️ Titles will have [TEST] prefix")
            print("   📂 Using debug data copy\n")
        else:
            print("\n🚀 PRODUCTION MODE ACTIVE")
            print("   ✅ Stories will be marked as used after generation\n")
        
        stats = story_loader.get_stats()
        print(f"📊 Available stories: {stats['unused_stories']} unused out of {stats['total_stories']} total")
        
        if stats['unused_stories'] <= STORY_REFILL_THRESHOLD:
            print("\n⚠️⚠️  STORY BANK LOW — only "
                  f"{stats['unused_stories']} unused stories left!")
            print("    Run 'python fetch_stories.py' on your laptop (or wait for its")
            print("    weekly schedule) to auto-refill before the bank runs dry.\n")
        
        if stats['unused_stories'] == 0:
            return {
                "status": "error",
                "detail": "No unused stories available! Run story_manager.py to add more stories."
            }
        
        story = story_loader.get_random_story(subreddit, force_real=force_real)
        
        if not story:
            return {
                "status": "error",
                "detail": f"No unused stories available in {'r/' + subreddit if subreddit else 'any subreddit'}"
            }
        
        title = story.get('title', 'No Title')
        story_text = story.get('story', '')
        subreddit_name = story.get('subreddit', 'unknown')
        story_id = story.get('story_id') or story.get('id', 'unknown')
        score = story.get('score', 0)
        author = story.get('author', 'unknown')
        
        # --- ADAPT REDDIT STORY (Generate Hook + Normalize Slang) ---
        print(f"\n🪝 Generating hook and normalizing slang...")
        adapted = adapt_reddit_story(title, story_text, use_hook=True)
        
        hook = adapted.get('hook')
        normalized_title = adapted.get('normalized_title')
        story_text = adapted['script']
        
        if hook:
            title = hook
            print(f"   🪝 Hook: {hook}")
        else:
            title = normalized_title or title
        
        # FIXED: the [TEST] prefix was only ever PRINTED, never applied — a
        # test-mode run could post a video with a real (non-test) title, and
        # the dashboard/YouTube gave no hint it was a test. Apply it for real
        # so test uploads are unmistakable (they're still private).
        if DEBUG_MODE:
            title = f"[TEST] {title}"
        
        print(f"   📝 Normalized title: {title}")
        
        # --- GENDER DETECTION ---
        detected_gender = gender_detector.detect_gender(
            username=author,
            subreddit=subreddit_name,
            story_text=story_text
        )
        
        selected_voice = gender_detector.get_voice_by_gender(
            gender=detected_gender,
            female_voice_id=FEMALE_VOICE_ID,
            male_voice_id=MALE_VOICE_ID
        )
        
        voice_display = "Sarah (Female)" if detected_gender == 'female' else "Brian (Male)"
        if detected_gender is None:
            voice_display = "Brian (Male - Default)"
        
        print(f"\n🎤 VOICE SELECTION:")
        print(f"   👤 Author: {author}")
        print(f"   📂 Subreddit: {subreddit_name}")
        print(f"   🎯 Detected gender: {detected_gender if detected_gender else 'Unknown (using default)'}")
        print(f"   🎙️ Selected voice: {voice_display}")
        
        # --- COMMENT EXTRACTION ---
        comment_script = story.get('comment_script', '')
        
        if not comment_script and 'top_comments' in story and story['top_comments']:
            from script_gen import build_comment_script
            comments = story['top_comments']
            if comments:
                comment_script, reason = build_comment_script(
                    comments=comments,
                    max_comment_length=500,
                    max_comments=2
                )
                print(f"   💬 {reason}")

        if not comment_script and 'comments' in story and story['comments']:
            comments = story['comments']
            if isinstance(comments, list) and comments:
                from script_gen import build_comment_script
                comment_script, reason = build_comment_script(
                    comments=comments,
                    max_comment_length=600,
                    max_comments=2
                )
                print(f"   💬 {reason} (from 'comments' field)")

        if not comment_script:
            print(f"   💬 No valid comments found - skipping comments for this video")
        
        #--- SPLIT LOGIC ---
        MAX_CHARS_PER_VIDEO = 12000
        MIN_PART_2_CHARS = 2000

        full_narration = f"{story_text} {comment_script}" if comment_script else story_text
        needs_split = len(full_narration) > MAX_CHARS_PER_VIDEO

        if needs_split:
            split_point = max(int(len(story_text) * 0.5), len(story_text) - 1500)
            
            for char in ['. ', '? ', '! ', '\n\n']:
                last_pos = story_text[:split_point].rfind(char)
                if last_pos != -1 and last_pos > int(len(story_text) * 0.35):
                    split_point = last_pos + len(char)
                    break
            
            part1_story = story_text[:split_point].strip()
            part2_story = story_text[split_point:].strip()
            
            if len(part2_story) < MIN_PART_2_CHARS:
                part1_script = full_narration
                part2_script = None
                part_count = 1
                print(f"   📝 Story fits in 1 video (Part 2 too short)")
            else:
                if comment_script:
                    part1_script = f"{part1_story} {comment_script}"
                else:
                    part1_script = part1_story
                
                part2_script = f"Continuing the story... {part2_story}"
                part_count = 2
                
                print(f"   📝 Split into 2 parts (Part 1: {len(part1_story)} chars, Part 2: {len(part2_story)} chars)")
        else:
            part1_script = full_narration
            part2_script = None
            part_count = 1
            print(f"   📝 Story fits in 1 video ({len(full_narration)} chars)")
        
        print(f"\n📖 Generating video from r/{subreddit_name}")
        print(f"   📝 Title: {title}")
        print(f"   📝 Story length: {len(story_text)} chars")
        print(f"   🆔 Story ID: {story_id}")
        print(f"   👤 Author: {author}")
        print(f"   ⭐ Score: {score}")
        print(f"   📊 Parts: {part_count}")
        print(f"   🎙️ Voice: {voice_display}")
        
        if DEBUG_MODE:
            print(f"   🔬 This is a TEST video - story will NOT be consumed")
        else:
            print(f"   🚀 This is a PRODUCTION video - story will be marked as used")
        
        # ----- ANIMATED REDDIT FRAME (the intro card) -----
        # The reddit post card is drawn with the REAL story data (subreddit,
        # title, score, comments) and overlaid on the start of every part
        # while the narrator says the title. It's generated once per story
        # and reused by both parts (the same title opens both voiceovers).
        frame_path = None
        try:
            comment_count = None
            if story.get('top_comments'):
                comment_count = len(story['top_comments'])
            elif story.get('comments'):
                comment_count = len(story['comments'])
            frame_dir = os.environ.get('OUTPUT_DIR', '.')
            if not os.path.exists(frame_dir):
                frame_dir = '.'
            frame_path = os.path.join(
                frame_dir, f"reddit_frame_{int(time.time())}.png")
            generate_frame(
                title=title,
                subreddit=subreddit_name,
                username=FRAME_USERNAME,
                score=score,
                comments=comment_count,
                out_path=frame_path,
            )
        except Exception as e:
            print(f"   ⚠️ Reddit frame generation failed (continuing without it): {e}")
            frame_path = None
        
        # ----- GENERATE PART 1 -----
        print("\n🎬 GENERATING PART 1...")
        video_path_1, audio_path_1, final_audio_1 = generate_single_video(
            title=title,
            script=part1_script,
            part_label=None,
            topic=title,
            include_title_in_script=True,
            subreddit=subreddit_name,
            voice_id=selected_voice,
            intro_frame=frame_path
        )
        print(f"✅ Part 1 ready: {video_path_1}")
        
        # ----- ADD CAPTIONS TO PART 1 -----
        if USE_CAPTIONS:
            print("\n🎬 ADDING CAPTIONS TO PART 1...")
            try:
                captioned_path = add_subtitles_to_video(
                    video_path=video_path_1,
                    audio_path=audio_path_1,       # raw voiceover -> whisper transcribes this
                    mux_audio_path=final_audio_1,  # exact audio in the video (sped + music)
                    output_path=video_path_1.replace(".mp4", f"_captioned_{int(time.time())}.mp4"),
                    whisper_model="base",   # more accurate word-by-word captions
                    font_size=16,  # smaller captions (user request) — scaled per frame height
                    speed_factor=VOICE_SPEED,  # timestamps map exactly onto the sped-up audio
                    bold=True,
                    alignment=10,
                    margin_v=90
                )
                
                # Check if a NEW file was created
                if captioned_path and captioned_path != video_path_1 and os.path.exists(captioned_path) and os.path.getsize(captioned_path) > 1000000:
                    base_name = video_path_1.replace(".mp4", "")
                    cleanup_intermediate_files(base_name)
                    video_path_1 = captioned_path
                    print(f"✅ Part 1 captions added: {video_path_1}")
                else:
                    print(f"⚠️ Captioning didn't produce a new file, keeping original")
                    # Ensure we delete the extracted files anyway
                    base_name = video_path_1.replace(".mp4", "")
                    final_cleanup(base_name)
            except Exception as e:
                print(f"⚠️ Captioning failed for Part 1: {e}")
                print("   Keeping original video without captions")
                # Clean up extracted files
                base_name = video_path_1.replace(".mp4", "")
                final_cleanup(base_name)
            finally:
                # The processed audio was already muxed into the video (or the
                # original kept) — free the temp file either way.
                if final_audio_1 and os.path.exists(final_audio_1) and final_audio_1 != audio_path_1:
                    os.remove(final_audio_1)
        
        # ----- GENERATE PART 2 -----
        video_path_2 = None
        if part_count == 2 and part2_script:
            print("\n🎬 GENERATING PART 2...")
            # FIXED: generate_single_video returns (video, voiceover, final_audio) -
            # unpack all three instead of assigning the tuple to video_path_2.
            video_path_2, audio_path_2, final_audio_2 = generate_single_video(
                title=title,
                script=part2_script,
                part_label="Part 2",
                topic=title,
                include_title_in_script=True,
                subreddit=subreddit_name,
                voice_id=selected_voice,
                intro_frame=frame_path
            )
            print(f"✅ Part 2 ready: {video_path_2}")
            
            # ----- ADD CAPTIONS TO PART 2 -----
            if USE_CAPTIONS:
                print("\n🎬 ADDING CAPTIONS TO PART 2...")
                try:
                    captioned_path_2 = add_subtitles_to_video(
                        video_path=video_path_2,
                        audio_path=audio_path_2,       # raw voiceover -> whisper transcribes this
                        mux_audio_path=final_audio_2,  # exact audio in the video (sped + music)
                        output_path=video_path_2.replace(".mp4", f"_captioned_{int(time.time())}.mp4"),
                        whisper_model="base",   # more accurate word-by-word captions
                        font_size=16,  # smaller captions (user request) — scaled per frame height
                        # FIXED: Part 2 was missing speed_factor, so its captions
                        # were timed to the raw voice while the video played the
                        # sped-up track — out of sync.
                        speed_factor=VOICE_SPEED,
                        bold=True,
                        alignment=10,
                        margin_v=90
                    )
                    
                    if captioned_path_2 and captioned_path_2 != video_path_2 and os.path.exists(captioned_path_2) and os.path.getsize(captioned_path_2) > 1000000:
                        base_name = video_path_2.replace(".mp4", "")
                        cleanup_intermediate_files(base_name)
                        video_path_2 = captioned_path_2
                        print(f"✅ Part 2 captions added: {video_path_2}")
                    else:
                        print(f"⚠️ Captioning didn't produce a new file, keeping original")
                        base_name = video_path_2.replace(".mp4", "")
                        final_cleanup(base_name)
                except Exception as e:
                    print(f"⚠️ Captioning failed for Part 2: {e}")
                    print("   Keeping original video without captions")
                    base_name = video_path_2.replace(".mp4", "")
                    final_cleanup(base_name)
                finally:
                    if final_audio_2 and os.path.exists(final_audio_2) and final_audio_2 != audio_path_2:
                        os.remove(final_audio_2)
        
        # ----- SAVE METADATA FOR THE YOUTUBE UPLOAD STEP -----
        print("\n📝 SAVING VIDEO METADATA...")
        # Custom thumbnail matching the reddit-card intro: a still from the
        # video (blurred background) + the sharp card. One per video, so the
        # YouTube custom thumbnail shows the exact intro frame.
        def _make_thumb(video_path):
            if not frame_path:
                return None
            thumb_path = video_path.replace(".mp4", "_thumb.jpg")
            try:
                from make_thumbnail import _make_thumbnail as _mt
                _mt(video_path, frame_path, thumb_path)
                return thumb_path if os.path.exists(thumb_path) else None
            except Exception as e:
                print(f"   ⚠️ Thumbnail skipped: {e}")
                return None

        thumb_1 = _make_thumb(video_path_1)
        save_metadata(video_path_1, title, subreddit_name, score, author, thumb_1)
        if video_path_2 and isinstance(video_path_2, str):
            thumb_2 = _make_thumb(video_path_2)
            save_metadata(video_path_2, f"{title} (Part 2)", subreddit_name, score, author, thumb_2)

        # Mark the story as used (only in production mode)
        marked = False
        if mark_used and not DEBUG_MODE:
            marked = story_loader.mark_story_used(story_id)
        elif DEBUG_MODE:
            print(f"   🔬 DEBUG: Story {story_id} was NOT marked as used")
        
        return {
            "status": "success",
            "part_1_url": video_path_1,
            "part_2_url": video_path_2,
            "part_count": part_count,
            "story_id": story_id,
            "title": title,
            "subreddit": subreddit_name,
            "author": author,
            "score": score,
            "has_comments": bool(comment_script),
            "comment_count": len(story.get('top_comments', [])),
            "debug_mode": DEBUG_MODE,
            "marked_used": marked,
            "detected_gender": detected_gender,
            "voice_used": selected_voice,
            "captions_added": bool(USE_CAPTIONS)
        }
        
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "detail": str(e)}


# ============================================================
# TASK: generate_videos_batch
# ============================================================

@app.task
def generate_videos_batch(subreddit=None, count=5, force_real=False):
    """Generate multiple videos in one go."""
    print(f"\n📦 BATCH GENERATION: {count} videos")
    print(f"   Debug mode: {'ON (stories safe)' if DEBUG_MODE else 'OFF (stories consumed)'}")
    print(f"   Captions: {'ON' if USE_CAPTIONS else 'OFF'}")
    
    results = []
    successful = 0
    
    for i in range(count):
        print(f"\n{'='*50}")
        print(f"🎬 Generating video {i+1}/{count}")
        print('='*50)
        
        result = generate_video_from_reddit(subreddit, mark_used=True, force_real=force_real)
        results.append(result)
        
        if result.get('status') == 'success':
            successful += 1
        else:
            print(f"⚠️ Stopped early due to error: {result.get('detail')}")
            break
    
    return {
        "status": "success",
        "total_attempted": count,
        "total_generated": successful,
        "debug_mode": DEBUG_MODE,
        "captions_enabled": USE_CAPTIONS,
        "results": results
    }


# ============================================================
# TASK: generate_video (Original - Keep for fallback)
# ============================================================

@app.task
def generate_video(topic=None, subreddit=None, use_reddit=False):
    """Original task - kept for compatibility."""
    try:
        if use_reddit:
            used_subreddit, title, raw_story = get_reddit_story_with_fallback()
            print(f"📖 Fetched Reddit post from r/{used_subreddit}: {title}")
            adapted = adapt_reddit_story(title, raw_story)
            part_count = adapted['part_count']
            part1_script = adapted['script']
            part2_script = adapted.get('part2_script', None)
            print(f"📝 Part 1: {len(part1_script)} words")
            if part2_script:
                print(f"📝 Part 2: {len(part2_script)} words")
            subreddit_name = used_subreddit
        else:
            part1_script = generate_story_script(topic)
            title = topic
            part_count = 1
            part2_script = None
            subreddit_name = topic if topic else "AITAH"
        
        print("🎬 GENERATING PART 1...")
        # FIXED: unpack the (video, voiceover, final_audio) tuple
        video_path_1, audio_path_1, _final_audio_1 = generate_single_video(
            title=title,
            script=part1_script,
            part_label="Part 1" if part_count == 2 else None,
            topic=topic,
            include_title_in_script=True,
            subreddit=subreddit_name
        )
        print(f"✅ Part 1 ready: {video_path_1}")
        
        video_path_2 = None
        if part2_script:
            print("🎬 GENERATING PART 2...")
            video_path_2, audio_path_2, _final_audio_2 = generate_single_video(
                title=title,
                script=part2_script,
                part_label="Part 2",
                topic=topic,
                include_title_in_script=True,
                subreddit=subreddit_name
            )
            print(f"✅ Part 2 ready: {video_path_2}")
        
        return {
            "status": "success",
            "part_1_url": video_path_1,
            "part_2_url": video_path_2,
            "part_count": part_count
        }
    except Exception as e:
        print(f"❌ ERROR: {e}")
        return {"status": "error", "detail": str(e)}
