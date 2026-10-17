#!/usr/bin/env python3
"""
Confidence intervals and paired significance tests for the benchmark results.

For each run: accuracy, false positive rate and false negative rate with 95% Wilson intervals.
For each pair of runs: exact two-sided McNemar test on the messages where they disagree
(all messages, and legitimate messages only).

Usage:
  python3 python/stats.py <dataset.jsonl> name=path [name=path ...] [--ids ids.txt]

Each path is a *_predictions.csv written by swift/main.swift or python/groq_bench.py
(columns id, pred), or a console log from the iPhone app (PRED lines).
--ids limits the analysis to the message ids listed in a file (one per line).

Example:
  python3 python/stats.py data/es_adversarial_v2.jsonl \\
    mac27=results/reference/2026-10_macos27.0.1/es_adversarial_v2_predictions.csv \\
    g20=results/reference/2026-10_groq/adv_v2_g20_predictions.csv
"""
import csv, json, math, os, sys
from itertools import combinations
from math import comb


def wilson(k, n, z=1.96):
    if n == 0:
        return "n/a"
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return f"{k}/{n} = {p*100:.1f}% [{max(0, c-h)*100:.1f}, {min(1, c+h)*100:.1f}]"


def mcnemar_exact(b, c):
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n)


def load_preds(path, dataset_name):
    if path.endswith(".csv"):
        with open(path, encoding="utf-8") as f:
            return {r["id"]: r["pred"] for r in csv.DictReader(f)}
    preds = {}
    with open(path, encoding="utf-8") as f:   # iPhone console log
        for line in f:
            p = line.rstrip("\n").split("\t")
            if p[0] == "PRED" and p[1] == dataset_name:
                preds[p[2]] = p[3]
    return preds


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__); sys.exit(2)
    ids_filter = None
    if "--ids" in args:
        i = args.index("--ids")
        with open(args[i + 1], encoding="utf-8") as f:
            ids_filter = {l.strip() for l in f if l.strip()}
        del args[i:i + 2]
    dataset = args[0]
    name = os.path.splitext(os.path.basename(dataset))[0]
    with open(dataset, encoding="utf-8") as f:
        labels = {r["id"]: r["label"] for r in map(json.loads, filter(str.strip, f))}
    if ids_filter is not None:
        labels = {k: v for k, v in labels.items() if k in ids_filter}
    runs = {}
    for a in args[1:]:
        key, path = a.split("=", 1)
        p = load_preds(path, name)
        missing = [i for i in labels if i not in p]
        if missing:
            print(f"ERROR: {key} has no prediction for {len(missing)} messages (e.g. {missing[0]})"); sys.exit(1)
        runs[key] = p

    legit = [i for i in labels if labels[i] == "legit"]
    scam = [i for i in labels if labels[i] == "scam"]
    ok = lambda p, i: (p[i] == "JUNK") == (labels[i] == "scam")

    print(f"{name}: {len(labels)} messages ({len(scam)} scam, {len(legit)} legit). 95% Wilson intervals.\n")
    for k, p in runs.items():
        fp = sum(p[i] == "JUNK" for i in legit)
        fn = sum(p[i] != "JUNK" for i in scam)
        print(f"{k}\n  accuracy  {wilson(len(labels) - fp - fn, len(labels))}\n"
              f"  FPR       {wilson(fp, len(legit))}\n  FNR       {wilson(fn, len(scam))}")
    if len(runs) > 1:
        print("\nPaired exact McNemar test (b = only A right, c = only B right):")
        for a, b in combinations(runs, 2):
            A, B = runs[a], runs[b]
            x = sum(ok(A, i) and not ok(B, i) for i in labels); y = sum(ok(B, i) and not ok(A, i) for i in labels)
            xl = sum(ok(A, i) and not ok(B, i) for i in legit); yl = sum(ok(B, i) and not ok(A, i) for i in legit)
            print(f"  {a} vs {b}: all b={x} c={y} p={mcnemar_exact(x, y):.3f} | legit only b={xl} c={yl} p={mcnemar_exact(xl, yl):.3f}")


if __name__ == "__main__":
    main()
