#!/usr/bin/env python3
"""
Metriche decisionali della dashboard.

Qui vivono i numeri che fanno prendere decisioni:

  Indice operativo (0–100) =
      0,30·Metodo + 0,25·Affidabilità + 0,25·Strumenti + 0,20·Copertura
  - Metodo       = % sessioni operative dentro flussi codificati (chat escluse)
  - Affidabilità = la "prima stesura buona" (first-pass yield): % di consegne
                   uscite giuste al primo colpo, senza interventi successivi.
                   Senza consegne valutabili nella finestra l'ingrediente manca
                   e vale la formula a tre: 0,40·Metodo + 0,30·Strumenti +
                   0,30·Copertura (la nota della tile lo dichiara)
  - Strumenti    = 100 − penalità SOLO su ciò che si usa (le auth scadute su
                   servizi mai usati NON pesano: sono "decisioni in sospeso")
  - Copertura    = % clienti con attività entro la loro cadenza attesa
                   (default thresholds.freddo_giorni, override in cadenza_clienti)

  L'indice è un semaforo (verde ≥80 · giallo 60–79 · rosso <60), non una
  misura di precisione: i pesi sono una scelta progettuale dichiarata, e la
  nota della tile mostra sempre gli ingredienti.

Più: le misure della card Metodo (vivaio, automazioni ferme, banco di prova,
livello di delega, coda investimenti), il proxy dei consumi (peso per consegna)
e lo storico su file (storico.json nell'output_dir) con backfill settimanale
della Quota di metodo ricavato dai diari ancora conservati.

Le soglie e il nome del progetto-hub arrivano da config.C.
"""

import json
from datetime import datetime, timedelta

import config
import flows

CUR_SYMBOL = {"EUR": "€", "GBP": "£", "JPY": "¥", "CHF": "CHF"}


# --------------------------------------------------------------------- storico

def load_storico():
    try:
        return json.loads(config.C.storico_file.read_text(encoding="utf-8"))
    except Exception:
        return {"days": []}


def append_storico(entry):
    st = load_storico()
    st["days"] = [d for d in st["days"] if d["d"] != entry["d"]]
    st["days"].append(entry)
    st["days"].sort(key=lambda x: x["d"])
    st["days"] = st["days"][-400:]
    config.C.storico_file.write_text(
        json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    return st


# --------------------------------------------------------------------- helper

def _fam_key(chain):
    return flows._family_key(chain or [])


def _pct(num, den):
    return round(100 * num / den) if den else None


def _delta(cur, prev, invert=False):
    """Pillola di variazione: dir 'good'/'bad'/'flat' + testo. invert=True se
    scendere è un bene (es. peso per consegna, clienti da verificare)."""
    if prev is None or cur is None:
        return None
    diff = round(cur - prev, 1)
    if abs(diff) < 0.5:
        return {"text": "stabile", "dir": "flat"}
    good = (diff < 0) if invert else (diff > 0)
    sign = "+" if diff > 0 else "−"
    return {"text": f"{sign}{abs(diff):g} vs scan precedente",
            "dir": "good" if good else "bad"}


def _tok(n):
    """Numero di token in forma compatta (1.2M, 46k)."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{round(n / 1000)}k"
    return str(n)


# --------------------------------------------------------------------- compute

def compute(chains, projects, skills, use_skill_map, riparare, orch_names,
            agent_candidates, orch_candidates, consegne_files=None, telemetry=None,
            rate=None, rate_date=""):
    """Calcola metriche, aggiorna lo storico e prepara le serie per il grafico.

    chains: sessioni della finestra, ognuna con chain/skills/mcp/web/outs/size/
    mtime più i campi di attrito (user_msgs, rev_rounds, data_imports, t0/t1).
    consegne_files: registro file consegnati (da scan.scan_consegne).
    telemetry: costi e token reali dalla sonda locale (da scan.scan_telemetry).
    rate/rate_date: cambio USD→valuta di vetrina (BCE); la fonte dà solo dollari.
    """
    th = config.C.th
    now = datetime.now()
    oggi = now.strftime("%Y-%m-%d")
    consegne_files = consegne_files or []

    # --- classificazione sessioni (+ contatori di attrito)
    operative, con_metodo, con_orch = 0, 0, 0
    peso_kb, consegne = 0.0, 0
    libero_outs = {}
    libero_files = {}                    # (proj, ext) -> [(epoch, relpath)] per gli esempi del Vivaio
    dati_auto, dati_tot = 0, 0           # autonomia dati: MCP vs import manuale
    giri_tot, giri_sess = 0, 0           # giri di revisione nelle sessioni con consegna
    sanguisughe = []                     # pesanti, tanti giri, zero consegne
    flussi_proj = {}                     # famiglia -> progetti con consegne (riuso)
    for s in chains:
        key = _fam_key(s.get("chain"))
        if key == "_chat":
            continue
        operative += 1
        peso_kb += s.get("size", 0) / 1024
        consegne += len(s.get("outs") or [])
        if key != "_libero":
            con_metodo += 1
            for o in s.get("outs") or []:
                flussi_proj.setdefault(key, set()).add(o[0])
        else:
            for o in s.get("outs") or []:
                name = (o[2] or "").rsplit("/", 1)[-1]
                if name == "CLAUDE.md":
                    continue         # config di progetto, non una consegna
                k = (o[0], o[1])
                libero_outs[k] = libero_outs.get(k, 0) + 1
                libero_files.setdefault(k, []).append((o[3] or 0, o[2]))
        if any(ev[6:] in orch_names for ev in s.get("chain") or []
               if ev.startswith("skill:")):
            con_orch += 1
        has_mcp = any(k not in flows.SKIP_MCP for k in (s.get("mcp") or {}))
        has_man = s.get("data_imports", 0) > 0
        if has_mcp or has_man:
            dati_tot += 1
            if has_mcp and not has_man:
                dati_auto += 1
        if s.get("outs"):
            giri_sess += 1
            giri_tot += s.get("rev_rounds", 0)
        elif (s.get("size", 0) > th["sanguisuga_kb"] * 1000
              and s.get("rev_rounds", 0) >= th["sanguisuga_giri"]):
            sanguisughe.append({
                "label": flows.flow_name(key),
                "date": datetime.fromtimestamp(s["mtime"]).strftime("%d/%m"),
                "giri": s.get("rev_rounds", 0), "size": s.get("size", 0)})
    sanguisughe.sort(key=lambda x: -x["size"])
    for x in sanguisughe:
        x.pop("size", None)

    metodo = _pct(con_metodo, operative)
    delega = _pct(con_orch, operative)
    peso_consegna = round(peso_kb / consegne) if consegne else None

    # --- attrito sulle consegne (registro file)
    autonomia = _pct(dati_auto, dati_tot)
    giri = round(giri_tot / giri_sess, 1) if giri_sess else None
    # prima stesura buona = il file non è più stato riscritto dopo un messaggio
    # dell'utente (in chat), né ripreso in un'altra sessione entro la soglia,
    # né corretto a mano. Le modifiche su copie cloud restano invisibili.
    chat_corrette = sum(1 for f in consegne_files if f.get("chat"))
    riprese = sum(1 for f in consegne_files if f.get("rilavorata"))
    corrette_mano = sum(1 for f in consegne_files if f.get("manuale"))
    buone = sum(1 for f in consegne_files
                if not f.get("chat") and not f.get("rilavorata")
                and not f.get("manuale"))
    conformita = _pct(buone, len(consegne_files))
    riviste_note = [f for f in consegne_files if f.get("rivista") is not None]
    riviste = sum(1 for f in riviste_note if f["rivista"])

    # consegne per settimana (per file, non per salvataggio)
    weekly_out = {}
    for f in consegne_files:
        day = datetime.fromtimestamp(f["first_ts"] or now.timestamp())
        monday = (day - timedelta(days=day.weekday())).strftime("%Y-%m-%d")
        weekly_out[monday] = weekly_out.get(monday, 0) + 1
    this_monday = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
    week_now = weekly_out.get(this_monday, 0)
    prima = sum(v for k, v in weekly_out.items() if k != this_monday)
    media_sett = round(prima / 4, 1) if prima else None

    # --- costo reale per consegna (telemetria locale, se la sonda ha dati)
    costo, n_tel = None, 0
    if telemetry:
        n_tel = sum(1 for f in consegne_files
                    if (f.get("first_ts") or 0) >= telemetry["first_ts"])
        if n_tel:
            costo = round(telemetry["cost"] / n_tel, 2)

    attrito = {
        "autonomia": {"pct": autonomia, "auto": dati_auto, "tot": dati_tot},
        "giri": {"avg": giri, "sessioni": giri_sess},
        "conformita": {"pct": conformita, "ok": buone, "tot": len(consegne_files),
                       "chat": chat_corrette, "riprese": riprese,
                       "manuali": corrette_mano},
        "riviste": {"n": riviste, "tot": len(riviste_note)},
        "consegne": {"week": week_now, "media": media_sett,
                     "tot": len(consegne_files)},
        "sanguisughe": sanguisughe[:3],
    }

    # --- copertura clienti (il progetto-hub non è un cliente): ogni cliente
    #     ha la sua cadenza attesa (cadenza_clienti), default freddo_giorni
    freddo_gg = th["freddo_giorni"]
    cadenze = config.C.cadenza_clienti
    clienti = [p for p in projects if p["name"] != config.C.hub]
    freddi = [p["name"] for p in clienti
              if p.get("days_since") is None
              or p["days_since"] > cadenze.get(p["name"], freddo_gg)]
    copertura = _pct(len(clienti) - len(freddi), len(clienti))

    # --- strumenti: 100 − penalità degli alert "da riparare"
    strumenti = max(0, 100 - sum(a.get("peso", 0) for a in riparare))

    salute, band = None, None
    salute_note = "ingredienti non ancora misurabili (servono sessioni e clienti)"
    if metodo is not None and copertura is not None:
        # l'Affidabilità (prima stesura buona) entra solo se c'è almeno una
        # consegna valutabile; altrimenti vale la formula a tre ingredienti
        if conformita is not None:
            salute = round(0.30 * metodo + 0.25 * conformita
                           + 0.25 * strumenti + 0.20 * copertura)
            salute_note = (f"0,30·Metodo {metodo} + 0,25·Affidabilità {conformita} "
                           f"+ 0,25·Strumenti {strumenti} + 0,20·Copertura {copertura}")
        else:
            salute = round(0.40 * metodo + 0.30 * strumenti + 0.30 * copertura)
            salute_note = (f"0,40·Metodo {metodo} + 0,30·Strumenti {strumenti} "
                           f"+ 0,30·Copertura {copertura} · senza consegne "
                           f"valutabili l'Affidabilità non entra")
        band = ({"cls": "good", "text": "verde: in salute"} if salute >= 80
                else {"cls": "warn", "text": "giallo: da tenere d'occhio"}
                if salute >= 60 else {"cls": "bad", "text": "rosso: serve un intervento"})

    # --- card Metodo
    def _esempi(k, n=3):
        # i file più recenti del gruppo (nomi unici): dicono QUALE lavoro era
        seen, out = set(), []
        for _, rel in sorted(libero_files.get(k, []), key=lambda x: -x[0]):
            name = rel.rsplit("/", 1)[-1]
            if name not in seen:
                seen.add(name)
                out.append(name)
            if len(out) == n:
                break
        return out

    vivaio = [{"label": f"{proj} · consegne {ext}", "count": c,
               "files": _esempi((proj, ext))}
              for (proj, ext), c in sorted(libero_outs.items(), key=lambda kv: -kv[1])
              if c >= th["soglia_vivaio"]]

    ferme, banco = [], []
    for sk in skills:
        usi = use_skill_map.get(sk["name"], 0)
        created = sk.get("created_ts")
        eta = (now.timestamp() - created) / 86400 if created else None
        if eta is not None and eta <= th["banco_giorni"]:
            banco.append({"name": sk["name"], "giorni": max(0, round(eta)), "usi": usi})
        if usi == 0 and (eta is None or eta > th["grace_skill_giorni"]):
            ferme.append(sk["name"])
    banco.sort(key=lambda b: b["giorni"])

    # riuso tra progetti: i flussi codificati che hanno consegnato per 2+
    # progetti diversi — il metodo che è diventato capitale, non one-shot
    trasversali = sorted(
        ({"label": flows.flow_name(k), "projects": len(v)}
         for k, v in flussi_proj.items() if len(v) >= 2),
        key=lambda x: -x["projects"])

    metodo_card = {
        "quota": metodo, "operative": operative, "con_metodo": con_metodo,
        "vivaio": vivaio[:5],
        "ferme": ferme,
        "banco": banco[:6],
        "delega": delega,
        "trasversali": {"n": len(trasversali), "tot": len(flussi_proj),
                        "flows": trasversali[:5]},
        "coda": {"orchestratori": len(orch_candidates),
                 "agenti": len(agent_candidates)},
    }

    # --- storico: delta vs ultimo scan, poi aggiungo oggi
    st = load_storico()
    prev = st["days"][-1] if st["days"] else None
    cur = config.C.currency
    symbol = CUR_SYMBOL.get(cur, cur)
    if costo is not None:
        # la sonda telemetria dà i costi veri: la tile proxy lascia il posto.
        # Lo storico salva sempre i DOLLARI (unità della fonte); la valuta di
        # vetrina usa il cambio BCE del giorno, applicato anche al valore passato.
        t = telemetry["tok"]
        tok_note = f"{_tok(t['in'])} in · {_tok(t['out'])} out · {_tok(t['cache'])} cache"
        conseg = f"{n_tel} {'consegna' if n_tel == 1 else 'consegne'}"
        prev_c = prev and prev.get("costo")
        if cur != "USD" and rate:
            costo_tile = {
                "id": "peso", "label": "Costo per consegna",
                "value": round(costo * rate, 2), "unit": f" {symbol}",
                "delta": _delta(round(costo * rate, 2),
                                prev_c and round(prev_c * rate, 2), invert=True),
                "note": f"{telemetry['cost']:.2f} $ ≈ "
                        f"{telemetry['cost'] * rate:.2f} {symbol} (cambio BCE "
                        f"{rate_date}) per {conseg} dal {telemetry['first']} · "
                        f"token: {tok_note}"}
        else:
            costo_tile = {
                "id": "peso", "label": "Costo per consegna", "value": costo,
                "unit": " $", "delta": _delta(costo, prev_c, invert=True),
                "note": f"{telemetry['cost']:.2f} $ di modello per {conseg} "
                        f"dal {telemetry['first']} · token: {tok_note}"}
    else:
        nota = "proxy dei consumi: diario ÷ consegne"
        nota += (" · telemetria attiva: il costo reale arriva con le prime consegne"
                 if telemetry else " (i token veri non sono nei diari)")
        costo_tile = {
            "id": "peso", "label": "Peso per consegna", "value": peso_consegna,
            "unit": " KB", "delta": _delta(peso_consegna, prev and prev.get("peso"),
                                           invert=True),
            "note": nota}
    tiles = [
        {"id": "salute", "label": "Indice operativo", "value": salute,
         "unit": "/100", "band": band,
         "delta": _delta(salute, prev and prev.get("salute")),
         "note": salute_note},
        {"id": "metodo", "label": "Quota di metodo", "value": metodo, "unit": "%",
         "delta": _delta(metodo, prev and prev.get("metodo")),
         "note": f"{con_metodo} sessioni con flusso su {operative} operative"},
        {"id": "freddi", "label": "Clienti da verificare", "value": len(freddi), "unit": "",
         "delta": _delta(len(freddi), prev and prev.get("freddi"), invert=True),
         "note": ", ".join(freddi) if freddi
                 else ("tutti entro la loro cadenza attesa" if cadenze
                       else f"tutti attivi ≤{freddo_gg}gg")},
        costo_tile,
        {"id": "riparare", "label": "Da riparare", "value": len(riparare), "unit": "",
         "delta": _delta(len(riparare), prev and prev.get("riparare"), invert=True),
         "note": "alert che pesano sull'indice"},
    ]

    entry = {"d": oggi, "salute": salute, "metodo": metodo, "strumenti": strumenti,
             "copertura": copertura, "freddi": len(freddi), "peso": peso_consegna,
             "riparare": len(riparare), "sessioni": operative,
             "costo": costo, "autonomia": autonomia, "giri": giri,
             "conformita": conformita, "consegne30": len(consegne_files)}
    st = append_storico(entry)

    # --- serie per il grafico: backfill settimanale del Metodo dai diari vivi,
    #     poi i punti giornalieri dello storico (che da oggi si accumula)
    weekly = {}
    for s in chains:
        key = _fam_key(s.get("chain"))
        if key == "_chat" or not s.get("mtime"):
            continue
        day = datetime.fromtimestamp(s["mtime"])
        monday = (day - timedelta(days=day.weekday())).strftime("%Y-%m-%d")
        w = weekly.setdefault(monday, [0, 0])
        w[0] += 1
        if key != "_libero":
            w[1] += 1
    first_hist = st["days"][0]["d"] if st["days"] else oggi
    points = []
    for monday, (op, met) in sorted(weekly.items()):
        sunday = (datetime.strptime(monday, "%Y-%m-%d")
                  + timedelta(days=6)).strftime("%Y-%m-%d")
        if sunday < first_hist and op >= 3:
            points.append({"d": sunday, "metodo": _pct(met, op), "backfill": True})
    for d in st["days"]:
        points.append({"d": d["d"], "salute": d.get("salute"),
                       "metodo": d.get("metodo"), "copertura": d.get("copertura")})

    return {"tiles": tiles, "metodo_card": metodo_card, "attrito": attrito,
            "trend": {"points": points,
                      "series": [
                          {"key": "salute", "label": "Indice", "color": "acc"},
                          {"key": "metodo", "label": "Metodo %", "color": "blu"},
                          {"key": "copertura", "label": "Copertura clienti %", "color": "aqua"},
                      ]},
            "salute": salute, "strumenti": strumenti, "copertura": copertura}
