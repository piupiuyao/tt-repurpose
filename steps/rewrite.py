import json
import os
import re
from pathlib import Path
from openai import OpenAI
import httpx


def run(output_dir: Path, style: str = "fruit-drama") -> dict:
    beats_path = output_dir / "beats.json"
    if not beats_path.exists():
        raise FileNotFoundError(f"beats.json not found in {output_dir} — run analyze first")

    with open(beats_path) as f:
        beats_data = json.load(f)

    # Prefer auto-detected config from this video, fall back to static style config
    dynamic_config = output_dir / "style_config.json"
    if dynamic_config.exists():
        config_path = dynamic_config
    else:
        config_path = Path(__file__).parent.parent / "config" / f"{style.replace('-', '_')}.json"
        if not config_path.exists():
            raise FileNotFoundError(f"Style config not found: {config_path}")

    with open(config_path) as f:
        style_config = json.load(f)

    system_prompt_path = Path(__file__).parent.parent / "prompts" / "rewrite_system.txt"
    system_prompt = system_prompt_path.read_text().strip()

    # Pass full original beats (including dialogue) so Claude keeps the same plot
    beats_list = beats_data.get("beats", beats_data)

    # Build character summary with family roles
    all_chars = (
        style_config.get("characters", {}).get("females", []) +
        style_config.get("characters", {}).get("males", [])
    )
    char_summary = "\n".join([
        f"{c['name']} — {c.get('role', '?')} ({c.get('food_type', '')}): {c.get('description', '')[:100]}"
        for c in all_chars
    ])

    num_beats = len(beats_list) if isinstance(beats_list, list) else len(beats_list.get("beats", []))
    user_message = (
        f"ORIGINAL {num_beats}-BEAT STORY (keep this plot EXACTLY — only swap the characters, output EXACTLY {num_beats} beats):\n{json.dumps(beats_list, indent=2)}\n\n"
        f"NEW CHARACTERS TO USE:\n{char_summary}"
    )

    print("  >> Calling Claude via OpenRouter for script rewrite...")
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
        timeout=httpx.Timeout(120.0, connect=30.0),
        max_retries=3,
    )
    response = client.chat.completions.create(
        model="anthropic/claude-opus-4-5",
        max_tokens=max(16000, num_beats * 1500),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    )
    raw = response.choices[0].message.content.strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            result = json.loads(match.group())
        else:
            raise ValueError(f"Model returned non-JSON response:\n{raw}")

    script_path = output_dir / "script.json"
    with open(script_path, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    # Also write a human-readable version
    readable_path = output_dir / "script.txt"
    with open(readable_path, "w") as f:
        for beat in result.get("beats", []):
            f.write(f"\n{'='*50}\n")
            f.write(f"BEAT {beat['beat_number']}: {beat['beat_name'].upper()} [{beat['emotion']}]\n")
            f.write(f"{'='*50}\n")
            for line in beat.get("dialogue", []):
                f.write(f"{line['character']}: {line['line']}\n")
            f.write(f"\n[IMAGE PROMPT]\n{beat.get('image_prompt', '')}\n")
            f.write(f"\n[KLING PROMPT]\n{beat.get('grok_prompt', '')}\n")

    beats = result.get("beats", [])
    print(f"  >> Rewrite complete: {len(beats)} beats saved to {script_path}")
    for beat in beats:
        print(f"     Beat {beat['beat_number']}: {beat['beat_name']} — {len(beat.get('dialogue', []))} lines")

    return result
