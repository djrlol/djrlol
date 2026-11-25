#!/usr/bin/env python3
"""Builds field.svg and the numbers in README.md from the contribution calendar.

The panel is the last year as a grid of dots (one dot per day, brighter means
busier). The dots blink in a few different rhythms and a small light wanders
over the grid, waking the dots it passes.

Standard library only. Token: $GITHUB_TOKEN, or the gh CLI when run locally.
The output depends only on the calendar data, so a second run on the same day
changes nothing.
"""
import json
import math
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGIN = os.environ.get("GITHUB_REPOSITORY_OWNER", "djrlol")

# ---- layout (SVG user units) -------------------------------------------------
COLS, ROWS = 53, 7
PITCH = 18
R = 4
W = 1060
PAD_Y = 48
H = PAD_Y * 2 + (ROWS - 1) * PITCH
X0 = (W - (COLS - 1) * PITCH) / 2
Y0 = PAD_Y

# ---- the light ---------------------------------------------------------------
LOOP = 26.0  # seconds for one lap
SAMPLES = 104
WAKE_RADIUS = 24  # dots this close to the path light up as it passes

LEVELS = {  # contribution level -> dot opacity
    None: 0.03,  # day that has not happened yet
    "NONE": 0.10,
    "FIRST_QUARTILE": 0.30,
    "SECOND_QUARTILE": 0.50,
    "THIRD_QUARTILE": 0.74,
    "FOURTH_QUARTILE": 1.0,
}
LEVEL_KEYS = list(LEVELS)

# blink rhythms: (seconds per cycle, keyframes on opacity)
BLINKS = [
    (3.4, "0%,100%{opacity:1}50%{opacity:.35}"),  # slow breath
    (5.2, "0%,62%,100%{opacity:1}68%{opacity:.15}74%{opacity:1}80%{opacity:.15}86%{opacity:1}"),  # double blink
    (7.0, "0%,84%,100%{opacity:1}92%{opacity:.1}"),  # long hold, one dip
    (2.3, "0%,100%{opacity:1}30%{opacity:.55}60%{opacity:.9}80%{opacity:.4}"),  # flicker
]
BLINK_SHARE = 0.55  # share of dots that blink at all
DELAY_STEPS = 9

QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      restrictedContributionsCount
      contributionCalendar {
        totalContributions
        weeks { contributionDays { weekday contributionLevel } }
      }
    }
  }
}
"""


def token() -> str:
    t = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if t:
        return t
    return subprocess.run(
        ["gh", "auth", "token"], check=True, capture_output=True, text=True
    ).stdout.strip()


def fetch() -> dict:
    body = json.dumps({"query": QUERY, "variables": {"login": LOGIN}}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers={
            "Authorization": f"bearer {token()}",
            "Content-Type": "application/json",
            "User-Agent": "profile-field",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        data = json.load(res)
    if data.get("errors"):
        sys.exit(f"GraphQL error: {data['errors']}")
    return data["data"]["user"]["contributionsCollection"]


def f(x: float) -> str:
    """Short, stable number formatting."""
    s = f"{x:.2f}".rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def noise(c: int, r: int, salt: int) -> float:
    """Fixed pseudo-random value in [0, 1) per dot, independent of the data."""
    v = math.sin(c * 127.1 + r * 311.7 + salt * 74.7) * 43758.5453
    return v - math.floor(v)


def path_point(u: float) -> tuple:
    """The light's closed wandering path, u in [0, 1)."""
    a = 2 * math.pi * u
    x = W / 2 + 430 * math.sin(a) + 46 * math.sin(3 * a + 1.0)
    y = H / 2 + 54 * math.sin(2 * a + 0.6) + 16 * math.sin(5 * a + 2.1)
    return x, y


def flight() -> tuple:
    pts = [path_point(i / SAMPLES) for i in range(SAMPLES + 1)]
    move, rot = [], []
    prev = None
    for i, (x, y) in enumerate(pts):
        nx, ny = path_point((i + 0.5) / SAMPLES)
        px, py = path_point((i - 0.5) / SAMPLES)
        ang = math.degrees(math.atan2(ny - py, nx - px))
        if prev is not None:  # unwrap so the tail never spins the long way round
            while ang - prev > 180:
                ang -= 360
            while ang - prev < -180:
                ang += 360
        prev = ang
        p = f(i / SAMPLES * 100) + "%"
        move.append(f"{p}{{transform:translate({f(x)}px,{f(y)}px)}}")
        rot.append(f"{p}{{transform:rotate({f(ang)}deg)}}")
    return pts, "".join(move), "".join(rot)


def build_svg(cal: dict) -> str:
    weeks = cal["weeks"][-COLS:]
    weeks = [{"contributionDays": []}] * (COLS - len(weeks)) + weeks
    grid = {}
    for c, week in enumerate(weeks):
        for day in week["contributionDays"]:
            grid[(c, day["weekday"])] = day["contributionLevel"]

    pts, move, rot = flight()
    dots = []
    for c in range(COLS):
        for r in range(ROWS):
            x, y = X0 + c * PITCH, Y0 + r * PITCH
            cls = f"d{LEVEL_KEYS.index(grid.get((c, r)))}"
            if noise(c, r, 1) < BLINK_SHARE:
                cls += f" k{int(noise(c, r, 2) * len(BLINKS))} w{int(noise(c, r, 3) * DELAY_STEPS)}"
            # when does the light come closest to this dot?
            dist, i = min((math.hypot(px - x, py - y), i) for i, (px, py) in enumerate(pts[:-1]))
            wake = ""
            if dist < WAKE_RADIUS:
                wake = f' style="animation-delay:{f(i / SAMPLES * LOOP - 0.15)}s"'
            dots.append(f'<g class="{cls}"><circle cx="{f(x)}" cy="{f(y)}" r="{R}"{wake}/></g>')

    level_css = "".join(f".d{i}{{--d:{f(LEVELS[k])}}}" for i, k in enumerate(LEVEL_KEYS))
    blink_css = "".join(
        f"@keyframes k{i}{{{frames}}}.k{i}{{animation:k{i} {f(sec)}s ease-in-out infinite}}"
        for i, (sec, frames) in enumerate(BLINKS)
    )
    delay_css = "".join(f".w{i}{{animation-delay:-{f(i * 0.83)}s}}" for i in range(1, DELAY_STEPS))
    x0, y0 = pts[0]
    style = (
        f"circle{{fill:#fff}}{level_css}"
        f".g circle{{fill-opacity:var(--d)}}"
        f".l{{transform:translate({f(x0)}px,{f(y0)}px)}}"
        f"@media (prefers-reduced-motion:no-preference){{"
        f"{blink_css}{delay_css}"
        f"@keyframes wake{{0%{{fill-opacity:var(--d)}}1.2%{{fill-opacity:1}}7%,100%{{fill-opacity:var(--d)}}}}"
        f".g circle[style]{{animation:wake {f(LOOP)}s ease-out infinite}}"
        f"@keyframes fly{{{move}}}@keyframes rot{{{rot}}}"
        f"@keyframes glow{{0%,100%{{opacity:.75}}50%{{opacity:1}}}}"
        f".l{{animation:fly {f(LOOP)}s linear infinite}}"
        f".lr{{animation:rot {f(LOOP)}s linear infinite}}"
        f".h{{animation:glow 3.2s ease-in-out infinite}}"
        f"}}"
    )

    light = (
        '<g class="l">'
        '<g class="lr"><path d="M0 -2.2 L-44 0 L0 2.2 Z" fill="url(#tail)" opacity=".75"/></g>'
        '<circle class="h" r="17" style="fill:url(#halo)"/>'
        '<circle r="2.7"/>'
        "</g>"
    )
    defs = (
        "<defs>"
        '<radialGradient id="halo">'
        '<stop offset="0" stop-color="#fff" stop-opacity=".95"/>'
        '<stop offset=".22" stop-color="#fff" stop-opacity=".5"/>'
        '<stop offset=".5" stop-color="#fff" stop-opacity=".11"/>'
        '<stop offset="1" stop-color="#fff" stop-opacity="0"/>'
        "</radialGradient>"
        '<linearGradient id="tail" x1="1" x2="0" y1="0" y2="0">'
        '<stop offset="0" stop-color="#fff" stop-opacity=".9"/>'
        '<stop offset=".45" stop-color="#fff" stop-opacity=".35"/>'
        '<stop offset="1" stop-color="#fff" stop-opacity="0"/>'
        "</linearGradient>"
        "</defs>"
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'width="{W}" height="{H}" role="img" aria-label="a year of contributions">'
        f"<style>{style}</style>{defs}"
        f'<rect width="{W}" height="{H}" rx="12" fill="#000"/>'
        f'<g class="g">{"".join(dots)}</g>{light}</svg>\n'
    )


README = """<img src="field.svg" width="100%" alt="A grid of white dots on black, one dot per day of the last year, brighter for busier days. The dots blink and a small light wanders across them.">

every dot is a day. brighter dot, busier day.<br><!-- stats -->{stats}<!-- /stats --> contributions are private, so this is most of what you get to see.

[djr.sh](https://djr.sh) · `curl donut.vatra.sh`
"""


def main() -> None:
    col = fetch()
    cal = col["contributionCalendar"]
    (ROOT / "field.svg").write_text(build_svg(cal))

    stats = f"{col['restrictedContributionsCount']:,} of the last {cal['totalContributions']:,}"
    readme = ROOT / "README.md"
    if readme.exists():
        text = re.sub(
            r"(<!-- stats -->).*?(<!-- /stats -->)",
            lambda m: m.group(1) + stats + m.group(2),
            readme.read_text(),
            flags=re.S,
        )
    else:
        text = README.format(stats=stats)
    readme.write_text(text)
    print(f"field.svg {(ROOT / 'field.svg').stat().st_size} bytes, stats: {stats}")


if __name__ == "__main__":
    main()
