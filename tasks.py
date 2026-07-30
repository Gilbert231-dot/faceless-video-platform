import os
import time
import tempfile
import subprocess
from celery import Celery
from config import FAST_MODE, DEBUG_MODE
from voiceover import generate_voiceover
from drive_clip_manager import get_next_segment
from broll_fetcher import fetch_gameplay_footage
from video_compile import compile_video, get_duration
from reddit_fetcher import get_reddit_story_with_fallback
from script_gen import generate_story_script, adapt_reddit_story
from reddit_story_loader import RedditStoryLoader

# Celery setup
app = Celery('tasks', broker='redis://localhost:6379/0', backend='redis://localhost:6379/0')

# Initialize the story loader
STORY_DATA_PATH = "../Get_stories/reddit_stories"  # Adjust as needed
story_loader = RedditStoryLoader(STORY_DATA_PATH, debug_mode=DEBUG_MODE)

# ===========================
# TASK: generate_single_video
# ===========================
def generate_single_video(title, script, part_label=None, topic=None, include_title_in_script=True, subreddit=None):
    """Generate a single video with NO intro/title card."""
    
    if include_title_in_script:
        if part_label and part_label != "Part 1":
            full_script = f"{title} {part_label}. {script}"
        else:
            full_script = f"{title}. {script}"
        print(f"   📝 Prepended title to script: '{title}'")
    else:
        full_script = script

    # Generate voiceover
    audio_path, _ = generate_voiceover(full_script)
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
    
    Args:
        subreddit: Optional specific subreddit to get stories from.
                   If None, picks from all available subreddits.
        mark_used: Whether to mark the story as used after generation.
                   In debug mode, this is ignored (stories are never marked used).
        force_real: If True, use real data even in debug mode.
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
        story_id = story.get('id', 'unknown')
        score = story.get('score', 0)
        author = story.get('author', 'unknown')
        url = story.get('url', '')
        
        # Check for comment script
        comment_script = story.get('comment_script', '')
        
        # If no comment_script, try to build one from top_comments
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
        
        # Combine story with comments
        if comment_script:
            full_narration = f"{story_text} {comment_script}"
            print(f"   💬 Story has comment script ({len(comment_script)} chars)")
        else:
            full_narration = story_text
            print(f"   📝 No comment script available, using story only")
        
        print(f"\n📖 Generating video from r/{subreddit_name}")
        print(f"   📝 Title: {title}")
        print(f"   📝 Story length: {len(story_text)} chars")
        print(f"   🆔 Story ID: {story_id}")
        print(f"   👤 Author: {author}")
        print(f"   ⭐ Score: {score}")
        
        if DEBUG_MODE:
            print(f"   🔬 This is a TEST video - story will NOT be consumed")
        else:
            print(f"   🚀 This is a PRODUCTION video - story will be marked as used")
        
        # Generate the video
        print("\n🎬 GENERATING VIDEO...")
        video_path = generate_single_video(
            title=title,
            script=full_narration,
            part_label=None,
            topic=title,
            include_title_in_script=True,
            subreddit=subreddit_name
        )
        print(f"\n✅ Video ready: {video_path}")
        
        # Mark the story as used (only in production mode)
        marked = False
        if mark_used and not DEBUG_MODE:
            marked = story_loader.mark_story_used(story_id)
        elif DEBUG_MODE:
            print(f"   🔬 DEBUG: Story {story_id} was NOT marked as used")
        
        return {
            "status": "success",
            "video_url": video_path,
            "story_id": story_id,
            "title": title,
            "subreddit": subreddit_name,
            "author": author,
            "score": score,
            "has_comments": bool(comment_script),
            "comment_count": len(story.get('top_comments', [])),
            "debug_mode": DEBUG_MODE,
            "marked_used": marked
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
    
    Args:
        subreddit: Optional specific subreddit
        count: Number of videos to generate
        force_real: If True, use real data even in debug mode
    """
    print(f"\n📦 BATCH GENERATION: {count} videos")
    print(f"   Debug mode: {'ON (stories safe)' if DEBUG_MODE else 'OFF (stories consumed)'}")
    
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
