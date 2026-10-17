// ProbeApp.swift — runs the same pipeline as the Mac CLI on an iPhone or iPad.
// Production config only (permissive guardrails, greedy sampling, PHASE=1).
// Prints one "PRED" line per message so the output can be diffed against the Mac predictions,
// plus metrics and resource use: latency, cold start, peak memory (phys_footprint), CPU time,
// thermal state and battery. Runs in the app process, not inside an SMS filter extension.
// Note: the model itself runs in a system daemon, so its weights are NOT in this app's memory.

import SwiftUI
import FoundationModels

struct Sample: Codable { let id: String; let label: String; let text: String }

func physFootprintMB() -> Double {
    var info = task_vm_info_data_t()
    var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<natural_t>.size)
    let kr = withUnsafeMutablePointer(to: &info) {
        $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
            task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count)
        }
    }
    return kr == KERN_SUCCESS ? Double(info.phys_footprint) / 1024 / 1024 : -1
}

/// CPU time (user + system) used by this process so far, in seconds.
func cpuSeconds() -> Double {
    var u = rusage(); getrusage(RUSAGE_SELF, &u)
    return Double(u.ru_utime.tv_sec + u.ru_stime.tv_sec) + Double(u.ru_utime.tv_usec + u.ru_stime.tv_usec) / 1e6
}

func thermal() -> String {
    switch ProcessInfo.processInfo.thermalState {
    case .nominal: return "nominal"; case .fair: return "fair"
    case .serious: return "serious"; case .critical: return "critical"
    @unknown default: return "unknown"
    }
}

func loadDataset(_ name: String) -> [Sample] {
    guard let url = Bundle.main.url(forResource: name, withExtension: "jsonl"),
          let raw = try? String(contentsOf: url, encoding: .utf8) else { return [] }
    return raw.split(separator: "\n").compactMap { try? JSONDecoder().decode(Sample.self, from: Data($0.utf8)) }
}

func pctl(_ xs: [Double], _ p: Double) -> Double {
    guard !xs.isEmpty else { return 0 }
    let s = xs.sorted(); return s[Int((Double(s.count - 1) * p).rounded())]
}

@MainActor
final class Runner: ObservableObject {
    @Published var lines: [String] = []
    func log(_ s: String) { print(s); lines.append(s) }

    let model = makeModel()
    let options = makeOptions()

    func run(_ name: String) async {
        let data = loadDataset(name)
        guard !data.isEmpty else { log("(\(name) not found in bundle)"); return }
        log("=== \(name) (n=\(data.count)) ===")
        var TP = 0, FP = 0, TN = 0, FN = 0, errors = 0, unparsed = 0
        var lat: [Double] = []; var peak = 0.0
        let cpu0 = cpuSeconds(), wall0 = Date()
        for s in data {
            let t0 = Date()
            let o = await classify(s.text, model: model, options: options)
            let ms = Date().timeIntervalSince(t0) * 1000
            if o.source == "model" { lat.append(ms) }
            if o.error != nil { errors += 1 }
            if o.unparsed { unparsed += 1; print("UNPARSED\t\(name)\t\(s.id)\t\(o.raw ?? "")") }
            peak = max(peak, physFootprintMB())
            print("PRED\t\(name)\t\(s.id)\t\(o.pred)\t\(o.source)\t\(Int(ms))")
            let predScam = o.pred == "JUNK", isScam = s.label == "scam"
            if isScam && predScam { TP += 1 } else if !isScam && predScam { FP += 1 }
            else if !isScam && !predScam { TN += 1 } else { FN += 1 }
        }
        let acc = Double(TP + TN) / Double(data.count)
        let recall = TP + FN > 0 ? Double(TP) / Double(TP + FN) : 0
        let fpr = FP + TN > 0 ? Double(FP) / Double(FP + TN) : 0
        log(String(format: "acc=%.1f%% recall=%.1f%% FPR=%.1f%% (TP=%d FN=%d FP=%d TN=%d) errors=%d unparsed=%d",
                   acc * 100, recall * 100, fpr * 100, TP, FN, FP, TN, errors, unparsed))
        let wall = Date().timeIntervalSince(wall0), cpu = cpuSeconds() - cpu0
        log(String(format: "model latency p50=%.0f ms p95=%.0f ms max=%.0f ms (n=%d model calls)",
                   pctl(lat, 0.5), pctl(lat, 0.95), lat.max() ?? 0, lat.count))
        log(String(format: "RESOURCES %@: peak phys_footprint=%.1f MB | wall=%.1f s | app CPU=%.2f s (%.0f ms/msg) | thermal=%@",
                   name, peak, wall, cpu, cpu * 1000 / Double(data.count), thermal()))
    }

    func runAll() async {
        log("=== FMSmishingProbe — \(UIDevice.current.model) iOS \(UIDevice.current.systemVersion) ===")
        guard case .available = model.availability else { log("Foundation Models not available ❌"); return }
        let v = model.variant
        log("MODEL: variant=\(v == .coreAdvanced3 ? "coreAdvanced3" : (v == .core3 ? "core3" : "other")) contextSize=\(model.contextSize)")
        if CommandLine.arguments.contains("info") { log("=== DONE (info) ==="); return }
        UIDevice.current.isBatteryMonitoringEnabled = true
        let bat0 = UIDevice.current.batteryLevel
        log(String(format: "START: phys_footprint=%.1f MB | thermal=%@ | battery=%.0f%% | lowPower=%@",
                   physFootprintMB(), thermal(), bat0 * 100, ProcessInfo.processInfo.isLowPowerModeEnabled ? "on" : "off"))
        // Cold start: first model call in this process (the system daemon may or may not be warm).
        let t0 = Date()
        _ = await classify("Quiero confirmar la reunion de manana por la tarde.", model: model, options: options)
        let cold = Date().timeIntervalSince(t0) * 1000
        let t1 = Date()
        _ = await classify("Te dejo el informe encima de la mesa.", model: model, options: options)
        log(String(format: "COLD first model call=%.0f ms | next call (new session)=%.0f ms | phys_footprint=%.1f MB",
                   cold, Date().timeIntervalSince(t1) * 1000, physFootprintMB()))
        let start = Date()
        for name in ["es_curated", "es_adversarial", "es_adversarial_v2"] { await run(name) }
        log(String(format: "END: total wall=%.0f s | phys_footprint=%.1f MB | thermal=%@ | battery %.0f%% → %.0f%%",
                   Date().timeIntervalSince(start), physFootprintMB(), thermal(), bat0 * 100, UIDevice.current.batteryLevel * 100))
        log("=== DONE ===")
    }
}

@main
struct FMSmishingProbeApp: App {
    @StateObject var runner = Runner()
    var body: some Scene {
        WindowGroup {
            ScrollView {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(Array(runner.lines.enumerated()), id: \.offset) { _, l in
                        Text(l).font(.system(.caption2, design: .monospaced)).textSelection(.enabled)
                    }
                }.padding()
            }
            .task { await runner.runAll() }
        }
    }
}
