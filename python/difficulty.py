#!/usr/bin/env python3
"""
Is the adversarial set actually harder? Compares each on-device run's error rate on
es_curated (110) vs es_adversarial_v2 (227), with a one-sided Fisher exact test
(H1: more errors on the adversarial set). Also reports the error rate by the
per-message `difficulty` label and how often the model is needed (rules can't decide).

Usage: python3 python/difficulty.py
"""
import csv, json, os
from collections import Counter, defaultdict
from math import comb

ROOT = os.path.join(os.path.dirname(__file__), "..")
R = os.path.join(ROOT, "results", "reference")
SETS = ["es_curated", "es_adversarial_v2"]


def load(name):
    with open(os.path.join(ROOT, "data", f"{name}.jsonl"), encoding="utf-8") as f:
        return {r["id"]: r for r in map(json.loads, filter(str.strip, f))}


def from_csv(path):
    with open(os.path.join(R, path), encoding="utf-8") as f:
        return {r["id"]: r["pred"] for r in csv.DictReader(f)}


def from_log(path, ds):
    out = {}
    with open(os.path.join(R, path), encoding="utf-8") as f:
        for line in f:
            x = line.rstrip("\n").split("\t")
            if x[0] == "PRED" and x[1] == ds:
                out[x[2]] = (x[3], x[4])
    return out


def fisher_more_errors(e1, n1, e2, n2):
    """P(errors in set 1 >= e1 | total errors), hypergeometric. Set 1 = adversarial."""
    k, N = e1 + e2, n1 + n2
    return sum(comb(n1, x) * comb(n2, k - x) for x in range(e1, min(n1, k) + 1)) / comb(N, k)


data = {s: load(s) for s in SETS}
iphone_log = "2026-10_ios27.0.1_iphone15pro/run1_console.log"
runs = {
    "Apple FM, Mac, macOS 26.5": {"es_curated": from_csv("2026-07_macos26.5/es_predictions.csv"),
                                  "es_adversarial_v2": from_csv("2026-07_macos26.5/adv_v2_applefm_predictions.csv")},
    "Apple FM, Mac, macOS 27.0.1": {s: from_csv(f"2026-10_macos27.0.1/{s}_predictions.csv") for s in SETS},
    "Apple FM, iPhone, iOS 27.0.1": {s: {k: v[0] for k, v in from_log(iphone_log, s).items()} for s in SETS},
}
wrong = lambda pred, row: (pred == "JUNK") != (row["label"] == "scam")

print("Error rate, es_curated vs es_adversarial_v2 (one-sided Fisher exact test)")
for name, preds in runs.items():
    e = {s: sum(wrong(preds[s][i], r) for i, r in data[s].items()) for s in SETS}
    n = {s: len(data[s]) for s in SETS}
    p = fisher_more_errors(e["es_adversarial_v2"], n["es_adversarial_v2"], e["es_curated"], n["es_curated"])
    print(f"  {name:30} curated {e['es_curated']}/{n['es_curated']} = {e['es_curated']/n['es_curated']*100:.1f}%"
          f"   adversarial {e['es_adversarial_v2']}/{n['es_adversarial_v2']} = {e['es_adversarial_v2']/n['es_adversarial_v2']*100:.1f}%"
          f"   p = {p:.3f}")

print("\nError rate by the `difficulty` label (all messages, pooled over the runs above)")
agg, msgs = defaultdict(lambda: [0, 0]), Counter()
for preds in runs.values():
    for s in SETS:
        for i, r in data[s].items():
            agg[r["difficulty"]][0] += wrong(preds[s][i], r)
            agg[r["difficulty"]][1] += 1
for s in SETS:
    msgs.update(r["difficulty"] for r in data[s].values())
for d, (e, n) in sorted(agg.items()):
    print(f"  {d:9} {msgs[d]:4} messages   {e}/{n} = {e/n*100:.1f}%")

print("\nShare of messages the rules can't decide (they go to the model), iPhone run")
for s in SETS:
    c = Counter(v[1] for v in from_log(iphone_log, s).values())
    print(f"  {s:18} {c['model']}/{sum(c.values())} = {c['model']/sum(c.values())*100:.0f}%")
