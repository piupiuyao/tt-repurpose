# TT Repurpose

Turn any TikTok video into a brand new AI-generated video — with new characters, rewritten script, and animated scenes.

https://github.com/user-attachments/assets/demo-placeholder

## What it does

Give it a TikTok URL. It will:

1. **Download & transcribe** the video (yt-dlp + Whisper)
2. **Detect scenes** and extract key frames (PySceneDetect + FFmpeg)
3. **Analyze** the story structure, characters, and emotions (Claude)
4. **Rewrite** the script with completely new characters (Claude)
5. **Generate character portraits** matching the original art style (Gemini)
6. **Generate scene images** for each story beat (Gemini)
7. **Animate** each scene into video clips (Grok)
8. **Assemble** everything into a final video with subtitles (FFmpeg)

You can run the full pipeline with one command, or go step by step through the Streamlit UI to review and tweak at each stage.

## Examples

| Source (TikTok) | Output (AI-generated) |
|---|---|
| Carrot family drama | New vegetable characters, same story |
| Real person marketing skit | Stick figure line art version |

## Quick start

### Prerequisites

- Python 3.10+
- FFmpeg installed (`brew install ffmpeg` on Mac)

### Setup

```bash
git clone https://github.com/piupiuyao/tt-repurpose.git
cd tt-repurpose
pip install -r requirements.txt
```

Create a `.env` file with your API keys:

```
OPENROUTER_API_KEY=your_key_here
GOOGLE_API_KEY=your_key_here
XAI_API_KEY=your_key_here
```

| Key | What it's for | Where to get it |
|---|---|---|
| `OPENROUTER_API_KEY` | Script analysis & rewriting (Claude) | [openrouter.ai](https://openrouter.ai) |
| `GOOGLE_API_KEY` | Image generation (Gemini) | [aistudio.google.com](https://aistudio.google.com) |
| `XAI_API_KEY` | Video animation (Grok) | [console.x.ai](https://console.x.ai) |

### Run with UI

```bash
streamlit run app.py
```

Open http://localhost:8501 — paste a TikTok URL and follow the steps.

### Run from command line

```bash
# Full pipeline
python repurpose.py --url "https://www.tiktok.com/@..." --output my_video

# Or step by step
python repurpose.py --url "..." --output my_video --step extract
python repurpose.py --url "..." --output my_video --step analyze
python repurpose.py --url "..." --output my_video --step rewrite
python repurpose.py --url "..." --output my_video --step portraits
python repurpose.py --url "..." --output my_video --step scenes
python repurpose.py --url "..." --output my_video --step animate
python repurpose.py --url "..." --output my_video --step assemble
```

## Project structure

```
tt-repurpose/
├── app.py              # Streamlit UI
├── repurpose.py        # CLI entry point
├── steps/
│   ├── extract.py      # Download, transcribe, scene detection
│   ├── analyze.py      # Story analysis + character detection
│   ├── rewrite.py      # Script rewriting with new characters
│   ├── generate_images.py  # Portrait & scene image generation
│   ├── animate.py      # Video animation
│   └── assemble.py     # Final video assembly with subtitles
├── prompts/            # System prompts for Claude
├── config/             # Style configs (character templates)
└── requirements.txt
```

## How it works under the hood

The key insight: **copy the source video's art style, not the characters.** The pipeline analyzes the original video's 3D render style, lighting, and character archetypes, then generates a completely new cast that matches the same visual quality. Gemini is given reference frames from the source video to match the style, while creating original character designs.

For script rewriting, Claude receives the full beat-by-beat story structure and maps each original character to a new one, keeping the same plot and emotional beats while swapping in the new cast.

## License

MIT
