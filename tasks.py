import os
import time
import tempfile
import subprocess
from celery import Celery
from config import FAST_MODE
from script_gen import generate_story_script, adapt_reddit_story
from voiceover import generate_voiceover
from broll_fetcher import fetch_gameplay_footage
from video_compile import compile_video          # <-- NEW signature
from reddit_fetcher import get_reddit_story_with_fallback

# Celery setup
app = Celery('tasks', broker='redis://localhost:6379/0', backend='redis://localhost:6379/0')

# -------------------------------------------------------------------
# Helper: concatenate video clips into a single MP4 (no re-encoding)
# -------------------------------------------------------------------
def concat_clips(clip_paths, output_path):
    """
    Concatenate multiple MP4 clips using FFmpeg's concat demuxer.
    This is fast (copy‑codec) and preserves quality.
    """
    if len(clip_paths) == 1:
        # Just copy the single clip
        subprocess.run(['ffmpeg', '-y', '-i', clip_paths[0], '-c', 'copy', output_path], check=True)
        return output_path

    # Create a concat file list
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
def generate_single_video(title, script, part_label=None, topic=None, include_title_in_script=True):
    if include_title_in_script:
        full_script = f"{title} {part_label}. {script}" if part_label and part_label != "Part 1" else f"{title}. {script}"
    else:
        full_script = script

    audio_path, subtitle_path = generate_voiceover(full_script)
    print(f"🎙️ Voiceover saved to: {audio_path}")

    # Get audio duration
    audio_duration = get_duration(audio_path)

    # Get next segment from large videos
    segment_path = get_next_segment(audio_duration)
    print(f"🎬 Using segment: {segment_path}")

    # Compile
    final_output = f"final_{title.replace(' ', '_')}_{part_label or 'part1'}.mp4"
    BG_MUSIC = 'assets/music/my_action_track.mp3'

    final_video_path = compile_video(
        video_path=segment_path,
        audio_mp3=audio_path,
        output_path=final_output,
        subtitle_ass=subtitle_path,
        background_music=BG_MUSIC if os.path.exists(BG_MUSIC) else None,
        voice_volume=2.0,
        bg_volume=0.3,
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
