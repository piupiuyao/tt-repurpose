import json
import os
import io
import time
from pathlib import Path
from google import genai
from google.genai import types
from PIL import Image

# Safety vocabulary: replace terms that trigger content filters with softer equivalents
SAFETY_REPLACEMENTS = {
    "physical altercation": "tense standoff",
    "violent struggle": "intense moment",
    "violent confrontation": "dramatic confrontation",
    "violent": "intense",
    "violence": "tension",
    "grabs aggressively": "holds firmly",
    "aggressively": "firmly",
    "aggressive": "determined",
    "grabs": "holds",
    "grab": "hold",
    "attack": "confront",
    "attacks": "confronts",
    "fight": "confrontation",
    "fighting": "tense confrontation",
    "fights": "confronts",
    "hits": "reaches toward",
    "threatening": "intense",
    "threaten": "confront",
    "harsh lighting": "dramatic lighting",
}


def _structure_image_prompt(image_prompt: str, scene_chars_ordered: list, all_characters: list) -> str:
    """Apply safety replacements and add structured CHARACTER labels for multi-char scenes."""
    safe_prompt = image_prompt
    for bad, good in SAFETY_REPLACEMENTS.items():
        safe_prompt = safe_prompt.replace(bad, good)

    n = len(scene_chars_ordered)
    if n <= 1:
        return safe_prompt

    positions = ["left", "center", "right", "foreground left", "foreground right"]
    char_lines = []
    for i, name in enumerate(scene_chars_ordered):
        pos = positions[i] if i < len(positions) else f"position {i+1}"
        brief = ""
        for c in all_characters:
            if c["name"] == name:
                brief = c.get("ref_hint", c.get("description", "")[:100])
                break
        char_lines.append(f"CHARACTER {i+1} ({name}, {pos}): {brief}")

    char_block = "\n".join(char_lines)
    return (
        f"EXACTLY {n} anthropomorphic food characters, ALL fully visible in frame, "
        f"each with a DISTINCT head and body — do NOT merge any two characters into one body.\n"
        f"CHARACTERS IN THIS SCENE:\n{char_block}\n"
        f"SCENE: {safe_prompt}"
    )


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


def _load_config(output_dir: Path, style: str = "fruit-drama"):
    """Load style config and build character metadata."""
    dynamic_config = output_dir / "style_config.json"
    if dynamic_config.exists():
        config_path = dynamic_config
    else:
        config_path = Path(__file__).parent.parent / "config" / f"{style.replace('-', '_')}.json"

    with open(config_path) as f:
        style_config = json.load(f)

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

    female_ref = load_image_part(frames_dir / FEMALE_REF_FRAME)
    male_ref   = load_image_part(frames_dir / MALE_REF_FRAME)

    clone_mode = style_config.get("clone_mode", False)

    return style_config, all_characters, CHARACTER_FRAME_HINT, female_ref, male_ref, clone_mode


def run_portraits(output_dir: Path, style: str = "fruit-drama") -> dict:
    """Generate character portraits only."""
    images_dir = output_dir / "images"
    images_dir.mkdir(exist_ok=True)

    style_config, all_characters, CHARACTER_FRAME_HINT, female_ref, male_ref, clone_mode = _load_config(output_dir, style)

    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    model = "models/gemini-2.5-flash-image"
    gen_config = types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"])

    # In clone mode, load ALL original frames as character references
    clone_ref_frames = []
    if clone_mode:
        frames_dir = output_dir / "frames"
        all_frames = sorted(frames_dir.glob("frame_*.png"))
        # Pick up to 4 diverse frames as reference
        step = max(1, len(all_frames) // 4)
        for f in all_frames[::step][:4]:
            clone_ref_frames.append(load_image_part(f))

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
        if clone_mode:
            # Clone mode: copy the EXACT character from original frames
            contents = []
            contents.append("These are frames from the ORIGINAL video. Find and copy the EXACT character described below — same head shape, same colors, same textures, same outfit. IGNORE any watermarks or text overlays in the frames:")
            for ref in clone_ref_frames:
                contents.append(ref)
            contents.append(
                f"Extract and recreate this EXACT character: {desc}. "
                f"Copy the character's appearance PRECISELY as shown in the reference frames — same head design, same body, same colors, same outfit, same accessories. "
                f"Do NOT redesign or reinterpret the character. Make it look IDENTICAL to the original. "
                f"IMPORTANT: Do NOT include any watermarks, text overlays, or logos from the reference frames. The output must be completely clean. "
                f"Full body shot, neutral pose, pure white background. "
                f"Hyperrealistic 3D render, warm studio lighting."
            )
        else:
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

    print(f"\n[Step 4a] Done. {len(char_images)} portraits saved.")
    return {"character_portraits": {k: str(v) for k, v in char_images.items()}}


def run_scenes(output_dir: Path, style: str = "fruit-drama") -> dict:
    """Generate scene images only. Requires portraits to exist."""
    script_path = output_dir / "script.json"
    if not script_path.exists():
        raise FileNotFoundError(f"script.json not found — run rewrite first")

    with open(script_path) as f:
        script = json.load(f)

    images_dir = output_dir / "images"
    images_dir.mkdir(exist_ok=True)

    style_config, all_characters, CHARACTER_FRAME_HINT, female_ref, male_ref, clone_mode = _load_config(output_dir, style)

    # In clone mode, load original frames for scene reference
    clone_scene_refs = {}
    if clone_mode:
        frames_dir = output_dir / "frames"
        all_frames = sorted(frames_dir.glob("frame_*.png"))
        for i, f in enumerate(all_frames):
            clone_scene_refs[i + 1] = f

    # Load existing portraits
    char_images = {}
    for char in all_characters:
        name = char["name"]
        portrait_path = images_dir / f"char_{name.lower().replace(' ', '_')}.png"
        if portrait_path.exists():
            char_images[name] = portrait_path
    if not char_images:
        raise FileNotFoundError("No character portraits found — run portraits first")

    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    model = "models/gemini-2.5-flash-image"
    gen_config = types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"])

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

        # Find which characters appear in this scene — ordered (dialogue order first, then prompt mentions)
        scene_chars_ordered = []
        seen = set()
        for line in beat.get("dialogue", []):
            name = line.get("character", "")
            if name in char_images and name not in seen:
                scene_chars_ordered.append(name)
                seen.add(name)
        for name in char_images:
            if name in image_prompt and name not in seen:
                scene_chars_ordered.append(name)
                seen.add(name)
        if not scene_chars_ordered:
            scene_chars_ordered = list(char_images.keys())
        scene_chars = set(scene_chars_ordered)  # keep set for membership tests

        print(f"  >> Generating scene {beat_num}: {beat['beat_name']} ({', '.join(scene_chars_ordered)})...")

        char_names_str = ", ".join(scene_chars)
        identity_reminders = []
        for name in scene_chars:
            hint = CHARACTER_FRAME_HINT.get(name, (None, None))[1]
            if hint:
                identity_reminders.append(f"{name} = {hint}")
        identity_str = "; ".join(identity_reminders)

        contents = []

        structured_prompt = _structure_image_prompt(image_prompt, scene_chars_ordered, all_characters)

        if clone_mode:
            # Clone mode: use ONLY character portraits + text prompt (no original frames to avoid watermarks)
            for name in scene_chars_ordered:
                contents.append(f"{name} — character reference. Copy this character EXACTLY:")
                contents.append(load_image_part(char_images[name]))
            contents.append(
                f"Generate this scene using ONLY the character portraits above as reference. "
                f"Copy each character EXACTLY — same head shape, texture, colors, outfit. "
                f"{structured_prompt} "
                f"ABSOLUTELY NO TEXT, NO WATERMARKS, NO LOGOS, NO WORDS of any kind in the image. "
                f"Hyperrealistic 3D render, warm sunny lighting, 9:16 vertical."
            )
        else:
            for name in scene_chars_ordered:
                char_desc = ""
                for c in all_characters:
                    if c["name"] == name:
                        char_desc = c.get("description", "")
                        break
                contents.append(f"{name} — reference portrait. Copy this character EXACTLY as shown (head shape, texture, color, outfit):")
                contents.append(load_image_part(char_images[name]))
                if char_desc:
                    contents.append(f"Character details for {name}: {char_desc}")
            child_chars = [n for n in scene_chars_ordered if CHARACTER_FRAME_HINT.get(n, (None,))[0] == "child"]
            size_note = "IMPORTANT size consistency: all adult characters are the same full height as in portraits. "
            if child_chars:
                size_note += f"Child characters ({', '.join(child_chars)}) are exactly half the height of adults — small, compact, child-sized bodies. "

            contents.append(
                f"STRICT CHARACTER CONSISTENCY: Copy the EXACT appearance of {char_names_str} from the reference portraits above — "
                f"same head shape, same head COLOR, same head TEXTURE/PATTERN (spots, bumps, seeds, etc.), same outfit/clothing, same accessories. "
                f"The reference portraits are the GROUND TRUTH — if the portrait shows a textured head with dots, the scene MUST show the same texture. "
                f"Do NOT simplify or smooth out any details from the portraits. "
                f"Character identities: {identity_str}. Do NOT mix up characters. "
                f"{size_note}"
                f"{structured_prompt} "
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

    print(f"\n[Step 4b] Done. {len(scene_images)} scenes saved.")
    return {"scene_images": {k: str(v) for k, v in scene_images.items()}}


def run(output_dir: Path, style: str = "fruit-drama") -> dict:
    """Generate both portraits and scenes (backward compatible)."""
    results = run_portraits(output_dir, style)
    results.update(run_scenes(output_dir, style))

    manifest_path = output_dir / "images_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(results, f, indent=2)

    return results
