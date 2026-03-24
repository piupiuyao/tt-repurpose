import streamlit as st
import subprocess
import sys
import json
import shutil
from pathlib import Path
from dotenv import load_dotenv
import time

load_dotenv()

PROJECT_DIR = Path(__file__).parent
sys.path.insert(0, str(PROJECT_DIR))

st.set_page_config(page_title="🎬 TT Repurpose", layout="wide")

# ── Styles available ────────────────────────────────────────────────────────
STYLES = {
    "candy-drama": "🍬 Candy Drama (pregnancy / hospital)",
    "fruit-drama": "🍉 Fruit Drama (dating show)",
}

# ── Session state defaults ───────────────────────────────────────────────────
def _init():
    defaults = {
        "stage": "input",          # input | frames | script | images | animate | done
        "output_dir": None,
        "style": "candy-drama",
        "url": "",
        "frame_sel": {},           # {filename: bool}
        "script": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init()

# ── Helpers ──────────────────────────────────────────────────────────────────
def out_dir() -> Path:
    return PROJECT_DIR / st.session_state.output_dir

def run_cmd(cmd: list, placeholder) -> int:
    """Run a subprocess, stream stdout line-by-line into placeholder."""
    env = {**__import__("os").environ, "PYTHONUNBUFFERED": "1"}
    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, cwd=str(PROJECT_DIR), env=env
    )
    lines = []
    start = time.time()
    for line in process.stdout:
        lines.append(line.rstrip())
        elapsed = int(time.time() - start)
        lines_display = lines[-30:]
        placeholder.code(f"⏱ {elapsed}s elapsed\n\n" + "\n".join(lines_display))
    process.wait()
    return process.returncode

def repurpose_cmd(step: str) -> list:
    return [
        sys.executable, "repurpose.py",
        "--url", st.session_state.url,
        "--output", st.session_state.output_dir,
        "--style", st.session_state.style,
        "--step", step,
    ]

def rewrite_prompt_with_feedback(original_prompt: str, feedback: str) -> str:
    """Call Claude via OpenRouter to rewrite a prompt incorporating user feedback."""
    import os
    from openai import OpenAI
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ["OPENROUTER_API_KEY"],
    )
    system = (
        "You are a creative director for AI-generated video content with anthropomorphic food characters. "
        "Rewrite the given prompt incorporating the user's feedback. "
        "Keep the same format, length, and structure. Return ONLY the rewritten prompt, no explanation."
    )
    user_msg = (
        f"ORIGINAL PROMPT:\n{original_prompt}\n\n"
        f"USER FEEDBACK:\n{feedback}\n\n"
        "Rewrite the prompt incorporating this feedback."
    )
    response = client.chat.completions.create(
        model="anthropic/claude-sonnet-4-5",
        max_tokens=1024,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
    )
    return response.choices[0].message.content.strip()

def update_script_prompt(beat_num: int, prompt_key: str, new_prompt: str):
    """Update a specific beat's prompt in script.json and session state."""
    script_path = out_dir() / "script.json"
    with open(script_path) as f:
        script = json.load(f)
    for beat in script.get("beats", []):
        if beat["beat_number"] == beat_num:
            beat[prompt_key] = new_prompt
            break
    with open(script_path, "w") as f:
        json.dump(script, f, indent=2, ensure_ascii=False)
    st.session_state.script = script

# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🎬 TT Repurpose")
    st.caption("Turn any TikTok into a new AI drama video")
    st.divider()

    stages = ["input", "frames", "script", "images", "animate", "done"]
    labels = ["① URL & Style", "② Select Frames", "③ Script", "④ Images", "⑤ Animate", "⑥ Final Video"]
    current = stages.index(st.session_state.stage) if st.session_state.stage in stages else 0
    for i, (s, l) in enumerate(zip(stages, labels)):
        icon = "✅" if i < current else ("▶️" if i == current else "⏳")
        st.write(f"{icon} {l}")

    if st.session_state.output_dir:
        st.divider()
        st.caption(f"Output: `{st.session_state.output_dir}`")

    st.divider()
    st.caption("Resume a previous session:")
    existing_dirs = sorted([d.name for d in PROJECT_DIR.glob("output_*") if d.is_dir()])
    if existing_dirs:
        resume_dir = st.selectbox("Pick output folder", ["—"] + existing_dirs, label_visibility="collapsed")
        if resume_dir != "—" and st.button("▶️ Resume"):
            d = PROJECT_DIR / resume_dir
            # Detect stage from what files exist
            if (d / "final.mp4").exists():
                stage = "done"
            elif (d / "videos").exists() and any((d / "videos").glob("*.mp4")):
                stage = "animate"
            elif (d / "images").exists() and any((d / "images").glob("*.png")):
                stage = "images"
            elif (d / "script.json").exists():
                stage = "script"
            if (d / "script.json").exists():
                with open(d / "script.json") as f:
                    st.session_state.script = json.load(f)
            elif (d / "frames").exists():
                stage = "frames"
            else:
                stage = "input"
            st.session_state.output_dir = resume_dir
            st.session_state.url = "resumed"
            st.session_state.stage = stage
            st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 1 — Input
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.stage == "input":
    st.title("Step 1 — Paste TikTok URL")

    url = st.text_input("TikTok URL", value=st.session_state.url, placeholder="https://www.tiktok.com/@...")
    out_name = st.text_input("Output folder name", value=f"output_{int(time.time())}")
    st.caption("Characters will be auto-detected from the video frames — no style config needed.")

    if st.button("🚀 Extract Video", type="primary", disabled=not url.strip() or st.session_state.get("running", False)):
        st.session_state.url = url.strip()
        st.session_state.output_dir = out_name

        st.session_state.running = True
        with st.status("Extracting video, transcribing audio, pulling frames…", expanded=True) as status:
            st.write("⬇️ Downloading video...")
            st.write("🎙️ Transcribing with Whisper — this takes 1-3 min, please wait...")
            st.write("🖼️ Extracting frames...")
            placeholder = st.empty()
            rc = run_cmd(repurpose_cmd("extract"), placeholder)
            st.session_state.running = False
            if rc == 0:
                status.update(label="✅ Extraction complete!", state="complete")
                st.session_state.stage = "frames"
                # Init all frames as selected
                frames_dir = out_dir() / "frames"
                st.session_state.frame_sel = {
                    f.name: True for f in sorted(frames_dir.glob("frame_*.png"))
                }
                st.rerun()
            else:
                status.update(label="❌ Extraction failed", state="error")

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 2 — Frame Selection
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.stage == "frames":
    st.title("Step 2 — Select Reference Frames")
    st.caption("Keep only the clearest frames. These will be used as style reference for image generation.")

    frames_dir = out_dir() / "frames"
    all_frames = sorted(frames_dir.glob("frame_*.png"))

    # Display grid — 4 columns, checkboxes update session_state directly via key
    cols = st.columns(4)
    for i, frame_path in enumerate(all_frames):
        key = f"frame_{frame_path.name}"
        # Init default
        if key not in st.session_state:
            st.session_state[key] = True
        with cols[i % 4]:
            st.image(str(frame_path), use_container_width=True)
            st.checkbox("Keep", key=key)

    selected_count = sum(1 for f in all_frames if st.session_state.get(f"frame_{f.name}", True))
    st.caption(f"{selected_count} / {len(all_frames)} frames selected")

    st.divider()
    if st.button("✅ Confirm & Analyze Script", type="primary", disabled=selected_count == 0):
        # Delete unselected frames
        for frame_path in all_frames:
            if not st.session_state.get(f"frame_{frame_path.name}", True):
                frame_path.unlink(missing_ok=True)

        out_placeholder = st.empty()
        out_placeholder.info("🔍 Analyzing story beats and detecting characters...")
        rc1 = run_cmd(repurpose_cmd("analyze"), out_placeholder)
        if rc1 == 0:
            out_placeholder.info("✍️ Rewriting script with new characters...")
            rc2 = run_cmd(repurpose_cmd("rewrite"), out_placeholder)
            if rc2 == 0:
                with open(out_dir() / "script.json") as f:
                    st.session_state.script = json.load(f)
                st.session_state.stage = "script"
                st.rerun()
            else:
                st.error("❌ Rewrite failed — check output above")
        else:
            st.error("❌ Analyze failed — check output above")

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 3 — Script Review
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.stage == "script":
    st.title("Step 3 — Review Script")

    script = st.session_state.script or {}
    beats = script.get("beats", [])

    for beat in beats:
        with st.expander(f"Beat {beat['beat_number']}: {beat['beat_name']} ({beat.get('emotion', '')})", expanded=True):
            st.markdown("**Dialogue:**")
            for line in beat.get("dialogue", []):
                st.markdown(f"**{line['character']}:** {line['line']}")
            if beat.get("image_prompt"):
                st.markdown("**Image Prompt:**")
                st.caption(beat["image_prompt"])
            if beat.get("grok_prompt"):
                st.markdown("**Video Prompt:**")
                st.caption(beat["grok_prompt"])

    st.divider()
    if st.button("🎨 Generate Images", type="primary"):
        st.session_state.stage = "images"
        st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 4 — Image Generation
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.stage == "images":
    st.title("Step 4 — Generate Images")

    images_dir = out_dir() / "images"

    if not images_dir.exists() or not list(images_dir.glob("*.png")):
        with st.status("Generating character portraits and scene images…", expanded=True) as status:
            placeholder = st.empty()
            rc = run_cmd(repurpose_cmd("images"), placeholder)
            if rc == 0:
                status.update(label="✅ Images generated!", state="complete")
                st.rerun()
            else:
                status.update(label="❌ Image generation failed", state="error")
    else:
        # Show portraits
        portraits = sorted(images_dir.glob("char_*.png"))
        scenes = sorted(images_dir.glob("scene_*.png"))

        st.subheader(f"🧑 Character Portraits ({len(portraits)})")
        p_cols = st.columns(min(len(portraits), 4))
        for i, p in enumerate(portraits):
            with p_cols[i % 4]:
                name = p.stem.replace("char_", "").replace("_", " ").title()
                st.image(str(p), caption=name, use_container_width=True)

        # Get beats for captions
        beats_data = (st.session_state.script or {}).get("beats", [])
        beat_map = {b["beat_number"]: b for b in beats_data}

        st.subheader(f"🎬 Scene Images ({len(scenes)} / {len(beats_data)})")
        for s in scenes:
            # Parse beat number from filename (scene_01.png → 1)
            beat_num = int(s.stem.split("_")[1])
            beat = beat_map.get(beat_num, {})
            caption = f"Scene {beat_num}: {beat.get('beat_name','')}"

            col_img, col_feedback, col_btn = st.columns([4, 3, 1])
            with col_img:
                st.image(str(s), caption=caption, use_container_width=True)
            with col_feedback:
                st.text_area(
                    "Feedback",
                    key=f"feedback_img_{beat_num}",
                    height=100,
                    label_visibility="collapsed",
                    placeholder="修改意见，留空则直接重新生成",
                )
            with col_btn:
                st.write("")
                st.write("")
                if st.button("🔁 Redo", key=f"regen_{beat_num}"):
                    feedback_text = st.session_state.get(f"feedback_img_{beat_num}", "").strip()
                    if feedback_text:
                        original_prompt = beat.get("image_prompt", "")
                        with st.spinner(f"Rewriting prompt for scene {beat_num}..."):
                            new_prompt = rewrite_prompt_with_feedback(original_prompt, feedback_text)
                        update_script_prompt(beat_num, "image_prompt", new_prompt)
                    s.unlink()
                    out = st.empty()
                    out.info(f"⏳ Regenerating scene {beat_num}...")
                    run_cmd(repurpose_cmd("images"), out)
                    st.rerun()

        # Show missing scenes (if any failed)
        existing_nums = {int(s.stem.split("_")[1]) for s in scenes}
        missing = [b["beat_number"] for b in beats_data if b["beat_number"] not in existing_nums]
        if missing:
            st.warning(f"Missing scenes: {missing} — click below to generate them")
            if st.button("⚡ Generate Missing Scenes"):
                out = st.empty()
                run_cmd(repurpose_cmd("images"), out)
                st.rerun()

        st.divider()
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔁 Regenerate All Images"):
                shutil.rmtree(images_dir)
                st.rerun()
        with col2:
            if st.button("🎬 Animate Scenes", type="primary"):
                st.session_state.stage = "animate"
                st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 5 — Animation
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.stage == "animate":
    st.title("Step 5 — Animate Scenes")

    videos_dir = out_dir() / "videos"
    final_path = out_dir() / "final.mp4"

    if final_path.exists():
        st.success("✅ Final video is ready!")
        st.video(str(final_path))
        st.divider()
        with open(final_path, "rb") as f:
            st.download_button("⬇️ Download final.mp4", f, file_name="final.mp4", mime="video/mp4")
        if st.button("🔁 Start New Video"):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()

    else:
        existing = list(videos_dir.glob("scene_*.mp4")) if videos_dir.exists() else []
        beats = (st.session_state.script or {}).get("beats", [])

        if not existing:
            with st.status("Generating videos with Grok… (this takes ~5 min)", expanded=True) as status:
                placeholder = st.empty()
                rc = run_cmd(repurpose_cmd("animate"), placeholder)
                if rc == 0:
                    status.update(label="✅ Videos generated!", state="complete")
                    st.rerun()
                else:
                    status.update(label="❌ Animation failed", state="error")
        else:
            beat_map = {b["beat_number"]: b for b in beats}
            existing_sorted = sorted(existing)

            st.subheader(f"🎬 Generated Scenes ({len(existing)}/{len(beats)})")
            for v in existing_sorted:
                beat_num = int(v.stem.split("_")[1])
                beat = beat_map.get(beat_num, {})
                caption = f"Scene {beat_num}: {beat.get('beat_name', '')}"

                col_vid, col_feedback, col_btn = st.columns([4, 3, 1])
                with col_vid:
                    st.video(str(v))
                    st.caption(caption)
                with col_feedback:
                    st.text_area(
                        "Feedback",
                        key=f"feedback_vid_{beat_num}",
                        height=100,
                        label_visibility="collapsed",
                        placeholder="修改意见，留空则直接重新生成",
                    )
                with col_btn:
                    st.write("")
                    st.write("")
                    if st.button("🔁 Redo", key=f"regen_vid_{beat_num}"):
                        feedback_text = st.session_state.get(f"feedback_vid_{beat_num}", "").strip()
                        if feedback_text:
                            original_prompt = beat.get("grok_prompt", "")
                            with st.spinner(f"Rewriting prompt for video {beat_num}..."):
                                new_prompt = rewrite_prompt_with_feedback(original_prompt, feedback_text)
                            update_script_prompt(beat_num, "grok_prompt", new_prompt)
                        v.unlink()
                        out = st.empty()
                        out.info(f"⏳ Regenerating video {beat_num}...")
                        run_cmd(repurpose_cmd("animate"), out)
                        st.rerun()

            # Show missing videos
            existing_nums = {int(v.stem.split("_")[1]) for v in existing}
            missing = [b["beat_number"] for b in beats if b["beat_number"] not in existing_nums]
            if missing:
                st.warning(f"Missing videos: {missing}")

            if len(existing) < len(beats):
                if st.button("🔁 Retry Missing Scenes"):
                    with st.status("Retrying missing scenes…", expanded=True) as status:
                        placeholder = st.empty()
                        run_cmd(repurpose_cmd("animate"), placeholder)
                        status.update(label="Done", state="complete")
                        st.rerun()

            st.divider()
            if st.button("✂️ Assemble Final Video with Subtitles", type="primary"):
                with st.status("Assembling and adding subtitles…", expanded=True) as status:
                    placeholder = st.empty()
                    rc = run_cmd(repurpose_cmd("assemble"), placeholder)
                    if rc == 0:
                        status.update(label="✅ Final video ready!", state="complete")
                        st.session_state.stage = "done"
                        st.rerun()
                    else:
                        status.update(label="❌ Assembly failed", state="error")

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 6 — Done
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.stage == "done":
    st.title("🎉 Done!")
    final_path = out_dir() / "final.mp4"

    if final_path.exists():
        st.video(str(final_path))
        st.divider()
        with open(final_path, "rb") as f:
            st.download_button("⬇️ Download final.mp4", f, file_name="final.mp4", mime="video/mp4", type="primary")

    if st.button("🔁 Make Another Video"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()
