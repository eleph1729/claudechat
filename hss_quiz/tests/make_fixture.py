"""Write a small synthetic dataset laid out like ScaleAI/HSS, for offline testing.

    python tests/make_fixture.py /tmp/hss-fixture

It has data/test.jsonl with the real column names, generated images and
ffmpeg test-pattern videos of assorted lengths, and a media_credits.csv.
The questions are placeholders; nothing here comes from the real benchmark.
"""

import csv
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

DOMAINS = {
    "Temporal & Causal Dynamics": ["Retrodiction", "Mechanistic Causality", "Extrapolation"],
    "Physical & Spatial Logic": ["Hidden & Invisible", "Affordance & Feasibility",
                                 'Spatial "Alien Viewpoint"', "Spatial Reachability"],
    "Social Understanding": ["Theory of Mind", "Social Role, Norm & Power Dynamics"],
    "Abstract & Contextual Inference": ["Change & Consequence", "Patterns & Pareidolia"],
}
COLORS = ["#d97757", "#3ecf8e", "#8ab4ff", "#f2b84b", "#c084fc", "#ff6b6b"]


def main(root: Path) -> None:
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "media" / "images").mkdir(parents=True, exist_ok=True)
    (root / "media" / "videos").mkdir(parents=True, exist_ok=True)
    pairs = [(d, s) for d, subs in DOMAINS.items() for s in subs]
    rows, credits = [], []
    for i in range(40):
        domain, sub = pairs[i % len(pairs)]
        n = 3 + i % 4
        color = COLORS[i % len(COLORS)]
        rel = f"media/images/img_{i:03d}.{'png' if i % 2 else 'jpg'}"
        im = Image.new("RGB", (1600, 1000) if i % 3 else (900, 1400), "#202430")
        d = ImageDraw.Draw(im)
        for k in range(n):
            d.ellipse([150 + k * 260, 400, 350 + k * 260, 600], fill=color)
        d.text((60, 60), f"fixture image {i}", fill="white")
        im.save(root / rel)
        rows.append(task(f"img-{i:03d}", rel, "image", domain, sub,
                         f"How many {color} circles are in the picture?",
                         f"There are {n} circles.", [f"States that there are {n} circles"]))
        credits.append({"media_path": rel, "author": f"Fixture Photographer {i}",
                        "license": "CC BY 4.0", "source_url": f"https://example.org/img/{i}"})
    durations = [12, 70, 30, 8, 95, 41, 50, 20, 44, 120, 15, 33, 60, 25, 38, 47, 10, 28, 55, 18,
                 36, 22, 90, 14, 40, 26, 31, 65, 19, 43]
    for i, dur in enumerate(durations):
        domain, sub = pairs[(i * 3) % len(pairs)]
        rel = f"media/videos/vid_{i:03d}.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                        "-i", f"testsrc2=size=480x270:rate=24:duration={dur}",
                        "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "35",
                        str(root / rel)], check=True)
        rows.append(task(f"vid-{i:03d}", rel, "video", domain, sub,
                         "Which way does the coloured bar move, and when does the counter reach 5?",
                         "The bar moves right to left; the counter reaches 5 at about five seconds.",
                         ["Says the bar moves right to left", "Says the counter reaches 5 around 5 s"]))
        if i % 2 == 0:  # leave some without credits to exercise the fallback text
            credits.append({"media_path": rel, "author": f"Fixture Videographer {i}",
                            "license": "CC BY-SA 4.0", "source_url": ""})
    with open(root / "data" / "test.jsonl", "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    with open(root / "media_credits.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["media_path", "author", "license", "source_url"])
        w.writeheader()
        w.writerows(credits)
    print(f"fixture: {len(rows)} tasks in {root}")


def task(tid, rel, kind, domain, sub, prompt, golden, crits):
    return {"task_id": tid, "prompt": prompt, "media_path": rel, "media_type": kind,
            "domain": domain, "subdomain": sub, "golden_response": golden,
            "rubric_criteria": json.dumps([{"id": f"c{k + 1}", "title": t} for k, t in enumerate(crits)])}


if __name__ == "__main__":
    main(Path(sys.argv[1]))
