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


def detect_characters(output_dir: Path) -> dict:
    """Analyze video frames to extract family structure + art style, then generate a NEW cast of different food types."""
    import re
    frames_dir = output_dir / "frames"
    frames = sorted(frames_dir.glob("frame_*.png"))
    if not frames:
        raise FileNotFoundError("No frames found — run extract first")

    step = max(1, len(frames) // 5)
    sample_frames = frames[::step][:5]

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
                "1. ALL CHARACTERS: every character that speaks or appears — including main family AND secondary characters (lover/affair partner, friend, employer, police, judge, neighbor, accomplice, etc). Infer from the transcript if they don't appear in the frames. For each: family_role (mother/father/child/lover/friend/employer/police/accomplice/other), gender, age (adult/child)\n"
                "2. VISUAL STYLE: describe the render quality precisely — is it photorealistic, Pixar-style, cartoon? What is the lighting like? Be specific so image generation can match it exactly.\n"
                "3. ORIGINAL FOOD TYPES: list of food/candy types used (to avoid copying them)\n"
                "4. Best ref frames: which frame best shows female characters? male characters?\n\n"
                + transcript_section +
                "\n\nReturn ONLY valid JSON:\n"
                "{\n"
                '  "visual_style": "...",\n'
                '  "female_ref_frame": "frame_XXXX.png",\n'
                '  "male_ref_frame": "frame_XXXX.png",\n'
                '  "original_food_types": ["strawberry", "eggplant", ...],\n'
                '  "family_structure": [\n'
                '    {"family_role": "mother", "gender": "female", "age": "adult"},\n'
                '    {"family_role": "lover", "gender": "female", "age": "adult"},\n'
                '    {"family_role": "friend", "gender": "female", "age": "adult"},\n'
                '    {"family_role": "father", "gender": "male", "age": "adult"},\n'
                '    {"family_role": "child", "gender": "male", "age": "child"}\n'
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
    family_structure = analysis.get("family_structure", [])
    visual_style = analysis.get("visual_style", "Hyperrealistic Pixar-style 3D render with cinematic lighting")
    print(f"  >> Original food types: {original_food_types}")
    print(f"  >> Family structure: {[c['family_role'] for c in family_structure]}")

    # ── Step 2: Generate a NEW cast with different food types ──
    print("  >> Generating NEW character cast with different food types...")
    # peach is permanently banned — triggers Grok video content moderation
    BANNED_FOOD_TYPES = ["peach"]
    avoid_types = list(set(original_food_types + BANNED_FOOD_TYPES))
    avoid_str = ", ".join(avoid_types) if avoid_types else "none"
    gen_prompt = (
        f"Create a NEW cast of anthropomorphic food/candy characters for a TikTok drama video.\n\n"
        f"Family structure to recreate:\n{json.dumps(family_structure, indent=2)}\n\n"
        f"RULES:\n"
        f"- Do NOT use these food types (permanently banned): {avoid_str}\n"
        f"- Choose visually interesting, distinct food/candy types\n"
        f"- Adults should look stylish and glamorous; children should look cute and small\n"
        f"- If multiple characters have the same role (e.g. two police), MERGE them into ONE character\n"
        f"- Maximum 6 characters total — keep the cast lean\n"
        f"- Art style: {visual_style}\n\n"
        f"For each character provide:\n"
        f"- name: fun memorable name matching food type (e.g. 'Mango Mama', 'Lemon Larry')\n"
        f"- food_type: the food/candy type chosen\n"
        f"- gender: 'female' or 'male'\n"
        f"- family_role: same as input structure\n"
        f"- role: narrative role (protagonist/antagonist/sidekick/other)\n"
        f"- description: detailed visual description — food head shape/texture/color, body proportions, outfit, accessories, expression\n"
        f"- ref_hint: brief visual identifier (e.g. 'tall mango character in yellow dress')\n\n"
        "Return ONLY valid JSON:\n"
        "{\n"
        '  "characters": {\n'
        '    "females": [{"role":"...","family_role":"...","name":"...","food_type":"...","gender":"female","description":"...","ref_hint":"..."}],\n'
        '    "males": [{"role":"...","family_role":"...","name":"...","food_type":"...","gender":"male","description":"...","ref_hint":"..."}]\n'
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
    new_chars = json.loads(match2.group())

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
        print(f"     {c['name']} ({c['food_type']}) — {c['family_role']}")

    return config


def run(output_dir: Path) -> dict:
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

    # Sample frames for visual analysis
    frames_dir = output_dir / "frames"
    frames = sorted(frames_dir.glob("frame_*.png")) if frames_dir.exists() else []
    step = max(1, len(frames) // 9)
    sample_frames = frames[::step][:9]

    # Build multimodal user message: frames + transcript
    user_content = []
    if sample_frames:
        user_content.append({"type": "text", "text": "VIDEO FRAMES (in order):"})
        for frame_path in sample_frames:
            user_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{_load_image_b64(frame_path)}"}})
            user_content.append({"type": "text", "text": f"(frame: {frame_path.name})"})

    transcript_text = f"\n\nTRANSCRIPT:\n{transcript}"
    if segments:
        transcript_text += "\n\nSEGMENT TIMESTAMPS (for reference):\n"
        for seg in segments:
            transcript_text += f"[{seg['start']:.1f}s - {seg['end']:.1f}s] {seg['text'].strip()}\n"
    user_content.append({"type": "text", "text": transcript_text})

    print("  >> Calling Claude via OpenRouter for 7-beat analysis...")
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
    )
    response = client.chat.completions.create(
        model="anthropic/claude-sonnet-4-5",
        max_tokens=2048,
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

    beats_path = output_dir / "beats.json"
    with open(beats_path, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    beats = result.get("beats", [])
    print(f"  >> Analysis complete: {len(beats)} beats saved to {beats_path}")

    for beat in beats:
        print(f"     Beat {beat['beat_number']}: {beat['beat_name']} [{beat['emotion']}]")

    detect_characters(output_dir)

    return result
