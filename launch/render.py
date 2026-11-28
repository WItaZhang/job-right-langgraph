"""Compose the real product recordings into captioned 16:9 and 9:16 launch films."""

import argparse
import json
import re
import subprocess
from functools import lru_cache
from pathlib import Path

import imageio_ffmpeg
import qrcode
from PIL import Image, ImageDraw, ImageFont

FPS = 30
FF = imageio_ffmpeg.get_ffmpeg_exe()
PAPER = "#f4f1e6"
INK = "#162c27"
DEEP = "#182e29"
PALE = "#d4e2ba"
ORANGE = "#de8a64"
FONT_ROOT = Path("/System/Library/Fonts/Supplemental")
SPEECH = [
    [
        "Yesterday you saved a great hybrid role.",
        "Today you switch to remote only.",
        "What happens to the job you wanted to keep?",
    ],
    [
        "With Job Right, you can see the tradeoff before you save.",
        "That backend role at Fernhill Freight would disappear",
        "because it has two office days.",
    ],
    [
        "Keep hybrid roles at Fernhill.",
        "Review the revised rule, then approve it.",
        "The job stays in your matches.",
    ],
    [
        "Open the role and bring your base résumé.",
        "Job Right moves relevant accomplishments up within each position,",
        "keeping them with the employer where they happened.",
    ],
    [
        "Your cover letter draws on the same work.",
        "Trace a result back to the résumé, edit the wording,",
        "and download the pair.",
    ],
    ["Bring a job description of your own.", "Try Job Right."],
]
CHINESE = [
    ["昨天，你收藏了一个很合适的混合办公岗位。", "今天，你改成了只看远程。", "那个想保留的岗位会怎样？"],
    [
        "在 Job Right，保存偏好前就能看到取舍。",
        "Fernhill Freight 的后端岗位会被过滤，",
        "因为这份工作每周有两天需要到办公室。",
    ],
    ["为 Fernhill 保留混合办公例外。", "检查修改后的规则，再批准。", "这个岗位依然在你的匹配列表里。"],
    [
        "打开岗位，放入你的简历底稿。",
        "Job Right 会在每份经历内优先呈现相关成果，",
        "每项成果依然归于原来的雇主。",
    ],
    ["求职信也从同一份经历出发。", "对照简历核实成果，调整措辞，", "然后下载两份材料。"],
    ["试试你自己的岗位描述。", "打开 Job Right。"],
]


@lru_cache
def font(size, family="text"):
    name = {
        "text": "Arial.ttf",
        "bold": "Arial Bold.ttf",
        "display": "Baskerville.ttc",
        "italic": "Georgia Italic.ttf",
    }[family]
    return ImageFont.truetype(str(FONT_ROOT / name), size)


def lines(text, f, width):
    result = []
    for word in text.split():
        if result and f.getlength(result[-1] + " " + word) <= width:
            result[-1] += " " + word
        else:
            result.append(word)
    return result


def textblock(draw, xy, text, f, width, color, leading, center=False):
    x, y = xy
    for line in lines(text, f, width):
        draw.text((x, y), line, fill=color, font=f, anchor="mt" if center else "lt")
        y += leading
    return y


def logo(d, x, y, light=False, scale=1):
    unit = scale
    icon = PALE if light else INK
    bg = INK if light else PAPER
    d.rounded_rectangle((x, y, x + 44 * unit, y + 44 * unit), radius=5 * unit, fill=icon)
    for i in range(3):
        left = x + (8 + i * 8) * unit
        floor = y + 34 * unit
        h = (9 + i * 7) * unit
        d.polygon(
            [
                (left, floor),
                (left + 6 * unit, floor),
                (left + 6 * unit + h * 0.23, floor - h),
                (left + h * 0.23, floor - h),
            ],
            fill=bg,
        )
    d.text(
        (x + 58 * unit, y + 1 * unit),
        "Job Right",
        fill=PAPER if light else INK,
        font=font(round(45 * unit), "display"),
        anchor="lt",
    )


def timestamps(cues):
    output = []
    for i, c in enumerate(cues):
        words = [len(s.split()) for s in SPEECH[i]]
        cursor = c["start"] + c["lead"]
        group = []
        for j, w in enumerate(words):
            end = cursor + c["speech"] * w / sum(words)
            group.append((cursor, end, SPEECH[i][j], CHINESE[i][j]))
            cursor = end
        output.append(group)
    return output


def stamp(seconds):
    ms = round(seconds * 1000)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def subtitles(groups, out):
    for language, idx in [("en", 2), ("zh-Hans", 3)]:
        chunks = []
        for row in sum(groups, []):
            chunks.append(f"{len(chunks) + 1}\n{stamp(row[0])} --> {stamp(row[1])}\n{row[idx]}\n")
        (out / f"job-right-{language}.srt").write_text("\n".join(chunks), encoding="utf-8")


def duration(path):
    run = subprocess.run([FF, "-i", str(path)], capture_output=True, text=True)
    h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", run.stderr).groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def reader(path, width, height, frames):
    skip = 0.48
    speed = (frames / FPS) / (duration(path) - skip)
    return subprocess.Popen(
        [
            FF,
            "-loglevel",
            "error",
            "-ss",
            str(skip),
            "-i",
            str(path),
            "-an",
            "-vf",
            f"setpts={speed}*PTS,fps={FPS},scale={width}:{height}:flags=lanczos,tpad=stop_mode=clone:stop_duration=1",
            "-frames:v",
            str(frames),
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        stdout=subprocess.PIPE,
    )


def smooth(x):
    x = min(1, max(0, x))
    return x * x * (3 - 2 * x)


def compose(captures, sound, out, vertical, domain):
    W, H = (1080, 1920) if vertical else (1920, 1080)
    meta = json.loads(Path(__file__).with_name("film.json").read_text())
    cues = json.loads((sound / "timing.json").read_text())
    groups = timestamps(cues)
    out.mkdir(parents=True, exist_ok=True)
    subtitles(groups, out)
    target = out / ("job-right-vertical.mp4" if vertical else "job-right-widescreen.mp4")
    code = qrcode.QRCode(box_size=8, border=4)
    code.add_data("https://" + domain)
    code.make(fit=True)
    qr = code.make_image(fill_color=INK, back_color=PAPER).convert("RGB")
    writer = subprocess.Popen(
        [
            FF,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{W}x{H}",
            "-r",
            str(FPS),
            "-i",
            "-",
            "-i",
            str(sound / "mix.wav"),
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "18",
            "-threads",
            "4",
            "-pix_fmt",
            "yuv420p",
            "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=8,aresample=48000",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            "-shortest",
            str(target),
        ],
        stdin=subprocess.PIPE,
    )
    saved = Image.open(captures / "saved.png").convert("RGB")
    try:
        for idx, (scene, cue) in enumerate(zip(meta, cues, strict=True)):
            n = round(cue["duration"] * FPS)
            dark = idx in (0, 2, 5)
            bg, fg = (DEEP, PAPER) if dark else (PAPER, INK)
            accent = ORANGE if dark else "#9e4c2b"
            base = Image.new("RGB", (W, H), bg)
            d = ImageDraw.Draw(base)
            margin = 70 if vertical else 78
            logo(d, margin, 62 if vertical else 45, dark)
            footer = 1650 if vertical else 926
            d.line((margin, footer, W - margin, footer), fill="#78877a" if dark else "#aeb5aa", width=2)
            proc = None
            if 0 < idx < 5:
                if vertical:
                    sw, sh = 660, 1252
                    sx, sy = (W - sw) // 2, 352
                    textblock(d, (margin, 164), scene["title"], font(72, "display"), W - 2 * margin, fg, 73)
                    d.text((margin, 278), scene["topic"], font=font(28), fill=accent, anchor="lt")
                else:
                    sw, sh = 1120, 768
                    sx, sy = 720, 127
                    d.text((margin, 245), scene["topic"], font=font(28), fill=accent, anchor="lt")
                    textblock(d, (margin, 311), scene["title"], font(92, "display"), 600, fg, 94)
                d.rectangle((sx - 4, sy - 4, sx + sw + 3, sy + sh + 4), fill="#acb4a6" if dark else "#b1b9ad")
                proc = reader(captures / f"scene-{idx}.webm", sw, sh, n)
            for i in range(n):
                t = i / FPS
                abs_time = cue["start"] + t
                img = base.copy()
                pen = ImageDraw.Draw(img)
                if proc:
                    frame = proc.stdout.read(sw * sh * 3)
                    if len(frame) != sw * sh * 3:
                        raise RuntimeError(f"Missing screen recording frame {idx}/{i}")
                    screen = Image.frombytes("RGB", (sw, sh), frame)
                    img.paste(screen, (sx, sy))
                elif idx == 0:
                    if vertical:
                        textblock(
                            pen,
                            (margin, 298 - int(20 * smooth(t / 0.6))),
                            scene["title"],
                            font(130, "display"),
                            950,
                            fg,
                            134,
                        )
                        if t > 0.65:
                            textblock(
                                pen,
                                (margin, 478),
                                scene["second"],
                                font(91, "italic"),
                                930,
                                PALE,
                                106,
                            )
                        cw = 924
                        x, y = (W - cw) // 2, 850
                    else:
                        textblock(
                            pen,
                            (margin, 253 - int(20 * smooth(t / 0.6))),
                            scene["title"],
                            font(156, "display"),
                            1050,
                            fg,
                            165,
                        )
                        if t > 0.65:
                            textblock(
                                pen,
                                (margin, 470),
                                scene["second"],
                                font(96, "italic"),
                                800,
                                PALE,
                                111,
                            )
                        cw = 820
                        x, y = 1015, 378
                    card = saved.resize(
                        (cw, round(saved.height * cw / saved.width)), Image.Resampling.LANCZOS
                    )
                    lift = round((1 - smooth((t - 0.10) / 0.7)) * 75)
                    pen.rectangle(
                        (x - 6, y + lift - 6, x + cw + 5, y + lift + card.height + 7), fill="#b6c6aa"
                    )
                    img.paste(card, (x, y + lift))
                    # The wipe visualizes the change shown in the following real review.
                    wipe = round(cw * smooth((t - 3.5) / 1.2))
                    if wipe:
                        tinted = Image.blend(card, Image.new("RGB", card.size, DEEP), 0.68)
                        img.paste(tinted.crop((0, 0, wipe, card.height)), (x, y + lift))
                        pen.line((x + wipe, y + lift, x + wipe, y + lift + card.height), fill=ORANGE, width=4)
                    pen.text(
                        (x, y + lift + card.height + 38),
                        "Remote only  /  hidden" if wipe > cw / 2 else "Saved before the rule change",
                        fill=PALE,
                        font=font(29),
                        anchor="lt",
                    )
                else:
                    cy = 465 if vertical else 267
                    pen.text(
                        (margin, cy - round(16 * smooth(t / 0.7))),
                        "Job Right",
                        fill=fg,
                        font=font(183 if vertical else 210, "display"),
                        anchor="lt",
                    )
                    textblock(
                        pen,
                        (margin, cy + (228 if vertical else 256)),
                        scene["second"],
                        font(68 if vertical else 72, "italic"),
                        W - 2 * margin,
                        PALE,
                        80,
                    )
                    yy = cy + (486 if vertical else 405)
                    pen.line(
                        (margin, yy, margin + round((W - 2 * margin) * 0.64 * smooth(t / 0.8)), yy),
                        fill=ORANGE,
                        width=4,
                    )
                    pen.text(
                        (margin, yy + 33), domain, font=font(44 if vertical else 49), fill=fg, anchor="lt"
                    )
                    if t > 0.5:
                        side = qr.width
                        img.paste(
                            qr, (margin if vertical else W - margin - side, yy + 130 if vertical else 358)
                        )
                active = next((s[2] for s in groups[idx] if s[0] <= abs_time < s[1]), "")
                if active:
                    textblock(
                        pen,
                        (W // 2, footer + (40 if vertical else 38)),
                        active,
                        font(37 if vertical else 34),
                        W - 2 * margin - 50,
                        fg,
                        48,
                        True,
                    )
                amount = abs_time / (cues[-1]["start"] + cues[-1]["duration"])
                pen.rectangle((0, H - 7, round(W * amount), H), fill=accent)
                writer.stdin.write(img.tobytes())
                if i == n // 2:
                    img.resize((W // 2, H // 2)).save(captures / f"proof-{idx}.jpg", quality=88)
            if proc:
                proc.stdout.close()
                if proc.wait():
                    raise RuntimeError("Screen decoder failed")
            print(f"Rendered {target.name}: {idx + 1}/{len(meta)}", flush=True)
    finally:
        writer.stdin.close()
    if writer.wait():
        raise RuntimeError("Video encoder failed")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("captures", type=Path)
    p.add_argument("audio", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--vertical", action="store_true")
    p.add_argument("--domain", default="job-right-langgraph.vercel.app")
    a = p.parse_args()
    compose(a.captures, a.audio, a.out, a.vertical, a.domain)
