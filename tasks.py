import os
import time
import tempfile
import subprocess
from celery import Celery
from config import FAST_MODE, DEBUG_MODE, USE_CAPTIONS  # <-- Added USE_CAPTIONS
from voiceover import generate_voiceover
from drive_clip_manager import get_next_segment
from broll_fetcher import fetch_gameplay_footage
from video_compile import compile_video, get_duration
from reddit_fetcher import get_reddit_story_with_fallback
from script_gen import generate_story_script, adapt_reddit_story
from reddit_story_loader import RedditStoryLoader
from caption_utils import add_subtitles_to_video  # <-- Already imported
from gender_detector import GenderDetector  # <-- Need to import this

# ElevenLabs Voice IDs
MALE_VOICE_ID = "nPczCjzI2devNBz1zQrb"      # Brian
FEMALE_VOICE_ID = "EXAVITQu4vr4xnSDxMaL"    # Sarah
DEFAULT_VOICE_ID = MALE_VOICE_ID

# Initialize gender detector
gender_detector = GenderDetector(default_voice="male")

# Celery setup
app = Celery('tasks', broker='redis://localhost:6379/0', backend='redis://localhost:6379/0')

# Initialize the story loader
STORY_DATA_PATH = "reddit_stories"
story_loader = RedditStoryLoader(STORY_DATA_PATH, debug_mode=DEBUG_MODE)

# -------------------------------------------------------------------
# Helper: concatenate video clips into a single MP4 (no re-encoding)
# -------------------------------------------------------------------
def concat_clips(clip_paths, output_path):
    """
    Concatenate multiple MP4 clips using FFmpeg's concat demuxer.
    This is fast (copy‑codec) and preserves quality.
    """
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

# ===========================
# TASK: generate_single_video
# ===========================
def generate_single_video(title, script, part_label=None, topic=None, 
                          include_title_in_script=True, subreddit=None,
                          voice_id=None):  # <-- Added voice_id parameter
    """
    Generate a single video with proper Part 1/Part 2 voiceover handling.
    
    Part 1: Narrator says "Title" (no "Part 1")
    Part 2: Narrator says "Part 2. Title" (reminds viewers)
    """
    
    # Build the script with proper Part handling
    if part_label == "Part 2":
        full_script = f"{part_label}. {title}. {script}"
        print(f"   📝 Part 2 script: '{part_label}. {title}'")
    elif part_label == "Part 1":
        full_script = f"{title}. {script}"
        print(f"   📝 Part 1 script: '{title}' (no 'Part 1' spoken)")
    elif include_title_in_script:
        full_script = f"{title}. {script}"
        print(f"   📝 Prepended title to script: '{title}'")
    else:
        full_script = script

    # Generate voiceover with the selected voice
    audio_path, _ = generate_voiceover(full_script, voice_id=voice_id)  # <-- Pass voice_id
    print(f"🎙️ Voiceover saved to: {audio_path}")

    # Get audio duration
    audio_duration = get_duration(audio_path)

    # Get next segment from Drive
    segment_path = get_next_segment(audio_duration)
    print(f"🎬 Using segment: {segment_path}")

    # No title card
    title_card_path = None

    # Compile video with NO intro frame
    final_video_path = compile_video(
        video_paths=[segment_path],
        audio_path=audio_path,
        script=full_script,
        subtitle_path=None,
        intro_frame=title_card_path,
        title=title,
        part_label=part_label
    )
    
    return final_video_path

# ===========================
# TASK: generate_video_from_reddit
# ===========================
@app.task
def generate_video_from_reddit(subreddit=None, mark_used=True, force_real=False):
    """
    Generate a video using real Reddit stories from your local data.
    Supports splitting long stories into Part 1 and Part 2.
    """
    try:
        # Check if we're in debug mode
        if DEBUG_MODE:
            print("\n🔬 DEBUG MODE ACTIVE")
            print("   ⚠️ Stories will NOT be marked as used")
            print("   🏷️ Titles will have [TEST] prefix")
            print("   📂 Using debug data copy\n")
        else:
            print("\n🚀 PRODUCTION MODE ACTIVE")
            print("   ✅ Stories will be marked as used after generation\n")
        
        # Get stats first
        stats = story_loader.get_stats()
        print(f"📊 Available stories: {stats['unused_stories']} unused out of {stats['total_stories']} total")
        
        if stats['unused_stories'] == 0:
            return {
                "status": "error",
                "detail": "No unused stories available! Run story_manager.py to add more stories."
            }
        
        # Get a random unused story
        story = story_loader.get_random_story(subreddit, force_real=force_real)
        
        if not story:
            return {
                "status": "error",
                "detail": f"No unused stories available in {'r/' + subreddit if subreddit else 'any subreddit'}"
            }
        
        # Extract story data
        title = story.get('title', 'No Title')
        story_text = story.get('story', '')
        subreddit_name = story.get('subreddit', 'unknown')
        story_id = story.get('story_id') or story.get('id', 'unknown')
        score = story.get('score', 0)
        author = story.get('author', 'unknown')
        url = story.get('url', '')
        
        # --- GENDER DETECTION FOR VOICE SELECTION ---
        detected_gender = gender_detector.detect_gender(
            username=author,
            subreddit=subreddit_name,
            story_text=story_text
        )
        
        # Select the appropriate voice ID
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
        
        # Check for comment script
        comment_script = story.get('comment_script', '')
        
        if not comment_script and 'top_comments' in story and story['top_comments']:
            comments = story['top_comments']
            if comments:
                comment_lines = ["The top comments say:"]
                for i, comment in enumerate(comments[:5], 1):
                    body = comment.get('body', '').strip()
                    comment_author = comment.get('author', 'user')
                    if body:
                        comment_lines.append(f"Number {i} comment from {comment_author}: {body}")
                comment_script = " ".join(comment_lines)
                print(f"   💬 Built comment script from {len(comments)} top comments")
        
        # --- SPLIT LOGIC ---
        full_narration = f"{story_text} {comment_script}" if comment_script else story_text
        MAX_CHARS_PER_VIDEO = 2800
        needs_split = len(full_narration) > MAX_CHARS_PER_VIDEO
        
        if needs_split and comment_script:
            if len(story_text) <= MAX_CHARS_PER_VIDEO:
                truncated_story = story_text
                if len(truncated_story) + len(comment_script) > MAX_CHARS_PER_VIDEO:
                    available_space = MAX_CHARS_PER_VIDEO - len(comment_script) - 50
                    cut_point = available_space
                    for char in ['. ', '? ', '! ']:
                        last_pos = truncated_story[:cut_point].rfind(char)
                        if last_pos != -1 and last_pos > cut_point * 0.6:
                            cut_point = last_pos + len(char)
                            break
                    truncated_story = truncated_story[:cut_point]
                
                part1_script = f"{truncated_story} {comment_script}"
                part2_script = None
                part_count = 1
                print(f"   📝 Story + comments fit in 1 video (truncated story slightly)")
            else:
                split_point = MAX_CHARS_PER_VIDEO - len(comment_script) - 50
                if split_point < 500:
                    split_point = 500
                
                for char in ['. ', '? ', '! ', '\n\n']:
                    last_pos = story_text[:split_point].rfind(char)
                    if last_pos != -1 and last_pos > split_point * 0.6:
                        split_point = last_pos + len(char)
                        break
                
                part1_story = story_text[:split_point].strip()
                part2_story = story_text[split_point:].strip()
                
                part1_script = f"{part1_story} {comment_script}" if comment_script else part1_story
                part2_script = f"Continuing the story... {part2_story}" if part2_story else None
                part_count = 2 if part2_script else 1
                
                print(f"   📝 Story split into 2 parts (comments in Part 1)")
                if part2_script:
                    print(f"   📝 Part 2: {len(part2_script)} chars (continues story)")
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
        
        # ----- GENERATE PART 1 -----
        print("\n🎬 GENERATING PART 1...")
        video_path_1 = generate_single_video(
            title=title,
            script=part1_script,
            part_label=None,
            topic=title,
            include_title_in_script=True,
            subreddit=subreddit_name,
            voice_id=selected_voice
        )
        print(f"✅ Part 1 ready: {video_path_1}")
        
        # ----- ADD CAPTIONS TO PART 1 (if enabled) -----
        if USE_CAPTIONS:
            print("\n🎬 ADDING CAPTIONS TO PART 1...")
            try:
                captioned_video_path_1 = add_subtitles_to_video(
                    video_url=video_path_1,  # This will need to be a URL or local path
                    output_path=video_path_1.replace(".mp4", "_captioned.mp4"),
                    preset="glass"
                )
                video_path_1 = captioned_video_path_1
                print(f"✅ Part 1 captions added: {video_path_1}")
            except Exception as e:
                print(f"⚠️ Captioning failed for Part 1: {e}")
                print("   Continuing without captions...")
        
        # ----- GENERATE PART 2 (if needed) -----
        video_path_2 = None
        if part_count == 2 and part2_script:
            print("\n🎬 GENERATING PART 2...")
            video_path_2 = generate_single_video(
                title=title,
                script=part2_script,
                part_label="Part 2",
                topic=title,
                include_title_in_script=True,
                subreddit=subreddit_name,
                voice_id=selected_voice
            )
            print(f"✅ Part 2 ready: {video_path_2}")
            
            # ----- ADD CAPTIONS TO PART 2 (if enabled) -----
            if USE_CAPTIONS:
                print("\n🎬 ADDING CAPTIONS TO PART 2...")
                try:
                    captioned_video_path_2 = add_subtitles_to_video(
                        video_url=video_path_2,
                        output_path=video_path_2.replace(".mp4", "_captioned.mp4"),
                        preset="glass"
                    )
                    video_path_2 = captioned_video_path_2
                    print(f"✅ Part 2 captions added: {video_path_2}")
                except Exception as e:
                    print(f"⚠️ Captioning failed for Part 2: {e}")
                    print("   Continuing without captions...")
        
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

# ===========================
# TASK: generate_videos_batch
# ===========================
@app.task
def generate_videos_batch(subreddit=None, count=5, force_real=False):
    """
    Generate multiple videos in one go.
    """
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

# ===========================
# TASK: generate_video (Original - Keep for fallback)
# ===========================
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
        video_path_1 = generate_single_video(
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
            video_path_2 = generate_single_video(
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
