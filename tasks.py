import os
import time
import tempfile
from celery import Celery
from config import FAST_MODE
from script_gen import generate_story_script, adapt_reddit_story
from voiceover import generate_voiceover
from broll_fetcher import fetch_gameplay_footage
from video_compile import compile_video, create_reddit_frame
from reddit_fetcher import get_reddit_story_with_fallback

# Celery setup
app = Celery('tasks', broker='redis://localhost:6379/0', backend='redis://localhost:6379/0')

# ===========================
# TASK: generate_single_video
# ===========================
def generate_single_video(title, script, part_label=None, topic=None, include_title_in_script=True):
    # Build the spoken script
    if include_title_in_script:
        if part_label and part_label != "Part 1":
            # For Part 2+, narrator says: "Title Part X. [story]"
            full_script = f"{title} {part_label}. {script}"
        else:
            # For Part 1 or single-part videos: narrator says: "Title. [story]"
            full_script = f"{title}. {script}"
        print(f"   📝 Prepended title to script: '{title}'")
    else:
        full_script = script
    
    # Generate voiceover
    audio_path, subtitle_path = generate_voiceover(full_script)
    print(f"🎙️ Voiceover saved to: {audio_path}")
    
    # Fetch gameplay footage
    video_paths = fetch_gameplay_footage(title if title else topic)
    print(f"🎬 Downloaded {len(video_paths)} clips")
    
    # No intro frame (removed)
    intro_frame_path = None
    print(f"🖼️ Intro frame: skipped (Reddit frame removed)")
    
    # Compile video
    final_video_path = compile_video(
        video_paths, 
        audio_path, 
        full_script, 
        subtitle_path, 
        intro_frame_path,   # None
        title,
        part_label=part_label
    )
    return final_video_path

# =====================
# TASK: generate_video
# =====================
@app.task
def generate_video(topic=None, subreddit=None, use_reddit=False):
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
        else:
            part1_script = generate_story_script(topic)
            title = topic
            part_count = 1
            part2_script = None
        
        print("🎬 GENERATING PART 1...")
        video_path_1 = generate_single_video(
            title=title,
            script=part1_script,
            part_label="Part 1" if part_count == 2 else None,
            topic=topic,
            include_title_in_script=True
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
                include_title_in_script=True
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