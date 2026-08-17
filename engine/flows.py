#!/usr/bin/env python3
"""
Flussi di lavoro — estrazione e mappatura dai diari di sessione.

Ogni sessione di Claude Code lascia un diario (file .jsonl). Da lì questo modulo
ricostruisce, per ogni sessione, la catena compatta delle fasi attraversate:
skill usate, servizi MCP interpellati, ricerche web, trascrizioni lette, file
consegnati nelle cartelle progetto (anche quando li genera uno script), pagine
pubblicate. Poi raggruppa le sessioni simili in "famiglie di flusso" e produce
la mappa: fasi in ordine tipico, con quanto sono ricorrenti, e destinazioni.

Regola fissa: SOLO metadati. Niente prompt, niente testi, niente contenuti.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

# Punto di partenza: li sostituisce configure(), chiamata da scan.py col config
# dell'utente. Nel codice non c'è il workspace di nessuno.
WS = None
PROJ_PREFIX = ""     # prefisso della cartella progetti: identifica le "consegne"

# File "consegna" quando scritti dentro la cartella progetti con queste estensioni.
DELIV_EXTS = {".md", ".xlsx", ".docx", ".pdf", ".csv", ".pptx", ".html"}

# File-dato: se letti senza essere stati scritti nella stessa sessione contano
# come "import manuale" (un MCP avrebbe potuto portare quei dati da solo).
DATA_EXTS = (".csv", ".xlsx", ".xls", ".tsv")

# Consegne generate da script: cerco path di Projects dentro i comandi Bash.
# Gruppi: 1 = progetto, 2 = resto del percorso relativo, 3 = estensione.
RE_BASH_OUT = None   # dipende dal prefisso dei progetti: la compila configure()

# File-dato letti da script Bash fuori da Projects (Downloads, Desktop…).
RE_BASH_DATA = re.compile(r'[\'"\s=]((?:/|~/)[^\'";|<>]*?\.(?:csv|xlsx|xls|tsv))\b')

# Etichette leggibili per i servizi MCP.
MCP_LABEL = {
    "claude_ai_Ubersuggest_MCP": "Ubersuggest (keyword)",
    "answerthepublic": "AnswerThePublic (domande)",
    "apify": "apify (estrazione web)",
    "brevo": "Brevo (email)",
    "claude_ai_Google_Calendar": "Google Calendar",
    "claude_ai_Adobe_for_creativity": "Adobe",
    "claude_ai_Canva": "Canva",
}
SKIP_MCP = {"ccd_session"}  # servizio interno, rumore


# Nome breve dei servizi MCP, per le etichette per-fase.
MCP_SHORT = {
    "claude_ai_Ubersuggest_MCP": "Ubersuggest",
    "answerthepublic": "AnswerThePublic",
    "apify": "apify",
    "brevo": "Brevo",
    "claude_ai_Google_Calendar": "Google Calendar",
    "claude_ai_Adobe_for_creativity": "Adobe",
    "claude_ai_Canva": "Canva",
}

# Soglie "fase da agente": frequente (≥ metà delle sessioni, famiglia ≥ 2),
# pesante (in media ≥ AGENT_MIN_EVENTS azioni a sessione), senza checkpoint tuo.
AGENT_MIN_EVENTS = 8

# Skill "di servizio": aiutano un flusso ma di rado lo definiscono.
UTILITY_SKILLS = {
    "dataviz", "artifact-design", "update-config", "xlsx", "docx", "pdf", "pptx",
    "consolidate-memory", "keybindings-help", "fewer-permission-prompts",
    "verify", "simplify", "code-review", "schedule", "loop", "setup-cowork",
}

# Nome "di finalità" di ogni famiglia di flusso: dice a cosa serve il lavoro,
# non come si chiama la skill. Dal config (voce "flow_names"); senza, vale lo
# slug della skill.
FLOW_NAMES = {}

# Le uniche etichette che il motore conosce da sé: non sono skill di nessuno.
BUILTIN_FLOW_NAMES = {
    "_libero": "Lavoro libero (senza ricetta codificata)",
    "_chat": "Conversazione (nessuna consegna)",
}


# Divisioni del lavoro per flusso (1-2, la prima è la principale). Dal config
# (voce "flow_tags"); i flussi non mappati compaiono come "Da classificare".
FLOW_TAGS = {}


def _tags_for(key):
    """Divisioni di una famiglia. Le skill-attrezzo (xlsx, dataviz…) non dicono
    la divisione: contano come lavoro libero. «Da classificare» resta il segnale
    per le skill vere ancora senza tag."""
    tags = FLOW_TAGS.get(key)
    if tags:
        return tags
    if key == "_libero" or key in UTILITY_SKILLS:
        return ["Lavoro libero"]
    return ["Da classificare"]


def assign_session_divisions(sessions):
    """Attribuisce le divisioni alle sessioni, inclusi i lavori senza skill.

    L'evidenza viene dalle consegne: per ogni progetto raccogliamo le divisioni
    dei flussi codificati che vi hanno lavorato. Una sessione libera eredita
    quindi le divisioni dei clienti presenti nei suoi ``outs``. È intenzionale
    non dedurre divisioni dal nome del cliente o dall'estensione del file: senza
    evidenza resta l'etichetta già prevista dalla tassonomia, ``Da classificare``.
    """
    project_tags = {}
    for s in sessions:
        key = _family_key(s.get("chain") or [])
        if key == "_chat" or _tags_for(key) == ["Lavoro libero"]:
            continue
        tags = [t for t in _tags_for(key) if t != "Da classificare"]
        for out in s.get("outs") or []:
            project_tags.setdefault(out[0], set()).update(tags)

    for s in sessions:
        key = _family_key(s.get("chain") or [])
        if key == "_chat":
            s["divisions"] = []
        elif key != "_libero" and _tags_for(key) != ["Lavoro libero"]:
            s["divisions"] = list(_tags_for(key))
        else:
            inferred = set()
            for out in s.get("outs") or []:
                inferred.update(project_tags.get(out[0], ()))
            s["divisions"] = sorted(inferred) if inferred else ["Da classificare"]
    return {project: sorted(tags) for project, tags in project_tags.items()}


def _session_tags(session):
    """Divisioni già inferite, con fallback compatibile per vecchi chiamanti."""
    return session.get("divisions") or _tags_for(_family_key(session.get("chain") or []))

# Mappe-processo curate: per ogni skill, le attività del processo reale (comprese
# quelle che avvengono fuori da Claude Code). Vengono dal config dell'utente
# (voce "flow_phases"); senza mappa, le fasi si deducono dai diari.
FLOW_PHASES = {}


def configure(cfg):
    """Riempie col config dell'utente tutto ciò che dipende dal SUO workspace:
    dove stanno i progetti, come si chiamano i suoi flussi, in quali divisioni
    li colloca. Le etichette generiche dei servizi restano quelle del motore,
    con le sue in aggiunta."""
    global WS, PROJ_PREFIX, DELIV_EXTS, AGENT_MIN_EVENTS, RE_BASH_OUT
    global FLOW_NAMES, FLOW_TAGS, FLOW_PHASES, UTILITY_SKILLS
    WS = cfg.ws
    PROJ_PREFIX = cfg.proj_prefix
    DELIV_EXTS = set(cfg.deliv_exts)
    AGENT_MIN_EVENTS = cfg.th["agent_min_events"]
    FLOW_NAMES = {**BUILTIN_FLOW_NAMES, **cfg.flow_names}
    FLOW_TAGS = dict(cfg.flow_tags)
    FLOW_PHASES = dict(cfg.flow_phases)
    UTILITY_SKILLS = UTILITY_SKILLS | set(cfg.utility_skills)
    MCP_LABEL.update(cfg.mcp_labels)
    MCP_SHORT.update(cfg.mcp_labels)
    ext = "|".join(e.lstrip(".") for e in sorted(DELIV_EXTS))
    RE_BASH_OUT = re.compile(
        re.escape(PROJ_PREFIX) + r'([^/"\';]+)/([^"\';]*?(\.(?:' + ext + r')))\b')


def _epoch(o):
    """Epoch dal campo timestamp ISO del diario (None se assente o illeggibile)."""
    t = o.get("timestamp")
    if not t:
        return None
    try:
        return datetime.fromisoformat(str(t).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def extract_session(path):
    """Legge un diario .jsonl e ritorna la sintesi della sessione.

    Ritorna None se il file non è leggibile, altrimenti:
      chain        catena compatta degli eventi (uguali consecutivi = uno)
      skills/mcp   conteggi per nome
      outs         consegne: [progetto, ext, percorso relativo, epoch, round]
                   (round = n. di messaggi utente già arrivati: se lo stesso file
                   viene riscritto in un round successivo, la prima stesura è
                   stata corretta in chat)
      web          n. ricerche/letture web
      user_msgs    tuoi messaggi di testo (esclusi comandi e notifiche)
      rev_rounds   tuoi messaggi DOPO la prima consegna = giri di revisione
      data_imports n. file-dato (CSV/Excel) letti ma mai scritti in sessione
      t0/t1        primo e ultimo timestamp visti (epoch)
    """
    chain, skills, mcp, outs = [], {}, {}, []
    web = user_msgs = rev_rounds = 0
    t0 = t1 = None
    data_reads, written = set(), set()

    def push(token):
        if not chain or chain[-1] != token:
            chain.append(token)

    def clock(ts):
        nonlocal t0, t1
        if ts is not None:
            t0 = ts if t0 is None or ts < t0 else t0
            t1 = ts if t1 is None or ts > t1 else t1

    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return None

    with fh:
        for line in fh:
            # filtri veloci prima del parse JSON: righe assistant con tool_use
            # oppure messaggi utente veri (niente tool_result, niente meta)
            is_tool = '"tool_use"' in line
            is_user = ('"type":"user"' in line and '"tool_result"' not in line
                       and '"isMeta":true' not in line)
            if not is_tool and not is_user:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            typ = o.get("type")
            ts = _epoch(o)

            if typ == "user":
                content = (o.get("message") or {}).get("content")
                text = content if isinstance(content, str) else None
                if text is None and isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "text":
                            text = b.get("text") or ""
                            break
                if text is None:
                    continue
                t = text.lstrip()
                # scarta comandi, caveat e notifiche di sistema: non sono tuoi turni
                if t.startswith(("<", "Caveat:", "[Request")):
                    continue
                clock(ts)
                user_msgs += 1
                if outs:
                    rev_rounds += 1
                continue

            if typ != "assistant":
                continue
            content = (o.get("message") or {}).get("content") or []
            if not isinstance(content, list):
                continue
            clock(ts)
            for b in content:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                n = b.get("name", "")
                inp = b.get("input") or {}
                if n == "Skill":
                    s = str(inp.get("skill"))
                    skills[s] = skills.get(s, 0) + 1
                    push("skill:" + s)
                elif n.startswith("mcp__"):
                    parts = n.split("__")
                    srv = parts[1] if len(parts) > 2 else n[5:]
                    mcp[srv] = mcp.get(srv, 0) + 1
                    if srv not in SKIP_MCP:
                        push("mcp:" + srv)
                elif n in ("Write", "Edit"):
                    p = str(inp.get("file_path", ""))
                    if p.startswith(PROJ_PREFIX):
                        rel = p[len(PROJ_PREFIX):]
                        proj = rel.split("/")[0]
                        ext = os.path.splitext(p)[1].lower()
                        if ext in DELIV_EXTS:
                            outs.append([proj, ext, rel, ts, user_msgs])
                            written.add(p)
                            push(f"out:{proj}|{ext}")
                elif n == "Bash":
                    cmd = str(inp.get("command", ""))
                    for m in RE_BASH_OUT.finditer(cmd):
                        proj, rel = m.group(1), f"{m.group(1)}/{m.group(2)}"
                        outs.append([proj, m.group(3), rel, ts, user_msgs])
                        written.add(PROJ_PREFIX + rel)
                        push(f"out:{proj}|{m.group(3)}")
                    for m in RE_BASH_DATA.finditer(cmd):
                        if not m.group(1).startswith(PROJ_PREFIX):
                            data_reads.add(m.group(1))
                elif n in ("WebSearch", "WebFetch"):
                    web += 1
                    push("web")
                elif n == "Artifact":
                    push("artifact")
                elif n == "Read":
                    p = str(inp.get("file_path", ""))
                    if p.lower().endswith((".vtt", ".srt")) or "/Trascrizion" in p:
                        push("in:trascrizione")
                    elif p.lower().endswith(DATA_EXTS):
                        data_reads.add(p)

    data_imports = len(data_reads - written)
    if data_imports:
        push("in:dati")
    return {"chain": chain, "skills": skills, "mcp": mcp, "outs": outs, "web": web,
            "user_msgs": user_msgs, "rev_rounds": rev_rounds,
            "data_imports": data_imports, "t0": t0, "t1": t1}


def _family_key(chain):
    """La skill che 'dà il nome' alla sessione: la prima non di servizio."""
    first_any = None
    for ev in chain:
        if ev.startswith("skill:"):
            name = ev[6:]
            if first_any is None:
                first_any = name
            if name not in UTILITY_SKILLS:
                return name
    if first_any:
        return first_any
    return "_libero" if chain else "_chat"


def _phase(ev):
    """Normalizza un evento in 'fase' (tutte le consegne diventano una fase sola)."""
    return "out" if ev.startswith("out:") else ev


def _events_for(detect, s):
    """Quante azioni reali della sessione s ricadono nelle voci di `detect`.

    È la misura di 'pesantezza' di una fase: 20 chiamate Ubersuggest o 30
    ricerche web = lavoro di gambe, candidabile a un agente.
    """
    tot = 0
    phases = {_phase(ev) for ev in s.get("chain") or []}
    for d in detect:
        if d.startswith("mcp:"):
            tot += (s.get("mcp") or {}).get(d[4:], 0)
        elif d == "web":
            tot += s.get("web", 0)
        elif d.startswith("skill:"):
            tot += (s.get("skills") or {}).get(d[6:], 0)
        elif d == "out":
            tot += len(s.get("outs") or [])
        elif d in phases:
            tot += 1
    return tot


def _label(ph, fam):
    """Etichetta-attività per i flussi SENZA mappa curata (fallback)."""
    if ph == "out":
        return "consegna nel progetto"
    if ph == "web":
        return "ricerca web"
    if ph == "artifact":
        return "pubblicazione pagina"
    if ph == "in:trascrizione":
        return "lettura trascrizione della call"
    if ph == "in:dati":
        return "import manuale di dati (CSV/Excel)"
    if ph.startswith("skill:"):
        s = ph[6:]
        return f"lavorazione con la skill {s}" if s != fam else f"lavorazione ({s})"
    if ph.startswith("mcp:"):
        return "raccolta dati: " + MCP_LABEL.get(ph[4:], ph[4:])
    return ph


def _kind(ph):
    """Categoria della fase, per il colore in mappa."""
    if ph.startswith("skill:"):
        return "skill"
    if ph.startswith("mcp:"):
        return "mcp"
    if ph.startswith("in:"):
        return "in"
    return ph  # web / out / artifact


def build(sessions):
    """Dalle sintesi di sessione alle famiglie di flusso con mappa delle fasi.

    Per ogni famiglia: fasi in ordine di posizione media, ognuna con la quota di
    sessioni in cui compare; più il riepilogo delle consegne (formati → progetti).
    """
    fams, chat = {}, 0
    for s in sessions:
        key = _family_key(s.get("chain") or [])
        if key == "_chat":
            chat += 1
            continue
        fams.setdefault(key, []).append(s)

    families = []
    agent_candidates = []
    for key, group in sorted(fams.items(), key=lambda kv: -len(kv[1])):
        n = len(group)
        phase_sets = [{_phase(ev) for ev in s["chain"]} for s in group]
        detected_mcp = set()
        if key in FLOW_PHASES:
            # mappa curata: le attività del processo; frequenza e pesantezza dai diari
            steps = []
            for ph in FLOW_PHASES[key]:
                det = ph.get("detect")
                mcp_list, avg, agent = [], 0, False
                if det:
                    present = [s for s, ss in zip(group, phase_sets)
                               if any(d in ss for d in det)]
                    c = len(present)
                    share = round(c / n, 2)
                    for d in det:
                        if d.startswith("mcp:"):
                            srv = d[4:]
                            detected_mcp.add(srv)
                            k_c = sum(1 for ss in phase_sets if d in ss)
                            mcp_list.append({"name": MCP_SHORT.get(srv, srv),
                                             "count": k_c})
                    if c:
                        avg = round(sum(_events_for(det, s) for s in present) / c)
                    agent = (ph["kind"] in ("mcp", "web") and n >= 2
                             and share >= 0.5 and avg >= AGENT_MIN_EVENTS)
                else:
                    c, share = None, None
                steps.append({"label": ph["label"], "kind": ph["kind"],
                              "tool": ph.get("tool", ""), "share": share, "count": c,
                              "mcp": mcp_list, "avg": avg, "agent": agent})
        else:
            # fallback: fasi dedotte dai diari, in ordine di posizione media
            pos, cnt = {}, {}
            for s in group:
                phases, seen = [], set()
                for ev in s["chain"]:
                    ph = _phase(ev)
                    if ph not in seen:
                        seen.add(ph)
                        phases.append(ph)
                for i, ph in enumerate(phases):
                    pos[ph] = pos.get(ph, 0) + i
                    cnt[ph] = cnt.get(ph, 0) + 1
            ordered = sorted(cnt, key=lambda ph: pos[ph] / cnt[ph])[:10]
            steps = []
            for ph in ordered:
                kind = _kind(ph)
                c = cnt[ph]
                share = round(c / n, 2)
                mcp_list, avg, agent = [], 0, False
                if ph.startswith("mcp:"):
                    srv = ph[4:]
                    detected_mcp.add(srv)
                    mcp_list = [{"name": MCP_SHORT.get(srv, srv), "count": c}]
                present = [s for s, ss in zip(group, phase_sets) if ph in ss]
                if present and kind in ("mcp", "web"):
                    avg = round(sum(_events_for([ph], s) for s in present) / len(present))
                    agent = n >= 2 and share >= 0.5 and avg >= AGENT_MIN_EVENTS
                steps.append({"label": _label(ph, key), "kind": kind, "tool": "",
                              "share": share, "count": c,
                              "mcp": mcp_list, "avg": avg, "agent": agent})

        # servizi MCP visti nelle sessioni della famiglia ma non attribuiti a una fase
        seen_srv = {}
        for s in group:
            for srv, calls in (s.get("mcp") or {}).items():
                if srv not in SKIP_MCP and calls > 0:
                    seen_srv[srv] = seen_srv.get(srv, 0) + 1
        mcp_extra = [{"name": MCP_SHORT.get(srv, srv), "count": c}
                     for srv, c in sorted(seen_srv.items(), key=lambda kv: -kv[1])
                     if srv not in detected_mcp]

        fam_name = FLOW_NAMES.get(key, key)
        fam_tags = sorted({tag for s in group for tag in _session_tags(s)})
        for st in steps:
            if st.get("agent"):
                agent_candidates.append({"family": fam_name, "phase": st["label"],
                                         "detail": f"in media {st['avg']} azioni a sessione"})

        exts, projs = {}, []
        for s in group:
            for o in s.get("outs") or []:
                proj, ext = o[0], o[1]
                exts[ext] = exts.get(ext, 0) + 1
                if proj not in projs:
                    projs.append(proj)
        deliver = ""
        if exts:
            resto = f" e altri {len(projs) - 3}" if len(projs) > 3 else ""
            deliver = "file " + " + ".join(sorted(exts)) + " per " \
                + ", ".join(projs[:3]) + resto

        families.append({
            "name": fam_name,
            "slug": "" if key == "_libero" else key,
            "sessions": n,
            "tags": fam_tags,
            "steps": steps,
            "deliver": deliver,
            "mcp_extra": mcp_extra,
        })

    # sessioni per divisione (su TUTTE le famiglie, anche oltre il taglio delle
    # card): una famiglia con due divisioni conta in entrambe
    div = {}
    for key, group in fams.items():
        for s in group:
            for t in _session_tags(s):
                div[t] = div.get(t, 0) + 1
    divisions = [{"name": k, "sessions": v}
                 for k, v in sorted(div.items(), key=lambda kv: -kv[1])]

    return {"families": families[:8], "chat_sessions": chat,
            "divisions": divisions,
            "agent_candidates": agent_candidates}
