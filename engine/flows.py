#!/usr/bin/env python3
"""
Flussi di lavoro — estrazione e mappatura dai diari di sessione.

Ogni sessione di Claude Code lascia un diario (file .jsonl). Da lì questo modulo
ricostruisce, per ogni sessione, la catena compatta delle fasi attraversate:
skill usate, servizi MCP interpellati, ricerche web, trascrizioni lette, file
consegnati nelle cartelle progetto (anche quando li genera uno script), pagine
pubblicate. Poi raggruppa le sessioni simili in "famiglie di flusso" e produce
la mappa: fasi in ordine tipico, con quanto sono ricorrenti, e destinazioni.

Tutto ciò che dipende dal workspace dell'utente (percorsi, nomi dei flussi,
divisioni del lavoro, mappe-processo curate) arriva da config.C: qui vive solo
il meccanismo, più i fallback generici che funzionano senza alcuna curatela.

Regola fissa: SOLO metadati. Niente prompt, niente testi, niente contenuti.
"""

import json
import os
import re
from datetime import datetime

import config

# File-dato: se letti senza essere stati scritti nella stessa sessione contano
# come "import manuale" (un MCP avrebbe potuto portare quei dati da solo).
DATA_EXTS = (".csv", ".xlsx", ".xls", ".tsv")

# File-dato letti da script Bash fuori dai progetti (Downloads, Desktop…).
RE_BASH_DATA = re.compile(r'[\'"\s=]((?:/|~/)[^\'";|<>]*?\.(?:csv|xlsx|xls|tsv))\b')

SKIP_MCP = {"ccd_session"}  # servizio interno, rumore

# Skill "di servizio": aiutano un flusso ma di rado lo definiscono.
# (Le built-in di Claude Code; il config può aggiungerne altre.)
BASE_UTILITY_SKILLS = {
    "dataviz", "artifact-design", "update-config", "xlsx", "docx", "pdf", "pptx",
    "consolidate-memory", "keybindings-help", "fewer-permission-prompts",
    "verify", "simplify", "code-review", "schedule", "loop", "setup-cowork",
    "claude-api", "run", "init", "review", "security-review",
}

# Nomi "di finalità" per i flussi che nascono da skill universali (plugin
# ufficiali e attrezzi): il config aggiunge/sovrascrive per le skill proprie.
BASE_FLOW_NAMES = {
    "skill-creator:skill-creator": "Officina: costruzione di nuove skill",
    "dashboard-update": "Aggiornamento della dashboard",
    "dataviz": "Grafica dati e visualizzazioni",
    "update-config": "Configurazione di Claude Code",
    "xlsx": "Lavorazione fogli Excel",
    "_libero": "Lavoro libero (senza ricetta codificata)",
}

BASE_FLOW_TAGS = {
    "skill-creator:skill-creator": ["Officina del metodo"],
    "dashboard-update": ["Officina del metodo"],
    "update-config": ["Officina del metodo"],
}


def utility_skills():
    return BASE_UTILITY_SKILLS | config.C.utility_skills


def flow_name(key):
    names = {**BASE_FLOW_NAMES, **config.C.flow_names}
    return names.get(key, key)


def flow_phases():
    return config.C.flow_phases


def agent_min_events():
    return config.C.th["agent_min_events"]


def mcp_label(srv):
    """Etichetta leggibile di un servizio MCP: dal config, o derivata dal nome
    (claude_ai_Google_Calendar → Google Calendar)."""
    lbl = config.C.mcp_labels.get(srv)
    if lbl:
        return lbl
    return re.sub(r"^claude_ai_", "", srv).replace("_", " ")


_re_bash_out = None


def _bash_out_re():
    """Regex per le consegne generate da script Bash (path di progetto + ext)."""
    global _re_bash_out
    if _re_bash_out is None:
        exts = "|".join(sorted(e.lstrip(".") for e in config.C.deliv_exts))
        _re_bash_out = re.compile(
            re.escape(config.C.proj_prefix)
            + r'([^/"\';]+)/([^"\';]*?(\.(?:' + exts + r')))\b')
    return _re_bash_out


def _tags_for(key):
    """Divisioni di una famiglia. Le skill-attrezzo (xlsx, dataviz…) non dicono
    la divisione: contano come lavoro libero. «Da classificare» resta il segnale
    per le skill vere ancora senza tag nel config."""
    tags = {**BASE_FLOW_TAGS, **config.C.flow_tags}.get(key)
    if tags:
        return tags
    if key == "_libero" or key in utility_skills():
        return ["Lavoro libero"]
    return ["Da classificare"]


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
      user_msgs    messaggi di testo dell'utente (esclusi comandi e notifiche)
      rev_rounds   messaggi DOPO la prima consegna = giri di revisione
      data_imports n. file-dato (CSV/Excel) letti ma mai scritti in sessione
      t0/t1        primo e ultimo timestamp visti (epoch)
    """
    proj_prefix = config.C.proj_prefix
    deliv_exts = config.C.deliv_exts
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
                # scarta comandi, caveat e notifiche di sistema: non sono turni veri
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
                    if proj_prefix and p.startswith(proj_prefix):
                        rel = p[len(proj_prefix):]
                        proj = rel.split("/")[0]
                        ext = os.path.splitext(p)[1].lower()
                        if ext in deliv_exts:
                            outs.append([proj, ext, rel, ts, user_msgs])
                            written.add(p)
                            push(f"out:{proj}|{ext}")
                elif n == "Bash":
                    cmd = str(inp.get("command", ""))
                    if proj_prefix:
                        for m in _bash_out_re().finditer(cmd):
                            proj, rel = m.group(1), f"{m.group(1)}/{m.group(2)}"
                            outs.append([proj, m.group(3), rel, ts, user_msgs])
                            written.add(proj_prefix + rel)
                            push(f"out:{proj}|{m.group(3)}")
                    for m in RE_BASH_DATA.finditer(cmd):
                        if not (proj_prefix and m.group(1).startswith(proj_prefix)):
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
    util = utility_skills()
    for ev in chain:
        if ev.startswith("skill:"):
            name = ev[6:]
            if first_any is None:
                first_any = name
            if name not in util:
                return name
    if first_any:
        return first_any
    return "_libero" if chain else "_chat"


def _phase(ev):
    """Normalizza un evento in 'fase' (tutte le consegne diventano una fase sola)."""
    return "out" if ev.startswith("out:") else ev


def _events_for(detect, s):
    """Quante azioni reali della sessione s ricadono nelle voci di `detect`.

    È la misura di 'pesantezza' di una fase: 20 chiamate a un MCP keyword o 30
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
        return "raccolta dati: " + mcp_label(ph[4:])
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

    phases_map = flow_phases()
    min_events = agent_min_events()
    families = []
    agent_candidates = []
    for key, group in sorted(fams.items(), key=lambda kv: -len(kv[1])):
        n = len(group)
        phase_sets = [{_phase(ev) for ev in s["chain"]} for s in group]
        detected_mcp = set()
        if key in phases_map:
            # mappa curata: le attività del processo; frequenza e pesantezza dai diari
            steps = []
            for ph in phases_map[key]:
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
                            mcp_list.append({"name": mcp_label(srv), "count": k_c})
                    if c:
                        avg = round(sum(_events_for(det, s) for s in present) / c)
                    agent = (ph["kind"] in ("mcp", "web") and n >= 2
                             and share >= 0.5 and avg >= min_events)
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
                    mcp_list = [{"name": mcp_label(srv), "count": c}]
                present = [s for s, ss in zip(group, phase_sets) if ph in ss]
                if present and kind in ("mcp", "web"):
                    avg = round(sum(_events_for([ph], s) for s in present) / len(present))
                    agent = n >= 2 and share >= 0.5 and avg >= min_events
                steps.append({"label": _label(ph, key), "kind": kind, "tool": "",
                              "share": share, "count": c,
                              "mcp": mcp_list, "avg": avg, "agent": agent})

        # servizi MCP visti nelle sessioni della famiglia ma non attribuiti a una fase
        seen_srv = {}
        for s in group:
            for srv, calls in (s.get("mcp") or {}).items():
                if srv not in SKIP_MCP and calls > 0:
                    seen_srv[srv] = seen_srv.get(srv, 0) + 1
        mcp_extra = [{"name": mcp_label(srv), "count": c}
                     for srv, c in sorted(seen_srv.items(), key=lambda kv: -kv[1])
                     if srv not in detected_mcp]

        fam_name = flow_name(key)
        fam_tags = _tags_for(key)
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
        for t in _tags_for(key):
            div[t] = div.get(t, 0) + len(group)
    divisions = [{"name": k, "sessions": v}
                 for k, v in sorted(div.items(), key=lambda kv: -kv[1])]

    return {"families": families[:8], "chat_sessions": chat,
            "divisions": divisions,
            "agent_candidates": agent_candidates}
