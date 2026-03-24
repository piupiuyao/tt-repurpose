import json
import os
import io
import time
from pathlib import Path
from google import genai
from google.genai import types
from PIL import Image


def load_image_part(image_path: Path) -> types.Part:
    with open(image_path, "rb") as f:
        data = f.read()
    return types.Part.from_bytes(data=data, mime_type="image/png")


def save_image_from_response(response, path: Path) -> bool:
    for part in response.candidates[0].content.parts:
        if part.inline_data:
            img = Image.open(io.BytesIO(part.inline_data.data))
            img.save(path)
            return True
    return False


def run(output_dir: Path, style: str = "fruit-drama") -> dict:
    script_path = output_dir / "script.json"
    if not script_path.exists():
        raise FileNotFoundError(f"script.json not found — run rewrite first")

    with open(script_path) as f:
        script = json.load(f)

    images_dir = output_dir / "images"
    images_dir.mkdir(exist_ok=True)

    # Prefer auto-detected config, fall back to static style config
    dynamic_config = output_dir / "style_config.json"
    if dynamic_config.exists():
        config_path = dynamic_config
    else:
        config_path = Path(__file__).parent.parent / "config" / f"{style.replace('-', '_')}.json"

    with open(config_path) as f:
        style_config = json.load(f)

    # Build ref frame mapping from config
    frames_dir = output_dir / "frames"
    all_frames = sorted(frames_dir.glob("frame_*.png"))
    default_female = all_frames[0].name if all_frames else "frame_0001.png"
    default_male = all_frames[min(1, len(all_frames)-1)].name if all_frames else "frame_0001.png"

    FEMALE_REF_FRAME = style_config.get("female_ref_frame", default_female)
    MALE_REF_FRAME   = style_config.get("male_ref_frame", default_male)

    CHARACTER_FRAME_HINT = {}
    for char in style_config.get("characters", {}).get("females", []):
        role = "child" if char.get("family_role") == "child" else "female"
        CHARACTER_FRAME_HINT[char["name"]] = (role, char.get("ref_hint", "one of the female characters"))
    for char in style_config.get("characters", {}).get("males", []):
        role = "child" if char.get("family_role") == "child" else "male"
        CHARACTER_FRAME_HINT[char["name"]] = (role, char.get("ref_hint", "one of the male characters"))

    all_characters = (
        style_config["characters"]["females"] +
        style_config["characters"]["males"]
    )

    frames_dir = output_dir / "frames"
    female_ref = load_image_part(frames_dir / FEMALE_REF_FRAME)
    male_ref   = load_image_part(frames_dir / MALE_REF_FRAME)

    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    model = "models/gemini-2.5-flash-image"
    gen_config = types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"])

    results = {}

    # ─────────────────────────────────────────────
    # Step 4a: Generate character portraits
    # Each portrait uses the matching reference frame
    # ─────────────────────────────────────────────
    print("\n[Step 4a] Generating character reference portraits...")
    char_images = {}

    for char in all_characters:
        name = char["name"]
        desc = char["description"]
        portrait_path = images_dir / f"char_{name.lower().replace(' ', '_')}.png"

        if portrait_path.exists():
            print(f"  >> {name}: already exists, skipping")
            char_images[name] = portrait_path
            continue

        gender, ref_hint = CHARACTER_FRAME_HINT.get(name, ("female", "one of the characters"))
        ref_frame = female_ref if gender == "female" else male_ref

        print(f"  >> Generating portrait: {name}...")
        contents = [
            "Reference image — use this ONLY to match the 3D render art style, lighting quality, and body proportions. Do NOT copy any character design from it:",
            ref_frame,
            f"Create a brand new original character called {name}. "
            f"{desc}. "
            f"Match the same hyperrealistic 3D render quality, body proportions, and lighting style as the reference image. "
            f"Full body shot, neutral pose, pure white background. "
            f"Hyperrealistic 3D render, warm studio lighting.",
        ]
        try:
            response = client.models.generate_content(
                model=model, contents=contents, config=gen_config
            )
            if save_image_from_response(response, portrait_path):
                print(f"     Saved: {portrait_path.name}")
                char_images[name] = portrait_path
            else:
                print(f"     WARNING: No image for {name}")
        except Exception as e:
            print(f"     ERROR for {name}: {e}")
        time.sleep(3)

    results["character_portraits"] = {k: str(v) for k, v in char_images.items()}

    # ─────────────────────────────────────────────
    # Step 4b: Generate scene images
    # Each scene is a FRESH independent request — no chat history
    # Only relevant character portraits are passed each time
    # ─────────────────────────────────────────────
    print("\n[Step 4b] Generating scene images (fresh request per scene)...")

    beats = script.get("beats", [])
    scene_images = {}

    for beat in beats:
        beat_num = beat["beat_number"]
        image_prompt = beat.get("image_prompt", "")
        if not image_prompt:
            continue

        scene_path = images_dir / f"scene_{beat_num:02d}.png"
        if scene_path.exists():
            print(f"  >> Beat {beat_num}: already exists, skipping")
            scene_images[beat_num] = scene_path
            continue

        # Find which characters appear in this scene
        scene_chars = set()
        for line in beat.get("dialogue", []):
            name = line.get("character", "")
            if name in char_images:
                scene_chars.add(name)
        for name in char_images:
            if name in image_prompt:
                scene_chars.add(name)
        if not scene_chars:
            scene_chars = set(char_images.keys())

        print(f"  >> Generating scene {beat_num}: {beat['beat_name']} ({', '.join(scene_chars)})...")

        # Fresh request — portraits + prompt only, no history
        char_names_str = ", ".join(scene_chars)
        # Build per-character identity reminder to prevent mixing up similar-looking characters
        identity_reminders = []
        for name in scene_chars:
            hint = CHARACTER_FRAME_HINT.get(name, (None, None))[1]
            if hint:
                identity_reminders.append(f"{name} = {hint}")
        identity_str = "; ".join(identity_reminders)

        contents = []
        for name in scene_chars:
            contents.append(f"{name} (reference portrait — match exactly):")
            contents.append(load_image_part(char_images[name]))
        # Build size consistency instruction
        child_chars = [n for n in scene_chars if CHARACTER_FRAME_HINT.get(n, (None,))[0] == "child"]
        adult_chars = [n for n in scene_chars if n not in child_chars]
        size_note = "IMPORTANT size consistency: all adult characters are the same full height as in portraits. "
        if child_chars:
            size_note += f"Child characters ({', '.join(child_chars)}) are exactly half the height of adults — small, compact, child-sized bodies. "

        contents.append(
            f"Keep the exact same {char_names_str} characters from the reference portraits above. "
            f"IMPORTANT character identities: {identity_str}. Do NOT mix up their head shapes. "
            f"{size_note}"
            f"{image_prompt} "
            f"Hyperrealistic 3D render, warm sunny lighting, 9:16 vertical."
        )

        try:
            response = client.models.generate_content(
                model=model, contents=contents, config=gen_config
            )
            if save_image_from_response(response, scene_path):
                print(f"     Saved: {scene_path.name}")
                scene_images[beat_num] = scene_path
            else:
                print(f"     WARNING: No image for beat {beat_num}")
        except Exception as e:
            print(f"     ERROR for beat {beat_num}: {e}")
        time.sleep(3)

    results["scene_images"] = {k: str(v) for k, v in scene_images.items()}

    manifest_path = output_dir / "images_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n[Step 4] Done. {len(char_images)} portraits + {len(scene_images)} scenes saved.")
    return results
