#!/usr/bin/env python3
import click
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


@click.command()
@click.option("--url", required=True, help="TikTok video URL")
@click.option("--style", default="fruit-drama", show_default=True, help="Style config name")
@click.option("--output", default="output", show_default=True, help="Output directory")
@click.option("--step", default="all", show_default=True,
              type=click.Choice(["all", "extract", "analyze", "rewrite", "portraits", "scenes", "images", "animate", "assemble"]),
              help="Run only a specific step")
@click.option("--clone", is_flag=True, default=False, help="Clone mode: keep original characters instead of generating new ones")
def main(url: str, style: str, output: str, step: str, clone: bool):
    """TikTok AI Video Repurpose Tool — turn any TikTok into a new AI drama video."""
    output_dir = Path(output)

    if step in ("all", "extract"):
        from steps.extract import run as extract
        extract(url, output_dir)

    if step in ("all", "analyze"):
        from steps.analyze import run as analyze
        analyze(output_dir, keep_original=clone)

    if step in ("all", "rewrite"):
        from steps.rewrite import run as rewrite
        rewrite(output_dir, style)

    if step in ("all", "portraits", "images"):
        from steps.generate_images import run_portraits
        run_portraits(output_dir, style)

    if step in ("all", "scenes", "images"):
        from steps.generate_images import run_scenes
        run_scenes(output_dir, style)

    if step in ("all", "animate"):
        from steps.animate import run as animate
        animate(output_dir)

    if step in ("all", "assemble"):
        from steps.assemble import run as assemble
        assemble(output_dir)


if __name__ == "__main__":
    main()
