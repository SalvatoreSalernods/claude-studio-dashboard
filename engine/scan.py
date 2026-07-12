#!/usr/bin/env python3
"""
Claude Studio Dashboard — scanner locale.

Fotografa il workspace Claude Code dell'utente (skill, progetti, MCP, plugin,
uso recente, consegne, costi) e produce nell'output_dir del config:

  data.json       — snapshot in JSON: solo nomi, conteggi e date. Nessun segreto,
                    nessun contenuto di file, nessun testo delle conversazioni.
  dashboard.html  — pagina pronta da pubblicare come Artifact (template.html + dati).

Uso:
  python3 scan.py --config <path/config.json>   # scansiona e scrive i due file
  python3 scan.py --config ... --dump           # più riepilogo leggibile a terminale
  python3 scan.py --demo [--out FILE]           # NON scansiona: dashboard-demo.html
                                                # con dati di fantasia (demo.py)

Il config si genera con la skill `dashboard-setup` (intervista guidata) o
copiando config.example.json. Senza --config cerca ./config.json, poi la
variabile d'ambiente STUDIO_DASHBOARD_CONFIG.

Zero dipendenze: solo libreria standard. La scansione dei diari usa una
cache incrementale (.cache.json nell'output_dir) per restare veloce.
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

import config
import flows    # estrazione flussi dai diari
import metrics  # metriche decisionali, storico e serie per il grafico

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "template.html"
HOME_SKILLS = Path.home() / ".claude" / "skills"
PLUGINS_JSON = Path.home() / ".claude" / "plugins" / "installed_plugins.json"

MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
        "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]

RE_LOG_ENTRY = re.compile(r'^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\] \(exit (\d+)\)')


def datefmt(ts):
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%d/%m/%Y")


def read_text(p):
    return p.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- indice del workspace

def parse_index():
    """Slug delle skill e nomi dei progetti linkati nell'indice del workspace.

    Ritorna (None, None) se l'indice non è configurato o non esiste: in quel
    caso tutto conta come indicizzato e non scattano alert."""
    idx = config.C.index_md
    if not idx or not idx.exists():
        return None, None
    md = read_text(idx)
    sk = config.C.skill_dir.name if config.C.skill_dir else "SKILL"
    pj = config.C.projects_dir.name if config.C.projects_dir else "Projects"
    skills = {unquote(m) for m in
              re.findall(r'\(' + re.escape(sk) + r'/([^/)]+)/SKILL\.md\)', md)}
    projects = {unquote(m) for m in
                re.findall(r'\(' + re.escape(pj) + r'/([^/)]+)/CLAUDE\.md\)', md)}
    return skills, projects


# ---------------------------------------------------------------------- skill

def _skill_row(name, symlink, indexed_slugs, d):
    try:
        mtime = max((f.stat().st_mtime for f in d.rglob("*") if f.is_file()),
                    default=d.stat().st_mtime)
    except OSError:
        mtime = d.stat().st_mtime
    try:
        created_ts = d.stat().st_birthtime
    except (OSError, AttributeError):
        created_ts = d.stat().st_mtime
    return {"name": name, "symlink": symlink,
            "indexed": indexed_slugs is None or name in indexed_slugs,
            "updated": datefmt(mtime), "created_ts": created_ts, "dir": str(d)}


def scan_skills(indexed_slugs):
    """Le skill dell'utente.

    Modalità A (skills_dir nel config): le skill vivono nel workspace e sono
    collegate in ~/.claude/skills con symlink — se il collegamento manca o
    punta altrove, la skill è invisibile a Claude Code (alert).
    Modalità B (skills_dir = ""): le skill vivono direttamente in
    ~/.claude/skills; il concetto di symlink non si applica.
    """
    rows, anomalies = [], []
    skill_dir = config.C.skill_dir
    if skill_dir:
        if not skill_dir.is_dir():
            return rows, [f"Cartella {skill_dir.name}/ non trovata"]
        for d in sorted(skill_dir.iterdir(), key=lambda p: p.name.lower()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            if not (d / "SKILL.md").exists():
                anomalies.append(d.name)
                continue
            link = HOME_SKILLS / d.name
            if link.is_symlink():
                symlink = "ok" if Path(os.path.realpath(link)) == d.resolve() \
                    else "altrove"
            elif link.exists():
                symlink = "dir reale"
            else:
                symlink = "mancante"
            rows.append(_skill_row(d.name, symlink, indexed_slugs, d))
        return rows, anomalies

    # Modalità B: ~/.claude/skills è la fonte (solo cartelle scrivibili:
    # quelle in sola lettura sono skill ufficiali installate)
    if HOME_SKILLS.is_dir():
        for d in sorted(HOME_SKILLS.iterdir(), key=lambda p: p.name.lower()):
            if d.name.startswith(".") or not d.is_dir() or d.is_symlink():
                continue
            if not os.access(d, os.W_OK):
                continue
            if not (d / "SKILL.md").exists():
                anomalies.append(d.name)
                continue
            rows.append(_skill_row(d.name, "ok", indexed_slugs, d))
    return rows, anomalies


def scan_home_skills():
    """Voci di ~/.claude/skills oltre alle skill dell'utente.

    - dir di sola lettura  -> skill ufficiali installate (informativo)
    - dir scrivibili       -> "fuori posto" SOLO in modalità skills_dir
    - symlink verso altre posizioni -> collegate da repo (informativo)
    """
    official, strays, repo_links = [], [], []
    skill_dir = config.C.skill_dir
    if not HOME_SKILLS.is_dir():
        return official, strays, repo_links
    for e in sorted(HOME_SKILLS.iterdir(), key=lambda p: p.name.lower()):
        if e.name.startswith("."):
            continue
        if e.is_symlink():
            target = Path(os.path.realpath(e))
            if skill_dir:
                direct = skill_dir / e.name
                if direct.exists() and target == direct.resolve():
                    continue  # già coperta da scan_skills
            if not target.exists():
                strays.append(e.name + " (symlink rotto)")
            else:
                repo_links.append(e.name)
            continue
        if e.is_dir():
            if os.access(e, os.W_OK):
                if skill_dir:            # in modalità B sono le skill stesse
                    strays.append(e.name)
            else:
                official.append(e.name)
    return official, strays, repo_links


# -------------------------------------------------------------------- progetti

def scan_projects(indexed_names):
    now = time.time()
    rows = []
    pdir = config.C.projects_dir
    if not pdir or not pdir.is_dir():
        return rows
    for d in sorted(pdir.iterdir(), key=lambda p: p.name.lower()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        mod7 = mod30 = 0
        latest_ts, latest_name = 0, ""
        for root, dirs, files in os.walk(d):
            dirs[:] = [x for x in dirs if not x.startswith(".")]
            for f in files:
                if f.startswith("."):
                    continue
                try:
                    m = (Path(root) / f).stat().st_mtime
                except OSError:
                    continue
                if now - m < 7 * 86400:
                    mod7 += 1
                if now - m < 30 * 86400:
                    mod30 += 1
                if m > latest_ts:
                    latest_ts, latest_name = m, f
        rows.append({
            "name": d.name,
            "claude_md": (d / "CLAUDE.md").exists(),
            "indexed": indexed_names is None or d.name in indexed_names,
            "mod7": mod7,
            "mod30": mod30,
            "last_file": latest_name or "—",
            "last_date": datefmt(latest_ts),
            "days_since": int((now - latest_ts) // 86400) if latest_ts else None,
        })
    return rows


# ------------------------------------------------------------------- MCP e plugin

CLAUDEAI_SKIP = {"claude.ai Claude Code Remote"}  # canale interno, non un servizio


def scan_mcp():
    """Inventario MCP completo, da tre fonti locali:

    1. i file .mcp.json (workspace e progetti) — server configurati a mano;
    2. ~/.claude.json — server a scope utente + connettori claude.ai collegati;
    3. la cache locale mcp-needs-auth-cache.json — chi richiede ri-autorizzazione.

    Solo nomi e stati: mai chiavi o valori.
    """
    servers = []

    def ukey(name):
        # nome → chiave usata nei diari (mcp__<chiave>__): "claude.ai X Y" → claude_ai_X_Y
        return re.sub(r"[^A-Za-z0-9]+", "_",
                      name.replace("claude.ai ", "claude_ai ")).strip("_")

    needs_auth = set()
    try:
        cache = json.loads(read_text(Path.home() / ".claude" / "mcp-needs-auth-cache.json"))
        needs_auth = {k for k in cache if not k.startswith("plugin:")}
    except Exception:
        pass

    def add(name, scope, kind, env=None, problem=""):
        servers.append({
            "name": name,
            "scope": scope,
            "type": kind,
            "env": sorted((env or {}).keys()),
            "status": "riautorizzare" if name in needs_auth else "",
            "problem": problem,
            "ukey": ukey(name),
        })

    def load(path, scope):
        if not path.exists():
            return
        try:
            cfg = json.loads(read_text(path))
        except Exception as exc:
            add(f"(errore parse {path.name})", scope, "?", problem=str(exc)[:80])
            return
        for name, spec in (cfg.get("mcpServers") or {}).items():
            kind = "http" if spec.get("url") else "stdio"
            problem = ""
            if kind == "stdio":
                for a in spec.get("args", []):
                    if a.endswith(".py") and not Path(a).exists():
                        problem = "script non trovato"
            add(name, scope, kind, spec.get("env"), problem)

    load(config.C.ws / ".mcp.json", "workspace")
    pdir = config.C.projects_dir
    if pdir and pdir.is_dir():
        for d in sorted(pdir.iterdir()):
            if d.is_dir():
                load(d / ".mcp.json", d.name)

    try:
        user_cfg = json.loads(read_text(Path.home() / ".claude.json"))
    except Exception:
        user_cfg = {}
    for name, spec in (user_cfg.get("mcpServers") or {}).items():
        add(name, "utente", "http" if spec.get("url") else "stdio", spec.get("env"))

    connectors = set(user_cfg.get("claudeAiMcpEverConnected") or [])
    connectors |= {k for k in needs_auth if k.startswith("claude.ai ")}
    for name in sorted(connectors):
        if name not in CLAUDEAI_SKIP:
            add(name, "account claude.ai", "connettore")
    return servers


def scan_plugins():
    rows = []
    if not PLUGINS_JSON.exists():
        return rows
    try:
        data = json.loads(read_text(PLUGINS_JSON))
    except Exception:
        return rows
    for full_name, installs in (data.get("plugins") or {}).items():
        info = installs[0] if installs else {}
        rows.append({
            "name": full_name.split("@")[0],
            "marketplace": full_name.split("@")[-1],
            "scope": info.get("scope", "?"),
            "updated": (info.get("lastUpdated") or "")[:10],
        })
    return rows


def scan_checks():
    """Le "sveglie" del workspace: controlli periodici registrati e il loro esito.

    Formato generico, definito nel config (voce "checks"): ogni controllo è uno
    script/hook DELL'UTENTE che appende al proprio log righe nel formato
    `[YYYY-MM-DD HH:MM] (exit N)`; qui si legge l'ultima riga e la si traduce
    con la mappa "codes" ({"0": ["good", "ok"], ...}). Un eventuale file
    "stamp" con dentro YYYY-MM segna il mese già coperto (cadenza mensile).
    """
    checks = []
    now = datetime.now()
    for spec in config.C.checks:
        last_run, last_code = "", None
        log = Path(spec.get("log", "")).expanduser()
        if log.exists():
            for line in read_text(log).splitlines():
                m = RE_LOG_ENTRY.match(line)
                if m:
                    d = m.group(1)
                    last_run = f"{d[8:10]}/{d[5:7]}/{d[0:4]} {d[11:]}"
                    last_code = int(m.group(2))
        codes = {int(k): v for k, v in (spec.get("codes") or {}).items()}
        level, outcome = codes.get(last_code, ("warning", "mai eseguita"))
        next_due = "alla prossima sessione"
        stamp = spec.get("stamp")
        if stamp:
            sp = Path(stamp).expanduser()
            if sp.exists() and sp.read_text().strip() == now.strftime("%Y-%m"):
                next_due = f"prima sessione di {MESI[now.month % 12]}"
        checks.append({
            "name": spec.get("name", "controllo"),
            "what": spec.get("what", ""),
            "cadence": spec.get("cadence", ""),
            "last_run": last_run or "—",
            "level": level,
            "outcome": outcome,
            "next": next_due,
        })
    return checks


# ---------------------------------------------------------------- orchestratori

ORCH_KEYWORDS = ("orchestr", "concaten", "filiera", "pipeline")


def scan_orchestrators(skills, chains):
    """Le skill-regia e i candidati suggeriti dai dati.

    Regia = skill il cui SKILL.md dichiara l'orchestrazione (parole chiave) e
    cita almeno 2 altre skill. Candidato = coppia di skill usate insieme in più
    sessioni senza che nessuna regia fosse in campo.
    """
    skill_names = [s["name"] for s in skills]
    existing = []
    for s in skills:
        try:
            text = read_text(Path(s["dir"]) / "SKILL.md")[:8000].lower()
        except OSError:
            continue
        if not any(k in text for k in ORCH_KEYWORDS):
            continue
        refs = sorted((text.find(o), o) for o in skill_names
                      if o != s["name"] and o in text)
        refs = [o for _, o in refs]
        if len(refs) >= 2:
            existing.append({"name": s["name"],
                             "purpose": flows.flow_name(s["name"])
                             if flows.flow_name(s["name"]) != s["name"] else "",
                             "chains": refs[:6]})
    orch_names = {o["name"] for o in existing}
    util = flows.utility_skills()
    pairs = {}
    for s in chains:
        used = sorted({ev[6:] for ev in s["chain"] if ev.startswith("skill:")
                       and ev[6:] not in util})
        if len(used) < 2 or any(x in orch_names for x in used):
            continue
        for i in range(len(used)):
            for j in range(i + 1, len(used)):
                k = (used[i], used[j])
                pairs[k] = pairs.get(k, 0) + 1
    candidates = [f"{a} + {b} (insieme in {n} sessioni)"
                  for (a, b), n in sorted(pairs.items(), key=lambda kv: -kv[1])
                  if n >= 2][:5]
    return {"existing": existing, "candidates": candidates}


# ------------------------------------------------------------------ uso (diari)

def load_cache():
    try:
        cache = json.loads(read_text(config.C.cache_file))
    except Exception:
        cache = {}
    if cache.get("version") != 5:
        cache = {"version": 5, "files": {}}
    return cache


def scan_usage():
    """Legge i diari di sessione (con cache per mtime+size) e ritorna, sulla
    finestra configurata: top skill, top MCP, n. sessioni e le catene-di-flusso
    per le mappe. L'estrazione vera è in flows.extract_session."""
    cache = load_cache()
    files_cache = cache["files"]
    cutoff = time.time() - config.C.window_days * 86400
    skills, mcp = {}, {}
    sessions = 0
    chains = []

    dirs = []
    root = config.C.transcripts_root
    if root.is_dir():
        dirs = [p for p in root.iterdir()
                if p.is_dir() and p.name.startswith(config.C.transcript_prefix)]

    for d in dirs:
        for f in d.glob("*.jsonl"):
            try:
                st = f.stat()
            except OSError:
                continue
            key = str(f)
            entry = files_cache.get(key)
            if not entry or entry["mtime"] != st.st_mtime or entry["size"] != st.st_size:
                extracted = flows.extract_session(f)
                if extracted is None:
                    continue
                entry = {"mtime": st.st_mtime, "size": st.st_size, **extracted}
                files_cache[key] = entry
            if st.st_mtime >= cutoff:
                sessions += 1
                for k, v in entry["skills"].items():
                    skills[k] = skills.get(k, 0) + v
                for k, v in entry["mcp"].items():
                    mcp[k] = mcp.get(k, 0) + v
                chains.append({"chain": entry["chain"], "outs": entry["outs"],
                               "skills": entry["skills"], "mcp": entry["mcp"],
                               "web": entry.get("web", 0),
                               "user_msgs": entry.get("user_msgs", 0),
                               "rev_rounds": entry.get("rev_rounds", 0),
                               "data_imports": entry.get("data_imports", 0),
                               "t0": entry.get("t0"), "t1": entry.get("t1"),
                               "size": st.st_size, "mtime": st.st_mtime})

    # elimina dalla cache i file spariti
    for key in [k for k in files_cache if not Path(k).exists()]:
        del files_cache[key]
    try:
        config.C.cache_file.write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        pass

    top = lambda d: [{"name": k, "n": v}
                     for k, v in sorted(d.items(), key=lambda kv: -kv[1])][:15]
    return top(skills), top(mcp), sessions, chains


# ------------------------------------------------------------------ telemetria

def get_rate():
    """Cambio USD→valuta di vetrina del giorno (dati BCE via frankfurter.app).
    La telemetria riporta i costi solo in dollari: la conversione è di
    cortesia. Se la rete manca si riusa l'ultimo cambio salvato; senza mai un
    cambio (o con currency = USD) si resta in dollari."""
    cur = config.C.currency
    if cur == "USD":
        return None, ""
    saved = {}
    try:
        saved = json.loads(read_text(config.C.rate_cache))
    except Exception:
        pass
    try:
        import urllib.request
        req = urllib.request.Request(
            f"https://api.frankfurter.app/latest?from=USD&to={cur}",
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
        with urllib.request.urlopen(req, timeout=4) as r:
            j = json.load(r)
        saved = {"rate": float(j["rates"][cur]), "date": j.get("date", "")}
        config.C.rate_cache.write_text(json.dumps(saved), encoding="utf-8")
    except Exception:
        pass
    return saved.get("rate"), saved.get("date", "")


def scan_telemetry():
    """Aggrega la telemetria locale (eventi api_request della sonda OTel,
    vedi telemetry/README.md del repo) sulla finestra: token e costo reali.
    Ritorna None se la sonda non è attiva o non ha ancora prodotto nulla."""
    if not config.C.telemetry_enabled:
        return None
    cutoff = time.time() - config.C.window_days * 86400
    cost = 0.0
    tok = {"in": 0, "out": 0, "cache": 0}
    n = 0
    first_ts = last_ts = None
    for p in config.C.telemetry_logs:
        if not p.exists():
            continue
        with p.open(encoding="utf-8") as fh:
            for line in fh:
                if '"claude_code.api_request"' not in line:
                    continue
                try:
                    a = json.loads(line).get("attrs") or {}
                    ts = datetime.fromisoformat(
                        str(a["event.timestamp"]).replace("Z", "+00:00")).timestamp()
                except Exception:
                    continue
                if last_ts is None or ts > last_ts:
                    last_ts = ts
                if ts < cutoff:
                    continue
                if first_ts is None or ts < first_ts:
                    first_ts = ts
                n += 1
                cost += float(a.get("cost_usd") or 0)
                tok["in"] += int(a.get("input_tokens") or 0)
                tok["out"] += int(a.get("output_tokens") or 0)
                tok["cache"] += (int(a.get("cache_read_tokens") or 0)
                                 + int(a.get("cache_creation_tokens") or 0))
    if n == 0:
        return None
    return {"cost": round(cost, 2), "requests": n, "tok": tok,
            "first_ts": first_ts, "last_ts": last_ts,
            "first": datefmt(first_ts), "last": datefmt(last_ts)}


# ------------------------------------------------------------------- consegne

def scan_consegne(chains):
    """Registro delle consegne della finestra, per le metriche di attrito.

    Per ogni file consegnato (percorso relativo alla cartella progetti): quante
    sessioni lo hanno scritto, se è stato CORRETTO IN CHAT (riscritto nella
    stessa sessione dopo un messaggio successivo alla prima stesura), se è
    stato rilavorato (riscritto in un'altra sessione entro la soglia), se è
    stato corretto A MANO (mtime del file oltre l'ultima scrittura di Claude +
    margine) e se risulta riaperto dopo la consegna (data di ultimo uso di
    Spotlight, solo macOS). Solo percorsi e conteggi.
    """
    margine = config.C.th["margine_scrittura_sec"]
    rilav_sec = config.C.th["rilavorazione_giorni"] * 86400
    files = {}
    for i, s in enumerate(chains):
        for o in s.get("outs") or []:
            if len(o) < 4:
                continue
            rel, ts = o[2], o[3] or s.get("mtime")
            f = files.setdefault(rel, {"proj": o[0], "ext": o[1],
                                       "writes": [], "sess": set(),
                                       "rounds": {}})
            f["writes"].append(ts)
            f["sess"].add(i)
            if len(o) > 4:
                f["rounds"].setdefault(i, set()).add(o[4])

    out = []
    mdls_left = 80   # tetto alle chiamate Spotlight: oltre, salto il controllo
    for rel, f in files.items():
        first_w, last_w = min(f["writes"]), max(f["writes"])
        span = last_w - first_w
        # scritture in round diversi della stessa sessione = prima stesura
        # corretta (o integrata) su richiesta in chat
        chat = any(len(r) >= 2 for r in f["rounds"].values())
        rilavorata = len(f["sess"]) >= 2 and margine <= span <= rilav_sec
        manuale = rivista = None
        path = config.C.projects_dir / rel if config.C.projects_dir else None
        if path:
            try:
                manuale = path.stat().st_mtime > last_w + margine
            except OSError:
                pass   # file spostato o rinominato: nessun verdetto
        if manuale is not None and mdls_left > 0 and sys.platform == "darwin":
            mdls_left -= 1
            try:
                import subprocess
                r = subprocess.run(
                    ["mdls", "-name", "kMDItemLastUsedDate", "-raw", str(path)],
                    capture_output=True, text=True, timeout=5)
                raw = r.stdout.strip()
                if raw and raw != "(null)":
                    used = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S %z").timestamp()
                    rivista = used > last_w
            except Exception:
                pass
        out.append({"rel": rel, "proj": f["proj"], "ext": f["ext"],
                    "first_ts": first_w, "last_ts": last_w,
                    "sessions": len(f["sess"]), "chat": chat,
                    "rilavorata": rilavorata,
                    "manuale": manuale, "rivista": rivista})
    return out


# ---------------------------------------------------------------------- alert

PESO_ALERT = {"critical": 40, "serious": 25, "warning": 10}
PESO_AUTH_USATO = 15


def build_alerts(skills, anomalies, strays, projects, mcp, checks, mcp_use):
    """Due liste: 'riparare' (tocca la salute, con peso per la formula Strumenti)
    e 'decidere' (pulizia: aspetta solo una scelta, non penalizza la salute)."""
    riparare, decidere = [], []
    gg = config.C.window_days

    # Servizi MCP da cui dipende un flusso codificato (dalle mappe curate del
    # config): una loro auth scaduta È un problema anche con uso recente zero.
    flow_deps = {d[4:] for phs in flows.flow_phases().values()
                 for ph in phs for d in ph.get("detect", []) if d.startswith("mcp:")}

    def rip(level, text, peso=None):
        riparare.append({"level": level, "text": text,
                         "peso": peso if peso is not None else PESO_ALERT[level]})

    for s in skills:
        if s["symlink"] in ("mancante", "altrove", "dir reale"):
            rip("serious", f"Skill «{s['name']}»: symlink in ~/.claude/skills "
                           f"{s['symlink']} — la skill è invisibile o incoerente")
        if not s["indexed"]:
            rip("warning", f"Skill «{s['name']}» non è nell'indice del workspace")
    for name in strays:
        rip("warning", f"«{name}» in ~/.claude/skills è una cartella reale fuori "
                       "dalla cartella skill del workspace — da migrare")
    for name in anomalies:
        rip("warning", f"«{name}» tra le skill non è una skill (manca SKILL.md)")
    for p in projects:
        if not p["claude_md"]:
            rip("warning", f"Progetto «{p['name']}» senza CLAUDE.md")
        if not p["indexed"]:
            rip("warning", f"Progetto «{p['name']}» non è linkato nell'indice")
    for srv in mcp:
        usato = mcp_use.get(srv.get("ukey", ""), 0) > 0
        dipendenza = srv.get("ukey", "") in flow_deps
        if srv.get("problem"):
            rip("serious", f"MCP «{srv['name']}» ({srv['scope']}): {srv['problem']}")
        if srv.get("status") == "riautorizzare":
            if usato or dipendenza:
                perche = "usato di recente" if usato else "un flusso ne dipende"
                rip("warning", f"MCP «{srv['name']}»: autorizzazione scaduta e {perche} "
                               "— riattivala con /mcp o dalle impostazioni claude.ai",
                    peso=PESO_AUTH_USATO)
            else:
                decidere.append({"text": f"«{srv['name']}»: autorizzazione scaduta e mai "
                                         f"usato in {gg} giorni — collegalo o scollegalo"})
        elif srv.get("scope") == "account claude.ai" and not usato:
            decidere.append({"text": f"«{srv['name']}»: collegato ma mai usato in "
                                     f"{gg} giorni — serve davvero?"})
    for c in checks:
        if c["level"] != "good":
            rip(c["level"] if c["level"] in PESO_ALERT else "warning",
                f"Controllo «{c['name']}»: {c['outcome']}")

    riparare.sort(key=lambda a: -a["peso"])
    return {"riparare": riparare, "decidere": decidere}


# ----------------------------------------------------------------------- main

def main():
    indexed_skills, indexed_projects = parse_index()
    skills, anomalies = scan_skills(indexed_skills)
    official, strays, repo_links = scan_home_skills()
    projects = scan_projects(indexed_projects)
    mcp = scan_mcp()
    plugins = scan_plugins()
    checks = scan_checks()
    use_skills, use_mcp, sessions, chains = scan_usage()
    use_skill_map = {u["name"]: u["n"] for u in use_skills}
    mcp_use = {u["name"]: u["n"] for u in use_mcp}
    flussi = flows.build(chains)
    orchestrators = scan_orchestrators(skills, chains)
    telemetry = scan_telemetry()
    consegne = scan_consegne(chains)
    rate, rate_date = get_rate() if telemetry else (None, "")
    alerts = build_alerts(skills, anomalies, strays, projects, mcp, checks, mcp_use)

    # sonda telemetria spenta? Le sessioni continuano ma i costi non arrivano più
    if telemetry and chains:
        ultimo_diario = max(s["mtime"] for s in chains)
        if ultimo_diario - telemetry["last_ts"] > 2 * 86400:
            alerts["riparare"].append({
                "level": "warning", "peso": PESO_ALERT["warning"],
                "text": "Sonda telemetria spenta: nessun dato di costo da "
                        f"{telemetry['last']} ma le sessioni continuano — "
                        + config.C.telemetry_restart_hint})
            alerts["riparare"].sort(key=lambda a: -a["peso"])

    met = metrics.compute(
        chains, projects, skills, use_skill_map, alerts["riparare"],
        {o["name"] for o in orchestrators["existing"]},
        flussi.get("agent_candidates", []), orchestrators["candidates"],
        consegne, telemetry, rate, rate_date)

    # Consigli per la sezione dedicata: dinamici (nascono dai dati di oggi,
    # marcati "ora") + sempre validi. Ogni voce ha i tag per il filtro.
    gg = config.C.window_days
    advice = []

    def tip(tags, text, ora=False):
        advice.append({"tags": tags, "text": text, "ora": ora})

    for a in alerts["riparare"]:
        t = a["text"]
        tags = (["MCP"] if t.startswith(("MCP", "Sonda")) else
                ["Skill"] if t.startswith("Skill") else
                ["Claude Code"] if t.startswith("Controllo") else ["Pulizia"])
        tip(tags, t, ora=True)
    for a in alerts["decidere"]:
        tip(["Pulizia", "MCP"], a["text"], ora=True)
    mcard = met["metodo_card"]
    if mcard["ferme"]:
        tip(["Skill", "Metodo"],
            f"Automazioni ferme (0 usi in {gg} giorni): " + ", ".join(mcard["ferme"]) +
            ". Rilanciale con un caso reale o archiviale.", ora=True)
    for v in mcard["vivaio"]:
        es = f", es. «{v['files'][0]}»" if v.get("files") else ""
        tip(["Skill", "Metodo"],
            f"Lavoro libero ricorrente ({v['label']}, {v['count']} volte{es}): maturo "
            "per diventare una skill — dillo in chat e la costruiamo.", ora=True)
    for a in flussi.get("agent_candidates", []):
        tip(["Agenti"],
            f"«{a['phase']}» in {a['family']}: {a['detail']}, senza un tuo checkpoint — "
            "una fase così può girare da sola come agente in background.", ora=True)
    for c in orchestrators["candidates"]:
        tip(["Skill", "Metodo"], "Orchestratore da creare: " + c, ora=True)
    att = met.get("attrito") or {}
    au = att.get("autonomia") or {}
    if au.get("pct") is not None and au["pct"] < 50 and au.get("tot", 0) >= 3:
        tip(["MCP", "Consumi"],
            f"Autonomia dati al {au['pct']}%: più della metà delle sessioni con dati "
            "esterni parte da file esportati a mano. Guarda quale fonte importi più "
            "spesso: un MCP (o uno script) su quella elimina il copia-incolla.",
            ora=True)
    co = att.get("conformita") or {}
    if co.get("pct") is not None and co["pct"] < 60 and co.get("tot", 0) >= 5:
        tip(["Metodo"],
            f"Prima stesura buona al {co['pct']}%: quasi metà delle consegne non esce "
            "definitiva al primo colpo. Se le correzioni in chat si somigliano, mettile "
            "nel CLAUDE.md del progetto o nella skill: costano meno che ripeterle.",
            ora=True)
    for s in att.get("sanguisughe") or []:
        tip(["Consumi", "Metodo"],
            f"Sessione sanguisuga il {s['date']} ({s['label']}): diario pesante, "
            f"{s['giri']} giri di richieste e nessuna consegna. Se il lavoro rinasce, "
            "spezzalo in sessioni più piccole o passa da una skill.", ora=True)
    freddo_gg = config.C.th["freddo_giorni"]
    for p in projects:
        soglia = config.C.cadenza_clienti.get(p["name"], freddo_gg)
        if p["name"] != config.C.hub and (p["days_since"] is None
                                          or p["days_since"] > soglia):
            giorni = p["days_since"] if p["days_since"] is not None else f"{gg}+"
            tip(["Clienti"],
                f"{p['name']} è fermo da {giorni} giorni (cadenza attesa: "
                f"≤{soglia}gg): una call o un contenuto questa settimana vale "
                "più di qualsiasi metrica.", ora=True)
    for tags, text in [
        (["Claude Code", "Consumi"],
         "Una sessione = un lavoro: apri una sessione nuova per ogni lavoro diverso "
         "invece di allungarne una sola. Contesti più precisi, diari più leggeri, "
         "consumi più bassi."),
        (["Claude Code", "Skill"],
         "Dopo aver creato o modificato una skill o un MCP, ricarica la sessione: "
         "gli strumenti nuovi compaiono solo da lì."),
        (["Metodo"],
         "Se ti accorgi che stai rifacendo a mano un lavoro già codificato, invoca "
         "la skill: la Quota di metodo misura proprio questa abitudine."),
        (["Consumi"],
         "Per i file grandi (trascrizioni, CSV) passa il percorso del file invece di "
         "incollarne il contenuto in chat: Claude lo legge a pezzi e il contesto "
         "resta leggero."),
        (["Agenti"],
         "Gli agenti servono per le fasi pesanti e ripetitive senza tue decisioni in "
         "mezzo; i checkpoint (ok al titolo, ok al brief) restano tuoi: non delegarli."),
        (["MCP", "Claude Code"],
         "/mcp in sessione mostra lo stato vero dei connettori; la dashboard legge le "
         "cache locali."),
        (["Metodo", "Claude Code"],
         "Chiudi la settimana con «aggiorna la dashboard»: alimenta lo storico e ti "
         "consegna la fotografia del venerdì."),
    ]:
        tip(tags, text)

    n_riparare = len(alerts["riparare"])
    data = {
        "generated": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "window_days": gg,
        "window_from": datefmt(time.time() - gg * 86400),
        "window_to": datefmt(time.time()),
        "brand": config.C.brand,
        "hub": config.C.hub,
        "inventory": {
            "skills": len(skills),
            "projects": len(projects),
            "mcp": len(mcp),
            "sessions": sessions,
        },
        "decision_tiles": met["tiles"],
        "metodo": met["metodo_card"],
        "attrito": met.get("attrito"),
        "telemetry": ({"cost": telemetry["cost"], "requests": telemetry["requests"],
                       "tok": telemetry["tok"], "first": telemetry["first"],
                       "last": telemetry["last"],
                       "cost_eur": (round(telemetry["cost"] * rate, 2)
                                    if rate else None),
                       "rate_date": rate_date} if telemetry else None),
        "storico": metrics.load_storico()["days"],
        "trend": met["trend"],
        "advice": advice,
        "alerts": alerts,
        "skills": [{k: v for k, v in s.items() if k != "dir"} for s in skills],
        "home_extra": {
            "official": official,
            "repo_links": repo_links,
            "anomalies": anomalies,
        },
        "projects": projects,
        "mcp": mcp,
        "plugins": plugins,
        "checks": checks,
        "flows": flussi,
        "orchestrators": orchestrators,
        "usage": {"skills": use_skills, "mcp": use_mcp, "sessions": sessions},
        "limits": [
            "Connettori claude.ai e stati di autorizzazione letti dalle cache locali di Claude Code: il dettaglio live è in /mcp.",
            f"Uso e flussi leggono i diari di sessione conservati sul computer (~{gg} giorni): fasi, conteggi e destinazioni — mai i contenuti.",
            "Token e costi vengono dalla telemetria locale (opzionale): coprono solo il periodo in cui la sonda è accesa; prima vale il proxy in KB.",
            "Giri di revisione e correzioni sono stime dai metadati (ordine di messaggi e scritture, date dei file): indicano la tendenza, non i minuti esatti.",
            "La «prima stesura buona» non vede le modifiche fatte su copie caricate nel cloud (es. Google Drive) prima della condivisione.",
        ],
    }

    config.C.out_json.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                                 encoding="utf-8")

    if TEMPLATE.exists():
        html = read_text(TEMPLATE).replace("__DATA__", json.dumps(data, ensure_ascii=False))
        config.C.out_html.write_text(html, encoding="utf-8")
        html_note = str(config.C.out_html)
    else:
        html_note = "template.html mancante: solo data.json"

    salute_txt = f"{met['salute']}/100" if met["salute"] is not None \
        else "— (arriva con le prime sessioni)"
    print(f"Scan ok {data['generated']} — Indice operativo {salute_txt} · "
          f"{len(skills)} skill, {len(projects)} progetti, {len(mcp)} MCP, "
          f"{n_riparare} da riparare, {len(alerts['decidere'])} da decidere. "
          f"HTML: {html_note}")

    if "--dump" in sys.argv:
        print("\n-- METRICHE --")
        for t in met["tiles"]:
            print(f"  {t['label']:22} {t['value']}{t['unit']}  ({t['note']})")
        print("\n-- DA RIPARARE --")
        for a in alerts["riparare"]:
            print(f"  [{a['level']} −{a['peso']}] {a['text']}")
        print("\n-- DA DECIDERE --")
        for a in alerts["decidere"]:
            print(f"  {a['text']}")
        print("\n-- SKILL --")
        for s in skills:
            print(f"  {s['name']:38} symlink={s['symlink']:9} indice={'sì' if s['indexed'] else 'NO'}")
        print("\n-- PROGETTI --")
        for p in projects:
            print(f"  {p['name']:38} CLAUDE.md={'sì' if p['claude_md'] else 'NO'} "
                  f"indice={'sì' if p['indexed'] else 'NO'} mod7={p['mod7']} mod30={p['mod30']}")
        print("\n-- MCP --")
        for m in mcp:
            print(f"  {m['name']:22} {m['type']:6} scope={m['scope']} env={','.join(m['env']) or '-'} {m.get('problem','')}")
        print("\n-- FLUSSI --")
        for fam in flussi["families"]:
            catena = " > ".join(
                s["label"] + (f" ({int(s['share'] * 100)}%)"
                              if s["share"] is not None and s["share"] < 1 else "")
                for s in fam["steps"])
            print(f"  {fam['name']}  ×{fam['sessions']}")
            print(f"     {catena}")
            if fam["deliver"]:
                print(f"     consegne: {fam['deliver']}")
        if flussi["chat_sessions"]:
            print(f"  (+{flussi['chat_sessions']} sessioni di sola conversazione)")
        print("\n-- USO SKILL --")
        for u in use_skills:
            print(f"  {u['n']:4}  {u['name']}")
        print("\n-- USO MCP --")
        for u in use_mcp:
            print(f"  {u['n']:4}  {u['name']}")


def _config_path():
    if "--config" in sys.argv:
        i = sys.argv.index("--config")
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        raise SystemExit("--config richiede un percorso")
    return os.environ.get("STUDIO_DASHBOARD_CONFIG", "config.json")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        import demo
        demo.main()
    else:
        config.init(_config_path())
        main()
