import json
import subprocess
import math
from pathlib import Path


def get_duration(video_path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(video_path)],
        capture_output=True, text=True
    )
    return float(result.stdout.strip())


def srt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def extract_lines_from_grok_prompt(grok_prompt: str) -> list[str]:
    """Extract spoken lines from grok_prompt.
    Handles both double quotes and single quotes (including contractions like you're, I'm).
    """
    import re
    # Try double quotes first (no apostrophe issue)
    results = re.findall(r'says[:\s]+"([^"]+)"', grok_prompt, re.IGNORECASE)
    if results:
        return results
    # Single quotes: closing quote must NOT be followed by a letter (to allow contractions)
    results = re.findall(r"says[:\s]+'(.*?)'(?![a-zA-Z])", grok_prompt, re.IGNORECASE | re.DOTALL)
    return results


def generate_srt(beats: list, durations: list) -> str:
    lines = []
    idx = 1
    t = 0.0

    for beat, dur in zip(beats, durations):
        # Extract dialogue from grok_prompt (reflects what was actually sent to Grok)
        grok_prompt = beat.get("grok_prompt", "")
        spoken_lines = extract_lines_from_grok_prompt(grok_prompt) if grok_prompt else []

        # Fallback to dialogue array if grok_prompt has no extractable lines
        if not spoken_lines:
            spoken_lines = [d["line"] for d in beat.get("dialogue", [])]

        if not spoken_lines:
            t += dur
            continue

        usable = dur - 1.0
        per_line = usable / len(spoken_lines)

        for i, text in enumerate(spoken_lines):
            start = t + 0.5 + i * per_line
            end = start + per_line - 0.2
            lines.append(f"{idx}\n{srt_time(start)} --> {srt_time(end)}\n{text}\n")
            idx += 1

        t += dur

    return "\n".join(lines)


def run(output_dir: Path) -> Path:
    script_path = output_dir / "script.json"
    videos_dir = output_dir / "videos"

    with open(script_path) as f:
        script = json.load(f)

    beats = script["beats"]

    # Collect scene videos in order
    scene_videos = []
    durations = []
    for beat in beats:
        n = beat["beat_number"]
        p = videos_dir / f"scene_{n:02d}.mp4"
        if not p.exists():
            raise FileNotFoundError(f"Missing video: {p}")
        scene_videos.append(p)
        durations.append(get_duration(p))

    print(f"\n[Step 6] Assembling {len(scene_videos)} scenes ({sum(durations):.1f}s total)...")

    # Write concat list
    concat_file = output_dir / "concat.txt"
    with open(concat_file, "w") as f:
        for v in scene_videos:
            f.write(f"file '{v.resolve()}'\n")

    # Generate SRT
    srt_content = generate_srt(beats, durations)
    srt_path = output_dir / "subtitles.srt"
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(srt_content)
    print(f"  >> Subtitles written: {srt_path}")

    # Concatenate videos
    raw_path = output_dir / "raw_concat.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy", str(raw_path)
    ], check=True, capture_output=True)
    print(f"  >> Concatenated: {raw_path.name}")

    # Burn subtitles with TikTok style
    final_path = output_dir / "final.mp4"
    subtitle_filter = (
        f"subtitles={srt_path}:force_style='"
        "FontName=Arial,"
        "FontSize=16,"
        "PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,"
        "BackColour=&H80000000,"
        "Bold=1,"
        "Outline=2,"
        "Shadow=1,"
        "Alignment=2,"
        "MarginV=40"
        "'"
    )
    subprocess.run([
        "ffmpeg", "-y",
        "-i", str(raw_path),
        "-vf", subtitle_filter,
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-c:a", "copy",
        str(final_path)
    ], check=True, capture_output=True)

    size_mb = final_path.stat().st_size / 1024 / 1024
    print(f"  >> Final video: {final_path.name} ({size_mb:.1f}MB)")
    print(f"\n[Step 6] Done! {final_path}")
    return final_path
