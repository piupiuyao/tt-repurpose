import json
import os
import time
import base64
import requests
from pathlib import Path


XAI_BASE = "https://api.x.ai/v1"


def submit_video(prompt: str, image_path: Path = None) -> str:
    headers = {
        "Authorization": f"Bearer {os.environ['XAI_API_KEY']}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "grok-imagine-video",
        "prompt": prompt,
    }
    if image_path and image_path.exists():
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        payload["image"] = {"url": f"data:image/png;base64,{b64}"}

    resp = requests.post(f"{XAI_BASE}/videos/generations", headers=headers, json=payload)
    resp.raise_for_status()
    return resp.json()["request_id"]


def poll_video(request_id: str, timeout: int = 300) -> str:
    headers = {"Authorization": f"Bearer {os.environ['XAI_API_KEY']}"}
    start = time.time()
    while time.time() - start < timeout:
        resp = requests.get(f"{XAI_BASE}/videos/{request_id}", headers=headers)
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status")
        if status == "done":
            return data["video"]["url"]
        elif status in ("failed", "error"):
            raise RuntimeError(f"Video generation failed: {data}")
        print(f"     Status: {status}, waiting...")
        time.sleep(8)
    raise TimeoutError(f"Video not ready after {timeout}s")


def download_video(url: str, path: Path):
    resp = requests.get(url, stream=True)
    resp.raise_for_status()
    with open(path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)


def run(output_dir: Path) -> dict:
    script_path = output_dir / "script.json"
    if not script_path.exists():
        raise FileNotFoundError("script.json not found — run rewrite first")

    with open(script_path) as f:
        script = json.load(f)

    images_dir = output_dir / "images"
    videos_dir = output_dir / "videos"
    videos_dir.mkdir(exist_ok=True)

    beats = script.get("beats", [])
    results = {}

    print(f"\n[Step 5] Animating {len(beats)} scenes with Grok video...")

    for beat in beats:
        beat_num = beat["beat_number"]
        grok_prompt = beat.get("grok_prompt", "")
        if not grok_prompt:
            print(f"  >> Beat {beat_num}: no grok_prompt, skipping")
            continue

        video_path = videos_dir / f"scene_{beat_num:02d}.mp4"
        if video_path.exists():
            print(f"  >> Beat {beat_num}: already exists, skipping")
            results[beat_num] = str(video_path)
            continue

        # Use corresponding scene image as reference
        scene_image = images_dir / f"scene_{beat_num:02d}.png"

        print(f"  >> Beat {beat_num}: {beat['beat_name']}")
        print(f"     Prompt: {grok_prompt[:80]}...")

        try:
            request_id = submit_video(grok_prompt, scene_image)
            print(f"     Submitted: {request_id}")
            video_url = poll_video(request_id)
            download_video(video_url, video_path)
            size_mb = video_path.stat().st_size / 1024 / 1024
            print(f"     Saved: {video_path.name} ({size_mb:.1f}MB)")
            results[beat_num] = str(video_path)
        except Exception as e:
            print(f"     ERROR for beat {beat_num}: {e}")

        time.sleep(3)

    manifest_path = output_dir / "videos_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n[Step 5] Done. {len(results)}/{len(beats)} videos generated.")
    return results
