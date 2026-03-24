import os
import json
import subprocess
import shutil
from pathlib import Path


def run_cmd(cmd: list[str], description: str = "") -> subprocess.CompletedProcess:
    print(f"  >> {description or ' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{result.stderr}")
    return result


def download_video(url: str, output_dir: Path) -> Path:
    output_path = output_dir / "input.mp4"
    run_cmd(
        ["yt-dlp", "--no-playlist", "-o", str(output_path), url],
        "Downloading video with yt-dlp"
    )
    if not output_path.exists():
        # yt-dlp may add extension, find the file
        candidates = list(output_dir.glob("input.*"))
        if not candidates:
            raise RuntimeError("Download failed: no output file found")
        output_path = candidates[0]
    return output_path


def transcribe(video_path: Path, output_dir: Path) -> dict:
    print("  >> Transcribing with Whisper (model: medium)")
    run_cmd(
        [
            "whisper", str(video_path),
            "--model", "medium",
            "--language", "en",
            "--output_format", "json",
            "--output_dir", str(output_dir),
        ],
        "Running Whisper transcription"
    )
    # Whisper outputs <stem>.json
    json_path = output_dir / (video_path.stem + ".json")
    if not json_path.exists():
        raise RuntimeError(f"Whisper output not found at {json_path}")

    # Rename to transcript.json
    transcript_json = output_dir / "transcript.json"
    json_path.rename(transcript_json)

    with open(transcript_json) as f:
        data = json.load(f)

    # Also write plain text transcript
    transcript_txt = output_dir / "transcript.txt"
    with open(transcript_txt, "w") as f:
        f.write(data.get("text", "").strip())

    print(f"  >> Transcript saved: {transcript_json}, {transcript_txt}")
    return data


def extract_keyframes(video_path: Path, output_dir: Path) -> list[Path]:
    frames_dir = output_dir / "frames"
    frames_dir.mkdir(exist_ok=True)

    run_cmd(
        [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-vf", "fps=0.5",
            str(frames_dir / "frame_%04d.png"),
        ],
        "Extracting frames at 0.5fps (one every 2 seconds)"
    )

    frames = sorted(frames_dir.glob("frame_*.png"))
    print(f"  >> Extracted {len(frames)} keyframes")
    return frames


def run(url: str, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)

    print("\n[Step 1] Downloading video...")
    video_path = download_video(url, output_dir)

    print("\n[Step 1] Transcribing audio...")
    transcript_data = transcribe(video_path, output_dir)

    print("\n[Step 1] Extracting keyframes...")
    frames = extract_keyframes(video_path, output_dir)

    result = {
        "video_path": str(video_path),
        "transcript_json": str(output_dir / "transcript.json"),
        "transcript_txt": str(output_dir / "transcript.txt"),
        "frames": [str(f) for f in frames],
        "frame_count": len(frames),
    }

    print(f"\n[Step 1] Done. Output in: {output_dir}")
    print(f"         Frames: {len(frames)} | Transcript words: {len(transcript_data.get('text','').split())}")
    return result
