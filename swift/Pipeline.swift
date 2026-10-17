// Pipeline.swift — the classifier shared by the Mac CLI (main.swift) and the iOS probe app.
//   Phase 0. genuine one-time code → ALLOW (rule, no model)
//   Phase 1. strong scam signal    → JUNK  (rule, no model)
//   Model.   Foundation Models + prompt v3 → JUNK / ALLOW

import FoundationModels
import Foundation

// Prompt v3. Kept in Spanish on purpose: this is the exact prompt that was measured.
let instructions = """
Eres un detector de fraude (smishing) que protege a personas mayores en España. Clasifica el MENSAJE.

Marca JUNK si hay CUALQUIERA de estas señales de estafa:
- Urgencia o amenaza: cuenta bloqueada/suspendida, multa, corte de suministro, "ultimo aviso", plazos de horas.
- Enlace sospechoso: bit.ly, dominios que imitan marcas, o una URL escrita como palabras (ej. "correos punto com").
- Pide datos sensibles: PIN, contrasena, DNI, IBAN, numero o digitos de tarjeta.
- RELAY DE CODIGO: te da un codigo y te pide compartirlo, dictarlo, introducirlo en una llamada, o dice \
que "un tecnico/agente/asesor se pondra en contacto" o "recibira una llamada". Un codigo legitimo NUNCA se \
comparte ni se usa en una llamada entrante. Si el codigo viene con una historia (reembolso, premio, revertir un cargo), es ESTAFA.
- LLAMADA DE VUELTA: te pide llamar a un numero de telefono para resolver un problema, deuda, virus o cargo.
- Premios, loterias, herencias o devoluciones inesperadas.
- Peticion de dinero por Bizum, transferencia o Western Union, o una donacion por SMS.
- Alguien que dice ser un familiar desde un numero NUEVO, o que "te escribe/llamara desde otro numero", pidiendo dinero, un pago o que contestes una llamada.
- Texto ofuscado a proposito: letras espaciadas, faltas raras, caracteres extranos que imitan una marca.

Marca ALLOW solo si es claramente legitimo:
- Un codigo de un solo uso que solo informa del codigo (aunque diga "no lo compartas"), SIN pedir compartirlo ni anunciar una llamada.
- Un aviso informativo de cargo, entrega, cita o factura que NO pide accion urgente ni datos y remite a la app/canal oficial.
- Un mensaje personal de un familiar SIN peticion de dinero ni cambio de numero.
- Una alerta de seguridad de un banco que dice "no tiene que hacer nada".

Responde SOLO una palabra: JUNK o ALLOW.
"""

// CRITICAL: with .default guardrails the model throws guardrailViolation on the most dangerous
// attacks ("your son is in trouble", investment, impersonation). Because the filter fails open,
// those scams pass silently. .permissiveContentTransformations is Apple's mode for apps that
// legitimately process potentially unsafe content.
func makeModel(guardrails: String = "permissive") -> SystemLanguageModel {
    guardrails == "default"
        ? SystemLanguageModel(guardrails: .default)
        : SystemLanguageModel(guardrails: .permissiveContentTransformations)
}

/// greedy = deterministic: the same message always gets the same verdict.
func makeOptions(sampling: String = "greedy") -> GenerationOptions {
    sampling == "random" ? GenerationOptions() : GenerationOptions(samplingMode: .greedy)
}

/// Narrow, deterministic allowlist for genuine one-time codes (avoids blocking the bank's 2FA).
/// Hardened against "code relay" attacks: any scam context means "don't auto-allow, let the
/// model decide". The rule never fails towards auto-ALLOW.
/// "no lo compartas" (legit) does not exclude; "compártala con el operador" (scam) does.
func isLegitOTP(_ text: String) -> Bool {
    let l = text.lowercased()
    if text.count > 120 { return false }                                   // a real OTP is short
    let urlMarks = ["http://","https://","www.","bit.ly",".com",".net",".info",".top",".online",".xyz","[.]"," punto "]
    if urlMarks.contains(where: { l.contains($0) }) { return false }
    let otpWords = ["codigo","código","code","pin","clave","otp","cl@ve"]
    guard otpWords.contains(where: { l.contains($0) }) else { return false }
    guard text.range(of: "\\b\\d{4,8}\\b", options: .regularExpression) != nil else { return false }
    let scamCtx = [
        // relaying / sharing the code
        "compartal","compartel","comparta con","comparta unicamente","comparta solo",
        "facilit","proporcion","reenvi","dicte","digale","dígale",
        // relay over a phone call / fake agent
        "operador","asesor","agente","tecnico","técnico","departamento",
        "se pondra","se pondrá","contacto","contactar","contactaremos","le llama","nos llama",
        "nuestra llamada","llamada de","recibira una llamada","cuando le llamemos","le atiende","atendiendo",
        // story elements that never appear in a real OTP
        "reembolso","premio","autoriz","revertir","fraude","gestionar","cobro","cargo",
        // requests for data
        "responda con","introduzca","introduzcalo","introdúzcalo","numero de cuenta","número de cuenta",
        "iban","dni","tarjeta","transferencia"
    ]
    if scamCtx.contains(where: { l.contains($0) }) { return false }
    return true
}

struct Outcome {
    let pred: String          // "JUNK" or "ALLOW"
    let source: String        // "otp", "strong" or "model"
    let medium: [String]      // medium signals (used for the two-level action)
    let error: Error?         // model error; the pipeline then fails open (ALLOW)
    var raw: String? = nil    // the model's raw answer
    /// The model answered, but with neither JUNK nor ALLOW (for example a refusal message,
    /// which permissive guardrails return as text instead of throwing). Treated as ALLOW.
    var unparsed: Bool = false
}

/// One NEW session per message, no shared history (like an SMS filter extension).
func classify(_ text: String, model: SystemLanguageModel, options: GenerationOptions, phase: Int = 1) async -> Outcome {
    let sig = extractSignals(text)
    if isLegitOTP(text) { return Outcome(pred: "ALLOW", source: "otp", medium: sig.medium, error: nil) }
    if phase >= 1 && !sig.strong.isEmpty { return Outcome(pred: "JUNK", source: "strong", medium: sig.medium, error: nil) }
    let aug = (phase >= 2 && !sig.medium.isEmpty) ? "\nSeñales automáticas a considerar: \(sig.medium.joined(separator: "; "))." : ""
    let session = LanguageModelSession(model: model, instructions: instructions)
    do {
        let out = try await session.respond(to: "MENSAJE: \"\(text)\"\(aug)", options: options)
        let raw = out.content.trimmingCharacters(in: .whitespacesAndNewlines)
        let upper = raw.uppercased()
        let junk = upper.contains("JUNK")
        return Outcome(pred: junk ? "JUNK" : "ALLOW", source: "model", medium: sig.medium, error: nil,
                       raw: raw, unparsed: !junk && !upper.contains("ALLOW"))
    } catch {
        return Outcome(pred: "ALLOW", source: "model", medium: sig.medium, error: error)   // fail open
    }
}
