#!/usr/bin/env python3
"""prepare_animations.py — Remove green screen from subscribe/like animations
and generate the ground shadow. Run once to create transparent overlay assets.

Green screen color is RGB(66, 154, 55) — NOT pure green (0x00FF00).
ffmpeg's colorkey filter doesn't work on these compressed files because
yuv420p ruins the green channel. Per-pixel PIL detection is needed.
"""

import os
import sys
import subprocess
import shutil
import tempfile
from pathlib import Path

try:
    from PIL import Image, ImageFilter, ImageDraw
except ImportError:
    print("Installing Pillow...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow", "-q"])
    from PIL import Image, ImageFilter, ImageDraw

ANIM_DIR = Path(__file__).parent / "assets" / "animations"
OUTPUT_DIR = Path(__file__).parent / "assets" / "overlays"
TEMP_DIR = Path(tempfile.mkdtemp(prefix="anim_"))

# Green screen thresholds (RGB 66, 154, 55 is the background)
GREEN_R_MAX = 130
GREEN_THRESHOLD = 15   # G - R > 15 AND G - B > 15
FRINGE_THRESHOLD = 8   # softer fringe detection


def is_green_pixel(r, g, b):
    """Detect green background pixel (strict)."""
    return (g - r > GREEN_THRESHOLD) and (g - b > GREEN_THRESHOLD) and (g > 60)


def is_green_fringe(r, g, b):
    """Detect green fringe/halo around edges (softer)."""
    return ((g - r > FRINGE_THRESHOLD) and (g - b > FRINGE_THRESHOLD) and
            (g > 50) and (r < GREEN_R_MAX) and (b < 100))


def remove_green_screen(input_video, output_name, crop_box=None):
    """Extract frames, remove green screen, encode as transparent MOV."""
    frames_dir = TEMP_DIR / f"{output_name}_frames"
    frames_dir.mkdir(exist_ok=True)
    
    print(f"   [STEP] Processing {output_name}...")
    
    # Extract frames as PNG
    cmd = ['ffmpeg', '-y', '-i', str(input_video)]
    if crop_box:
        x, y, w, h = crop_box
        cmd += ['-vf', f'crop={w}:{h}:{x}:{y}']
    cmd += ['-pix_fmt', 'rgba', str(frames_dir / 'frame_%04d.png')]
    
    print(f"   [EXTRACT] Extracting frames...")
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    
    # Remove green screen from each frame
    frame_files = sorted(frames_dir.glob('frame_*.png'))
    print(f"   [GREEN] Removing green from {len(frame_files)} frames...")
    
    for i, frame_path in enumerate(frame_files):
        img = Image.open(frame_path).convert('RGBA')
        pixels = img.load()
        w, h = img.size
        
        for y in range(h):
            for x in range(w):
                r, g, b, a = pixels[x, y]
                if is_green_pixel(r, g, b) or is_green_fringe(r, g, b):
                    pixels[x, y] = (0, 0, 0, 0)  # transparent
        
        img.save(frame_path)
        
        if (i + 1) % 50 == 0:
            print(f"      Frame {i+1}/{len(frame_files)}")
    
    print(f"   [OK] Green removed from all frames")
    
    # Encode as MOV with alpha (qtrle preserves transparency)
    output_mov = OUTPUT_DIR / f"{output_name}_transparent.mov"
    cmd = [
        'ffmpeg', '-y',
        '-framerate', '30',
        '-i', str(frames_dir / 'frame_%04d.png'),
        '-c:v', 'qtrle',
        '-pix_fmt', 'argb',
        str(output_mov)
    ]
    
    print(f"   [ENCODE] Encoding transparent MOV...")
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    
    # Clean up frames
    shutil.rmtree(frames_dir)
    
    print(f"   [OK] Created: {output_mov.name}")
    return output_mov


def generate_ground_shadow(width=500, height=40):
    """Generate elliptical ground shadow with alpha gradient."""
    print("   [SHADOW] Generating ground shadow...")
    
    # Create shadow image with transparency
    shadow = Image.new('RGBA', (width, height + 20), (0, 0, 0, 0))
    draw = ImageDraw.Draw(shadow)
    
    # Draw elliptical shadow with alpha
    for y in range(height):
        # Alpha falloff: strongest in center, fading at edges
        progress = y / height
        alpha = int(140 * (1 - progress))  # linear falloff
        
        # Draw horizontal line
        draw.line([(10, 10 + y), (width - 10, 10 + y)], fill=(0, 0, 0, alpha))
    
    # Apply Gaussian blur for soft edges
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=12))
    
    # Save as transparent MOV
    shadow_dir = TEMP_DIR / "shadow_frames"
    shadow_dir.mkdir(exist_ok=True)
    
    # Create 30 frames (1 second at 30fps, will be looped)
    for i in range(30):
        shadow.save(shadow_dir / f'frame_{i:04d}.png')
    
    output_mov = OUTPUT_DIR / "shadow_transparent.mov"
    cmd = [
        'ffmpeg', '-y',
        '-framerate', '30',
        '-i', str(shadow_dir / 'frame_%04d.png'),
        '-c:v', 'qtrle',
        '-pix_fmt', 'argb',
        '-t', '1',  # 1 second, will be looped
        str(output_mov)
    ]
    
    subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    shutil.rmtree(shadow_dir)
    
    print(f"   [OK] Created: {output_mov.name}")
    return output_mov


def merge_audio(video_file, audio_file, output_name):
    """Merge animation video with its audio track."""
    output = OUTPUT_DIR / f"{output_name}_final.mp4"
    cmd = [
        'ffmpeg', '-y',
        '-i', str(video_file),
        '-i', str(audio_file),
        '-c:v', 'copy',
        '-c:a', 'aac',
        str(output)
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    print(f"   [OK] Merged: {output.name}")
    return output


def main():
    print("[START] Preparing subscribe/like animations...")
    print(f"   Source: {ANIM_DIR}")
    print(f"   Output: {OUTPUT_DIR}\n")
    
    # Create output directory
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # Check source files exist
    subscribe_video = ANIM_DIR / "subscribe_green.mp4"
    subscribe_audio = ANIM_DIR / "subscribe_green.weba"
    like_video = ANIM_DIR / "like_green.mp4"
    like_audio = ANIM_DIR / "like_green.m4a"
    
    for f in [subscribe_video, subscribe_audio, like_video, like_audio]:
        if not f.exists():
            print(f"[ERROR] Missing: {f}")
            sys.exit(1)
    
    # 1. Remove green screen from subscribe animation
    # Crop to content area: 615x621 at (309, 647)
    print("\n[1/4] Subscribe animation:")
    sub_transparent = remove_green_screen(
        subscribe_video,
        "subscribe",
        crop_box=(309, 647, 615, 621)
    )
    
    # 2. Remove green screen from like animation
    # Crop to content area: 523x491 at (275, 711)
    print("\n[2/4] Like animation:")
    like_transparent = remove_green_screen(
        like_video,
        "like",
        crop_box=(275, 711, 523, 491)
    )
    
    # 3. Generate ground shadow
    print("\n[3/4] Ground shadow:")
    shadow = generate_ground_shadow(width=500, height=40)
    
    # 4. Merge animations with their audio
    print("\n[4/4] Merging audio:")
    merge_audio(sub_transparent, subscribe_audio, "subscribe")
    merge_audio(like_transparent, like_audio, "like")
    
    # Cleanup temp directory
    shutil.rmtree(TEMP_DIR, ignore_errors=True)
    
    print("\n" + "="*50)
    print("[DONE] All animations prepared!")
    print(f"   [OUTPUT] {OUTPUT_DIR}")
    print("\nFiles created:")
    for f in sorted(OUTPUT_DIR.glob('*')):
        print(f"   - {f.name}")


if __name__ == "__main__":
    main()
