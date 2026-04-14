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


def has_audio(video_path: Path) -> bool:
    """Check if video file has an audio stream."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-select_streams", "a",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(video_path)],
        capture_output=True, text=True
    )
    return bool(result.stdout.strip())


def transcribe(video_path: Path, output_dir: Path) -> dict:
    transcript_json = output_dir / "transcript.json"
    transcript_txt = output_dir / "transcript.txt"

    if not has_audio(video_path):
        print("  >> No audio track found — skipping transcription")
        data = {"text": "", "segments": []}
        with open(transcript_json, "w") as f:
            json.dump(data, f)
        with open(transcript_txt, "w") as f:
            f.write("")
        return data

    # Extract audio to mp3 for API upload
    audio_path = output_dir / "audio.mp3"
    print("  >> Extracting audio track...")
    run_cmd(
        ["ffmpeg", "-i", str(video_path), "-vn", "-acodec", "libmp3lame",
         "-q:a", "4", str(audio_path), "-y"],
        "Extracting audio to mp3"
    )

    print("  >> Transcribing with Gemini...")
    from google import genai

    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])

    # Upload audio file
    audio_file = client.files.upload(file=audio_path)

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            audio_file,
            (
                "Transcribe this audio exactly as spoken. "
                "Return ONLY a JSON object with this format:\n"
                '{"text": "full transcript here", "segments": [{"start": 0.0, "end": 2.5, "text": "segment text"}]}\n'
                "Include timestamps for each natural sentence or phrase. Return ONLY valid JSON, no markdown."
            ),
        ],
    )

    import re
    raw = response.text.strip()
    # Strip markdown code fences if present
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    data = json.loads(raw)

    with open(transcript_json, "w") as f:
        json.dump(data, f, indent=2)

    with open(transcript_txt, "w") as f:
        f.write(data.get("text", "").strip())

    # Clean up temp audio
    audio_path.unlink(missing_ok=True)

    print(f"  >> Transcript saved: {transcript_json}, {transcript_txt}")
    return data


def detect_scenes(video_path: Path, output_dir: Path) -> list[dict]:
    """Use PySceneDetect to find scene boundaries and extract a representative frame per scene."""
    from scenedetect import open_video, SceneManager, ContentDetector
    from scenedetect.scene_manager import save_images

    frames_dir = output_dir / "frames"
    frames_dir.mkdir(exist_ok=True)

    print("  >> Detecting scene boundaries with PySceneDetect...")
    video = open_video(str(video_path))
    sm = SceneManager()
    sm.add_detector(ContentDetector(threshold=35, min_scene_len=15))
    sm.detect_scenes(video)
    scene_list = sm.get_scene_list()

    # Merge scenes shorter than 2s into the next scene to avoid too many beats
    MIN_SCENE_SEC = 2.0
    merged = []
    for scene in scene_list:
        dur = scene[1].get_seconds() - scene[0].get_seconds()
        if merged and dur < MIN_SCENE_SEC:
            merged[-1] = (merged[-1][0], scene[1])
        else:
            merged.append(scene)
    scene_list = merged

    if not scene_list:
        print("  >> No scene cuts detected — falling back to single-scene")
        # Get total duration
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video_path)],
            capture_output=True, text=True
        )
        total_dur = float(result.stdout.strip())
        scene_list = [(video.base_timecode, video.base_timecode + total_dur)]

    # Save middle frame of each scene
    save_images(
        scene_list,
        video,
        num_images=1,
        output_dir=str(frames_dir),
        image_name_template='$SCENE_NUMBER',
        image_extension='png',
    )

    # Rename saved frames to frame_XXXX.png format for compatibility
    scenes_meta = []
    for i, (start, end) in enumerate(scene_list):
        scene_num = i + 1
        # PySceneDetect saves as 001.png, 002.png, etc.
        src = frames_dir / f"{scene_num:03d}.png"
        dst = frames_dir / f"frame_{scene_num:04d}.png"
        if src.exists():
            src.rename(dst)

        duration = round(end.get_seconds() - start.get_seconds(), 1)
        scenes_meta.append({
            "scene_number": scene_num,
            "start": round(start.get_seconds(), 2),
            "end": round(end.get_seconds(), 2),
            "duration": duration,
            "frame": f"frame_{scene_num:04d}.png",
        })

    # Save scene metadata
    scenes_json = output_dir / "scenes.json"
    with open(scenes_json, "w") as f:
        json.dump(scenes_meta, f, indent=2)

    frames = sorted(frames_dir.glob("frame_*.png"))
    total_dur = sum(s["duration"] for s in scenes_meta)
    print(f"  >> Detected {len(scenes_meta)} scenes ({total_dur:.1f}s total)")
    for s in scenes_meta:
        print(f"     Scene {s['scene_number']}: {s['start']:.1f}s - {s['end']:.1f}s ({s['duration']:.1f}s)")
    return scenes_meta


def run(url: str, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)

    print("\n[Step 1] Downloading video...")
    video_path = download_video(url, output_dir)

    print("\n[Step 1] Transcribing audio...")
    transcript_data = transcribe(video_path, output_dir)

    print("\n[Step 1] Detecting scenes and extracting keyframes...")
    scenes_meta = detect_scenes(video_path, output_dir)

    frames = sorted((output_dir / "frames").glob("frame_*.png"))
    result = {
        "video_path": str(video_path),
        "transcript_json": str(output_dir / "transcript.json"),
        "transcript_txt": str(output_dir / "transcript.txt"),
        "scenes_json": str(output_dir / "scenes.json"),
        "frames": [str(f) for f in frames],
        "frame_count": len(frames),
        "scene_count": len(scenes_meta),
    }

    print(f"\n[Step 1] Done. Output in: {output_dir}")
    print(f"         Scenes: {len(scenes_meta)} | Transcript words: {len(transcript_data.get('text','').split())}")
    return result
