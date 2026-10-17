// DetectionEngine.swift — Phase 1 (deterministic signals) + support for Phase 2 (signals in the prompt).
// High precision: "strong" signals only fire on hard evidence that does not show up in legitimate
// messages, so they can mark JUNK without calling the model. "Medium" signals only feed the model.
// Signal labels and patterns are in Spanish because the dataset is Spanish SMS.

import Foundation

struct Signals { var strong: [String] = []; var medium: [String] = [] }

private let officialDomains: Set<String> = [
    "bbva.es","santander.es","caixabank.es","caixabank.com","ing.es","bancosabadell.com","abanca.com",
    "unicajabanco.es","kutxabank.es","correos.es","seur.com","dhl.com","gls-spain.es","ups.com",
    "amazon.es","amazon.com","netflix.com","apple.com","icloud.com","microsoft.com","paypal.com",
    "iberdrola.es","endesa.com","naturgy.es","movistar.es","vodafone.es","zara.com",
    "agenciatributaria.gob.es","seg-social.es","segsocial.es","dgt.es","aemet.es","once.es","cruzroja.es"
]
private let brands = ["bbva","santander","caixabank","caixa","sabadell","abanca","unicaja","kutxabank",
    "correos","seur","dhl","gls","amazon","netflix","appleid","apple","icloud","microsoft","paypal",
    "iberdrola","endesa","naturgy","movistar","vodafone","hacienda","tributaria","bizum","mercadona",
    "instagram","whatsapp","prime","corteingles"]
private let suspiciousTLD = [".top",".info",".online",".xyz",".link",".click",".live",".icu",".vip",".rest"]

private func rx(_ text: String, _ pattern: String) -> Bool {
    text.range(of: pattern, options: [.regularExpression, .caseInsensitive]) != nil
}

/// Extract host names like `fake-brand.info` from the text.
private func hosts(in lower: String) -> [String] {
    guard let re = try? NSRegularExpression(pattern: "([a-z0-9\\-]+\\.)+[a-z]{2,24}") else { return [] }
    let ns = lower as NSString
    return re.matches(in: lower, range: NSRange(location: 0, length: ns.length)).map {
        ns.substring(with: $0.range).trimmingCharacters(in: CharacterSet(charactersIn: "."))
    }
}

/// Detect letters outside the Spanish alphabet (Cyrillic/Greek homoglyphs that imitate brands).
private func hasHomoglyph(_ text: String) -> Bool {
    let allowed = Set("abcdefghijklmnopqrstuvwxyzáéíóúüñçABCDEFGHIJKLMNOPQRSTUVWXYZÁÉÍÓÚÜÑÇ")
    for ch in text where ch.isLetter && !allowed.contains(ch) { return true }
    return false
}

/// Detect spaced-out text like "B B V A" or "V e r i f i q u e".
private func hasLetterSpacing(_ text: String) -> Bool {
    let tokens = text.split(separator: " ")
    var run = 0
    for t in tokens {
        if t.count == 1 && t.first!.isLetter { run += 1; if run >= 4 { return true } } else { run = 0 }
    }
    return false
}

func extractSignals(_ text: String) -> Signals {
    let l = text.lowercased()
    var s = Signals()

    // ---- STRONG signals (hard evidence -> deterministic JUNK) ----

    // 1. Domain that imitates a brand / URL shortener / obfuscated URL
    for h in hosts(in: l) {
        if officialDomains.contains(where: { h == $0 || h.hasSuffix("." + $0) }) { continue }
        if brands.contains(where: { h.contains($0) }) { s.strong.append("dominio imita marca (\(h))"); break }
    }
    if rx(l, "\\b(bit\\.ly|tinyurl|t\\.co|goo\\.gl|acortar|cutt\\.ly)\\b") { s.strong.append("URL acortada") }
    if l.contains("[.]") || l.contains("hxxp") || rx(l, "\\w+ punto (com|es|net|info|org)") { s.strong.append("URL ofuscada") }

    // 2. Visual obfuscation
    if hasHomoglyph(text) { s.strong.append("homóglifos (caracteres no latinos)") }
    if hasLetterSpacing(text) { s.strong.append("texto con letras espaciadas") }

    // 3. Premium-rate number (80X/90X), typical of call-back scams
    if rx(l, "\\b(80[3-9]|90[5-9])[\\s.]?\\d{2}[\\s.]?\\d{2}[\\s.]?\\d{2}\\b") { s.strong.append("teléfono premium (80X/90X)") }

    // 4. Fake tech support + contact request
    if (l.contains("virus") || l.contains("microsoft") || l.contains("windows") || l.contains("soporte tecnico"))
        && rx(l, "(llame|llamar|contacte|contactar|soporte|numero)") { s.strong.append("soporte técnico falso") }

    // 5. Asks for sensitive data by reply
    if rx(l, "(responda|respondiendo|confirme|facilite|introduzca|envie)") &&
        rx(l, "(dni|iban|numero de cuenta|número de cuenta|contrasena|contraseña|\\bpin\\b|digitos de|dígitos de|numero de tarjeta|número de tarjeta|numero de afiliacion)") {
        s.strong.append("pide datos sensibles por respuesta")
    }

    // 5b. Code relay: a code + a request to share/dictate it, or "an agent/technician will contact you".
    //     Unambiguous markers (a real OTP never uses them). "autorizar"/"reembolso" are left out on
    //     purpose, because they also show up in real 2FA ("código para autorizar la transferencia").
    if rx(l, "\\b(codigo|código|clave|pin|otp)\\b") && rx(text, "\\b\\d{4,8}\\b")
        && rx(l, "(compartal|compartel|comparta con|comparta unicamente|facilit|proporcion|dicte|reenvi|operador|asesor|\\bagente\\b|se pondra|se pondrá|contactaremos|recibira una llamada|recibirá una llamada|nuestra llamada|cuando le llamemos)") {
        s.strong.append("relay de código (compartir/agente)")
    }

    // 6. Money request with impersonation / urgency / Western Union
    let impersonation = rx(l, "(nuevo numero|nuevo número|otro numero|otro número|este es mi numero|este es mi número|cambie de numero|cambié de número|se me (rompio|rompió|ha roto) (el )?(movil|móvil|telefono|teléfono|pantalla))")
    let moneyMove = rx(l, "(bizum|transferencia|western union|ingreso|mandes|manda|envia|envía|hazme|necesito).{0,30}(€|euros|\\d{2,})") || l.contains("western union")
    if moneyMove && (impersonation || l.contains("western union") || rx(l, "(urgent|urge|es importante|ya mismo|ahora mismo)")) {
        s.strong.append("dinero con suplantación/urgencia")
    }

    // 7. "New number" that asks you to answer a call (cloned-voice setup)
    if impersonation && rx(l, "(contesta|responde|coge|cuando (te|le) llame|cuando llame|contestame|llamada)") {
        s.strong.append("número nuevo + pide contestar llamada")
    }

    // ---- MEDIUM signals (uncertain -> only feed the model, never auto-JUNK) ----

    if rx(l, "(premio|loteria|lotería|sorteo|has ganado|ha ganado|herencia|beneficiario|premiado)") { s.medium.append("premio/lotería/herencia") }
    if rx(l, "(\\d{2,}\\s?€.{0,15}(al dia|al día|dia|día|semana)|gana.{0,15}€|trabaj[ae].{0,20}(casa|movil|móvil))") { s.medium.append("ingresos fáciles/empleo") }
    if rx(l, "(escane[ae].{0,15}(qr|codigo qr|código qr))") { s.medium.append("pago por QR") }
    if rx(l, "(me equivoque de numero|me equivoqué de número|nos conocimos|que conociste|inviertes en|invertir en cripto)") { s.medium.append("apertura de romance/inversión") }
    if suspiciousTLD.contains(where: { l.contains($0) }) && s.strong.isEmpty { s.medium.append("TLD sospechoso") }
    if rx(l, "(llame|llamar|contacte|contactar) (urgente )?al? ?\\d") && rx(l, "(deuda|multa|corte|cargo|bloqueo|sancion|sanción|virus|impago)") { s.medium.append("llamada de vuelta ante amenaza") }

    return s
}
