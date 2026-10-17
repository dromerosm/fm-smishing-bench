#!/bin/bash
# Build and run the on-device benchmark. Needs macOS 26+, Apple Silicon and Apple Intelligence turned on.
# Optional toggles (see swift/main.swift): PHASE, GUARDRAILS, SAMPLING.
set -e
cd "$(dirname "$0")"
SDK="$(xcrun --sdk macosx --show-sdk-path)"
mkdir -p build results

echo "== Building =="
swiftc -O -sdk "$SDK" -target arm64-apple-macos27.0 swift/DetectionEngine.swift swift/Pipeline.swift swift/main.swift -o build/bench

for ds in es_curated es_adversarial es_adversarial_v2; do
  echo "== $ds =="
  ./build/bench "data/$ds.jsonl" "results/$ds" "${1:-}"
done

echo "== Done. Results in results/ =="
