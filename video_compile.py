import os
import sys
import re
import math
import time
import subprocess
import tempfile
import shutil
from tqdm import tqdm
from config import (
    VOICE_SPEED,
    VOICE_LUFS_TARGET,
    VOICE_TP_MAX,
    FEMALE_VOICE_BOOST_DB,
)

# Force line-buffered stdout so every print() appears in the Actions log
# immediately (Python defaults to block-buffered when piped, which hides
# critical ffmpeg error output until the process exits — too late to help).
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# --- ENCODING CONSTANTS (module-level so tasks.py can stay in sync) ---
# Background footage playback speed. 1.0 = the source's ORIGINAL speed —
# the 4K 60fps gameplay plays at native motion (no added blur from temporal
# stretching). The narration is still sped to VOICE_SPEED; EXTRACT_FACTOR
# below buys back enough footage to cover the sped narration.
SPEED_FACTOR = 1.0
SEGMENT_DURATION = 30
# Locked output framerate: every video renders at exactly 60fps to MATCH
# the 4K 60fps background sources (a 24/30fps source is pulled up to 60).
# FIXED (motion blur): the old 30fps cap halved the 60fps source's motion
# detail, and combined with the 1.35x speed-up every output frame carried
# ~2.7x the source's motion — fast gameplay (Fortnite) rendered visibly
# blurry no matter the CRF. 60fps keeps the source's native motion, and
# YouTube/Shorts accept 60fps.
OUTPUT_FPS = 60
# Output resolution (9:16 vertical). Was 1080x1920; now 1440x2560 so the
# 4K (3840x2160) background sources pay off: a 9:16 center-crop of a 4K
# landscape frame is 1215x2160 native pixels, so 1440x2560 is only a mild
# 1.19x upscale — the closest standard YouTube tier to the source's real
# detail. Going 2160x3840 would be a 1.78x upscale (SOFTER than 1440p, not
# sharper) and ~4x the veryslow encode time, risking the Actions timeout,
# so 1440p is the quality/risk sweet spot. YouTube offers a 1440p stream on
# phones, fixing the "lower quality on my phone" complaint. H.264 level 5.1
# is required for 1440x2560@60 (level 5.0 caps below 1440p60; level 4.0
# caps at ~1080p frame sizes).
#
# IMPORTANT: the 1.19x figure only holds for TRUE 4K sources. The resolution
# is deliberately NOT chosen per-source (output stays 1440x2560 always) but
# the renderer now measures and reports the real upscale factor, and warns
# loudly when a source cannot fill the frame — a soft background is a source
# problem, and silently rendering it smaller would only hide that.
OUTPUT_W = 1440
OUTPUT_H = 2560

# --- HOW THE FOOTAGE REACHES THE RENDERER ---
# direct (default): the renderer decodes straight from the downloaded source and
#   applies crop/scale/speed/captions/overlays in ONE encode. Everything that
#   produces pixels is CRF 15 + veryslow, and nothing is encoded twice.
# staged: the legacy path — 4K 60s batches re-encoded, then a whole-footage
#   normalizer re-encode, then the segment render. Kept working as a rollback:
#   set FOOTAGE_MODE=staged in the workflow env to restore the old behaviour.
FOOTAGE_MODE = os.environ.get("FOOTAGE_MODE", "direct").strip().lower()

# The ONLY permitted degradation. CRF 15 + veryslow is the primary setting for
# every pass; a segment that fails is retried at CRF 18 + slow. (The old
# fallback was ultrafast preset, which is WORSE quality than 18/slow — it
# traded quality for speed when quality was the whole point.)
CRF_VALUE = int(os.environ.get("VIDEO_CRF", "15"))
FALLBACK_CRF = int(os.environ.get("VIDEO_FALLBACK_CRF", "18"))
FALLBACK_PRESET = os.environ.get("VIDEO_FALLBACK_PRESET", "slow")

# The STAGED intermediate (FOOTAGE_MODE=staged, or a VP9/AV1 source) exists
# only to normalize a source into clean, densely-keyframed H.264 the renderer
# can decode. It is a WORKING file, not the delivered pixels — the segment
# render still encodes the delivered video at VIDEO_CRF/VIDEO_PRESET. It stays
# on a fast preset on purpose: x264 veryslow on 4K across 2 cores needs ~70+
# minutes per 60s of footage, which cannot fit the job timeout. FOOTAGE_MODE=
# direct (the default) removes this pass entirely, which is what makes
# "everything CRF 15 veryslow" achievable.
STAGED_CRF = int(os.environ.get("STAGED_CRF", "15"))
STAGED_PRESET = os.environ.get("STAGED_PRESET", "veryfast")

# Mild post-upscale sharpening. A 9:16 crop of a 4K landscape frame is
# 1215x2160, so it is still upscaled 1.19x to reach 1440x2560 and a plain
# lanczos upscale reads slightly soft. This recovers perceived crispness
# without an extra encode. Set VIDEO_UNSHARP=off to disable.
VIDEO_UNSHARP = os.environ.get("VIDEO_UNSHARP", "5:5:0.6:5:5:0.0").strip()

# Caption burn style (burned inside the render — see compile_video).
CAPTION_FONT_SIZE = int(os.environ.get("CAPTION_FONT_SIZE", "16"))
CAPTION_MARGIN_V = int(os.environ.get("CAPTION_MARGIN_V", "90"))
CAPTION_ALIGNMENT = int(os.environ.get("CAPTION_ALIGNMENT", "10"))
# How much footage to grab relative to the narration: the background plays at
# SPEED_FACTOR x and the voice is sped to VOICE_SPEED x, so to cover the whole
# narration (with 10% slack) we need:
#   footage = audio_duration * SPEED_FACTOR / VOICE_SPEED * 1.1
# This replaces the old hardcoded 1.5x which (a) rendered ~25% more footage
# than needed and (b) combined with a 1x supply from get_next_segment, made
# the sped-up video end BEFORE the narration so -shortest cut stories short.
EXTRACT_FACTOR = round((SPEED_FACTOR / VOICE_SPEED) * 1.1, 3)

# --- FEMALE NARRATOR VOLUME BOOST ---
# Both narrators are normalized to the same LUFS target, so the female and male
# voices come out at the same loudness. FEMALE_VOICE_BOOST_DB (config.py) is an
# extra offset ON TOP of that target — it is 0.0, i.e. identical levels, and the
# env var still overrides it if a particular voice ever needs a nudge.
FEMALE_VOICE_ID = "CT97FgDtAHKczJP3Yl78"      # "Female yappy voice" (see tasks.py)

# --- ANIMATED TITLE FRAME (burned into segment 0's filter chain) ---
# The narrator speaks the story TITLE at the very start of the voiceover
# ("<title>. <story>..."), so the reddit post card generated by
# generate_reddit_frame.py (real subreddit, title, avatar, score) is overlaid
# on screen while it's being said. The card is FULLY visible from the very
# first frame (no fade-in — so YouTube's auto-picked Shorts thumbnail usually
# shows the card), holds while the title is narrated, then fades out while
# sliding LEFT and away (like the TikTok reference). Pure overlay inside
# segment 0's existing encode - no extra pass, negligible CPU on the Actions runner.
TITLE_FADE_SEC = 0.25    # slide-OUT duration — fast swipe LEFT (no fade, just motion)
TITLE_HOLD_SEC = 1.0     # extra time the card stays FULLY visible after the title is narrated
TITLE_MIN_SEC = 1.8      # never shorter than this (tiny titles still readable)
TITLE_MAX_SEC = 12.0     # never longer than this (covers the longest hooks)
# Set TITLE_INTRO=false in the workflow env to disable the intro entirely.
TITLE_INTRO = os.environ.get("TITLE_INTRO", "true").lower() != "false"

# --- ENDING ANIMATIONS (subscribe + like buttons) ---
# Two animated buttons with their own sound, drawn over the last ~20s of every
# video. Set ENDING_ANIMATIONS=true in the workflow env to bring them back.
# OFF by default: a run nobody has deliberately decided about should not spend
# the last 20 seconds of the video asking the viewer for something.
ENDING_ANIMATIONS = os.environ.get("ENDING_ANIMATIONS", "false").lower() == "true"

# --- SOUND EFFECTS ---
# Ding sound when the reddit card appears (0:00)
DING_SOUND_PATH = "assets/sound_effects/ding.mp3"
# Whoosh sound when the card exits (slides LEFT)
WHOOSH_SOUND_PATH = "assets/sound_effects/whoosh.mp3"
# The ding is OFF by default: the card and the whoosh already carry the intro,
# and the bell lands on top of the narrator's first words. The file is left on
# disk untouched — set INTRO_DING=true in the workflow env to bring it back.
INTRO_DING = os.environ.get("INTRO_DING", "false").lower() == "true"

# Helper: Measure integrated loudness (LUFS) and true peak (dBFS) via EBU R128.
def measure_loudness(media_path: str):
    """Return (integrated_LUFS, true_peak_dBFS), or (None, None) on failure."""
    cmd = [
        'ffmpeg', '-y', '-nostats',
        '-i', media_path,
        '-af', 'ebur128=peak=true',
        '-f', 'null', '-'
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        out = result.stdout + result.stderr
        summary = out.split("Summary:")[-1]
        m_i = re.search(r"I:\s+(-?[\d.]+) LUFS", summary)
        m_tp = re.search(r"Peak:\s+(-?[\d.]+) dBFS", out)
        i = float(m_i.group(1)) if m_i else None
        tp = float(m_tp.group(1)) if m_tp else None
        return i, tp
    except Exception as e:
        print(f"   ⚠️ Loudness measurement failed: {e}")
        return None, None


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


def probe_video(media_path: str):
    """Log codec, resolution, pixel format, and frame rate via ffprobe.

    Returns the parsed dict for programmatic use, or None on failure.
    Pure diagnostic — never raises.
    """
    import json as _json
    cmd = [
        'ffprobe', '-v', 'error',
        '-select_streams', 'v:0',
        '-show_entries', 'stream=codec_name,profile,width,height,pix_fmt,r_frame_rate,avg_frame_rate',
        '-of', 'json',
        media_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        data = _json.loads(result.stdout or '{}')
        streams = data.get('streams') or []
        if streams:
            s = streams[0]
            info = {
                'codec': s.get('codec_name', '?'),
                'profile': s.get('profile', '?'),
                'width': s.get('width', '?'),
                'height': s.get('height', '?'),
                'pix_fmt': s.get('pix_fmt', '?'),
                'r_frame_rate': s.get('r_frame_rate', '?'),
                'avg_frame_rate': s.get('avg_frame_rate', '?'),
            }
            print(f"   🔍 Video probe: {info['codec']} {info['width']}x{info['height']} "
                  f"pix_fmt={info['pix_fmt']} profile={info['profile']} "
                  f"fps={info['avg_frame_rate']} ({os.path.basename(media_path)})")
            return info
        print(f"   ⚠️ No video stream found in {os.path.basename(media_path)}")
        return None
    except Exception as e:
        print(f"   ⚠️ Video probe failed for {os.path.basename(media_path)}: {e}")
        return None


def _escape_filter_path(path: str) -> str:
    """Return `path` in a form the ffmpeg filtergraph parser can actually take.

    Two rules, both learned the hard way by testing the filter directly:

    1. FORWARD SLASHES ONLY. A backslash inside a quoted filter argument is
       consumed by the filter parser, so a Windows path like
       `output\\caption_segments_1\\shifted_0000.srt` silently loses its
       separators and ffmpeg reports a file that looks like
       `outputcaption_segments_1shifted_0000.srt`.
    2. RELATIVE WHEN POSSIBLE. An absolute Windows path contains a `:`, and
       the filter option parser reads that as an option separator —
       `subtitles=C:/dir/x.srt` fails with the baffling
       `Unable to parse "original_size" option value`. Quoting does not help.
       A path relative to the process cwd has no colon and just works.

    Only the quote character needs escaping inside the surrounding quotes.
    """
    p = os.path.abspath(path).replace("\\", "/")
    try:
        rel = os.path.relpath(p, os.getcwd()).replace("\\", "/")
    except ValueError:      # different drive on Windows
        rel = None
    if rel and not rel.startswith(".."):
        return rel.replace("'", "\\'")
    return p.replace("'", "\\'")


def _plan_render_segments(spans, extract_duration, segment_duration, speed_factor):
    """Turn footage spans into the list of render jobs.

    A segment NEVER crosses a span boundary (spans come from different source
    files, so a crossing segment could not be a single input). Within one span
    the footage is split evenly by _segment_plan(), which also avoids the
    degenerate sub-second tail that used to crash the encoder.

    Returns [{"src", "src_start", "footage", "out_dur"}]; out_start is filled
    in by _annotate_plan() so it stays consistent if a boundary is moved.
    """
    plan = []
    remaining = extract_duration
    for span in spans:
        take_total = min(span["duration"], remaining)
        if take_total <= 1e-3:
            break
        local = 0.0
        for _, dur in _segment_plan(take_total, segment_duration):
            plan.append({
                "src": span["path"],
                "src_start": span["start"] + local,
                "footage": dur,
                "out_dur": dur / speed_factor,
                "out_start": 0.0,
            })
            local += dur
        remaining -= take_total
    _annotate_plan(plan, speed_factor)
    return plan


def _annotate_plan(plan, speed_factor):
    """(Re)compute out_start/out_dur from each job's footage."""
    t = 0.0
    for job in plan:
        job["out_dur"] = job["footage"] / speed_factor
        job["out_start"] = t
        t += job["out_dur"]


def _keep_overlays_whole(plan, overlays, speed_factor):
    """Move segment boundaries so no boundary cuts an overlay animation in half.

    Each overlay is (name, abs_start, duration) on the FINAL timeline. If a
    boundary falls strictly inside an overlay's window, the boundary is pulled
    back to the overlay's start — the previous segment gives up that slice and
    the next one takes it. Boundaries already at or outside an overlay's start
    are left alone.

    A boundary that cannot move (different source file, or it would leave a
    degenerate segment) is skipped; the overlay is then clamped to the segment
    it starts in, which is logged at render time.
    """
    if not overlays or not plan:
        return plan
    for name, ov_start, ov_dur in overlays:
        if ov_dur <= 0:
            continue
        ov_end = ov_start + ov_dur
        for i in range(1, len(plan)):
            b = plan[i]["out_start"]
            if not (ov_start < b < ov_end):
                continue
            prev, cur = plan[i - 1], plan[i]
            if cur["src"] != prev["src"]:
                print(f"   ⚠️ Cannot move a segment boundary for the '{name}' overlay "
                      f"(boundary falls at a source change) — clamping instead")
                break
            cut = b - ov_start
            if prev["footage"] - cut < 1.0:
                print(f"   ⚠️ Cannot move a segment boundary for the '{name}' overlay "
                      f"(would leave a sub-second segment) — clamping instead")
                break
            prev["footage"] -= cut
            cur["footage"] += cut
            cur["src_start"] -= cut
            print(f"   🎯 Moved segment {i} boundary back {cut:.2f}s so the '{name}' "
                  f"overlay stays in one piece")
            _annotate_plan(plan, speed_factor)
            break
    _annotate_plan(plan, speed_factor)
    return plan


def _crop_width(src_w, src_h):
    """Width of the 9:16 centre-crop taken from a source of this size.

    Mirrors the render filter exactly: crop=min(iw,ih*9/16):ih
    """
    if not src_w or not src_h:
        return 0
    return int(min(src_w, src_h * 9.0 / 16.0))


def _report_source_quality(spans):
    """Print (and warn about) the real upscale factor for the footage in use.

    A background that is soft because its source cannot fill 1440x2560 is a
    SOURCE problem and no encoder setting can fix it, so this is reported
    loudly rather than silently compensated for.
    """
    seen = []
    for s in spans or []:
        if s.get("path") in [p for p, _ in seen]:
            continue
        seen.append((s.get("path"), s))
    if not seen:
        return
    print("   🔎 Background source quality:")
    for path, s in seen:
        w, h = s.get("width") or 0, s.get("height") or 0
        crop_w = _crop_width(w, h)
        if not crop_w:
            print(f"      {os.path.basename(path)}: resolution unknown (probe failed)")
            continue
        upscale = OUTPUT_W / crop_w
        note = "✅ native-quality" if upscale <= 1.25 else (
            "⚠️ upscaled" if upscale <= 2.0 else "⚠️ HEAVILY upscaled")
        print(f"      {os.path.basename(path)}: {w}x{h} → crop {crop_w}x{h} → "
              f"output {OUTPUT_W}x{OUTPUT_H} → upscale {upscale:.2f}x  {note}")
        if upscale > 1.25:
            print(f"      ⚠️ WEAK SOURCE: this file cannot fill {OUTPUT_W}x{OUTPUT_H} "
                  f"({crop_w}px of real width for a {OUTPUT_W}px frame). The background "
                  f"will look soft no matter the CRF or preset — replace it with a "
                  f"true 4K file for a sharp result.")


def _stage_footage(source_video, extract_duration, output_dir):
    """STAGED PATH: re-encode the needed duration to clean H.264 first.

    Kept for FOOTAGE_MODE=staged (the rollback) and for sources whose codec is
    too expensive to decode directly at 4K on a 2-core runner (VP9/AV1).

    FIXED (exit-234 crash): the old -c:v copy preserved the source's original
    codec (VP9, AV1, ...) and container metadata — with sparse keyframes or a
    non-H.264 codec (common with Google Drive's re-encoded uploads) the copy-cut
    produced a file that passed ffprobe but failed when ffmpeg decoded frames
    for the filter chain. Re-encoding normalizes ANY input to clean H.264
    yuv420p with dense keyframes — the format the segment renderer expects.

    NOTE (quality): this re-encode is LOSSY, and removing it is precisely what
    FOOTAGE_MODE=direct does. Here the footage is compressed once at 4K and
    then AGAIN by the segment renderer, so it can never be as clean as the
    direct path.
    """
    gameplay_segment = os.path.join(output_dir, f"gameplay_segment_{int(time.time())}.mp4")
    cmd_extract = [
        'ffmpeg', '-y',
        '-i', source_video,
        '-t', str(extract_duration),
        '-c:v', 'libx264',
        '-preset', STAGED_PRESET,
        '-crf', str(STAGED_CRF),
        '-pix_fmt', 'yuv420p',
        '-movflags', '+faststart',
        '-an',
        gameplay_segment
    ]

    # Timeout scales with footage length: full-story videos can need 700+s of
    # footage, and this whole-duration re-encode at veryfast runs at roughly
    # 1-1.5x realtime on a 2-core runner. 2400s (40 min) covers the worst case.
    try:
        subprocess.run(cmd_extract, check=True, capture_output=True, timeout=2400)
        print(f"   ✅ Extracted {extract_duration:.2f}s segment (re-encoded to H.264).")
    except Exception as e:
        raise Exception(f"Segment extraction failed: {e}")

    # POST-EXTRACTION VALIDATION: verify the extracted segment is playable.
    probe_video(gameplay_segment)
    if not os.path.exists(gameplay_segment) or os.path.getsize(gameplay_segment) < 1024:
        raise Exception(
            f"Extraction produced invalid output "
            f"({os.path.getsize(gameplay_segment) if os.path.exists(gameplay_segment) else 0} bytes). "
            f"Source: {source_video} — check if the source video is corrupt or has an unsupported codec."
        )
    try:
        subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
             '-show_entries', 'stream=codec_name,width,height',
             '-of', 'json', gameplay_segment],
            check=True, capture_output=True, timeout=30
        )
    except Exception as e:
        raise Exception(
            f"Extracted segment is not decodable: {e}. "
            f"The source video may have a codec that libx264 cannot decode (e.g., VP9/AV1). "
            f"Source: {source_video}"
        )

    # DISK FIX: in staged mode the extracted segment replaces the huge source,
    # so the source can be deleted immediately. (Direct mode keeps the source
    # on disk until its last segment has rendered — see the render loop.)
    try:
        src_size = os.path.getsize(source_video) / (1024 * 1024)
        os.unlink(source_video)
        print(f"   🧹 Deleted source video ({src_size:.0f} MB freed)")
    except Exception:
        pass  # non-critical — log but don't abort

    return gameplay_segment


def _stage_spans(spans, output_dir):
    """STAGED fallback for an already-planned footage list.

    Re-encodes each planned window to clean H.264 and concatenates the pieces.
    Used when a source's codec is too expensive to decode directly (VP9/AV1 at
    4K on a 2-core runner) — the footage plan itself is unchanged, so this
    consumes exactly the same footage the direct path would have.

    See _stage_footage() for why the re-encode exists and what it costs.
    """
    pieces = []
    stamp = int(time.time())
    for i, s in enumerate(spans):
        piece = os.path.join(output_dir, f"stage_piece_{i}_{stamp}.mp4")
        cmd = [
            'ffmpeg', '-y',
            '-ss', str(s["start"]),
            '-t', str(s["duration"]),
            '-i', s["path"],
            '-c:v', 'libx264',
            '-preset', STAGED_PRESET,
            '-crf', str(STAGED_CRF),
            '-pix_fmt', 'yuv420p',
            '-an',
            piece,
        ]
        run_ffmpeg(cmd, timeout=2400, label=f"stage piece {i + 1}/{len(spans)}")
        pieces.append(piece)

    if len(pieces) == 1:
        return pieces[0]

    concat_file = os.path.join(output_dir, f"stage_concat_{stamp}.txt")
    with open(concat_file, 'w') as f:
        for p in pieces:
            f.write(f"file '{os.path.abspath(p)}'\n")
    staged = os.path.join(output_dir, f"gameplay_segment_{stamp}.mp4")
    run_ffmpeg(
        ['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', concat_file,
         '-c', 'copy', '-an', staged],
        timeout=300, label="stage concat",
    )
    for p in pieces:
        try:
            os.unlink(p)
        except OSError:
            pass
    os.unlink(concat_file)
    print(f"   ✅ Staged {len(spans)} span(s) into one H.264 file")
    return staged


def _segment_plan(duration, max_seg):
    """Return [(start_sec, dur_sec), ...] — evenly-sized segments.

    The old int(duration/max_seg)+1 split left a sub-second "tail" segment
    (e.g. 0.13s) at the end of the footage; on GitHub Actions' ffmpeg that
    degenerate sliver crashes the encoder (exit 234) and kills the whole
    video. Equal segments keep every segment healthy and per-segment encode
    time uniform. The tail is pure waste anyway: the mux step trims to the
    narration length (-shortest).
    """
    if duration <= 0:
        return []
    n = max(1, math.ceil(duration / max_seg))
    seg = duration / n
    plan = []
    for i in range(n):
        start = i * seg
        dur = min(seg, duration - start)
        if dur <= 0:
            break
        plan.append((start, dur))
    return plan


def run_ffmpeg(cmd, timeout=None, label="ffmpeg"):
    """Run ffmpeg with stderr surfaced on failure.

    Every ffmpeg call in the render path used capture_output=True and the
    real error (the reason ffmpeg exited non-zero) was silently swallowed,
    forcing blind guesses (e.g. the exit-234 crash). On failure, print the
    last lines of ffmpeg's stderr so the cause is in the Actions log.
    Also writes the FULL ffmpeg command + stderr to a log file so the
    error is never lost even if stdout is swallowed by threads/CI.
    """
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
        return None
    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"").decode("utf-8", errors="replace")
        tail = "\n".join(err.splitlines()[-25:]) if err.strip() else "(no stderr captured)"
        # Write FULL diagnostic to a file — survives thread/CI buffering
        try:
            log_path = os.path.join(os.environ.get('OUTPUT_DIR', '.'),
                                    f'ffmpeg_error_{label.replace(" ", "_")}.log')
            with open(log_path, 'w', encoding='utf-8') as lf:
                lf.write(f"Label: {label}\n")
                lf.write(f"Exit code: {e.returncode}\n")
                lf.write(f"Command: {' '.join(str(c) for c in cmd)}\n\n")
                lf.write(f"FULL STDERR:\n{err}\n")
            print(f"   ❌ {label} failed (exit {e.returncode}). Error log: {log_path}", flush=True)
        except Exception:
            print(f"   ❌ {label} failed (exit {e.returncode}). ffmpeg said:\n{tail}", flush=True)
        # Also write to stderr (unbuffered in CI) as a safety net
        try:
            sys.stderr.write(f"FFMPEG_ERROR ({label}): exit {e.returncode}\n{tail}\n")
            sys.stderr.flush()
        except Exception:
            pass
        raise

def compile_video(video_paths, audio_path, script, subtitle_path=None,
                  intro_frame=None, title=None, part_label=None,
                  voice_id=None, footage_spans=None, burn_captions=True,
                  output_name_captioned=False):
    """
    Compile video with segmented rendering.
    Now outputs YouTube-compatible format (yuv420p, faststart, aac audio).

    footage_spans: when given (FOOTAGE_MODE=direct) the segments are rendered
        straight from these source files — crop/scale/speed/captions/overlays
        all happen in ONE encode, so the footage is never re-encoded at 4K
        first. When None the legacy staged path runs (4K normalizer, then the
        segment render).
    subtitle_path: caption .srt (built by caption_utils.build_caption_track)
        to burn inside the render. When set, no separate caption pass is
        needed and the delivered file keeps the render's CRF 15 veryslow
        quality instead of being re-encoded afterwards.
    output_name_captioned: when True the output is named
        output_<ts>_captioned_<ts>.mp4 so the uploaders/globs that look for
        "*_captioned_*" keep working unchanged.
    """
    print("🎬 Starting video compilation (SEGMENTED, HIGH QUALITY)...")
    
    # --- VOICE LOUDNESS (calibrated to viral reddit-story videos) ---
    # Measured a viral reference (597K-view rSlash-style full-story video):
    # the narrator is the dominant element of the mix (overall integrated
    # -21.9 LUFS, narration ≈ -22 LUFS, music bed within ~2 dB under the
    # voice). YouTube normalizes playback to ~-14 LUFS regardless, so what
    # matters is a consistent, voice-forward narration level — the SAME for
    # the male and female voices. The old fixed 1.5x/1.8x gains left the two
    # voices mismatched (and could push peaks into clipping).
    #
    # The real normalization now happens EARLIER: voiceover.normalize_voice_
    # loudness() runs right after TTS and brings the raw narration to
    # VOICE_LUFS_TARGET, using loudnorm so the peaks are limited rather than
    # merely clamped. That is the only way a quiet clone reaches the target —
    # the female voice needed +9.7 dB while a pure gain was capped at +1.6 by
    # its peak ceiling, which is why she used to land ~13 dB under the male.
    # The measurement below stays as the VERIFIER (it should print ~0.0 dB) and
    # still corrects anything that arrives unnormalized. Both constants are
    # imported from config.py so there is one source of truth.
    voice_target = VOICE_LUFS_TARGET
    if voice_id == FEMALE_VOICE_ID:
        voice_target += FEMALE_VOICE_BOOST_DB
        if FEMALE_VOICE_BOOST_DB:
            print(f"   🎙️ Female narrator boost: +{FEMALE_VOICE_BOOST_DB:.1f} dB")
    
    # --- OTHER SETTINGS ---
    # Uniform CRF for the WHOLE background video. CRF controls quality; the
    # preset controls encode speed. CRF 15 + veryslow is near-visually-lossless
    # and gives the best motion estimation, and the footage is uploaded
    # straight to YouTube, so the slower encode and bigger files are fine.
    # CRF_VALUE / FALLBACK_CRF / FALLBACK_PRESET / VIDEO_UNSHARP come from the
    # module-level block above so every pass reads them from one place.
    PRESET = os.environ.get("VIDEO_PRESET", "veryslow")
    unsharp = VIDEO_UNSHARP if VIDEO_UNSHARP.lower() not in ("", "off", "none", "false") else None
    
    print(f"   🎙️ Voice target: {voice_target:.0f} LUFS (auto-gained per narration)")
    print(f"   🎙️ Voice speed: {VOICE_SPEED}x")
    
    # --- BACKGROUND MUSIC ---
    # "Valse Gymnopedie" by Kevin MacLeod (incompetech.com), CC BY 4.0 —
    # free to use with attribution (added to the video description).
    # (The original "Caleb Arredondo - Feeling Blue" track is still in
    # assets/ but unused: it is a commercially released song and was the
    # prime suspect for the YouTube Content ID block.)
    MUSIC_PATH = "assets/music/Kevin MacLeod - Valse Gymnopedie.mp3"
    MUSIC_ATTRIBUTION = ('"Valse Gymnopedie" Kevin MacLeod (incompetech.com)\n'
                         "Licensed under Creative Commons: By Attribution 4.0 License\n"
                         "http://creativecommons.org/licenses/by/4.0/")
    # ~13-14 dB below the -16 LUFS narration: clearly audible but secondary,
    # so the narrator leads the mix like the viral reference.
    MUSIC_VOLUME = 0.20
    
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
    
    output_dir = os.environ.get('OUTPUT_DIR', '.')
    if not os.path.exists(output_dir):
        output_dir = '.'
    
    # --- ANIMATED TITLE FRAME: build the segment-0 overlay chain ---
    # Timing comes from the script word-ratio: ElevenLabs speaks at a
    # near-uniform rate, so the title's share of the measured audio duration
    # is roughly its share of the words. The audio is later sped up by
    # VOICE_SPEED, so the title hold time on the FINAL timeline is that share
    # divided by VOICE_SPEED (same math as the caption timestamps).
    overlay_filter = None
    frame_input = None
    title_x_expr = None
    title_frame_top = None
    if intro_frame and os.path.exists(intro_frame) and TITLE_INTRO:
        total_words = len((script or "").split())
        title_words = len(title.split()) if title else 0
        if total_words and title_words:
            title_secs = (audio_duration * title_words / total_words) / VOICE_SPEED
            # Hold the card on screen a beat AFTER the title is done being
            # narrated so viewers have time to read it (the user's request:
            # it should stay a little longer before going away).
            title_secs = title_secs + TITLE_HOLD_SEC
            final_dur = audio_duration / VOICE_SPEED
            title_secs = min(max(title_secs, TITLE_MIN_SEC), TITLE_MAX_SEC, max(final_dur - 0.5, 1.0))
            t4 = title_secs
            t3 = max(t4 - TITLE_FADE_SEC, 0.0)  # fade-out starts this late; card fully visible from 0:00
            slide = round(0.03 * OUTPUT_H)
            # Card sits in the UPPER-MIDDLE of the screen (top edge ~14% down,
            # matching the darkflow001 TikTok style) with the story footage
            # and captions below it. NO entry animation: the card is pinned at
            # its resting spot from the first frame. On exit it fades out
            # while sliding LEFT and away (like the TikTok reference).
            frame_top = round(0.14 * OUTPUT_H)
            # Slide LEFT on exit — fast swipe, NO fade out (card stays opaque)
            slide_x = OUTPUT_W + 100  # slide FULL width + margin to go completely off-screen LEFT
            slide_duration = TITLE_FADE_SEC   # how long the slide takes (fast)
            x_expr = f"(W-w)/2-{slide_x}*clip((t-{t3:.3f})/{slide_duration:.3f},0,1)"
            overlay_filter = (
                f"[1:v]scale={OUTPUT_W}:-1:flags=lanczos[card];"
                f"[bg][card]overlay=x='{x_expr}':y={frame_top}:eval=frame"
            )
            # The render loop builds segment 0's filtergraph itself (it may also
            # carry an ending overlay), so it needs the card's geometry, not
            # just the pre-baked filter string.
            title_x_expr = x_expr
            title_frame_top = frame_top
            frame_input = intro_frame
            print(f"   ✨ Reddit frame intro: {os.path.basename(intro_frame)} "
                  f"({title_secs:.1f}s on screen)")
        else:
            print("   ⚠️ Frame intro skipped (empty title or script)")
    elif intro_frame and not os.path.exists(intro_frame):
        print(f"   ⚠️ Frame intro skipped (intro frame not found: {intro_frame})")
    
    extract_duration = audio_duration * EXTRACT_FACTOR
    print(f"   ⏱️ Using {extract_duration:.2f}s of footage (background plays at {SPEED_FACTOR}x)")

    gameplay_segment = None
    render_plan = None
    video_duration = 0.0
    source_video = video_paths[0] if video_paths else None

    # Decode the footage directly only when we were handed a plan AND the
    # sources use codecs we can afford to decode at 4K on a 2-core runner.
    # (Sourced from the spans themselves, so this is correct even if the plan
    # was built before a codec was known.)
    span_codecs = {(s.get("codec") or "").lower() for s in (footage_spans or [])}
    heavy_codecs = {c for c in span_codecs if c and c not in ("h264", "hevc")}
    direct_mode = bool(footage_spans) and FOOTAGE_MODE == "direct" and not heavy_codecs

    if heavy_codecs:
        print(f"   ⚠️ Source codec(s) {sorted(heavy_codecs)} are costly to decode "
              f"directly — staging to H.264 first (one extra re-encode)")

    if direct_mode:
        print("   🎞️ FOOTAGE_MODE=direct — rendering straight from the source "
              "(footage is encoded exactly once; no 4K intermediate passes)")
        _report_source_quality(footage_spans)
        render_plan = _plan_render_segments(footage_spans, extract_duration,
                                            SEGMENT_DURATION, SPEED_FACTOR)
        if not render_plan:
            raise Exception("Footage plan is empty — nothing to render")
        video_duration = sum(j["footage"] for j in render_plan)
        print(f"   📊 Footage: {video_duration:.2f}s across {len(render_plan)} segment(s) "
              f"from {len({j['src'] for j in render_plan})} source file(s)")
    elif footage_spans:
        # FOOTAGE_MODE=staged with a plan, or a codec we won't decode directly.
        # The plan is honoured exactly — same footage, same rotation state.
        print("   🎞️ Staging the planned footage (FOOTAGE_MODE=staged)")
        _report_source_quality(footage_spans)
        gameplay_segment = _stage_spans(footage_spans, output_dir)
        video_duration = get_duration(gameplay_segment)
        print(f"   📊 Video duration: {video_duration:.2f}s")
    else:
        # Legacy rollback: a single already-extracted file was handed in.
        if not source_video or not os.path.exists(source_video):
            raise Exception(f"Source video not found: {source_video}")
        gameplay_segment = _stage_footage(source_video, extract_duration, output_dir)
        video_duration = get_duration(gameplay_segment)
    
    # DISK CHECK: log free space so exit-234 errors can be correlated.
    try:
        disk = shutil.disk_usage(output_dir)
        free_gb = disk.free / (1024 ** 3)
        print(f"   💾 Disk free: {free_gb:.1f} GB")
        if free_gb < 3.0:
            print(f"   ⚠️ Low disk space ({free_gb:.1f} GB) — segment rendering may fail")
    except Exception:
        pass
    
    # STAGED ONLY: the extracted segment must be decodable before the
    # expensive render loop starts (a file can pass ffprobe and still hold
    # corrupt frame data). Direct mode decodes the source itself, per segment.
    if not direct_mode:
        try:
            subprocess.run(
                ['ffmpeg', '-y', '-i', gameplay_segment,
                 '-frames:v', '1', '-f', 'null', '-'],
                check=True, capture_output=True, timeout=60
            )
            print(f"   ✅ Single-frame decode test passed")
        except Exception as e:
            print(f"   ❌ Single-frame decode test FAILED: {e}")
            print(f"   💡 The gameplay segment is not decodable — the source video may be corrupt")
            raise Exception(
                f"Gameplay segment is not decodable. The source video may be corrupt "
                f"or have a codec issue. File: {gameplay_segment}"
            )

    # --- ENDING OVERLAYS (subscribe + like) ---
    # Baked into the segments that contain them, instead of being applied as a
    # separate whole-video pass. That pass re-encoded EVERY frame at veryfast
    # just to draw two small buttons in the last ~29s, which capped the whole
    # video at veryfast quality — the opposite of "everything CRF 15 veryslow".
    ANIM_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "animations")
    OVERLAY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "overlays")
    SUBSCRIBE_MOV = os.path.join(OVERLAY_DIR, "subscribe_capcut.mov")
    if not os.path.exists(SUBSCRIBE_MOV):
        SUBSCRIBE_MOV = os.path.join(OVERLAY_DIR, "subscribe_with_shadow.mov")
    if not os.path.exists(SUBSCRIBE_MOV):
        SUBSCRIBE_MOV = os.path.join(OVERLAY_DIR, "subscribe_chroma.mov")
    LIKE_MOV = os.path.join(OVERLAY_DIR, "like_with_shadow.mov")
    if not os.path.exists(LIKE_MOV):
        LIKE_MOV = os.path.join(OVERLAY_DIR, "like_chroma.mov")

    # Times are on the FINAL timeline (the mux trims to the narration length
    # with -shortest), which is exactly the render's own output timeline.
    final_timeline_dur = audio_duration / VOICE_SPEED
    ending_overlays = []   # (name, mov_path, abs_start, duration)
    ending_baked = False
    if not ENDING_ANIMATIONS:
        print("   🔕 Subscribe/like ending animations OFF "
              "(ENDING_ANIMATIONS=false — no buttons, no sound)")
    elif os.path.exists(SUBSCRIBE_MOV) and os.path.exists(LIKE_MOV):
        subscribe_start = max(final_timeline_dur - 20.0, 0.0)
        ending_overlays = [
            ("subscribe", SUBSCRIBE_MOV, subscribe_start, 6.0),
            ("like", LIKE_MOV, subscribe_start + 6.0, 2.74),
        ]
    else:
        print("   ⚠️ Subscribe/like animations not found — run prepare_animations.py")
    # Everything built from here on renders these overlays INTO the segments,
    # so the separate whole-video pass below must not run.
    ending_baked = bool(ending_overlays)

    # --- BUILD THE RENDER JOBS (uniform shape for both modes) ---
    if direct_mode:
        job_sources = [dict(j) for j in render_plan]
    else:
        job_sources = []
        for start, dur in _segment_plan(video_duration, SEGMENT_DURATION):
            if dur <= 0:
                continue
            job_sources.append({
                "src": gameplay_segment,
                "src_start": start,
                "footage": dur,
                "out_start": start / SPEED_FACTOR,
                "out_dur": dur / SPEED_FACTOR,
            })
    if not job_sources:
        raise Exception(f"Video has no playable duration ({video_duration:.2f}s) — nothing to render")

    # Keep each ending animation inside ONE segment: a boundary through an
    # overlay would play it half in one segment and half in the next.
    if direct_mode and ending_overlays:
        _keep_overlays_whole(job_sources,
                             [(n, s, d) for n, _, s, d in ending_overlays],
                             SPEED_FACTOR)

    print(f"   📦 Rendering {len(job_sources)} segment(s), fixed {SEGMENT_DURATION}s grid")
    print(f"   📊 Quality: CRF {CRF_VALUE} + preset {PRESET} "
          f"(only fallback: CRF {FALLBACK_CRF} + {FALLBACK_PRESET})")
    if ending_overlays:
        print("   🔔 Ending overlays baked into the render: "
              + ", ".join(f"{n}@{s:.1f}s" for n, _, s, _ in ending_overlays))

    # Caption SRTs, shifted per segment. caption_utils.shift_srt_for_segment
    # is the SAME maths the old separate caption pass used, so the burned-in
    # timings are unchanged — only WHERE the burn happens has moved.
    srt_abs = None
    caption_temp_dir = None
    if burn_captions and subtitle_path and os.path.exists(subtitle_path):
        from caption_utils import shift_srt_for_segment
        srt_abs = os.path.abspath(subtitle_path)
        caption_temp_dir = os.path.join(output_dir, f"caption_segments_{int(time.time())}")
        os.makedirs(caption_temp_dir, exist_ok=True)
        print(f"   💬 Burning captions inside the render "
              f"(font {round(CAPTION_FONT_SIZE * OUTPUT_H / 1920)}px, "
              f"{os.path.basename(srt_abs)})")
    else:
        def shift_srt_for_segment(*_a, **_k):  # pragma: no cover - unused
            raise RuntimeError("captions disabled")

    eff_font = round(CAPTION_FONT_SIZE * OUTPUT_H / 1920)
    SUB_W, SUB_H = 400, 404
    SUB_X, SUB_Y = 520, 1450
    LIKE_W, LIKE_H = 175, 163
    LIKE_X, LIKE_Y = 634, 1600
    quality_label = f"CRF {CRF_VALUE} ({PRESET})"

    def _base_chain(i, out_start):
        """crop → scale → fps → speed → [sharpen] → [captions].

        Format/setsar are appended by the job builder: RGBA when an overlay
        needs to composite onto it, yuv420p otherwise.
        """
        chain = (
            f'crop=min(iw\\,ih*9/16):ih:(iw-min(iw\\,ih*9/16))/2:0,'
            f'scale={OUTPUT_W}:{OUTPUT_H}:flags=lanczos,'
            f'fps={OUTPUT_FPS},'
            f'setpts={1/SPEED_FACTOR}*PTS'
        )
        if unsharp:
            chain += f',unsharp={unsharp}'
        if srt_abs:
            seg_srt = os.path.join(caption_temp_dir, f"shifted_{i:04d}.srt")
            shift_srt_for_segment(srt_abs, out_start, seg_srt)
            chain += (
                f",subtitles='{_escape_filter_path(seg_srt)}':force_style='"
                f"FontName=Arial,FontSize={eff_font},Bold=1,"
                f"Alignment={CAPTION_ALIGNMENT},MarginV={CAPTION_MARGIN_V},"
                f"Outline=2,"
                f"PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000'"
            )
        return chain

    jobs = []
    for i, job in enumerate(job_sources):
        seg_out_start = job["out_start"]
        seg_out_end = seg_out_start + job["out_dur"]
        chain = _base_chain(i, seg_out_start)

        # Which ending overlays fall inside this segment's window?
        seg_overlays = []
        for name, mov, abs_start, dur in ending_overlays:
            if abs_start < seg_out_end - 0.05 and abs_start + dur > seg_out_start + 0.05:
                local = abs_start - seg_out_start
                if local < -0.05:
                    print(f"   ⚠️ '{name}' overlay starts before segment {i+1} — clamping")
                    local = 0.0
                seg_overlays.append((name, mov, max(local, 0.0), dur))

        # Title card: segment 0 only (its whole animation is in the first ~12s).
        use_title = (i == 0 and overlay_filter is not None)

        print(f"   📌 Segment {i+1}/{len(job_sources)}: {quality_label} "
              f"({job['footage']:.1f}s from {os.path.basename(job['src'])})"
              + (f" + {len(seg_overlays)} overlay(s)" if seg_overlays else "")
              + (" + title card" if use_title else ""))

        segment_output = os.path.join(output_dir, f"segment_processed_{i}_{int(time.time())}.mp4")

        # `-ss`/`-t` BEFORE `-i` = input seeking: frame-accurate and
        # keyframe-independent, so a segment can never come back empty (the
        # old copy-cut could). `-t` MUST stay before `-i` — as an output
        # option it defeats the setpts speed-up.
        cmd_process = [
            'ffmpeg', '-y',
            '-ss', str(job["src_start"]),
            '-t', str(job["footage"]),
            '-i', job["src"],
        ]

        if use_title or seg_overlays:
            # TWO independent counters, and conflating them was a real bug:
            #
            #   `n_in`  = the next ffmpeg INPUT index. It must advance by
            #             exactly one per `-i` actually appended, in the same
            #             order. A filtergraph pad is NOT an input: with two
            #             ending overlays the old code emitted `[4:v]` for the
            #             second one while only inputs 0..2 existed, and
            #             ffmpeg died with the opaque
            #             `Invalid file index 4 in filtergraph description`
            #             — then the CRF 18 fallback retried the same broken
            #             graph and the segment failed outright. It only ever
            #             "worked" when a segment happened to contain a single
            #             overlay, which is why it survived casual testing.
            #   `nxt`   = the next filtergraph PAD number, which advances by
            #             one per overlay and two per composited overlay.
            extra_inputs = []
            parts = [f"[0:v]{chain},format=rgba,setsar=1[v0]"]
            label = "v0"
            n_in = 1      # input 0 is the source itself
            nxt = 1
            if use_title:
                # `-loop 1 -t 15`: without the loop the card is a SINGLE frame
                # at t=0; an unbounded loop never EOFs and hangs the render.
                # 15s covers the longest title intro (12s max + fade).
                extra_inputs += ['-loop', '1', '-t', '15', '-i', frame_input]
                card_idx = n_in
                n_in += 1
                card_pad = nxt
                nxt += 1
                parts.append(f"[{card_idx}:v]scale={OUTPUT_W}:-1:flags=lanczos[card]")
                parts.append(
                    f"[{label}][card]overlay=x='{title_x_expr}':"
                    f"y={title_frame_top}:eval=frame[v{card_pad}]"
                )
                label = f"v{card_pad}"
            for name, mov, local, dur in seg_overlays:
                extra_inputs += ['-i', mov]
                ov_idx = n_in
                n_in += 1
                ov_pad = nxt
                nxt += 1
                out_pad = nxt
                nxt += 1
                if name == "subscribe":
                    ov_w, ov_h, ov_x, ov_y = SUB_W, SUB_H, SUB_X, SUB_Y
                else:
                    ov_w, ov_h, ov_x, ov_y = LIKE_W, LIKE_H, LIKE_X, LIKE_Y
                parts.append(
                    f"[{ov_idx}:v]setpts=PTS-STARTPTS+{local:.3f}/TB,"
                    f"scale={ov_w}:{ov_h},format=rgba[ov{ov_pad}]"
                )
                parts.append(
                    f"[{label}][ov{ov_pad}]overlay=x={ov_x}:y={ov_y}:format=auto:"
                    f"eof_action=pass:enable='between(t,{local:.3f},{local + dur:.3f})'[v{out_pad}]"
                )
                label = f"v{out_pad}"
            parts.append(f"[{label}]format=yuv420p,setsar=1[v]")
            cmd_process += extra_inputs + [
                '-filter_complex', ';'.join(parts), '-map', '[v]'
            ]
        else:
            cmd_process += ['-vf', f"{chain},format=yuv420p,setsar=1"]

        cmd_process += [
            '-sws_flags', 'lanczos',
            '-c:v', 'libx264',
            '-preset', PRESET,
            '-crf', str(CRF_VALUE),
            '-profile:v', 'high',
            '-level', '5.1',
            '-an',
            '-movflags', '+faststart',
            segment_output
        ]

        jobs.append((i, segment_output, cmd_process))
    
    # DISK CHECK before rendering: log free space so exit-234 I/O errors
    # can be correlated with disk pressure on the runner. Direct mode holds
    # the full 4K source on disk while it renders, so this matters more.
    try:
        disk = shutil.disk_usage(output_dir)
        free_gb = disk.free / (1024 ** 3)
        print(f"   💾 Disk free before rendering: {free_gb:.1f} GB")
        if free_gb < 5.0:
            print(f"   ⚠️ Low disk space ({free_gb:.1f} GB) — segment rendering may fail")
    except Exception:
        pass
    
    # Segments are independent ffmpeg encodes. Every worker keeps the SAME
    # CRF 15 + veryslow; the ONLY permitted degradation is CRF 18 + slow for a
    # segment that failed outright. Results are stored by index so the concat
    # stays ordered.
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # DO NOT delete the sources here. In direct mode a "source" is the 4K
    # file drive_clip_manager cached at cached_videos/video_<id>.mp4, and that
    # file is SHARED: plan_footage() reuses an already-downloaded file for every
    # later video in the same batch, and only downloads when the path is
    # missing. Deleting it after its last segment would turn video 2/3 of a
    # batch into a fresh multi-GB Drive download (and a brand-new way for the
    # batch to fail on a transient download error). The old pipeline never
    # deleted it either — the runner has ~80 GB free against ~1.6-4.1 GB per
    # source — so holding it keeps disk use at parity with before.
    def _run_segment(job):
        i, segment_output, cmd_process = job
        try:
            run_ffmpeg(cmd_process, timeout=1800, label=f"segment {i+1} render")
            return i, segment_output, False
        except Exception as e:
            print(f"   ⚠️ Segment {i+1} failed: {e}")
            print(f"   🔄 Retrying segment {i+1} at the fallback setting "
                  f"(CRF {FALLBACK_CRF} + {FALLBACK_PRESET})...")
            cmd_fallback = list(cmd_process)
            cmd_fallback[cmd_fallback.index('-preset') + 1] = FALLBACK_PRESET
            cmd_fallback[cmd_fallback.index('-crf') + 1] = str(FALLBACK_CRF)
            run_ffmpeg(cmd_fallback, timeout=1800, label=f"segment {i+1} fallback")
            return i, segment_output, True
    
    segment_files = [None] * len(jobs)
    pbar = tqdm(total=len(jobs), desc="🎬 Rendering segments", unit="segment")
    with ThreadPoolExecutor(max_workers=1) as ex:
        futures = {ex.submit(_run_segment, job): job[0] for job in jobs}
        for fut in as_completed(futures):
            i, out, used_fallback = fut.result()
            segment_files[i] = out
            pbar.update(1)
            print(f"   ✅ Segment {i+1}/{len(jobs)} complete"
                  + (" (fallback)" if used_fallback else f" ({quality_label})"))
    pbar.close()
    segment_files = [s for s in segment_files if s]
    
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
    # 1) Speed the voice first (atempo can overshoot peaks by ~2-3 dB, so the
    #    loudness measurement MUST happen after the time-stretch).
    # 2) Measure the sped narration (EBU R128).
    # 3) Apply a FIXED gain to land on the target loudness (volume is linear,
    #    so the peak math is then exact and can't clip).
    # Identical logic for the male and female voices, so both narrators come
    # out at exactly the same level (the old 1.5x/1.8x gains were a guess that
    # left them mismatched).
    audio_sped = os.path.join(output_dir, f"audio_sped_{int(time.time())}.wav")
    audio_processed = os.path.join(output_dir, f"audio_processed_{int(time.time())}.mp3")
    try:
        subprocess.run([
            'ffmpeg', '-y', '-nostats',
            '-i', audio_path,
            '-af', f'atempo={VOICE_SPEED}',
            '-ac', '1',
            '-acodec', 'pcm_s16le',
            audio_sped
        ], check=True, capture_output=True, timeout=120)
        
        voice_i, voice_tp = measure_loudness(audio_sped)
        if voice_i is not None:
            gain_db = min(voice_target - voice_i, VOICE_TP_MAX - (voice_tp if voice_tp is not None else -99.0))
            print(f"   🎙️ Narration at {voice_i:.1f} LUFS (peak {voice_tp if voice_tp is not None else -99:.1f} dBFS) "
                  f"-> applying {gain_db:+.1f} dB")
        else:
            gain_db = 4.0  # sane fallback if measurement fails
            print(f"   ⚠️ Loudness measurement failed; using fallback gain {gain_db:+.1f} dB")
        
        subprocess.run([
            'ffmpeg', '-y', '-nostats',
            '-i', audio_sped,
            '-af', f'volume={gain_db:.2f}dB',
            '-ac', '1',
            '-acodec', 'mp3',
            '-b:a', '192k',
            audio_processed
        ], check=True, capture_output=True, timeout=120)
        os.unlink(audio_sped)
        print(f"   ✅ Audio processed (voice normalized to {voice_target:.0f} LUFS)")
    except Exception as e:
        print(f"   ⚠️ Audio processing failed: {e}")
        if os.path.exists(audio_sped):
            os.unlink(audio_sped)
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
                # FIXED: amix NORMALIZES by default (each input / 2 for two
                # inputs), which silently cut the narrator ~6 dB in every
                # video — a big reason the voice sounded quiet. normalize=0
                # keeps the levels we set; the voice is already at its target
                # so no extra gain here (the old volume=1.2 boost would clip).
                f'[voice][music]amix=inputs=2:duration=first:normalize=0',
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
    
    # --- SOUND EFFECTS + ENDING ANIMATION AUDIO ---
    # whoosh when the card slides out, the ding at 0:00 only if INTRO_DING is
    # on, plus the subscribe/like sounds for the ending animations.
    #
    # IMPORTANT: the ending animations are baked into the RENDER, which carries
    # no audio, so their sound is mixed here instead of in a separate pass over
    # the finished video. NOTE: no narration delay — captions are timed to the
    # original audio, so shifting the voice would desync them.
    sfx_inputs = []   # (path, volume, delay_seconds, strip_leading_silence)
    if INTRO_DING and TITLE_INTRO and os.path.exists(DING_SOUND_PATH):
        sfx_inputs.append((DING_SOUND_PATH, 0.45, 0.0, False))
    elif TITLE_INTRO and os.path.exists(DING_SOUND_PATH):
        print("   🔕 Intro ding disabled (INTRO_DING=false)")
    if TITLE_INTRO and os.path.exists(WHOOSH_SOUND_PATH) \
            and intro_frame and os.path.exists(intro_frame):
        total_words = len((script or "").split())
        title_words = len(title.split()) if title else 0
        if total_words and title_words:
            title_secs = (audio_duration * title_words / total_words) / VOICE_SPEED
            title_secs = title_secs + TITLE_HOLD_SEC
            final_dur = audio_duration / VOICE_SPEED
            title_secs = min(max(title_secs, TITLE_MIN_SEC), TITLE_MAX_SEC, max(final_dur - 0.5, 1.0))
            whoosh_at = max(title_secs - TITLE_FADE_SEC, 0.0)
            sfx_inputs.append((WHOOSH_SOUND_PATH, 0.7, whoosh_at, True))
            print(f"   🔊 Whoosh scheduled at {whoosh_at:.1f}s (card exit)")

    for name, _mov, abs_start, _dur in ending_overlays:
        if name == "subscribe":
            src = os.path.join(OVERLAY_DIR, "subscribe_audio.wav")
            if not os.path.exists(src):
                src = os.path.join(ANIM_DIR, "subscribe_green.weba")
        else:
            src = os.path.join(OVERLAY_DIR, "like_audio.wav")
            if not os.path.exists(src):
                src = os.path.join(ANIM_DIR, "like_green.m4a")
        if os.path.exists(src):
            sfx_inputs.append((src, 1.2, abs_start, False))
        else:
            print(f"   ⚠️ {name} animation audio not found ({os.path.basename(src)})")

    if sfx_inputs:
        print(f"🔊 Mixing {len(sfx_inputs)} extra audio track(s)...")
        audio_with_sfx = os.path.join(output_dir, f"audio_with_sfx_{int(time.time())}.mp3")
        parts = ["[0:a]volume=1.0[voice]"]
        mix_labels = "[voice]"
        for n, (path, vol, delay, strip) in enumerate(sfx_inputs, start=1):
            chain = ""
            if strip:
                chain += ("silenceremove=start_periods=1:start_threshold=-40dB:"
                          "start_silence=0,")
            chain += f"volume={vol},adelay={int(delay * 1000)}|{int(delay * 1000)}[x{n}]"
            parts.append(f"[{n}:a]{chain}")
            mix_labels += f"[x{n}]"
        parts.append(f"{mix_labels}amix=inputs={len(sfx_inputs) + 1}:"
                     f"duration=first:normalize=0[aout]")
        cmd_sfx = ['ffmpeg', '-y', '-i', final_audio]
        for path, _v, _d, _s in sfx_inputs:
            cmd_sfx += ['-i', path]
        cmd_sfx += [
            '-filter_complex', ';'.join(parts),
            '-map', '[aout]',
            '-t', str(get_duration(final_audio)),
            '-ac', '2',
            '-acodec', 'mp3',
            '-b:a', '192k',
            audio_with_sfx
        ]
        try:
            subprocess.run(cmd_sfx, check=True, capture_output=True, timeout=180)
            print("   ✅ Extra audio mixed: "
                  + ", ".join(f"{os.path.basename(p)}@{d:.1f}s" for p, _v, d, _s in sfx_inputs))
            if final_audio != audio_path and os.path.exists(final_audio):
                os.unlink(final_audio)
            final_audio = audio_with_sfx
        except Exception as e:
            print(f"   ⚠️ Extra audio mixing failed: {e}")
            print("   Continuing without extra audio...")
    else:
        print("   ⚠️ No ding/whoosh/animation audio to mix")
    
    # --- FINAL VIDEO COMPILATION (YouTube-Compatible) ---
    print("⚡ Adding audio to video...")
    _ts = int(time.time())
    if output_name_captioned and srt_abs:
        # Captions were burned inside the render, so THIS is the delivered
        # file. The "_captioned_" name is kept so the TikTok/Facebook/YouTube
        # uploaders and verify_youtube_compat (which glob for it) keep working.
        final_output = os.path.join(output_dir, f"output_{_ts}_captioned_{_ts}.mp4")
    else:
        final_output = os.path.join(output_dir, f"output_{_ts}.mp4")
    # FIXED: mux with -c:v copy instead of re-encoding. The segments are
    # already encoded at the final setting, so a second full encode of the
    # whole video would be pure wasted CPU and a major timeout cause.
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
    
    if gameplay_segment and os.path.exists(gameplay_segment):
        os.unlink(gameplay_segment)
    
    final_size = os.path.getsize(final_output) / (1024 * 1024)
    final_duration = get_duration(final_output)
    
    print(f"✅ Video compiled successfully: {final_output}")
    print(f"   📊 Video info:")
    print(f"      - Resolution: {OUTPUT_W}x{OUTPUT_H} (9:16, "
          f"{'single-pass from source' if direct_mode else 'staged 4K intermediate'})")
    print(f"      - Captions: {'✅ burned in this encode' if srt_abs else '❌ none'}")
    print(f"      - Ending overlay: "
              f"{'✅ baked into this encode' if ending_baked else ('❌ off (ENDING_ANIMATIONS=false)' if not ENDING_ANIMATIONS else '❌ none')}")
    print(f"      - Video codec: H.264 (High Profile)")
    print(f"      - Audio codec: AAC 192kbps")
    print(f"      - Video speed: {SPEED_FACTOR}x")
    print(f"      - Voice speed: {VOICE_SPEED}x")
    print(f"      - Voice loudness: normalized to {VOICE_LUFS_TARGET:.0f} LUFS (no clipping)")
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
            f"fontsize={24 * OUTPUT_H // 1920}:"  # scaled for 1440p frame height
            f"fontfile=/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf:"
            f"bordercolor=black:"
            f"borderw=2:"
            f"x=(w-text_w)/2:"
            f"y={round(50 * OUTPUT_H / 1920)}:"
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
    
    # --- SUBSCRIBE / LIKE ENDING OVERLAY (FALLBACK PATH ONLY) ---
    # Normally baked into the render (see the render loop), so this whole-video
    # re-encode runs only when the overlays could NOT be baked. It is kept as a
    # working fallback, NOT as the primary path: it re-encodes every frame of
    # the finished video just to draw two buttons in the last ~29s.
    if ending_baked:
        print("\n🔔 Subscribe/like ending overlay already baked into the render "
              "— no whole-video re-encode needed")
    elif not ENDING_ANIMATIONS:
        print("\n🔕 Subscribe/like ending overlay disabled (ENDING_ANIMATIONS=false) "
              "— no buttons, no sound, no re-encode")
    elif os.path.exists(SUBSCRIBE_MOV) and os.path.exists(LIKE_MOV):
        print("\n🔔 Adding subscribe/like ending overlay (separate pass)...")

        # Timing: subscribe starts 20s before end (6s duration), like starts after subscribe
        subscribe_dur = 6.0
        like_dur = 2.74
        subscribe_start = max(final_duration - 20.0, 0)
        like_start = subscribe_start + subscribe_dur

        print(f"   Subscribe@{subscribe_start:.1f}s, Like@{like_start:.1f}s")

        # Positions for 1440x2560 — lowered to match TikTok engagement area
        # (like/comment/share sit ~55-65% down on TikTok)
        sub_w, sub_h = 400, 404
        sub_x, sub_y = 520, 1450
        like_w, like_h = 175, 163
        like_x, like_y = 634, 1600

        # STEP 1: Video overlay using -itsoffset (proven working method)
        ending_output = final_output.replace(".mp4", "_ending.mp4")
        filter_script = (
            f"[1:v]scale={sub_w}:{sub_h},format=rgba[sb];"
            f"[0:v][sb]overlay=x={sub_x}:y={sub_y}:format=auto[v1];"
            f"[2:v]scale={like_w}:{like_h},format=rgba[lk];"
            f"[v1][lk]overlay=x={like_x}:y={like_y}:format=auto[vout];"
            f"[0:a]acopy[aout]"
        )
        inputs = [
            '-i', final_output,
            '-itsoffset', str(subscribe_start), '-i', SUBSCRIBE_MOV,
            '-itsoffset', str(like_start), '-i', LIKE_MOV,
        ]
        # FIXED (blurry background): this whole-video pass used to re-encode
        # EVERY frame at CRF 23 + ultrafast — the worst quality in the whole
        # chain — and its output became the input for the caption burn, so
        # the softness carried into the final file. The buttons only occupy
        # the last ~20s, but the re-encode touches all frames.
        # IMPORTANT (quality): this re-encode is LOSSY, so it must stay
        # near-transparent or the final file inherits its softness. The old
        # CRF 18 + ultrafast capped the whole video at that quality. Now
        # CRF 15 + veryfast keeps the overlay pass from degrading the CRF 15
        # background. (The caption burn that follows is CRF 18 slow — fine,
        # the preset's motion estimation matters more than the last CRF step.)
        cmd_ending = [
            'ffmpeg', '-y', *inputs,
            '-filter_complex', filter_script,
            '-map', '[vout]', '-map', '[aout]',
            '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '15',
            '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-b:a', '128k',
            '-movflags', '+faststart',
            ending_output
        ]
        try:
            # Whole-video re-encode: 600s is fine for shorts but a long
            # full-story video needs more headroom. veryfast is slower than
            # the old ultrafast, so 1800s (30 min) keeps the subscribe/like
            # overlay from being dropped on long videos.
            run_ffmpeg(cmd_ending, timeout=1800, label="ending video overlay")
        except Exception as e:
            print(f"   ⚠️ Ending overlay failed: {e}")
            print("   Continuing without ending overlay...")
            ending_output = None

        # STEP 2: Audio mix (separate pass — amix filter is unreliable in complex chains)
        if ending_output and os.path.exists(ending_output):
            has_sub_audio = os.path.exists(os.path.join(ANIM_DIR, "subscribe_green.weba")) or \
                           os.path.exists(os.path.join(OVERLAY_DIR, "subscribe_audio.wav"))
            has_like_audio = os.path.exists(os.path.join(ANIM_DIR, "like_green.m4a")) or \
                            os.path.exists(os.path.join(OVERLAY_DIR, "like_audio.wav"))
            if has_sub_audio or has_like_audio:
                print("   🔊 Mixing animation audio...")
                # Extract audio to wav
                sub_wav = os.path.join(tempfile.gettempdir(), "sub_audio_pipe.wav")
                like_wav = os.path.join(tempfile.gettempdir(), "like_audio_pipe.wav")
                sub_src = os.path.join(OVERLAY_DIR, "subscribe_audio.wav")
                if not os.path.exists(sub_src):
                    sub_src = os.path.join(ANIM_DIR, "subscribe_green.weba")
                like_src = os.path.join(OVERLAY_DIR, "like_audio.wav")
                if not os.path.exists(like_src):
                    like_src = os.path.join(ANIM_DIR, "like_green.m4a")
                if has_sub_audio:
                    subprocess.run(['ffmpeg', '-y', '-i', sub_src, '-acodec', 'pcm_s16le', '-ar', '48000', sub_wav],
                                   check=True, capture_output=True, timeout=30)
                if has_like_audio:
                    subprocess.run(['ffmpeg', '-y', '-i', like_src, '-acodec', 'pcm_s16le', '-ar', '48000', like_wav],
                                   check=True, capture_output=True, timeout=30)
                # Build audio filter
                audio_filters = []
                mix_inputs = "[0:a]"
                n_mix = 1
                if has_sub_audio:
                    audio_filters.append(f"[1:a]volume=1.2,adelay={int(subscribe_start*1000)}|{int(subscribe_start*1000)}[sa]")
                    mix_inputs += "[sa]"
                    n_mix += 1
                if has_like_audio:
                    idx = 2 if has_sub_audio else 1
                    audio_filters.append(f"[{idx}:a]volume=1.2,adelay={int(like_start*1000)}|{int(like_start*1000)}[la]")
                    mix_inputs += "[la]"
                    n_mix += 1
                audio_filters.append(f"{mix_inputs}amix=inputs={n_mix}:duration=first:dropout_transition=0:normalize=0[aout]")
                audio_fc = ";".join(audio_filters)
                audio_inputs = ['-i', ending_output]
                if has_sub_audio:
                    audio_inputs += ['-i', sub_wav]
                if has_like_audio:
                    audio_inputs += ['-i', like_wav]
                # FIX: write to temp file first — ffmpeg can't edit in-place
                audio_tmp = ending_output.replace(".mp4", "_audio_tmp.mp4")
                cmd_audio = [
                    'ffmpeg', '-y', *audio_inputs,
                    '-filter_complex', audio_fc,
                    '-map', '0:v', '-map', '[aout]',
                    '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
                    '-movflags', '+faststart', audio_tmp
                ]
                try:
                    run_ffmpeg(cmd_audio, timeout=300, label="ending audio mix")
                    # Swap: replace original with audio-mixed version
                    os.replace(audio_tmp, ending_output)
                except Exception as e:
                    print(f"   ⚠️ Audio mix failed: {e}")
                    # Clean up temp file if it exists
                    try:
                        if os.path.exists(audio_tmp):
                            os.unlink(audio_tmp)
                    except OSError:
                        pass

        if ending_output and os.path.exists(ending_output):
            os.unlink(final_output)
            final_output = ending_output
            # BUG FIX: extract audio from the ending video so the caption step
            # gets the version WITH subscribe/like sounds. Previously, final_audio
            # was the pre-overlay audio (no subscribe sound), and the caption step
            # muxed THAT back in, stripping the subscribe audio we just mixed.
            try:
                ending_audio = final_output.replace(".mp4", "_audio.wav")
                subprocess.run([
                    'ffmpeg', '-y', '-i', final_output,
                    '-vn', '-acodec', 'pcm_s16le', '-ar', '48000', ending_audio
                ], check=True, capture_output=True, timeout=60)
                if os.path.exists(final_audio) and final_audio != ending_audio:
                    try:
                        os.unlink(final_audio)
                    except OSError:
                        pass
                final_audio = ending_audio
                print(f"   🔊 Updated return audio to include subscribe/like sounds")
            except Exception as e:
                print(f"   ⚠️ Could not extract ending audio: {e}")
            print(f"   ✅ Ending overlay added")
        else:
            print("   ⚠️ Ending overlay skipped")
    else:
        print(f"   ⚠️ Subscribe animation not found — run prepare_animations.py first")

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
