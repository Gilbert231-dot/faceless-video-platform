#!/usr/bin/env python3
"""apply_ending_overlay.py — Apply subscribe/like ending overlay using transparent .mov files.

Positions reverse-engineered from CapCut edit (0824(1).mp4).
Uses transparent .mov files (green already removed) with proper alpha.
"""

import os
import sys
import subprocess
import tempfile


def get_duration(media_path):
    cmd = [
        'ffprobe', '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', media_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def run_ffmpeg(cmd, timeout=None, label="ffmpeg"):
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
        return True
    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"").decode("utf-8", errors="replace")
        tail = "\n".join(err.splitlines()[-25:]) if err.strip() else "(no stderr)"
        print(f"   [FAIL] {label} failed (exit {e.returncode}). ffmpeg said:\n{tail}")
        raise


def main():
    if len(sys.argv) < 2:
        print("Usage: python apply_ending_overlay.py <video_path>")
        sys.exit(1)

    video_path = sys.argv[1]
    if not os.path.exists(video_path):
        print(f"[ERROR] Video not found: {video_path}")
        sys.exit(1)

    base_dir = os.path.dirname(os.path.abspath(__file__))
    overlay_dir = os.path.join(base_dir, "assets", "overlays")
    anim_dir = os.path.join(base_dir, "assets", "animations")

    # Use transparent .mov files — prefer capcut version, then with_shadow, then chroma
    subscribe_mov = os.path.join(overlay_dir, "subscribe_capcut.mov")
    if not os.path.exists(subscribe_mov):
        subscribe_mov = os.path.join(overlay_dir, "subscribe_with_shadow.mov")
    if not os.path.exists(subscribe_mov):
        subscribe_mov = os.path.join(overlay_dir, "subscribe_chroma.mov")

    like_mov = os.path.join(overlay_dir, "like_with_shadow.mov")
    if not os.path.exists(like_mov):
        like_mov = os.path.join(overlay_dir, "like_chroma.mov")

    if not os.path.exists(subscribe_mov) or not os.path.exists(like_mov):
        print("[ERROR] Transparent animation files not found.")
        sys.exit(1)

    print(f"[INFO] Video: {video_path}")
    video_duration = get_duration(video_path)
    print(f"[INFO] Duration: {video_duration:.1f}s")

    # Timing — subscribe starts 20s before end
    sub_start = max(video_duration - 20.0, 0)
    sub_end = sub_start + 6.0
    like_start = sub_end
    like_end = like_start + 2.74

    print(f"[INFO] Subscribe: {sub_start:.1f}s - {sub_end:.1f}s")
    print(f"[INFO] Like: {like_start:.1f}s - {like_end:.1f}s")

    # ============================================================
    # POSITIONS — reverse-engineered from CapCut edit (0824(1).mp4)
    # ============================================================
    # CapCut was 1080x1920, pipeline is 1440x2560 (scale = 1.333)
    #
    # Subscribe button (1080p): X=350-729, Y=533-659 (379x126px)
    # Subscribe (1440p): X=466-972, Y=710-878 (506x168px)
    # subscribe_with_shadow.mov is 580x250 → scale to 560x242 (15% larger frame)
    # to prevent clipping during bounce animation, button stays same visual size
    # capcut animation is 614x620 (nearly square) — scale proportionally
    sub_w = 400
    sub_h = int(400 * 620 / 614)  # ≈ 404, preserves aspect ratio
    sub_x, sub_y = 520, 580

    # Heart/Like (1080p): X=476-607, Y=554-673 (131x119px)
    # Heart (1440p): X=634-809, Y=738-897 (175x159px)
    # like_with_shadow.mov is 490x456 → scale to 175x163 (uniform)
    like_w, like_h = 175, 163
    like_x, like_y = 634, 738

    print(f"[INFO] Subscribe: pos=({sub_x},{sub_y}), size={sub_w}x{sub_h}")
    print(f"[INFO] Like: pos=({like_x},{like_y}), size={like_w}x{like_h}")

    # Extract animation audio
    print("\n[STEP] Extracting animation audio...")
    sub_audio = os.path.join(tempfile.gettempdir(), "sub_audio_end.wav")
    like_audio = os.path.join(tempfile.gettempdir(), "like_audio_end.wav")

    # Prefer pre-extracted WAV audio (already in overlays dir), fall back to raw source
    sub_audio_wav = os.path.join(overlay_dir, "subscribe_audio.wav")
    like_audio_wav = os.path.join(overlay_dir, "like_audio.wav")

    has_sub_audio = os.path.exists(sub_audio_wav) or os.path.exists(os.path.join(anim_dir, "subscribe_green.weba"))
    has_like_audio = os.path.exists(like_audio_wav) or os.path.exists(os.path.join(anim_dir, "like_green.m4a"))

    if has_sub_audio:
        src = sub_audio_wav if os.path.exists(sub_audio_wav) else os.path.join(anim_dir, "subscribe_green.weba")
        subprocess.run([
            'ffmpeg', '-y', '-i', src,
            '-acodec', 'pcm_s16le', '-ar', '48000', sub_audio
        ], check=True, capture_output=True, timeout=30)

    if has_like_audio:
        src = like_audio_wav if os.path.exists(like_audio_wav) else os.path.join(anim_dir, "like_green.m4a")
        subprocess.run([
            'ffmpeg', '-y', '-i', src,
            '-acodec', 'pcm_s16le', '-ar', '48000', like_audio
        ], check=True, capture_output=True, timeout=30)

    # Build inputs with -itsoffset
    inputs = ['-i', video_path]
    inputs += ['-itsoffset', str(sub_start), '-i', subscribe_mov]
    inputs += ['-itsoffset', str(like_start), '-i', like_mov]

    if has_sub_audio:
        inputs += ['-i', sub_audio]
    if has_like_audio:
        inputs += ['-i', like_audio]

    # Input indices: [0]=video, [1]=subscribe, [2]=like, [3]=sub_audio, [4]=like_audio
    sb = 1
    lk = 2
    sa = 3 if has_sub_audio else -1
    la = 4 if has_like_audio else -1

    # Build filter — overlay transparent .mov files
    parts = []
    prev = "0:v"

    # Subscribe: scale and overlay with alpha
    parts.append(f"[{sb}:v]scale={sub_w}:{sub_h},format=rgba[sb_fmt]")
    parts.append(f"[{prev}][sb_fmt]overlay=x={sub_x}:y={sub_y}:format=auto[v2]")
    prev = "v2"

    # Like: scale and overlay with alpha
    parts.append(f"[{lk}:v]scale={like_w}:{like_h},format=rgba[lk_fmt]")
    parts.append(f"[{prev}][lk_fmt]overlay=x={like_x}:y={like_y}:format=auto[vout]")

    # Audio — skip filter mixing, handle separately after video render
    # (amix filter is unreliable for overlaying SFX on existing audio)
    parts.append("[0:a]acopy[aout]")

    fc = ";".join(parts)

    print(f"\n[FILTER] {fc}\n")

    output_path = video_path.replace(".mp4", "_ending.mp4")

    cmd = [
        'ffmpeg', '-y',
        *inputs,
        '-filter_complex', fc,
        '-map', '[vout]', '-map', '[aout]',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23',
        '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '128k',
        '-movflags', '+faststart',
        output_path
    ]

    print(f"[STEP] Overlaying -> {os.path.basename(output_path)}")

    try:
        run_ffmpeg(cmd, timeout=600, label="ending overlay")
        print(f"   Video overlay done: {os.path.getsize(output_path) / (1024*1024):.1f} MB")
    except Exception as e:
        print(f"\n[FAIL] {e}")
        sys.exit(1)    # ================================================================
    # STEP 2: Mix SFX audio onto the rendered video (separate pass)
    # ================================================================
    if has_sub_audio or has_like_audio:
        print("\n[STEP] Mixing SFX audio...")
        final_path = video_path.replace(".mp4", "_final.mp4")

        # Build audio-only filter using adelay + amix with normalize=0
        # normalize=0 prevents amix from dividing volume by number of inputs
        audio_filters = []
        mix_inputs = "[0:a]"  # original audio always present
        n_mix = 1

        if has_sub_audio:
            audio_filters.append(
                f"[1:a]volume=3.0,adelay={int(sub_start*1000)}|{int(sub_start*1000)}[sa]"
            )
            mix_inputs += "[sa]"
            n_mix += 1

        if has_like_audio:
            idx = 2 if has_sub_audio else 1
            audio_filters.append(
                f"[{idx}:a]volume=3.0,adelay={int(like_start*1000)}|{int(like_start*1000)}[la]"
            )
            mix_inputs += "[la]"
            n_mix += 1

        # normalize=0 keeps original volume — amix won't divide by n_mix
        audio_filters.append(
            f"{mix_inputs}amix=inputs={n_mix}:duration=first:dropout_transition=0:normalize=0[aout]"
        )
        audio_fc = ";".join(audio_filters)

        # Inputs: [0]=rendered video, [1]=sub_audio, [2]=like_audio
        audio_inputs = ['-i', output_path]
        if has_sub_audio:
            audio_inputs += ['-i', sub_audio]
        if has_like_audio:
            audio_inputs += ['-i', like_audio]

        cmd_audio = [
            'ffmpeg', '-y',
            *audio_inputs,
            '-filter_complex', audio_fc,
            '-map', '0:v', '-map', '[aout]',
            '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
            '-movflags', '+faststart', final_path
        ]

        try:
            run_ffmpeg(cmd_audio, timeout=300, label="audio mix")
            # Replace the video-only file with the final version
            os.replace(final_path, output_path)
            print(f"   Audio mixed successfully!")
        except Exception as e:
            print(f"   Audio mix failed: {e}")
            print(f"   Using video-only version (no SFX)")

    print(f"\n[DONE] Overlay applied!")
    print(f"   Output: {output_path}")
    print(f"   Size: {os.path.getsize(output_path) / (1024*1024):.1f} MB")


if __name__ == "__main__":
    main()
