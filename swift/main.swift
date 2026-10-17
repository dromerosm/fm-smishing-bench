// main.swift — benchmark Apple Foundation Models (on-device) on a JSONL smishing dataset.
// Usage: ./bench <dataset.jsonl> <results_prefix> [limit]
// Writes <prefix>_predictions.csv and <prefix>_metrics.json, and prints a report.
//
// One NEW LanguageModelSession per message (no shared history), the same way an SMS filter
// extension classifies each message. Positive class = "scam". For this use case the costly
// error is the FALSE POSITIVE (a legitimate message marked JUNK), so the report highlights it.
//
// Environment toggles (defaults = the production config):
//   PHASE=0|1|2                 0 = OTP rule + model · 1 = + strong signals → JUNK (default) · 2 = + signals in prompt (measured worse)
//   GUARDRAILS=permissive|default   on macOS 26.5, default guardrails threw guardrailViolation on the most dangerous scams
//   SAMPLING=greedy|random          greedy = deterministic, same message → same verdict

import FoundationModels
import Foundation

struct Sample: Codable {
    let id: String, lang: String, label: String, category: String, difficulty: String, text: String
}

let env = ProcessInfo.processInfo.environment
let phase = env["PHASE"].flatMap { Int($0) } ?? 1
let guardrailMode = env["GUARDRAILS"] ?? "permissive"
let samplingMode = env["SAMPLING"] ?? "greedy"

let model = makeModel(guardrails: guardrailMode)
let genOpts = makeOptions(sampling: samplingMode)

func percentile(_ xs: [Double], _ p: Double) -> Double {
    guard !xs.isEmpty else { return 0 }
    let s = xs.sorted(); let idx = Int((Double(s.count - 1) * p).rounded())
    return s[idx]
}

// --- args ---
let args = CommandLine.arguments
guard args.count >= 3 else { FileHandle.standardError.write("usage: bench <dataset.jsonl> <prefix> [limit]\n".data(using: .utf8)!); exit(2) }
let datasetPath = args[1], prefix = args[2]
let limit = args.count >= 4 ? Int(args[3]) : nil

// --- load ---
let raw = try String(contentsOfFile: datasetPath, encoding: .utf8)
var samples: [Sample] = []
for line in raw.split(separator: "\n") {
    guard let d = line.data(using: .utf8), let s = try? JSONDecoder().decode(Sample.self, from: d) else { continue }
    samples.append(s)
}
if let l = limit { samples = Array(samples.prefix(l)) }

guard case .available = model.availability else {
    FileHandle.standardError.write("Foundation Models is not available here (needs Apple Silicon + Apple Intelligence on).\n".data(using: .utf8)!); exit(1)
}

print("Dataset: \(datasetPath)  (\(samples.count) messages)  PHASE=\(phase) GUARDRAILS=\(guardrailMode) SAMPLING=\(samplingMode)\n")

// --- run ---
struct Row { let s: Sample; let pred: String; let ok: Bool; let ms: Double; let errored: Bool }
var rows: [Row] = []
var TP = 0, FP = 0, TN = 0, FN = 0, ruleFired = 0, detStrong = 0, modelCalls = 0, guardrailErrors = 0, refusals = 0
var latencies: [Double] = []
// Two-level action by provenance: (block/warn/allow) x (scam/legit)
var sBlock = 0, sWarn = 0, sMiss = 0, lBlock = 0, lWarn = 0, lAllow = 0

for (i, s) in samples.enumerated() {
    let t0 = Date()
    let o = await classify(s.text, model: model, options: genOpts, phase: phase)
    let pred = o.pred, source = o.source, medium = o.medium
    let errored = o.error != nil
    switch source { case "otp": ruleFired += 1; case "strong": detStrong += 1; default: modelCalls += 1 }
    if let e = o.error {
        switch e as? LanguageModelError {          // macOS/iOS 27 error type
        case .guardrailViolation?: guardrailErrors += 1
        case .refusal?:            refusals += 1
        default: break
        }
    }
    let ms = Date().timeIntervalSince(t0) * 1000
    latencies.append(ms)
    let predScam = (pred == "JUNK")
    let isScam = (s.label == "scam")
    let ok = predScam == isScam
    if isScam && predScam { TP += 1 } else if !isScam && predScam { FP += 1 }
    else if !isScam && !predScam { TN += 1 } else { FN += 1 }

    // Confidence = which layer decided, not a score the model makes up.
    //   rule layer (otp/strong) → high confidence → act silently (allow/block)
    //   model layer             → medium confidence → WARN, never block silently
    var action = "allow"
    switch source {
    case "otp":    action = "allow"
    case "strong": action = "block"
    default:       action = (pred == "JUNK" || !medium.isEmpty) ? "warn" : "allow"
    }
    if isScam {
        if action == "block" { sBlock += 1 } else if action == "warn" { sWarn += 1 } else { sMiss += 1 }
    } else {
        if action == "block" { lBlock += 1 } else if action == "warn" { lWarn += 1 } else { lAllow += 1 }
    }

    rows.append(Row(s: s, pred: pred, ok: ok, ms: ms, errored: errored))
    if (i+1) % 20 == 0 { print("  …\(i+1)/\(samples.count)") }
}

// --- metrics ---
let total = Double(samples.count)
let acc = Double(TP + TN) / total
let precision = TP + FP > 0 ? Double(TP)/Double(TP+FP) : 0
let recall = TP + FN > 0 ? Double(TP)/Double(TP+FN) : 0
let f1 = precision + recall > 0 ? 2*precision*recall/(precision+recall) : 0
let fpr = FP + TN > 0 ? Double(FP)/Double(FP+TN) : 0     // legitimate messages blocked
let fnr = FN + TP > 0 ? Double(FN)/Double(FN+TP) : 0     // scams that got through
let errs = rows.filter { $0.errored }.count

func pct(_ x: Double) -> String { String(format: "%.1f%%", x*100) }

print("""

================ RESULTS: \(prefix) ================
Confusion matrix (positive = scam):
                 pred JUNK   pred ALLOW
  real scam        TP=\(TP)        FN=\(FN)
  real legit       FP=\(FP)        TN=\(TN)

Accuracy .................. \(pct(acc))
Precision (scam) .......... \(pct(precision))
Recall (scams caught) ..... \(pct(recall))
F1 ........................ \(String(format: "%.3f", f1))
>>> FALSE POSITIVES (legit blocked) = \(pct(fpr))  [KEY METRIC]
>>> False negatives (scam passed) .. = \(pct(fnr))

Latency: first=\(String(format:"%.0f",latencies.first ?? 0))ms  p50=\(String(format:"%.0f",percentile(latencies,0.5)))ms  p95=\(String(format:"%.0f",percentile(latencies,0.95)))ms
Decided by: OTP rule=\(ruleFired)  ·  strong signal=\(detStrong)  ·  model=\(modelCalls)
Inference errors: \(errs)  (guardrailViolation: \(guardrailErrors), refusal: \(refusals))

Two-level action by provenance (block = silent · warn = alert user · allow):
  scams: BLOCK=\(sBlock)  WARN=\(sWarn)  PASSED_SILENTLY=\(sMiss)  → protected (block+warn)=\(pct(Double(sBlock+sWarn)/Double(max(1,sBlock+sWarn+sMiss))))
  legit: BLOCKED_SILENTLY(harm)=\(lBlock)  WARN=\(lWarn)  ALLOW=\(lAllow)  → harmful blocks=\(pct(Double(lBlock)/Double(max(1,lBlock+lWarn+lAllow))))
""")

func breakdown(by key: (Sample)->String) -> [String:(Int,Int)] {
    var m: [String:(Int,Int)] = [:]
    for r in rows { let k = key(r.s); var v = m[k] ?? (0,0); v.0 += 1; if r.ok { v.1 += 1 }; m[k] = v }
    return m
}
print("\nAccuracy by difficulty:")
for (k,v) in breakdown(by: {$0.difficulty}).sorted(by: {$0.key<$1.key}) { print("  \(k): \(v.1)/\(v.0) (\(pct(Double(v.1)/Double(v.0))))") }

print("\nERRORS:")
for r in rows where !r.ok {
    let kind = r.s.label == "legit" ? "FALSE POSITIVE" : "false negative"
    print("  [\(kind)] \(r.s.id) (\(r.s.category)): \(r.s.text.prefix(70))")
}

// --- write CSV ---
var csv = "id,lang,label,category,difficulty,pred,correct,ms,errored,text\n"
for r in rows {
    let t = r.s.text.replacingOccurrences(of: "\"", with: "'")
    csv += "\(r.s.id),\(r.s.lang),\(r.s.label),\(r.s.category),\(r.s.difficulty),\(r.pred),\(r.ok),\(String(format:"%.0f",r.ms)),\(r.errored),\"\(t)\"\n"
}
try csv.write(toFile: "\(prefix)_predictions.csv", atomically: true, encoding: .utf8)

// --- write metrics JSON ---
let metrics: [String: Any] = [
    "dataset": datasetPath, "n": samples.count,
    "phase": phase, "guardrails": guardrailMode, "sampling": samplingMode,
    "TP": TP, "FP": FP, "TN": TN, "FN": FN,
    "accuracy": acc, "precision": precision, "recall": recall, "f1": f1,
    "false_positive_rate": fpr, "false_negative_rate": fnr,
    "latency_cold_ms": latencies.first ?? 0,
    "latency_p50_ms": percentile(latencies,0.5), "latency_p95_ms": percentile(latencies,0.95),
    "inference_errors": errs, "guardrail_violations": guardrailErrors, "refusals": refusals,
    "harmful_blocks": lBlock, "scams_passed_silently": sMiss
]
let jd = try JSONSerialization.data(withJSONObject: metrics, options: [.prettyPrinted, .sortedKeys])
try jd.write(to: URL(fileURLWithPath: "\(prefix)_metrics.json"))
print("\n✓ Wrote \(prefix)_predictions.csv and \(prefix)_metrics.json")
