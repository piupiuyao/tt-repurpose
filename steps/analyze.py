import json
import os
import base64
from pathlib import Path
from openai import OpenAI


def _load_image_b64(path: Path, max_width: int = 768) -> str:
    from PIL import Image as _Image
    import io as _io
    img = _Image.open(path)
    if img.width > max_width:
        ratio = max_width / img.width
        img = img.resize((max_width, int(img.height * ratio)), _Image.LANCZOS)
    buf = _io.BytesIO()
    img.save(buf, format="JPEG", quality=75)
    return base64.b64encode(buf.getvalue()).decode()


def detect_characters(output_dir: Path, keep_original: bool = False) -> dict:
    """Analyze video frames to extract family structure + art style, then generate a NEW cast of different food types.
    If keep_original=True, keep the original characters instead of generating new ones (clone mode)."""
    import re
    frames_dir = output_dir / "frames"
    frames = sorted(frames_dir.glob("frame_*.png"))
    if not frames:
        raise FileNotFoundError("No frames found — run extract first")

    # Use all selected frames (user already filtered in Step 2), cap at 9 for API limits
    step = max(1, len(frames) // 9)
    sample_frames = frames[::step][:9]

    # Load transcript to help identify ALL characters (including ones not in sampled frames)
    transcript = ""
    transcript_path = output_dir / "transcript.txt"
    if transcript_path.exists():
        transcript = transcript_path.read_text().strip()

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
    )

    # ── Step 1: Analyze original video — extract structure & style only ──
    print("  >> Analyzing original video style and family structure...")
    transcript_section = f"\n\nVIDEO TRANSCRIPT (use this to identify ALL speaking characters — even if they don't appear in the sampled frames):\n{transcript}" if transcript else ""
    content = [
        {
            "type": "text",
            "text": (
                "These are frames from an AI-generated TikTok food/candy drama video.\n\n"
                "Extract:\n"
                "1. MAIN CHARACTERS ONLY: Identify ONLY the characters who play a role in the story (have dialogue, drive the plot, or appear in multiple scenes). IGNORE background extras, classroom fillers, crowd members, or any character that only appears blurred/tiny in the background. For each main character: role (free-form, describe their role in the story in 1-3 words, e.g. 'birth mother', 'adoptive father', 'school bully'), gender, age (adult/child), brief_description (e.g. 'green spiky-haired man in blazer')\n"
                "2. VISUAL STYLE: describe the render quality precisely — is it photorealistic, Pixar-style, cartoon? What is the lighting like? Be specific so image generation can match it exactly.\n"
                "3. ORIGINAL FOOD TYPES: list ALL food/candy/plant types used — one per character (to avoid copying them)\n"
                "4. CHARACTER CATEGORY: what type of objects are the characters? Examples: fruit, candy, vegetable, dessert, flower, plant, phone/electronics, household_item, toy, etc. Be specific.\n"
                "5. Best ref frames: which frame best shows female characters? male characters?\n\n"
                "CRITICAL: Only count MAIN story characters (ones with dialogue or plot importance). Background/crowd characters should be IGNORED. Typical TikTok dramas have 4-8 main characters.\n\n"
                + transcript_section +
                "\n\nReturn ONLY valid JSON:\n"
                "{\n"
                '  "visual_style": "...",\n'
                '  "food_category": "flower",\n'
                '  "female_ref_frame": "frame_XXXX.png",\n'
                '  "male_ref_frame": "frame_XXXX.png",\n'
                '  "original_food_types": ["rose", "orchid", "cactus", ...],\n'
                '  "characters": [\n'
                '    {"role": "host", "gender": "female", "age": "adult", "brief_description": "yellow flower woman in pink gown"},\n'
                '    {"role": "main love interest", "gender": "female", "age": "adult", "brief_description": "pink flower woman in flowing dress"},\n'
                '    {"role": "rival", "gender": "male", "age": "adult", "brief_description": "green plant man with spiky hair"},\n'
                '    {"role": "shy newcomer", "gender": "male", "age": "adult", "brief_description": "older green plant man in blazer"}\n'
                "  ]\n"
                "}"
            )
        }
    ]
    for frame_path in sample_frames:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{_load_image_b64(frame_path)}"}})
        content.append({"type": "text", "text": f"(frame: {frame_path.name})"})

    response = client.chat.completions.create(
        model="anthropic/claude-sonnet-4-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": content}],
    )
    raw = response.choices[0].message.content.strip()
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not match:
        raise ValueError(f"Step 1 returned non-JSON:\n{raw}")
    analysis = json.loads(match.group())

    original_food_types = analysis.get("original_food_types", [])
    char_structure = analysis.get("characters", analysis.get("family_structure", []))
    food_category = analysis.get("food_category", "fruit")
    visual_style = analysis.get("visual_style", "Hyperrealistic Pixar-style 3D render with cinematic lighting")
    print(f"  >> Original food types: {original_food_types}")
    print(f"  >> Food category: {food_category}")
    print(f"  >> Characters detected: {len(char_structure)}")
    for c in char_structure:
        print(f"     {c.get('role', '?')} ({c.get('gender', '?')}) — {c.get('brief_description', '')}")

    # ── Clone mode: keep original characters ──
    if keep_original:
        print("  >> Clone mode: keeping original characters (no new cast)")
        # Convert original char_structure into the same format as new chars
        females = [c for c in char_structure if c.get("gender", "").lower() == "female"]
        males = [c for c in char_structure if c.get("gender", "").lower() == "male"]
        # Add missing fields for compatibility
        for c in females + males:
            if "name" not in c:
                c["name"] = c.get("brief_description", c.get("role", "character"))
            if "food_type" not in c:
                c["food_type"] = c.get("brief_description", "unknown")
            if "description" not in c:
                c["description"] = c.get("brief_description", "")
            if "ref_hint" not in c:
                c["ref_hint"] = c.get("brief_description", "")

        config = {
            "visual_style": visual_style,
            "female_ref_frame": analysis.get("female_ref_frame", sample_frames[0].name),
            "male_ref_frame": analysis.get("male_ref_frame", sample_frames[0].name),
            "original_food_types": original_food_types,
            "characters": {"females": females, "males": males},
            "style": "auto",
            "aspect_ratio": "9:16",
            "target_duration_seconds": 60,
            "clone_mode": True,
        }

        config_path = output_dir / "style_config.json"
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

        print(f"  >> Original cast kept: {len(females)}F + {len(males)}M characters → saved to style_config.json")
        for c in females + males:
            print(f"     {c['name']} ({c.get('role', '?')})")
        return config

    # ── Step 2: Generate a NEW cast with different food types ──
    print("  >> Generating NEW character cast with different food types...")
    # peach is permanently banned — triggers Grok video content moderation
    avoid_types = list(set(original_food_types))
    avoid_str = ", ".join(avoid_types) if avoid_types else "none"
    gen_prompt = (
        f"Create a NEW cast of anthropomorphic {food_category} characters for a TikTok drama video.\n\n"
        f"The original video used: {', '.join(original_food_types)}. These are {food_category} characters.\n\n"
        f"Original cast to recreate (1:1 replacement for each):\n{json.dumps(char_structure, indent=2)}\n\n"
        f"RULES:\n"
        f"- CRITICAL: All characters MUST be the SAME category as the original: {food_category}. If originals are phones, use different phones/electronics. If originals are fruits, use different fruits. NEVER mix categories.\n"
        f"- Do NOT reuse the original types: {avoid_str}\n"
        f"- Be CREATIVE and DIVERSE — pick from a WIDE variety of {food_category} types (e.g. apple, strawberry, banana, watermelon, grape, pineapple, kiwi, blueberry, lemon, coconut, etc). Avoid defaulting to cherry or peach every time. Surprise us!\n"
        f"- Choose visually interesting, distinct {food_category} types that look DIFFERENT from each other in shape, color, and size\n"
        f"- Adults should look stylish and glamorous; children should look cute and small\n"
        f"- Do NOT merge characters — create EXACTLY {len(char_structure)} characters, one for each original\n"
        f"- Every character from the original must have a 1:1 replacement with the same role\n"
        f"- Art style: {visual_style}\n\n"
        f"For each character provide:\n"
        f"- name: fun memorable name matching food type (e.g. 'Mango Mama', 'Lemon Larry')\n"
        f"- food_type: the food/candy type chosen\n"
        f"- gender: 'female' or 'male'\n"
        f"- role: same role as the original character it replaces\n"
        f"- description: detailed visual description — food head shape/texture/color, body proportions, outfit, accessories. CRITICAL: every character MUST have a clearly visible FACE with BIG expressive cartoon eyes, eyebrows, a small nose, and a mouth/smile on the FRONT of the head. The food/flower/object element sits on TOP of the head like a hat or frames the face — it must NEVER cover or replace the face.\n"
        f"- ref_hint: brief visual identifier (e.g. 'tall mango character in yellow dress')\n\n"
        "Return ONLY valid JSON:\n"
        "{\n"
        '  "characters": {\n'
        '    "females": [{"role":"...","name":"...","food_type":"...","gender":"female","description":"...","ref_hint":"..."}],\n'
        '    "males": [{"role":"...","name":"...","food_type":"...","gender":"male","description":"...","ref_hint":"..."}]\n'
        "  }\n"
        "}"
    )
    response2 = client.chat.completions.create(
        model="anthropic/claude-sonnet-4-5",
        max_tokens=2048,
        messages=[{"role": "user", "content": gen_prompt}],
    )
    raw2 = response2.choices[0].message.content.strip()
    match2 = re.search(r'\{.*\}', raw2, re.DOTALL)
    if not match2:
        raise ValueError(f"Step 2 returned non-JSON:\n{raw2}")
    json_str = match2.group()
    try:
        new_chars = json.loads(json_str)
    except json.JSONDecodeError:
        # Try to fix common JSON issues (missing commas, trailing commas)
        import re as _re2
        fixed = _re2.sub(r',\s*}', '}', json_str)   # trailing comma before }
        fixed = _re2.sub(r',\s*]', ']', fixed)       # trailing comma before ]
        fixed = _re2.sub(r'"\s*\n\s*"', '",\n"', fixed)  # missing comma between strings
        fixed = _re2.sub(r'}\s*\n\s*{', '},\n{', fixed)  # missing comma between objects
        fixed = _re2.sub(r'}\s*{', '},{', fixed)
        new_chars = json.loads(fixed)

    config = {
        "visual_style": visual_style,
        "female_ref_frame": analysis.get("female_ref_frame", sample_frames[0].name),
        "male_ref_frame": analysis.get("male_ref_frame", sample_frames[0].name),
        "original_food_types": original_food_types,
        "characters": new_chars["characters"],
        "style": "auto",
        "aspect_ratio": "9:16",
        "target_duration_seconds": 60,
    }

    config_path = output_dir / "style_config.json"
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    females = config["characters"].get("females", [])
    males = config["characters"].get("males", [])
    print(f"  >> NEW cast: {len(females)}F + {len(males)}M characters → saved to style_config.json")
    for c in females + males:
        print(f"     {c['name']} ({c['food_type']}) — {c.get('role', '?')}")

    return config


def run(output_dir: Path, keep_original: bool = False) -> dict:
    transcript_txt = output_dir / "transcript.txt"
    transcript_json = output_dir / "transcript.json"

    if not transcript_txt.exists():
        raise FileNotFoundError(f"transcript.txt not found in {output_dir}")

    transcript = transcript_txt.read_text().strip()

    # Load whisper segments for timestamp reference
    segments = []
    if transcript_json.exists():
        with open(transcript_json) as f:
            data = json.load(f)
            segments = data.get("segments", [])

    system_prompt_path = Path(__file__).parent.parent / "prompts" / "analyze_system.txt"
    system_prompt = system_prompt_path.read_text().strip()

    # Use ALL remaining frames (user already selected the ones they want in Step 2)
    frames_dir = output_dir / "frames"
    sample_frames = sorted(frames_dir.glob("frame_*.png")) if frames_dir.exists() else []
    num_beats = len(sample_frames) if sample_frames else 7

    # Load scene durations from scenes.json
    scene_durations = {}
    scenes_json = output_dir / "scenes.json"
    if scenes_json.exists():
        with open(scenes_json) as f:
            scenes_meta = json.load(f)
        for s in scenes_meta:
            scene_durations[s["frame"]] = s["duration"]

    # Replace N in system prompt with actual frame count
    system_prompt = system_prompt.replace("EXACTLY N beats", f"EXACTLY {num_beats} beats")
    system_prompt = system_prompt.replace("exactly N beats", f"exactly {num_beats} beats")
    system_prompt = system_prompt.replace("selected N key frames", f"selected {num_beats} key frames")

    # Build multimodal user message: frames + transcript
    user_content = []
    if sample_frames:
        user_content.append({"type": "text", "text": f"VIDEO FRAMES ({num_beats} selected scenes, in order) — generate exactly {num_beats} beats, one per frame:"})
        for frame_path in sample_frames:
            user_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{_load_image_b64(frame_path)}"}})
            dur = scene_durations.get(frame_path.name, 0)
            dur_info = f" — original scene duration: {dur:.1f}s" if dur else ""
            user_content.append({"type": "text", "text": f"(frame: {frame_path.name}{dur_info})"})

    transcript_text = f"\n\nTRANSCRIPT:\n{transcript}"
    if segments:
        transcript_text += "\n\nSEGMENT TIMESTAMPS (for reference):\n"
        for seg in segments:
            transcript_text += f"[{seg['start']:.1f}s - {seg['end']:.1f}s] {seg['text'].strip()}\n"
    user_content.append({"type": "text", "text": transcript_text})

    print(f"  >> Calling Claude via OpenRouter for {num_beats}-beat analysis...")
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
    )
    response = client.chat.completions.create(
        model="anthropic/claude-sonnet-4-5",
        max_tokens=max(2048, num_beats * 400),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    )
    import re as _re
    if not response.choices:
        raise ValueError(f"API returned no choices. Full response: {response}")
    msg = response.choices[0].message
    raw = msg.content
    if not raw:
        # Content may be None if model refused or hit limits — log full response
        raise ValueError(f"Model returned empty content. Finish reason: {response.choices[0].finish_reason}. Refusal: {getattr(msg, 'refusal', None)}")
    raw = raw.strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        match = _re.search(r'\{.*\}', raw, _re.DOTALL)
        if match:
            result = json.loads(match.group())
        else:
            raise ValueError(f"Claude returned non-JSON response:\n{raw}")

    # Inject original scene durations into beats
    beats = result.get("beats", [])
    if scene_durations and sample_frames:
        for i, beat in enumerate(beats):
            if i < len(sample_frames):
                dur = scene_durations.get(sample_frames[i].name, 0)
                if dur:
                    beat["original_duration"] = dur

    beats_path = output_dir / "beats.json"
    with open(beats_path, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"  >> Analysis complete: {len(beats)} beats saved to {beats_path}")

    for beat in beats:
        print(f"     Beat {beat['beat_number']}: {beat['beat_name']} [{beat['emotion']}]")

    detect_characters(output_dir, keep_original=keep_original)

    return result
