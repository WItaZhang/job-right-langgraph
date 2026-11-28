"""Package the two finished cuts, a widescreen thumbnail and both subtitle tracks."""

import argparse
import subprocess
import zipfile
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw
from render import FF, font


def package(directory, domain):
    wide = directory / "job-right-widescreen.mp4"
    frame = subprocess.check_output(
        [
            FF,
            "-v",
            "error",
            "-ss",
            "2",
            "-i",
            str(wide),
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-c:v",
            "png",
            "-",
        ]
    )
    poster = Image.open(BytesIO(frame)).convert("RGB").resize((1280, 720), Image.Resampling.LANCZOS)
    pen = ImageDraw.Draw(poster)
    pen.rectangle((0, 625, 1280, 720), fill=poster.getpixel((10, 580)))
    pen.text((52, 643), domain, font=font(26), fill="#d4e2ba", anchor="lt")
    thumbnail = directory / "job-right-thumbnail.jpg"
    poster.save(thumbnail, quality=93, optimize=True)
    with zipfile.ZipFile(directory / "job-right-share-kit.zip", "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in (
            wide,
            directory / "job-right-vertical.mp4",
            directory / "job-right-en.srt",
            directory / "job-right-zh-Hans.srt",
            thumbnail,
        ):
            bundle.write(path, path.name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--domain", default="job-right-langgraph.vercel.app")
    args = parser.parse_args()
    package(args.directory, args.domain)
