# Builds assets/hero.html from the reference results. Every number in the image comes from the repo.
# Usage: python3 assets/make_hero.py . assets/hero.html
# Then render it to PNG (1200x630 at 2x) with any headless Chromium, for example:
#   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --hide-scrollbars \
#     --force-device-scale-factor=2 --window-size=1200,630 --screenshot=assets/hero.png "file://$PWD/assets/hero.html"
import json, csv, sys, os
repo = sys.argv[1]; out = sys.argv[2]

ids = []
for d in ["es_curated", "es_adversarial_v2"]:
    ids += [json.loads(l)["id"] for l in open(f"{repo}/data/{d}.jsonl", encoding="utf-8") if l.strip()]
assert len(ids) == 337

mac = {}
for d in ["es_curated", "es_adversarial_v2"]:
    for r in csv.DictReader(open(f"{repo}/results/reference/2026-10_macos27.0.1/{d}_predictions.csv", encoding="utf-8")):
        mac[r["id"]] = r["pred"]
ip = {}
for l in open(f"{repo}/results/reference/2026-10_ios27.0.1_iphone15pro/run1_console.log", encoding="utf-8"):
    p = l.rstrip("\n").split("\t")
    if p[0] == "PRED" and p[1] in ("es_curated", "es_adversarial_v2"):
        ip[p[2]] = p[3]
diff = {i for i in ids if ip[i] != mac[i]}
assert len(diff) == 12, len(diff)

W, H = 1200, 630
cols, step = 26, 19.5
gx, gy = 640, 160
dots = []
for k, i in enumerate(ids):
    x = gx + (k % cols) * step; y = gy + (k // cols) * step
    if i in diff:
        dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="7.5" fill="var(--hi)" stroke="var(--surface)" stroke-width="2"/>')
    else:
        dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5.5" fill="var(--rest)"/>')
grid_bottom = gy + ((len(ids) - 1) // cols) * step

stats = [
    ("0", "legitimate messages<br>blocked silently"),
    ("0", "errors in the<br>rule layer"),
    ("15 MB", "app memory<br>(model: ~455 MB, shared)"),
    ("0.75–1.25 s", "per message on<br>iPhone 15 Pro"),
]
stat_html = "".join(f'<div class="stat"><div class="v">{v}</div><div class="l">{l}</div></div>' for v, l in stats)

html = f"""<!doctype html><html><head><meta charset="utf-8">
<style>
:root {{ --surface:#1a1a19; --text:#f0efec; --text2:#c3c2b7; --muted:#8f8e86; --rest:#5f5e59; --hi:#d95926; --rule:#2e2e2c; }}
* {{ margin:0; box-sizing:border-box; }}
html,body {{ width:{W}px; height:{H}px; background:var(--surface); font-family:-apple-system,"SF Pro Display","Helvetica Neue",Arial,sans-serif; color:var(--text); overflow:hidden; }}
.left {{ position:absolute; left:64px; top:64px; width:520px; }}
.eyebrow {{ font-size:15px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); font-weight:600; }}
h1 {{ font-size:54px; line-height:1.05; font-weight:700; letter-spacing:-.02em; margin-top:22px; }}
h1 span {{ color:var(--text2); font-weight:600; }}
.sub {{ font-size:20px; line-height:1.4; color:var(--text2); margin-top:22px; }}
.cap {{ position:absolute; left:{gx-8}px; top:{gy-62}px; width:520px; font-size:17px; color:var(--text2); line-height:1.35; }}
.cap b {{ color:var(--text); font-weight:600; }}
.legend {{ position:absolute; left:{gx-8}px; top:{grid_bottom+22:.0f}px; font-size:15px; color:var(--text2); display:flex; gap:26px; }}
.legend i {{ display:inline-block; border-radius:50%; margin-right:8px; vertical-align:-2px; }}
.stats {{ position:absolute; left:64px; right:64px; bottom:52px; display:flex; border-top:1px solid var(--rule); padding-top:26px; }}
.stat {{ flex:1; }}
.stat .v {{ font-size:34px; font-weight:700; letter-spacing:-.01em; }}
.stat .l {{ font-size:15px; color:var(--text2); margin-top:6px; line-height:1.3; }}
svg {{ position:absolute; left:0; top:0; }}
</style></head><body>
<div class="left">
  <div class="eyebrow">Apple Foundation Models · on-device</div>
  <h1>Same code.<br>New OS.<br><span>Different answers.</span></h1>
  <div class="sub">An SMS scam filter for older people in Spain, measured on macOS&nbsp;and&nbsp;iOS 26.5&nbsp;→&nbsp;27.</div>
</div>
<div class="cap"><b>337 test messages, one dot each.</b><br>12 get a different verdict on iPhone than on Mac, both on 27.0.1.</div>
<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}">{''.join(dots)}</svg>
<div class="legend"><span><i style="width:11px;height:11px;background:var(--rest)"></i>Same verdict</span><span><i style="width:15px;height:15px;background:var(--hi)"></i>Different verdict (12)</span></div>
<div class="stats">{stat_html}</div>
</body></html>"""
open(out, "w", encoding="utf-8").write(html)
print("diff ids:", sorted(diff))
