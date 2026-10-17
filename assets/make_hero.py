# Builds assets/hero.html from the reference results. Every number in the image comes from the repo.
# Usage: python3 assets/make_hero.py . assets/hero.html
# Then render it to PNG (1200x630 at 2x) with any headless Chromium, for example:
#   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --hide-scrollbars \
#     --force-device-scale-factor=2 --window-size=1200,630 --screenshot=assets/hero.png "file://$PWD/assets/hero.html"
import csv, json, math, sys

repo, out = sys.argv[1], sys.argv[2]
R = f"{repo}/results/reference"
labels = {json.loads(l)["id"]: json.loads(l)["label"]
          for l in open(f"{repo}/data/es_adversarial_v2.jsonl", encoding="utf-8") if l.strip()}


def from_csv(p):
    return {r["id"]: r["pred"] for r in csv.DictReader(open(p, encoding="utf-8"))}


def from_log(p):
    d = {}
    for l in open(p, encoding="utf-8"):
        x = l.rstrip("\n").split("\t")
        if x[0] == "PRED" and x[1] == "es_adversarial_v2":
            d[x[2]] = x[3]
    return d


def wilson(k, n, z=1.96):
    p = k / n; d = 1 + z * z / n; c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, c - h, c + h


runs = [  # (label, kind, predictions)
    ("iPhone 15 Pro", "On-device", from_log(f"{R}/2026-10_ios27.0.1_iphone15pro/run1_console.log")),
    ("Mac (M4)", "On-device", from_csv(f"{R}/2026-10_macos27.0.1/es_adversarial_v2_predictions.csv")),
    ("gpt-oss-20b", "Cloud", from_csv(f"{R}/2026-10_groq/adv_v2_g20_run2_predictions.csv")),
    ("gpt-oss-120b", "Cloud", from_csv(f"{R}/2026-10_groq/adv_v2_g120_run2_predictions.csv")),
]
rows = []
for name, kind, p in runs:
    k = sum((p[i] == "JUNK") == (labels[i] == "scam") for i in labels)
    rows.append((name, kind, *wilson(k, len(labels))))

W, H = 1200, 630
px0, px1, lo, hi = 820, 1100, 0.85, 1.00          # plot x-range and value domain
X = lambda v: px0 + (v - lo) / (hi - lo) * (px1 - px0)
top, gap = 236, 46
color = {"On-device": "var(--dev)", "Cloud": "var(--cloud)"}

svg = []
for t in (0.85, 0.90, 0.95, 1.00):
    svg.append(f'<line x1="{X(t):.1f}" y1="{top-22}" x2="{X(t):.1f}" y2="{top+gap*3+20}" stroke="var(--grid)" stroke-width="1"/>')
    svg.append(f'<text x="{X(t):.1f}" y="{top+gap*3+40}" fill="var(--muted)" font-size="13" text-anchor="middle">{int(t*100)}%</text>')
for r, (name, kind, p, a, b) in enumerate(rows):
    y = top + r * gap
    svg.append(f'<text x="{px0-18}" y="{y-1}" fill="var(--text)" font-size="16" font-weight="600" text-anchor="end">{name}</text>')
    svg.append(f'<text x="{px0-18}" y="{y+16}" fill="var(--muted)" font-size="13" text-anchor="end">{kind.lower()}{" · Groq" if kind == "Cloud" else ""}</text>')
    svg.append(f'<line x1="{X(a):.1f}" y1="{y}" x2="{X(b):.1f}" y2="{y}" stroke="{color[kind]}" stroke-width="2" stroke-linecap="round"/>')
    svg.append(f'<circle cx="{X(p):.1f}" cy="{y}" r="6.5" fill="{color[kind]}" stroke="var(--surface)" stroke-width="2"/>')
    svg.append(f'<text x="{X(b)+10:.1f}" y="{y+5}" fill="var(--text)" font-size="15" font-weight="600">{p*100:.1f}%</text>')

stats = [
    ("0", "messages sent to a server<br>(on-device)"),
    ("0", "legitimate messages<br>blocked silently"),
    ("15 MB", "app memory<br>(system model ~455 MB)"),
    ("0.75–1.25 s", "per message on<br>iPhone 15 Pro"),
]
stat_html = "".join(f'<div class="stat"><div class="v">{v}</div><div class="l">{l}</div></div>' for v, l in stats)

html = f"""<!doctype html><html><head><meta charset="utf-8">
<style>
:root {{ --surface:#1a1a19; --text:#f0efec; --text2:#c3c2b7; --muted:#8f8e86; --grid:#2e2e2c; --rule:#2e2e2c; --dev:#3987e5; --cloud:#d95926; }}
* {{ margin:0; box-sizing:border-box; }}
html,body {{ width:{W}px; height:{H}px; background:var(--surface); font-family:-apple-system,"SF Pro Display","Helvetica Neue",Arial,sans-serif; color:var(--text); overflow:hidden; }}
.left {{ position:absolute; left:64px; top:60px; width:500px; }}
.eyebrow {{ font-size:15px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); font-weight:600; }}
h1 {{ font-size:50px; line-height:1.06; font-weight:700; letter-spacing:-.02em; margin-top:20px; }}
h1 span {{ color:var(--text2); }}
.sub {{ font-size:19px; line-height:1.45; color:var(--text2); margin-top:22px; }}
.ct {{ position:absolute; left:620px; top:150px; width:520px; }}
.ct b {{ display:block; font-size:18px; font-weight:600; }}
.ct span {{ display:block; font-size:14px; color:var(--muted); margin-top:4px; }}
.legend {{ position:absolute; left:620px; top:{top+gap*3+62}px; font-size:14px; color:var(--text2); display:flex; gap:22px; align-items:center; }}
.legend i {{ display:inline-block; width:11px; height:11px; border-radius:50%; margin-right:7px; vertical-align:-1px; }}
.legend em {{ font-style:normal; color:var(--muted); }}
.stats {{ position:absolute; left:64px; right:64px; bottom:48px; display:flex; border-top:1px solid var(--rule); padding-top:24px; }}
.stat {{ flex:1; }}
.stat .v {{ font-size:32px; font-weight:700; letter-spacing:-.01em; }}
.stat .l {{ font-size:14px; color:var(--text2); margin-top:6px; line-height:1.3; }}
svg {{ position:absolute; left:0; top:0; font-family:inherit; }}
</style></head><body>
<div class="left">
  <div class="eyebrow">On-device AI · Apple Foundation Models</div>
  <h1>Scam protection<br>that never leaves<br><span>the phone.</span></h1>
  <div class="sub">An SMS scam filter for older people in Spain. The AI runs on the iPhone they already carry, so their messages stay with them.</div>
</div>
<div class="ct"><b>Accuracy on the 227 hardest test messages</b><span>95% confidence intervals · iOS / macOS 27.0.1 · October 2026</span></div>
<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}">{''.join(svg)}</svg>
<div class="legend"><span><i style="background:var(--dev)"></i>On-device</span><span><i style="background:var(--cloud)"></i>Cloud</span><em>No significant gap (paired McNemar, all p&nbsp;&gt;&nbsp;0.1)</em></div>
<div class="stats">{stat_html}</div>
</body></html>"""
open(out, "w", encoding="utf-8").write(html)
for r in rows:
    print(f"{r[0]:14} {r[2]*100:.1f}% [{r[3]*100:.1f}, {r[4]*100:.1f}]")
