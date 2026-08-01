import fal_client
import os
import time

def add_subtitles_to_video(video_url: str, output_path: str, preset: str = "glass"):
    """
    Add burned-in subtitles to a video using the VEED Subtitles API on fal.ai.
    Preset options: 'glass', 'karaoke', 'whisper', etc.
    """
    print(f"🎬 Adding subtitles to video: {video_url}")
    
    # Submit the request to the API
    result = fal_client.run(
        "veed/subtitles",
        arguments={
            "video_url": video_url,
            "preset": preset
        }
    )
    
    # The result contains the video URL with subtitles
    subtitle_video_url = result.get("video", {}).get("url")
    
    if not subtitle_video_url:
        raise Exception("Failed to get subtitled video URL from fal.ai")
    
    # Download the video to your local output path
    import requests
    response = requests.get(subtitle_video_url)
    with open(output_path, "wb") as f:
        f.write(response.content)
    
    print(f"✅ Subtitled video saved to: {output_path}")
    return output_path
