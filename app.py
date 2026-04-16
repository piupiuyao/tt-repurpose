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

st.set_page_config(
    page_title="TTRepurpose: Remix viral fruit drama TikToks into your own videos",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── IP rate limiting (1 video per IP) ───────────────────────────────────────
USAGE_FILE = PROJECT_DIR / "ip_usage.json"

def _get_client_ip() -> str:
    """Get client IP from request headers (behind Railway proxy)."""
    try:
        headers = st.context.headers
        return headers.get("X-Forwarded-For", "unknown").split(",")[0].strip()
    except Exception:
        return "unknown"

def _load_usage() -> dict:
    if USAGE_FILE.exists():
        with open(USAGE_FILE) as f:
            return json.load(f)
    return {}

def _save_usage(usage: dict):
    with open(USAGE_FILE, "w") as f:
        json.dump(usage, f)

def _mark_ip_used(ip: str):
    usage = _load_usage()
    usage[ip] = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "count": usage.get(ip, {}).get("count", 0) + 1}
    _save_usage(usage)

def _ip_has_quota(ip: str) -> bool:
    if ip == "unknown":
        return True
    usage = _load_usage()
    return usage.get(ip, {}).get("count", 0) < 1

# ── Styles available ────────────────────────────────────────────────────────
STYLES = {
    "candy-drama": "🍬 Candy Drama (pregnancy / hospital)",
    "fruit-drama": "🍉 Fruit Drama (dating show)",
}

# ── Session state defaults ───────────────────────────────────────────────────
def _init():
    defaults = {
        "stage": "landing",        # landing | input | frames | script | portraits | scenes | animate | done
        "output_dir": None,
        "style": "candy-drama",
        "url": "",
        "frame_sel": {},           # {filename: bool}
        "script": None,
        "clone_mode": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init()

# ── Helpers ──────────────────────────────────────────────────────────────────
def out_dir() -> Path:
    return PROJECT_DIR / st.session_state.output_dir

def _kill_running_process():
    """Kill any pipeline process stored in session state."""
    proc = st.session_state.get("_running_proc")
    if proc is not None:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        st.session_state["_running_proc"] = None

# Kill any leftover process from a previous run on every page load
_kill_running_process()

def run_cmd(cmd: list, placeholder) -> int:
    """Run a subprocess, stream stdout line-by-line into placeholder."""
    env = {**__import__("os").environ, "PYTHONUNBUFFERED": "1"}
    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, cwd=str(PROJECT_DIR), env=env
    )
    st.session_state["_running_proc"] = process
    lines = []
    start = time.time()
    for line in process.stdout:
        lines.append(line.rstrip())
        elapsed = int(time.time() - start)
        lines_display = lines[-30:]
        placeholder.code(f"⏱ {elapsed}s elapsed\n\n" + "\n".join(lines_display))
    process.wait()
    st.session_state["_running_proc"] = None
    return process.returncode

def repurpose_cmd(step: str) -> list:
    cmd = [
        sys.executable, "repurpose.py",
        "--url", st.session_state.url,
        "--output", st.session_state.output_dir,
        "--style", st.session_state.style,
        "--step", step,
    ]
    if st.session_state.get("clone_mode", False):
        cmd.append("--clone")
    return cmd

def _call_openrouter(messages, max_tokens=1024, model="anthropic/claude-sonnet-4-5"):
    """Call OpenRouter using requests directly."""
    import os
    for attempt in range(3):
        try:
            resp = __import__("requests").post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
                    "Content-Type": "application/json",
                },
                json={"model": model, "max_tokens": max_tokens, "messages": messages},
                timeout=120,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"  >> OpenRouter attempt {attempt+1} failed: {e}")
            if attempt < 2:
                time.sleep(2)
            else:
                raise

def rewrite_prompt_with_feedback(original_prompt: str, feedback: str) -> str:
    """Call Claude via OpenRouter to rewrite a prompt incorporating user feedback."""
    system = (
        "You are a creative director for AI-generated video content with anthropomorphic food characters. "
        "Rewrite the given image/video prompt to fully incorporate the user's feedback. "
        "Make substantial changes where needed — do NOT preserve phrasing just because it was in the original. "
        "The rewritten prompt must clearly reflect the feedback. "
        "Return ONLY the rewritten prompt, no explanation, no preamble."
    )
    user_msg = (
        f"ORIGINAL PROMPT:\n{original_prompt}\n\n"
        f"USER FEEDBACK:\n{feedback}\n\n"
        "Rewrite the prompt so it clearly incorporates this feedback. Be specific and concrete."
    )
    return _call_openrouter(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
    ).strip()

def update_character_description(char_name: str, feedback: str):
    """Use Claude to rewrite a character's description based on user feedback, then update style_config.json."""
    import os
    from openai import OpenAI
    config_path = out_dir() / "style_config.json"
    with open(config_path) as f:
        cfg = json.load(f)

    # Find character and get current description
    target_char = None
    for group in ("females", "males"):
        for c in cfg.get("characters", {}).get(group, []):
            if c["name"] == char_name:
                target_char = c
                break

    if not target_char:
        return

    new_desc = _call_openrouter(
        messages=[
            {"role": "system", "content": (
                "You rewrite character descriptions for AI image generation. "
                "Incorporate the user's feedback into the existing description. "
                "CRITICAL RULE: every character MUST have a clearly visible FACE with BIG expressive cartoon eyes, eyebrows, a small nose, and a mouth/smile. "
                "The food/flower/object element sits on TOP of the head like a hat or frames the face — it must NEVER cover or replace the face. "
                "Return ONLY the new description text, nothing else."
            )},
            {"role": "user", "content": (
                f"CURRENT DESCRIPTION:\n{target_char['description']}\n\n"
                f"USER FEEDBACK:\n{feedback}\n\n"
                "Rewrite the description incorporating this feedback."
            )},
        ],
        max_tokens=512,
    ).strip()
    target_char["description"] = new_desc

    with open(config_path, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

    return new_desc



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

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 0 — Landing Page (full-page takeover)
# ══════════════════════════════════════════════════════════════════════════════
if st.session_state.stage == "landing":
    ASSETS = PROJECT_DIR / "assets"

    st.markdown("""
<style>
/* Hide all streamlit chrome on the landing page */
[data-testid="stSidebar"], [data-testid="stSidebarNav"], [data-testid="collapsedControl"] { display: none !important; }
#MainMenu, header, footer { visibility: hidden !important; }
[data-testid="stHeader"] { display: none !important; }

/* Tighter main block + roomy hero */
.main .block-container {
    max-width: 1040px;
    padding-top: 3rem;
    padding-bottom: 4rem;
}

/* Hero typography */
.tt-eyebrow {
    display: inline-block;
    font-size: 0.78rem;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #ff6b6b;
    background: rgba(255, 107, 107, 0.1);
    padding: 0.4rem 0.9rem;
    border-radius: 999px;
    margin-bottom: 1.25rem;
}
.tt-hero-title {
    font-size: 3.2rem;
    font-weight: 800;
    line-height: 1.08;
    letter-spacing: -0.02em;
    margin: 0 0 1.1rem 0;
    background: linear-gradient(135deg, #1a1a1a 0%, #4a4a4a 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}
.tt-hero-sub {
    font-size: 1.18rem;
    line-height: 1.55;
    color: #555;
    max-width: 640px;
    margin: 0 0 2rem 0;
}
.tt-hero-sub b { color: #222; }

/* Big rounded CTA button */
div.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #ff6b6b 0%, #ff8e53 100%);
    color: white;
    font-size: 1.15rem;
    font-weight: 700;
    padding: 0.95rem 2.4rem;
    border-radius: 999px;
    border: none;
    box-shadow: 0 12px 28px rgba(255, 107, 107, 0.32);
    transition: transform 0.15s ease, box-shadow 0.15s ease;
}
div.stButton > button[kind="primary"]:hover {
    transform: translateY(-2px);
    box-shadow: 0 16px 34px rgba(255, 107, 107, 0.4);
    color: white;
}

/* Section headings */
.tt-section-title {
    font-size: 0.82rem;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: #888;
    text-align: center;
    margin: 3.5rem 0 1.25rem 0;
}
.tt-section-sub {
    font-size: 1.8rem;
    font-weight: 800;
    text-align: center;
    line-height: 1.2;
    margin: 0 0 2.25rem 0;
    color: #1a1a1a;
}

/* Step cards */
.tt-step {
    background: #fafafa;
    border: 1px solid #eee;
    border-radius: 18px;
    padding: 1.6rem 1.4rem;
    height: 100%;
}
.tt-step-num {
    font-size: 0.75rem;
    font-weight: 800;
    color: #ff6b6b;
    letter-spacing: 0.1em;
    margin-bottom: 0.5rem;
}
.tt-step-title {
    font-size: 1.15rem;
    font-weight: 700;
    color: #1a1a1a;
    margin-bottom: 0.4rem;
}
.tt-step-body { font-size: 0.95rem; color: #666; line-height: 1.5; }

/* Character row */
.tt-char-caption {
    text-align: center;
    font-size: 0.85rem;
    color: #777;
    margin-top: 0.35rem;
}

/* Price strike callout */
.tt-price {
    display: inline-block;
    font-size: 0.95rem;
    color: #888;
    margin-top: 1rem;
}
.tt-price s { color: #bbb; margin-right: 0.5rem; }
.tt-price b { color: #ff6b6b; font-weight: 800; }

/* Footer */
.tt-footer {
    text-align: center;
    margin-top: 4rem;
    padding-top: 2rem;
    border-top: 1px solid #eee;
    font-size: 0.85rem;
    color: #999;
}
</style>
    """, unsafe_allow_html=True)

    # ── Hero ────────────────────────────────────────────────────────────────
    hero_left, hero_right = st.columns([5, 6], gap="large")
    with hero_left:
        st.markdown('<span class="tt-eyebrow">🎬 TTRepurpose</span>', unsafe_allow_html=True)
        st.markdown(
            '<h1 class="tt-hero-title">Your own viral fruit drama videos in 10 minutes, for free.</h1>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<p class="tt-hero-sub">Stop paying <b>$200+</b> per AI fruit drama video. '
            'Paste a viral TikTok, pick a style, and we\'ll remix it with new characters '
            'and a brand-new plot. Same addictive hook, your story.</p>',
            unsafe_allow_html=True,
        )
        if st.button("🚀  Try it free →", type="primary", key="landing_cta_top"):
            st.session_state.stage = "input"
            st.rerun()
        st.markdown(
            '<div class="tt-price"><s>$200+ / video (agency)</s> <b>Free · 1 video per visitor</b></div>',
            unsafe_allow_html=True,
        )

    with hero_right:
        demo_path = ASSETS / "demo.mp4"
        if demo_path.exists():
            st.video(str(demo_path))
            st.caption("↑ Real example: this AI fruit drama hit 69K views on TikTok.")
        else:
            st.info("Demo video will appear here once `assets/demo.mp4` is in place.")

    # ── How it works ────────────────────────────────────────────────────────
    st.markdown('<div class="tt-section-title">How it works</div>', unsafe_allow_html=True)
    st.markdown('<div class="tt-section-sub">Three steps. No editing skills required.</div>', unsafe_allow_html=True)

    steps = [
        ("STEP 01", "Paste a TikTok link",
         "Drop in the URL of any viral fruit drama you want to remix. We pull the video, transcribe it, and extract the key scenes automatically."),
        ("STEP 02", "Pick your style & scenes",
         "Choose which scenes to keep, then let AI rewrite the script with <b>your</b> characters and a brand-new plot. Not a copy of the original story."),
        ("STEP 03", "Download your video",
         "We generate character portraits, scene images, animate them into video, and burn in subtitles. You get a finished MP4, ready to post."),
    ]
    cols = st.columns(3, gap="medium")
    for col, (num, title, body) in zip(cols, steps):
        with col:
            st.markdown(
                f'<div class="tt-step">'
                f'<div class="tt-step-num">{num}</div>'
                f'<div class="tt-step-title">{title}</div>'
                f'<div class="tt-step-body">{body}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # ── Character showcase ─────────────────────────────────────────────────
    st.markdown('<div class="tt-section-title">Characters you\'ll get</div>', unsafe_allow_html=True)
    st.markdown('<div class="tt-section-sub">AI-generated portraits, consistent across every scene.</div>', unsafe_allow_html=True)

    chars = [
        ("char_banana.png", "Banana"),
        ("char_cherry.png", "Cherry"),
        ("char_mango.png", "Mango"),
        ("char_blueberry.png", "Blueberry"),
    ]
    char_cols = st.columns(4, gap="medium")
    for col, (filename, name) in zip(char_cols, chars):
        with col:
            img_path = ASSETS / filename
            if img_path.exists():
                st.image(str(img_path), use_container_width=True)
                st.markdown(f'<div class="tt-char-caption">{name}</div>', unsafe_allow_html=True)

    # ── Final CTA ──────────────────────────────────────────────────────────
    st.markdown('<div class="tt-section-title">Ready?</div>', unsafe_allow_html=True)
    st.markdown('<div class="tt-section-sub">Your first video is on us.</div>', unsafe_allow_html=True)
    cta_l, cta_c, cta_r = st.columns([1, 2, 1])
    with cta_c:
        if st.button("🚀  Try it free →", type="primary", key="landing_cta_bottom", use_container_width=True):
            st.session_state.stage = "input"
            st.rerun()

    st.markdown(
        '<div class="tt-footer">Built by a creator with 69K+ views on AI fruit drama · '
        'Powered by Claude, Gemini & Grok</div>',
        unsafe_allow_html=True,
    )
    st.stop()

# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🎬 TT Repurpose")
    st.caption("Turn any TikTok into a new AI drama video")
    st.divider()

    stages = ["input", "frames", "script", "portraits", "scenes", "animate", "done"]
    labels = ["① URL & Style", "② Select Frames", "③ Script", "④ Portraits", "⑤ Scenes", "⑥ Animate", "⑦ Final Video"]
    # Track the furthest stage reached so we know which are "completed"
    if "max_stage" not in st.session_state:
        st.session_state.max_stage = 0
    current = stages.index(st.session_state.stage) if st.session_state.stage in stages else 0
    st.session_state.max_stage = max(st.session_state.max_stage, current)
    for i, (s, l) in enumerate(zip(stages, labels)):
        icon = "✅" if i < st.session_state.max_stage else ("▶️" if i == current else "⏳")
        if i <= st.session_state.max_stage:
            if st.button(f"{icon} {l}", key=f"nav_{s}"):
                st.session_state.stage = s
                st.rerun()
        else:
            st.write(f"{icon} {l}")

    if st.session_state.output_dir:
        st.divider()
        st.caption(f"Output: `{st.session_state.output_dir}`")

    st.divider()
    st.caption("Resume a previous session:")
    existing_dirs = sorted([d.name for d in PROJECT_DIR.glob("output_*") if d.is_dir()])
    if existing_dirs:
        resume_dir = st.selectbox("Pick output folder", ["—"] + existing_dirs, label_visibility="collapsed")
    else:
        resume_dir = st.text_input("Output folder name", placeholder="output_1234567890", label_visibility="collapsed")
    if resume_dir and resume_dir != "—" and st.button("▶️ Resume"):
            d = PROJECT_DIR / resume_dir
            # Detect stage from what files exist
            if (d / "final.mp4").exists():
                stage = "done"
            elif (d / "videos").exists() and any((d / "videos").glob("*.mp4")):
                stage = "animate"
            elif (d / "images").exists() and any((d / "images").glob("scene_*.png")):
                stage = "scenes"
            elif (d / "images").exists() and any((d / "images").glob("char_*.png")):
                stage = "portraits"
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
    st.title("Step 1. Paste a TikTok URL")
    st.markdown(
        "**What happens here:** we download the video you want to remix, transcribe the audio with Whisper, "
        "and split it into scenes. This takes 3–5 minutes. You don't have to do anything during this step."
    )

    url = st.text_input("TikTok URL", value=st.session_state.url, placeholder="https://www.tiktok.com/@...")
    out_name = st.text_input("Output folder name", value=f"output_{int(time.time())}", help="A label for this run so you can resume it later. You can leave the default.")
    st.caption("Characters will be auto-detected from the video frames. No style config needed.")

    client_ip = _get_client_ip()
    if not _ip_has_quota(client_ip):
        st.warning("⚠️ You've already used your free trial. Each user gets 1 free video.")
        st.stop()

    if st.button("🚀 Extract Video", type="primary", disabled=not url.strip() or st.session_state.get("running", False)):
        st.session_state.url = url.strip()
        st.session_state.output_dir = out_name
        _mark_ip_used(client_ip)  # Mark immediately — before any processing starts

        st.session_state.running = True
        st.warning("⏳ This step takes about **3-5 minutes** — please don't close the page!")
        progress_bar = st.progress(0, text="Starting...")
        with st.status("Extracting video, transcribing audio, pulling frames…", expanded=True) as status:
            placeholder = st.empty()
            # Run with progress tracking based on output keywords
            env = {**__import__("os").environ, "PYTHONUNBUFFERED": "1"}
            process = subprocess.Popen(
                repurpose_cmd("extract"), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, cwd=str(PROJECT_DIR), env=env
            )
            st.session_state["_running_proc"] = process
            lines = []
            start = time.time()
            for line in process.stdout:
                lines.append(line.rstrip())
                elapsed = int(time.time() - start)
                # Update progress based on what step we're on
                if "Downloading" in line or "yt-dlp" in line:
                    progress_bar.progress(15, text="⬇️ Downloading video...")
                elif "Transcribing" in line or "audio" in line.lower():
                    progress_bar.progress(35, text="🎙️ Transcribing audio...")
                elif "Detecting scene" in line or "SceneDetect" in line:
                    progress_bar.progress(70, text="🖼️ Detecting scenes...")
                elif "Done" in line:
                    progress_bar.progress(95, text="Almost done...")
                lines_display = lines[-30:]
                placeholder.code(f"⏱ {elapsed}s elapsed\n\n" + "\n".join(lines_display))
            process.wait()
            st.session_state["_running_proc"] = None
            rc = process.returncode
            progress_bar.progress(100, text="✅ Complete!")
            st.session_state.running = False
            if rc == 0:
                status.update(label="✅ Extraction complete!", state="complete")
                st.session_state.stage = "frames"
                # Init all frames as UNselected — user picks key scenes
                frames_dir = out_dir() / "frames"
                st.session_state.frame_sel = {
                    f.name: True for f in sorted(frames_dir.glob("frame_*.png"))
                }
                # Load scene metadata if available
                scenes_json = out_dir() / "scenes.json"
                if scenes_json.exists():
                    with open(scenes_json) as f:
                        st.session_state.scenes_meta = json.load(f)
                st.rerun()
            else:
                status.update(label="❌ Extraction failed", state="error")
                st.error(
                    "**Extraction failed.** Common reasons:\n"
                    "- The TikTok link is private or has been deleted\n"
                    "- Download timed out — try again\n\n"
                    "Try pasting a different public TikTok URL."
                )
                if st.button("🔁 Try Again"):
                    st.session_state.stage = "input"
                    st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 2 — Frame Selection
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.stage == "frames":
    st.title("Step 2. Pick the scenes to keep")
    st.markdown(
        "**What to do:** each thumbnail below is one scene auto-detected from the original video. "
        "Uncheck any you don't want (ads, intros, boring shots). Every scene you keep becomes one beat in your new video, "
        "so fewer scenes = shorter video. **Tip:** 6–8 scenes makes a good 40–60s TikTok."
    )

    frames_dir = out_dir() / "frames"
    all_frames = sorted(frames_dir.glob("frame_*.png"))

    # Load scene metadata for duration display
    scenes_meta = st.session_state.get("scenes_meta", [])
    if not scenes_meta:
        scenes_json = out_dir() / "scenes.json"
        if scenes_json.exists():
            with open(scenes_json) as f:
                scenes_meta = json.load(f)
            st.session_state.scenes_meta = scenes_meta
    scene_by_frame = {s["frame"]: s for s in scenes_meta}

    # Display grid — 4 columns, checkboxes default to unselected
    cols = st.columns(4)
    for i, frame_path in enumerate(all_frames):
        key = f"frame_{frame_path.name}"
        if key not in st.session_state:
            st.session_state[key] = True
        scene_info = scene_by_frame.get(frame_path.name, {})
        dur = scene_info.get("duration", 0)
        label = f"Scene {i+1} ({dur:.1f}s)" if dur else f"Scene {i+1}"
        with cols[i % 4]:
            st.image(str(frame_path), use_container_width=True)
            st.checkbox(label, key=key)

    selected_count = sum(1 for f in all_frames if st.session_state.get(f"frame_{f.name}", False))
    selected_dur = sum(
        scene_by_frame.get(f.name, {}).get("duration", 0)
        for f in all_frames if st.session_state.get(f"frame_{f.name}", False)
    )
    st.info(f"✅ {selected_count} scenes selected ({selected_dur:.1f}s total) = {selected_count} beats will be generated")

    st.divider()
    if st.button("✅ Confirm & Analyze Script", type="primary", disabled=selected_count == 0):
        # Delete unselected frames and update scenes.json to only keep selected
        selected_scenes = []
        for frame_path in all_frames:
            if not st.session_state.get(f"frame_{frame_path.name}", False):
                frame_path.unlink(missing_ok=True)
            else:
                scene_info = scene_by_frame.get(frame_path.name, {})
                if scene_info:
                    selected_scenes.append(scene_info)

        # Overwrite scenes.json with only selected scenes
        if selected_scenes:
            scenes_json = out_dir() / "scenes.json"
            with open(scenes_json, "w") as f:
                json.dump(selected_scenes, f, indent=2)

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
    st.title("Step 3. Review the new script")
    st.markdown(
        "**What happened:** Claude analyzed the original video and wrote you a brand-new script with new fruit characters "
        "and a new plot, keeping the same beat structure and emotional arc. Each beat below shows the dialogue, the image prompt "
        "(for the still frame), and the video prompt (for the animation). Skim through. If it looks good, continue to portraits — "
        "you'll be able to edit individual prompts later if needed."
    )

    script = st.session_state.script or {}
    beats = script.get("beats", [])

    for beat in beats:
        dur = beat.get('duration', 0)
        dur_label = f" | {dur}s" if dur else ""
        with st.expander(f"Beat {beat['beat_number']}: {beat['beat_name']} ({beat.get('emotion', '')}){dur_label}", expanded=True):
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
    if st.button("🎨 Generate Portraits", type="primary"):
        st.session_state.stage = "portraits"
        st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 4 — Character Portraits
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.stage == "portraits":
    st.title("Step 4. Review character portraits")
    st.markdown(
        "**What happened:** Gemini generated a full-body portrait for every character. These portraits are the "
        "visual anchor for the whole video. Every scene image in the next step will use them as reference to keep "
        "characters consistent. **If any portrait looks wrong, fix it here.** Write what you want changed in the "
        "feedback box and click Redo. Only continue once you're happy with every character."
    )

    images_dir = out_dir() / "images"
    portraits = sorted(images_dir.glob("char_*.png")) if images_dir.exists() else []

    if not portraits:
        with st.status("Generating character portraits…", expanded=True) as status:
            placeholder = st.empty()
            rc = run_cmd(repurpose_cmd("portraits"), placeholder)
            if rc == 0:
                status.update(label="✅ Portraits generated!", state="complete")
                st.rerun()
            else:
                status.update(label="❌ Portrait generation failed", state="error")
                st.error(
                    "**Portrait generation failed.** Common reasons:\n"
                    "- Gemini API quota exceeded — wait a minute and retry\n"
                    "- Image content was blocked by safety filters\n\n"
                    "Click below to try again."
                )
                if st.button("🔁 Retry Portraits"):
                    st.rerun()
    else:
        # Load style config for character info
        config_path = out_dir() / "style_config.json"
        char_info = {}
        if config_path.exists():
            with open(config_path) as f:
                cfg = json.load(f)
            for c in cfg.get("characters", {}).get("females", []) + cfg.get("characters", {}).get("males", []):
                key = f"char_{c['name'].lower().replace(' ', '_')}.png"
                char_info[key] = c

        # Display portraits with feedback boxes
        redo_target = None
        for i, p in enumerate(portraits):
            info = char_info.get(p.name, {})
            name = info.get("name", p.stem.replace("char_", "").replace("_", " ").title())
            food_type = info.get("food_type", "")
            role = info.get("role", info.get("family_role", ""))
            caption = f"{name} ({food_type}) — {role}" if food_type else name

            col_img, col_feedback, col_btn = st.columns([2, 3, 1])
            with col_img:
                st.image(str(p), caption=caption, use_container_width=True)
            with col_feedback:
                st.text_area(
                    "Feedback",
                    key=f"feedback_portrait_{p.stem}",
                    height=100,
                    label_visibility="collapsed",
                    placeholder="What to change? (e.g. 'make the outfit blue', 'meaner expression', 'smaller fruit on head'). Leave empty to just regenerate.",
                )
            with col_btn:
                st.write("")
                st.write("")
                if st.button("🔁 Redo", key=f"redo_portrait_{p.stem}"):
                    redo_target = (p, name)

        # Handle redo after all widgets are rendered
        if redo_target:
            target_path, target_name = redo_target
            feedback_text = st.session_state.get(f"feedback_portrait_{target_path.stem}", "").strip()
            if feedback_text:
                with st.status(f"Updating {target_name} description…", expanded=True):
                    update_character_description(target_name, feedback_text)
                    st.write(f"✅ Description updated based on feedback")
            target_path.unlink()
            with st.status(f"Regenerating {target_name}…", expanded=True) as status:
                placeholder = st.empty()
                rc = run_cmd(repurpose_cmd("portraits"), placeholder)
                if rc == 0:
                    status.update(label=f"✅ {target_name} regenerated!", state="complete")
                else:
                    status.update(label=f"❌ Failed to regenerate {target_name}", state="error")
            time.sleep(1)
            st.rerun()

        st.divider()
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔁 Regenerate All Portraits"):
                for p in portraits:
                    p.unlink()
                st.rerun()
        with col2:
            if st.button("✅ Confirm & Generate Scenes", type="primary"):
                st.session_state.stage = "scenes"
                st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 5 — Scene Images
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.stage == "scenes":
    st.title("Step 5. Review scene images")
    st.markdown(
        "**What happened:** using your approved character portraits as reference, Gemini generated one still image "
        "for every beat of the script. These are the key frames that will get animated into video in the next step. "
        "**Review each one.** If composition, expression, or lighting is off, describe the fix in the feedback box and click Redo — "
        "only this one scene is regenerated (your others stay intact)."
    )

    images_dir = out_dir() / "images"
    scenes = sorted(images_dir.glob("scene_*.png")) if images_dir.exists() else []

    if not scenes:
        with st.status("Generating scene images…", expanded=True) as status:
            placeholder = st.empty()
            rc = run_cmd(repurpose_cmd("scenes"), placeholder)
            if rc == 0:
                status.update(label="✅ Scene images generated!", state="complete")
                st.rerun()
            else:
                status.update(label="❌ Scene generation failed", state="error")
                st.error(
                    "**Scene image generation failed.** Common reasons:\n"
                    "- Gemini API quota exceeded — wait a minute and retry\n"
                    "- A scene prompt was blocked by safety filters\n\n"
                    "Click below to retry — already-generated scenes will be skipped."
                )
                if st.button("🔁 Retry Scenes"):
                    st.rerun()
    else:
        beats_data = (st.session_state.script or {}).get("beats", [])
        beat_map = {b["beat_number"]: b for b in beats_data}

        st.subheader(f"🎬 Scene Images ({len(scenes)} / {len(beats_data)})")
        for s in scenes:
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
                    placeholder="What to change? (e.g. 'more dramatic lighting', 'move Cherry to the left'). Leave empty to just regenerate.",
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
                    run_cmd(repurpose_cmd("scenes"), out)
                    st.rerun()

        existing_nums = {int(s.stem.split("_")[1]) for s in scenes}
        missing = [b["beat_number"] for b in beats_data if b["beat_number"] not in existing_nums]
        if missing:
            st.warning(f"Missing scenes: {missing} — click below to generate them")
            if st.button("⚡ Generate Missing Scenes"):
                out = st.empty()
                run_cmd(repurpose_cmd("scenes"), out)
                st.rerun()

        st.divider()
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔁 Regenerate All Scenes"):
                for s in scenes:
                    s.unlink()
                st.rerun()
        with col2:
            if st.button("🎬 Animate Scenes", type="primary"):
                st.session_state.stage = "animate"
                st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 5 — Animation
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.stage == "animate":
    st.title("Step 6. Animate the scenes")
    st.markdown(
        "**What happened:** each still image was sent to Grok video (xAI) to be animated into a short clip, "
        "then stitched together. This step takes ~5 minutes total (each clip is a few seconds). "
        "**Check each clip.** If motion looks wrong, describe what you want in the feedback box and Redo just that clip. "
        "When all clips look good, click **Assemble Final Video** to stitch them together with subtitles."
    )

    videos_dir = out_dir() / "videos"

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
                st.error(
                    "**Video animation failed.** Common reasons:\n"
                    "- Grok API timed out — this step can take up to 10 min\n"
                    "- API quota reached\n\n"
                    "Click below to retry — already-generated clips will be skipped."
                )
                if st.button("🔁 Retry Animation"):
                    st.rerun()
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
                    placeholder="What to change? (e.g. 'slower camera', 'Cherry should walk forward'). Leave empty to just regenerate.",
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
                    st.error(
                        "**Assembly failed.** This usually means one of the video clips is corrupted.\n\n"
                        "Try regenerating the animation step first, then assemble again."
                    )

# ══════════════════════════════════════════════════════════════════════════════
# STAGE 6 — Done
# ══════════════════════════════════════════════════════════════════════════════
elif st.session_state.stage == "done":
    st.title("Step 7. Your video is ready 🎉")
    st.markdown(
        "**That's it.** The final video has your scenes stitched together with burned-in subtitles, "
        "ready to post. Download the MP4 below and upload it to TikTok directly."
    )
    final_path = out_dir() / "final.mp4"

    if final_path.exists():
        st.video(str(final_path))
        st.divider()
        with open(final_path, "rb") as f:
            st.download_button("⬇️ Download final.mp4", f, file_name="final.mp4", mime="video/mp4", type="primary")

    st.info("🎉 Thanks for trying! Each user gets 1 free video.")
