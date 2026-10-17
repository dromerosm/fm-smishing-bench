#!/usr/bin/env python3
"""
Exact replica of the on-device benchmark (swift/main.swift) that swaps ONLY the model layer:
Apple Foundation Models -> Groq (e.g. openai/gpt-oss-20b, openai/gpt-oss-120b).

Same pipeline (PHASE=1):
  Phase 0. is_legit_otp     -> ALLOW  (deterministic, ported from main.swift)
  Phase 1. strong signal    -> JUNK   (deterministic, ported from DetectionEngine.swift)
  Model.   Groq             -> JUNK/ALLOW  (prompt v3, temperature 0 = greedy)

Rate-limit safe: throttles call starts, honours retry-after on 429, counts tokens.

Usage:
  # 1) probe: one call, prints the real x-ratelimit-* headers and tokens per call
  GROQ_API_KEY=... python3 groq_bench.py --probe <model>
  # 2) full run
  GROQ_API_KEY=... python3 groq_bench.py <dataset.jsonl> <model> <prefix> [limit]

Optional tuning:
  GROQ_MIN_INTERVAL_S   minimum seconds between call starts (default 8.0, about 7.5 RPM)
  GROQ_WORKERS          concurrent threads (default 2)
"""
import sys, os, re, json, time, threading, urllib.request, urllib.error

# ---------- Prompt v3 (verbatim copy of main.swift; kept in Spanish on purpose) ----------
INSTRUCTIONS = """Eres un detector de fraude (smishing) que protege a personas mayores en España. Clasifica el MENSAJE.

Marca JUNK si hay CUALQUIERA de estas señales de estafa:
- Urgencia o amenaza: cuenta bloqueada/suspendida, multa, corte de suministro, "ultimo aviso", plazos de horas.
- Enlace sospechoso: bit.ly, dominios que imitan marcas, o una URL escrita como palabras (ej. "correos punto com").
- Pide datos sensibles: PIN, contrasena, DNI, IBAN, numero o digitos de tarjeta.
- RELAY DE CODIGO: te da un codigo y te pide compartirlo, dictarlo, introducirlo en una llamada, o dice que "un tecnico/agente/asesor se pondra en contacto" o "recibira una llamada". Un codigo legitimo NUNCA se comparte ni se usa en una llamada entrante. Si el codigo viene con una historia (reembolso, premio, revertir un cargo), es ESTAFA.
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

Responde SOLO una palabra: JUNK o ALLOW."""

# ---------- Phase 0: isLegitOTP (ported from main.swift) ----------
def is_legit_otp(text: str) -> bool:
    l = text.lower()
    if len(text) > 120:
        return False
    url_marks = ["http://","https://","www.","bit.ly",".com",".net",".info",".top",".online",".xyz","[.]"," punto "]
    if any(m in l for m in url_marks):
        return False
    otp_words = ["codigo","código","code","pin","clave","otp","cl@ve"]
    if not any(w in l for w in otp_words):
        return False
    if not re.search(r"\b\d{4,8}\b", text):
        return False
    scam_ctx = [
        "compartal","compartel","comparta con","comparta unicamente","comparta solo",
        "facilit","proporcion","reenvi","dicte","digale","dígale",
        "operador","asesor","agente","tecnico","técnico","departamento",
        "se pondra","se pondrá","contacto","contactar","contactaremos","le llama","nos llama",
        "nuestra llamada","llamada de","recibira una llamada","cuando le llamemos","le atiende","atendiendo",
        "reembolso","premio","autoriz","revertir","fraude","gestionar","cobro","cargo",
        "responda con","introduzca","introduzcalo","introdúzcalo","numero de cuenta","número de cuenta",
        "iban","dni","tarjeta","transferencia",
    ]
    if any(c in l for c in scam_ctx):
        return False
    return True

# ---------- Phase 1: extractSignals.strong (ported from DetectionEngine.swift) ----------
OFFICIAL_DOMAINS = {
    "bbva.es","santander.es","caixabank.es","caixabank.com","ing.es","bancosabadell.com","abanca.com",
    "unicajabanco.es","kutxabank.es","correos.es","seur.com","dhl.com","gls-spain.es","ups.com",
    "amazon.es","amazon.com","netflix.com","apple.com","icloud.com","microsoft.com","paypal.com",
    "iberdrola.es","endesa.com","naturgy.es","movistar.es","vodafone.es","zara.com",
    "agenciatributaria.gob.es","seg-social.es","segsocial.es","dgt.es","aemet.es","once.es","cruzroja.es",
}
BRANDS = ["bbva","santander","caixabank","caixa","sabadell","abanca","unicaja","kutxabank",
    "correos","seur","dhl","gls","amazon","netflix","appleid","apple","icloud","microsoft","paypal",
    "iberdrola","endesa","naturgy","movistar","vodafone","hacienda","tributaria","bizum","mercadona",
    "instagram","whatsapp","prime","corteingles"]

def rx(text: str, pattern: str) -> bool:
    return re.search(pattern, text, re.IGNORECASE) is not None

def hosts(lower: str):
    return [m.group(0).strip(".") for m in re.finditer(r"([a-z0-9\-]+\.)+[a-z]{2,24}", lower)]

def has_homoglyph(text: str) -> bool:
    allowed = set("abcdefghijklmnopqrstuvwxyzáéíóúüñçABCDEFGHIJKLMNOPQRSTUVWXYZÁÉÍÓÚÜÑÇ")
    for ch in text:
        if ch.isalpha() and ch not in allowed:
            return True
    return False

def has_letter_spacing(text: str) -> bool:
    run = 0
    for t in text.split(" "):
        if len(t) == 1 and t.isalpha():
            run += 1
            if run >= 4:
                return True
        else:
            run = 0
    return False

def has_strong_signal(text: str) -> bool:
    l = text.lower()
    for h in hosts(l):
        if any(h == d or h.endswith("." + d) for d in OFFICIAL_DOMAINS):
            continue
        if any(b in h for b in BRANDS):
            return True
    if rx(l, r"\b(bit\.ly|tinyurl|t\.co|goo\.gl|acortar|cutt\.ly)\b"): return True
    if "[.]" in l or "hxxp" in l or rx(l, r"\w+ punto (com|es|net|info|org)"): return True
    if has_homoglyph(text): return True
    if has_letter_spacing(text): return True
    if rx(l, r"\b(80[3-9]|90[5-9])[\s.]?\d{2}[\s.]?\d{2}[\s.]?\d{2}\b"): return True
    if (("virus" in l or "microsoft" in l or "windows" in l or "soporte tecnico" in l)
        and rx(l, r"(llame|llamar|contacte|contactar|soporte|numero)")): return True
    if (rx(l, r"(responda|respondiendo|confirme|facilite|introduzca|envie)")
        and rx(l, r"(dni|iban|numero de cuenta|número de cuenta|contrasena|contraseña|\bpin\b|digitos de|dígitos de|numero de tarjeta|número de tarjeta|numero de afiliacion)")):
        return True
    if (rx(l, r"\b(codigo|código|clave|pin|otp)\b") and rx(text, r"\b\d{4,8}\b")
        and rx(l, r"(compartal|compartel|comparta con|comparta unicamente|facilit|proporcion|dicte|reenvi|operador|asesor|\bagente\b|se pondra|se pondrá|contactaremos|recibira una llamada|recibirá una llamada|nuestra llamada|cuando le llamemos)")):
        return True
    impersonation = rx(l, r"(nuevo numero|nuevo número|otro numero|otro número|este es mi numero|este es mi número|cambie de numero|cambié de número|se me (rompio|rompió|ha roto) (el )?(movil|móvil|telefono|teléfono|pantalla))")
    money_move = rx(l, r"(bizum|transferencia|western union|ingreso|mandes|manda|envia|envía|hazme|necesito).{0,30}(€|euros|\d{2,})") or "western union" in l
    if money_move and (impersonation or "western union" in l or rx(l, r"(urgent|urge|es importante|ya mismo|ahora mismo)")):
        return True
    if impersonation and rx(l, r"(contesta|responde|coge|cuando (te|le) llame|cuando llame|contestame|llamada)"):
        return True
    return False

# ---------- Global rate limiter (RPM via min interval) + token counter ----------
class DailyBudgetError(Exception):
    """Daily budget (TPD/RPD) is used up; stop instead of sleeping for hours."""

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Groq's WAF returns 403 (error 1010) for urllib's default User-Agent. A custom UA works like curl.
UA = "fm-smishing-bench/1.0"
MIN_INTERVAL = float(os.environ.get("GROQ_MIN_INTERVAL_S", "8.0"))
_launch_lock = threading.Lock()
_last_launch = [0.0]
_usage_lock = threading.Lock()
_usage = {"prompt": 0, "completion": 0, "calls": 0}

def _throttle():
    """Space out call starts so we stay under RPM/TPM."""
    with _launch_lock:
        wait = _last_launch[0] + MIN_INTERVAL - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_launch[0] = time.time()

def groq_call(text: str, model: str, key: str, retries: int = 6):
    """Return (pred, usage_dict). Honours retry-after on 429. Raises on final failure."""
    body = json.dumps({
        "model": model,
        "temperature": 0,
        "max_completion_tokens": 4096,
        "reasoning_effort": "low",
        "messages": [
            {"role": "system", "content": INSTRUCTIONS},
            {"role": "user", "content": f'MENSAJE: "{text}"'},
        ],
    }).encode()
    last = ""
    for attempt in range(retries):
        _throttle()
        req = urllib.request.Request(GROQ_URL, data=body, headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read())
            content = (data["choices"][0]["message"].get("content") or "").strip()
            u = data.get("usage", {})
            usage = {"prompt": u.get("prompt_tokens", 0), "completion": u.get("completion_tokens", 0)}
            with _usage_lock:
                _usage["prompt"] += usage["prompt"]; _usage["completion"] += usage["completion"]; _usage["calls"] += 1
            up = content.upper()
            # neither JUNK nor ALLOW (e.g. a refusal): treated as ALLOW, but flagged
            return ("JUNK" if "JUNK" in up else "ALLOW"), usage, ("JUNK" not in up and "ALLOW" not in up)
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code == 429:
                ra = e.headers.get("retry-after")
                sleep = float(ra) + 0.5 if ra else 5 * (attempt + 1)
                if sleep > 180:
                    raise DailyBudgetError(f"retry-after={ra}s: daily limit (TPD/RPD) probably used up")
                sys.stderr.write(f"  [429] rate limit, waiting {sleep:.1f}s (retry-after={ra})\n")
                time.sleep(sleep); continue
            if e.code in (500, 502, 503):
                time.sleep(3 * (attempt + 1)); continue
            raise
        except Exception as e:  # noqa
            last = str(e); time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"groq failed: {last}")

def classify(text: str, model: str, key: str):
    """(pred, source). Fails open to ALLOW on a model error (same as on-device)."""
    if is_legit_otp(text):
        return "ALLOW", "otp"
    if has_strong_signal(text):
        return "JUNK", "strong"
    try:
        pred, _, unparsed = groq_call(text, model, key)
        return pred, ("model_unparsed" if unparsed else "model")
    except DailyBudgetError:
        raise
    except Exception:
        return "ALLOW", "model_error"

# ---------- Probe: one call, prints real x-ratelimit-* headers + tokens ----------
def probe(model: str, key: str):
    sample = 'MENSAJE: "BBVA: acceso no autorizado. Verifique en http://bbva-clientes-seguro.com"'
    body = json.dumps({
        "model": model, "temperature": 0, "max_completion_tokens": 4096, "reasoning_effort": "low",
        "messages": [{"role": "system", "content": INSTRUCTIONS}, {"role": "user", "content": sample}],
    }).encode()
    req = urllib.request.Request(GROQ_URL, data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": UA})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            hdr = {k.lower(): v for k, v in r.getheaders()}
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()[:400]}")
        print("Headers:", {k: v for k, v in e.headers.items() if k.lower().startswith("x-ratelimit") or k.lower() == "retry-after"})
        sys.exit(1)
    dt = (time.time() - t0) * 1000
    u = data.get("usage", {})
    content = (data["choices"][0]["message"].get("content") or "").strip()
    print(f"=== PROBE {model} ===")
    print(f"latency: {dt:.0f} ms   answer: {content[:40]!r}")
    print(f"tokens: prompt={u.get('prompt_tokens')}  completion={u.get('completion_tokens')}  total={u.get('total_tokens')}")
    print("x-ratelimit-* headers:")
    for k in sorted(hdr):
        if k.startswith("x-ratelimit") or k == "retry-after":
            print(f"  {k}: {hdr[k]}")
    tpm_hdr = hdr.get("x-ratelimit-limit-tokens")
    tot = u.get("total_tokens") or 0
    if tpm_hdr and tot:
        try:
            tpm = float(tpm_hdr)
            print(f"\n=> with TPM={tpm:.0f} and ~{tot} tok/call: max ~{tpm/tot:.1f} calls/min -> "
                  f"safe interval >= {max(2.1, 60.0*tot/tpm):.1f}s (GROQ_MIN_INTERVAL_S)")
        except ValueError:
            pass

# ---------- runner + metrics (same as main.swift) ----------
def run_eval(path, model, prefix, key, limit=None):
    from concurrent.futures import ThreadPoolExecutor, as_completed
    samples = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                samples.append(json.loads(line))
    if limit:
        samples = samples[:limit]

    n_model = sum(1 for s in samples if not is_legit_otp(s["text"]) and not has_strong_signal(s["text"]))
    workers = int(os.environ.get("GROQ_WORKERS", "2"))
    print(f"Dataset: {path}  ({len(samples)} msgs, {n_model} go to the model)  model={model}  "
          f"interval={MIN_INTERVAL}s  workers={workers}")
    results = [None] * len(samples)

    def work(i):
        return i, *classify(samples[i]["text"], model, key)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(work, i) for i in range(len(samples))]
        done = 0
        for fut in as_completed(futs):
            i, pred, source = fut.result()
            results[i] = (pred, source)
            done += 1
            if done % 10 == 0:
                print(f"  …{done}/{len(samples)}  (tokens used: {_usage['prompt']+_usage['completion']})")

    TP = FP = TN = FN = 0
    n_otp = n_strong = n_mdl = errors = unparsed = 0
    rows = []
    for s, (pred, source) in zip(samples, results):
        if source == "otp": n_otp += 1
        elif source == "strong": n_strong += 1
        else: n_mdl += 1
        if source == "model_error": errors += 1
        if source == "model_unparsed": unparsed += 1
        pred_scam = (pred == "JUNK"); is_scam = (s["label"] == "scam")
        if is_scam and pred_scam: TP += 1
        elif not is_scam and pred_scam: FP += 1
        elif not is_scam and not pred_scam: TN += 1
        else: FN += 1
        rows.append({**{k: s.get(k) for k in ("id","lang","label","category","difficulty")},
                     "pred": pred, "source": source, "correct": pred_scam == is_scam, "text": s["text"]})

    n = len(samples)
    acc = (TP + TN) / n if n else 0
    prec = TP / (TP + FP) if TP + FP else 0
    rec = TP / (TP + FN) if TP + FN else 0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0
    fpr = FP / (FP + TN) if FP + TN else 0
    fnr = FN / (FN + TP) if FN + TP else 0

    print(f"""
================ RESULTS: {prefix} ({model}) ================
                 pred JUNK   pred ALLOW
  real scam        TP={TP}        FN={FN}
  real legit       FP={FP}        TN={TN}

Accuracy .................. {acc*100:.1f}%
Precision (scam) .......... {prec*100:.1f}%
Recall (scams caught) ..... {rec*100:.1f}%
F1 ........................ {f1:.3f}
>>> FALSE POSITIVES (legit blocked) = {fpr*100:.1f}%  [KEY METRIC]
>>> False negatives (scam passed) .. = {fnr*100:.1f}%
Decided by: OTP rule={n_otp}  ·  strong signal={n_strong}  ·  model={n_mdl}
Answers that were neither JUNK nor ALLOW: {unparsed}
Tokens: prompt={_usage['prompt']}  completion={_usage['completion']}  total={_usage['prompt']+_usage['completion']}""")
    if errors:
        print(f"⚠️ {errors} inference errors (treated as ALLOW, fail open).")

    print("\nERRORS:")
    for r in rows:
        if not r["correct"]:
            kind = "FALSE POSITIVE" if r["label"] == "legit" else "false negative"
            print(f"  [{kind}] {r['id']} ({r['category']}/{r['difficulty']}): {r['text'][:70]}")

    metrics = {"dataset": path, "model": model, "n": n, "TP": TP, "FP": FP, "TN": TN, "FN": FN,
               "accuracy": acc, "precision": prec, "recall": rec, "f1": f1,
               "false_positive_rate": fpr, "false_negative_rate": fnr, "inference_errors": errors, "unparsed_answers": unparsed,
               "tokens_prompt": _usage["prompt"], "tokens_completion": _usage["completion"]}
    with open(f"{prefix}_metrics.json", "w") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)
    with open(f"{prefix}_predictions.csv", "w") as f:
        f.write("id,lang,label,category,difficulty,pred,source,correct,text\n")
        for r in rows:
            t = r["text"].replace('"', "'")
            f.write(f'{r["id"]},{r["lang"]},{r["label"]},{r["category"]},{r["difficulty"]},{r["pred"]},{r["source"]},{r["correct"]},"{t}"\n')
    print(f"\n✓ Wrote {prefix}_metrics.json and {prefix}_predictions.csv")

def main():
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        print("ERROR: GROQ_API_KEY is not set"); sys.exit(1)
    if len(sys.argv) >= 3 and sys.argv[1] == "--probe":
        probe(sys.argv[2], key); return
    if len(sys.argv) < 4:
        print("usage: groq_bench.py <dataset.jsonl> <model> <prefix> [limit]  |  --probe <model>"); sys.exit(2)
    path, model, prefix = sys.argv[1], sys.argv[2], sys.argv[3]
    limit = int(sys.argv[4]) if len(sys.argv) >= 5 else None
    run_eval(path, model, prefix, key, limit)

if __name__ == "__main__":
    main()
