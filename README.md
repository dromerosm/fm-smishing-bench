# fm-smishing-bench

A small, reproducible benchmark for detecting SMS scams (smishing) with **Apple's on-device Foundation Models**, compared with an open-weight model in the cloud (`gpt-oss` on Groq).

It comes from building an SMS filter that protects older people in Spain. You get the datasets, the exact prompt, the rule layer that runs before the model, a Mac command-line harness, a small iPhone app that runs the same pipeline, and the results we measured.

Current target: **macOS 27 / iOS 27**. Results from macOS 26.5 are kept for comparison, because the model changed between the two versions.

## What's in the repo

| Path | What it is |
|---|---|
| `data/es_curated.jsonl` | 110 Spanish SMS (55 scam / 55 legitimate). Bank, parcel, tax office, prizes, utilities, "your son is in trouble", plus hard legitimate cases: 2FA codes, delivery notices, family messages. |
| `data/es_adversarial.jsonl` | 65 Spanish SMS (40 / 25) written to get past the defences: homoglyphs, spaced letters, URLs written as words, code-relay attacks, call-back scams, romance, pig-butchering. |
| `data/es_adversarial_v2.jsonl` | 227 Spanish SMS (123 / 104). The adversarial set extended, with many more hard legitimate messages so the false positive rate has some resolution. |
| `data/uci_sms_spam.jsonl` | 300 English SMS (150 / 150), a balanced sample of the [UCI SMS Spam Collection](https://archive.ics.uci.edu/dataset/228/sms+spam+collection). Used only as a generalisation check. |
| `swift/Pipeline.swift` | The classifier: rule layer + Foundation Models + prompt v3. Shared by the Mac CLI and the iPhone app. |
| `swift/DetectionEngine.swift` | Deterministic "strong" and "medium" scam signals. |
| `swift/main.swift` | Mac CLI: runs a dataset, prints a report, writes CSV and JSON. |
| `ios/` | iPhone/iPad app that runs the same pipeline and measures resources. |
| `python/groq_bench.py` | Same pipeline, but the model layer calls Groq instead of Foundation Models. See [Cloud comparison](#cloud-comparison-groq). |
| `results/reference/` | Our predictions, metrics and logs, one folder per OS version and device, plus the Groq runs. |

Each JSONL line looks like this:

```json
{"id": "adv-s-001", "lang": "es", "label": "scam", "category": "banco", "difficulty": "frontier", "technique": "homoglyph", "text": "..."}
```

## Requirements

- A Mac with Apple Silicon, macOS 27 or later, and Apple Intelligence turned on
- Xcode 27 or later (for `swiftc` and the FoundationModels SDK)
- For the iPhone app: a device that supports Apple Intelligence (iPhone 15 Pro or later), iOS 27, [XcodeGen](https://github.com/yonaskolb/XcodeGen), and an Apple ID signed in to Xcode
- Optional: Python 3.9+ and a free Groq API key for the cloud comparison (steps below)

## Run it on a Mac

```bash
./run.sh
```

This builds `build/bench` and runs the three Spanish datasets with the production config. Results go to `results/<dataset>_metrics.json` and `results/<dataset>_predictions.csv`, and a report is printed (confusion matrix, false positive rate, latency, which layer decided, and every error).

To run a single dataset:

```bash
./build/bench data/es_adversarial.jsonl results/adv
```

### Try the settings that matter

The harness reads three environment variables. The defaults are the production config.

| Variable | Values | What it shows |
|---|---|---|
| `GUARDRAILS` | `permissive` (default), `default` | On macOS 26.5, `default` made the model throw `guardrailViolation` on some of the most dangerous scams, and because the filter fails open, they got through. On macOS 27.0.1 we saw no guardrail errors or refusals with either setting on these datasets. The report counts both. |
| `SAMPLING` | `greedy` (default), `random` | With `random`, the same message can get a different verdict on each run. |
| `PHASE` | `0`, `1` (default), `2` | `0` = OTP rule + model. `1` = also block on strong signals without calling the model. `2` = also inject medium signals into the prompt (we measured this as worse). |

```bash
GUARDRAILS=default ./build/bench data/es_adversarial_v2.jsonl results/adv_v2_default_guardrails
SAMPLING=random    ./build/bench data/es_adversarial.jsonl results/adv_random
PHASE=0            ./build/bench data/es_adversarial.jsonl results/adv_phase0
```

## Run it on an iPhone

```bash
cd ios
xcodegen generate
xcodebuild -project FMSmishingProbe.xcodeproj -scheme FMSmishingProbe -configuration Release \
  -destination 'id=<DEVICE_UDID>' -allowProvisioningUpdates -derivedDataPath ../build/ios \
  DEVELOPMENT_TEAM=<YOUR_TEAM_ID> PRODUCT_BUNDLE_IDENTIFIER=<your.bundle.id> build
xcrun devicectl device install app --device <DEVICE_UDID> ../build/ios/Build/Products/Release-iphoneos/FMSmishingProbe.app
xcrun devicectl device process launch --console --device <DEVICE_UDID> <your.bundle.id>
```

The app runs the three Spanish sets with the production config. It prints one `PRED` line per message, so you can diff the output against the Mac predictions. It also reports resource use:

- cold start: the first model call in a fresh process
- model latency per message (p50, p95, max)
- peak `phys_footprint` of the app process (the metric iOS uses to kill apps and extensions)
- CPU time used by the app process
- thermal state and battery level at the start and the end

The model runs in a system daemon, outside the app process, so the app's memory does not include the model weights. The app runs in the normal app process, not inside an `ILMessageFilterExtension`.

## Cloud comparison (Groq)

The script `python/groq_bench.py` runs the same pipeline, but the model layer calls an open-weight model on [Groq](https://groq.com) instead of Foundation Models. It needs only the Python standard library.

### Get a free Groq API key

1. Go to [console.groq.com](https://console.groq.com) and sign in (Google, GitHub or email). The free tier does not ask for a card.
2. Open **API Keys** in the menu ([console.groq.com/keys](https://console.groq.com/keys)) and click **Create API Key**.
3. Give it a name, for example `fm-smishing-bench`, and copy the key. It starts with `gsk_`. Groq shows it only once.
4. Keep it out of the repo. Put it in an environment variable in your shell:

```bash
export GROQ_API_KEY=gsk_...
```

If you want to store it in a file, use `.env` (it is in `.gitignore`) and load it with `export $(grep GROQ_API_KEY .env)`.

### Run it

```bash
# optional: one call to check the key and see your real rate limits
python3 python/groq_bench.py --probe openai/gpt-oss-20b

# full run on the 227-message set
python3 python/groq_bench.py data/es_adversarial_v2.jsonl openai/gpt-oss-20b  results/adv_v2_g20
python3 python/groq_bench.py data/es_adversarial_v2.jsonl openai/gpt-oss-120b results/adv_v2_g120
```

The rule layer is ported from Swift to Python, and only the model changes. The script uses `temperature 0` and one request per message. 167 of the 227 messages reach the model, at about 590 tokens per call (about 100k tokens per run).

On the free tier the binding limit is tokens per minute (8,000 when we ran it), not requests. The script spaces out calls (`GROQ_MIN_INTERVAL_S`, default 8 s), honours `retry-after` on HTTP 429 and stops if the daily limit runs out. With the default interval one run takes about 20 minutes; `GROQ_MIN_INTERVAL_S=5` is still safe at 8,000 TPM. Each model has its own limits, so you can run both models at the same time.

## How the classifier works

```
SMS ──► Phase 0: genuine one-time code?  ──yes──► ALLOW   (rule, no model)
         │no
         ▼
        Phase 1: strong scam signal?     ──yes──► BLOCK   (rule, no model)
         │no
         ▼
        Foundation Models (prompt v3)    ──JUNK─► WARN    (never a silent block)
                                         ──ALLOW► ALLOW   (or WARN if a medium signal fired)
```

- **Guardrails.** `SystemLanguageModel(guardrails: .permissiveContentTransformations)`. Apple's docs describe this mode for apps that process potentially unsafe content for a legitimate reason.
- **Sampling.** `GenerationOptions(samplingMode: .greedy)`, so the same message gets the same answer.
- **One session per message.** No shared history, the same as an SMS filter extension.
- **Prompt v3** is in Spanish (the messages are Spanish). It lists concrete fraud signals instead of asking the model to "be careful". It is in `swift/Pipeline.swift`.
- **Two-level action.** The rule layer made no errors in any of our runs, and every error came from the model. So the layer that decided acts as the confidence level. A rule can block silently. The model can only warn.

## Results

### October 2026: macOS 27.0.1 (Mac)

| Dataset | Accuracy | Recall | False positive rate | Legit blocked silently |
|---|---|---|---|---|
| `es_curated` (110) | 99.1% | 100.0% | 1.8% | 0 |
| `es_adversarial` (65) | 93.8% | 97.5% | 12.0% | 0 |
| `es_adversarial_v2` (227) | 95.2% | 96.7% | 6.7% | 0 |

- Two runs gave identical predictions. No guardrail errors and no refusals, with either guardrail setting.
- Model latency on the Mac: p50 about 300 ms, p95 about 345 ms per message.
- On the 227-message set, 7 legitimate messages were flagged: three 2FA codes, three family Bizum requests and one store pickup notice. All seven become warnings, not silent blocks.

### October 2026: iOS 27.0.1 (iPhone 15 Pro)

Two full runs with the iPhone app, production config. Both runs gave identical predictions.

| Dataset | Accuracy | Recall | False positive rate | Legit blocked silently |
|---|---|---|---|---|
| `es_curated` (110) | 98.2% | 100.0% | 3.6% | 0 |
| `es_adversarial` (65) | 89.2% | 95.0% | 20.0% | 0 |
| `es_adversarial_v2` (227) | 93.8% | 95.9% | 8.7% | 0 |

**The iPhone does not match the Mac on 27.0.1.** It is deterministic on its own, but 3, 3 and 9 messages get a different verdict than on the Mac with the same OS version and the same code. In July (26.5), the two matched exactly. So on 27 you need to measure on the device you ship to, not only on the Mac. All iPhone false positives came from the model layer, so all of them become warnings, not silent blocks.

Resource use on the iPhone 15 Pro (app process):

| | Run 1 | Run 2 (phone already warm) |
|---|---|---|
| First model call in a fresh process | 3,659 ms | 1,733 ms |
| Model latency per message, p50 | 751 to 978 ms | 1,034 to 1,111 ms |
| Model latency per message, p95 | 792 to 1,056 ms | 1,172 to 1,432 ms |
| Worst single call | 1,203 ms | 34,642 ms |
| Peak app memory (`phys_footprint`) | 15.0 MB | 15.1 MB |
| App CPU time | 7 to 9 ms per message | 8 to 9 ms per message |
| Thermal state at the end | fair | serious |
| Battery for the whole run (402 messages, screen on) | 75% → 70% | 70% → 65% |

- **Memory is not the problem.** The app stays around 15 MB, far below the roughly 120 MB an iOS extension gets. The model weights live in a system daemon and are not counted here.
- **Latency is about 2x what we saw on iOS 26.5** (about 385 ms then), and it grows as the phone heats up. Sustained back-to-back calls pushed the phone to "serious" thermal state, and one call took 34 s. Real SMS traffic is one message at a time, so this is a worst case, but it is worth knowing.
- **Battery:** about 5% for 402 messages in 4 to 6 minutes with the screen on. That is roughly 0.01% per message, an upper bound because it includes the screen.
- A third run (phone already hot, on a cable and charging) gave the same predictions again. Model latency p50 was 1.17 to 1.25 s.

**The model daemon.** The model runs outside the app, so we recorded a system-wide Instruments trace (Activity Monitor template, 480 s) during the third run. It covers 281 model calls plus 2 warm-up calls. The processes that work for the model:

| Process | Role (our reading) | CPU time | Avg CPU | Peak memory footprint | Peak real memory |
|---|---|---|---|---|---|
| `TGOnDeviceInferenceProviderService` | runs the model | 107 s | 15% | 455 MB | 125 MB |
| `aned` | Apple Neural Engine daemon | 84 s | 23% | 8 MB | 38 MB |
| `modelmanagerd` | model lifecycle | 22 s | 6% | 9 MB | 22 MB |
| `modelcatalogd` | model catalog | 22 s | 6% | 6 MB | 12 MB |
| `GenerativeExperiencesSafetyInference` | safety checks | 14 s | 4% | 6 MB | 10 MB |

- **Memory:** the model stack needs about 455 MB of footprint on the phone, compared with about 15 MB in our app. This memory is system memory, shared by every app that uses the model, and it does not count against the app or extension limit.
- **CPU:** about 0.38 s of CPU time in the inference service per model call, and about 0.9 s if you add up the whole model stack. Treat these as upper bounds: they also include whatever these daemons did in the background during the 480 s trace. The Neural Engine and GPU work itself is not CPU time and is not in these numbers.
- Spotify was playing during the trace. It does not touch these processes, but it adds load to the phone.

### July 2026: macOS 26.5 / iOS 26.5

Measured on a Mac M4. On an iPhone 15 Pro, the 110 and 65 sets gave exactly the same confusion matrices.

| Dataset | Accuracy | Recall | False positive rate |
|---|---|---|---|
| `es_curated` (110) | 100.0% | 100.0% | 0.0% |
| `es_adversarial` (65) | 90.8% | 90.0% | 8.0% |
| `es_adversarial_v2` (227) | 93.8% | 91.9% | 3.8% |
| `uci_sms_spam` (300, English) | 78.3% | 70.0% | 13.3% |

On the iPhone 15 Pro (iOS 26.5, app process), peak memory was about 14 MB and a message took about 385 ms with the system model already loaded. The first load of the model took about 2.4 s.

### What changed between 26.5 and 27

The rule layer decided exactly the same messages in both versions and on both devices. Only the model's verdicts moved. On the 227-message set the 27 model catches more scams (10 missed before, 4 now) but flags more legitimate messages (4 before, 7 now), including more 2FA codes. Results are deterministic within one OS version on one device, but not across versions, and on 27 not even between Mac and iPhone. If you ship this, treat every OS update as a new model and run your regression set again on the target device.

### Cloud comparison (October 2026, `es_adversarial_v2`)

Same pipeline and same rule layer; only the model changes. 167 of the 227 messages reach the model.

| Model layer | Accuracy | F1 | False positive rate | False negative rate |
|---|---|---|---|---|
| Apple Foundation Models, iPhone 15 Pro (iOS 27.0.1) | 93.8% | 0.944 | 8.7% | 4.1% |
| Apple Foundation Models, Mac (macOS 27.0.1) | 95.2% | 0.956 | 6.7% | 3.3% |
| `openai/gpt-oss-20b` on Groq | 96.5% | 0.967 | 2.9% | 4.1% |
| `openai/gpt-oss-120b` on Groq | 96.5% | 0.967 | 3.8% | 3.3% |

- The Groq numbers are the same as in July (same metrics), but 2 to 4 messages changed verdict between the two runs. `temperature 0` on a hosted API is close to deterministic, not fully.
- The models catch about the same share of scams. The difference is in false positives: on iOS 27 the on-device model flags 9 legitimate messages, `gpt-oss-20b` flags 3. Two of them are family Bizum requests that even a person could doubt; both Groq models flag those.
- In July, on 26.5, Apple FM had a 3.8% false positive rate, the same as `gpt-oss-120b`. The gap opened with the 27 model.
- The trade-off is not only accuracy. On-device means no server, no cost, it works offline, and message text never leaves the phone. A cloud model needs consent, a privacy label that declares the data, GDPR handling of other people's messages, and a running endpoint.

### What we tried that did not help (macOS 26.5)

These variants were measured during development. They are not toggles in this repo.

- **Default guardrails + a simple "when in doubt, ALLOW" prompt.** Recall was 45.5% on `es_curated`, with 8 to 10 guardrail errors. The first prompt was not kept, so this number cannot be reproduced exactly.
- **Structured output (`@Generable`) with chain-of-thought.** On `es_adversarial`, recall went to 97.5% but the false positive rate went to 64%.
- **The model's own confidence score (0 to 100).** Badly calibrated. It said "80 or more" for 42 of 44 messages and was still wrong 14% of the time.
- **Medium signals injected into the prompt (`PHASE=2`).** Recall dropped from 90% to 77% on `es_adversarial`.

## Limits

- **The Spanish datasets are curated, not real victim traffic.** They are written from publicly documented smishing campaigns in Spain. They are good for comparing configs and catching regressions, not for claiming real-world coverage.
- **Some scams leave no trace in the text.** Romance, pig-butchering, "call us back" and "an agent will review your case" have no URL and no code. A text classifier can't catch them reliably. They need other layers, such as DNS filtering, number reputation and user education.
- **The prompt is tuned for Spain.** The English UCI set does worse, as expected.
- **Not tested inside a real `ILMessageFilterExtension`.** The iPhone numbers come from the app process.
- **One device.** The iOS 27 numbers come from a single iPhone 15 Pro.

## License

Code: MIT (see `LICENSE`). Spanish datasets: CC BY 4.0. The UCI SMS Spam Collection sample is CC BY 4.0, by Tiago A. Almeida and José María Gómez Hidalgo.
