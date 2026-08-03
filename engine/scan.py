#!/usr/bin/env python3
"""
Dashboard Workspace — scanner locale.

Fotografa il workspace di Salvatore (skill, progetti, MCP, plugin, uso recente)
e produce due file accanto a questo script:

  data.json       — snapshot in JSON: solo nomi, conteggi e date. Nessun segreto,
                    nessun contenuto di file, nessun testo delle call.
  dashboard.html  — pagina pronta da pubblicare come Artifact (template.html + dati).

Uso:
  python3 scan.py           # scansiona e scrive i due file
  python3 scan.py --dump    # come sopra, ma stampa anche un riepilogo leggibile
  python3 scan.py --demo    # NON scansiona: renderizza dashboard-demo.html con
                            # dati di fantasia (demo.py) per gli screenshot social.
                            # Non tocca data.json, storico, cache né l'Artifact.

Zero dipendenze: solo libreria standard. La scansione dei transcript usa una
cache incrementale (.cache.json) per restare veloce.
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

import flows    # modulo accanto a questo script: estrazione flussi dai diari
import metrics  # metriche decisionali, storico e serie per il grafico

WS = Path("/Users/salvatore/Documents/Claude")
SKILL_DIR = WS / "SKILL"
PROJECTS_DIR = WS / "Projects"
CLAUDE_MD = WS / "CLAUDE.md"
HOME_SKILLS = Path.home() / ".claude" / "skills"
TRANSCRIPTS_ROOT = Path.home() / ".claude" / "projects"
TRANSCRIPT_PREFIX = "-Users-salvatore-Documents-Claude"
PLUGINS_JSON = Path.home() / ".claude" / "plugins" / "installed_plugins.json"

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "template.html"
OUT_JSON = HERE / "data.json"
OUT_HTML = HERE / "dashboard.html"
CACHE = HERE / ".cache.json"

WINDOW_DAYS = 30
ATP_DIR = WS / "mcp" / "answerthepublic"
MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
        "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]

SKILL_AREAS = {
    "SEO & Contenuti": [
        "articolo-seo-da-keyword",
        "seo-copywriter",
        "aeo-geo-italia",
        "articolo-blog-personale",
    ],
    "YouTube & Video": [
        "youtube-title-description",
        "youtube-tag-optimizer",
        "youtube-transcript-downloader",
        "filiera-video-articolo",
    ],
    "Personal Brand": [
        "estrai-spunti-personal-brand",
        "approfondisci-spunti-personal-brand",
    ],
    "Ricerca & Competitor": [
        "ubersuggest-keyword-analyzer",
        "deep-research-multitool",
        "analisi-landing-page",
        "elenco-landing-page-partner",
    ],
    "Operatività cliente": [
        "piano-operativo-da-call",
    ],
    "Deliverable & Dashboard": [
        "deck-brandizzato-cliente",
        "dashboard-cliente",
        "dashboard-workspace",
    ],
}

RE_LOG_ENTRY = re.compile(r'^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\] \(exit (\d+)\)')


def datefmt(ts):
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%d/%m/%Y")


def read_text(p):
    return p.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- indice CLAUDE.md

def parse_index():
    """Slug delle skill e nomi dei progetti linkati nell'indice CLAUDE.md."""
    if not CLAUDE_MD.exists():
        return set(), set()
    md = read_text(CLAUDE_MD)
    skills = {unquote(m) for m in re.findall(r'\(SKILL/([^/)]+)/SKILL\.md\)', md)}
    projects = {unquote(m) for m in re.findall(r'\(Projects/([^/)]+)/CLAUDE\.md\)', md)}
    return skills, projects


# ---------------------------------------------------------------------- skill

def scan_skills(indexed_slugs):
    rows, anomalies = [], []
    if not SKILL_DIR.is_dir():
        return rows, ["Cartella SKILL/ non trovata"]
    for d in sorted(SKILL_DIR.iterdir(), key=lambda p: p.name.lower()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        if not (d / "SKILL.md").exists():
            anomalies.append(d.name)
            continue
        link = HOME_SKILLS / d.name
        if link.is_symlink():
            symlink = "ok" if Path(os.path.realpath(link)) == d.resolve() else "altrove"
        elif link.exists():
            symlink = "dir reale"
        else:
            symlink = "mancante"
        try:
            mtime = max(
                (f.stat().st_mtime for f in d.rglob("*") if f.is_file()),
                default=d.stat().st_mtime,
            )
        except OSError:
            mtime = d.stat().st_mtime
        try:
            created_ts = d.stat().st_birthtime
        except (OSError, AttributeError):
            created_ts = d.stat().st_mtime
        rows.append({
            "name": d.name,
            "symlink": symlink,
            "indexed": d.name in indexed_slugs,
            "updated": datefmt(mtime),
            "created_ts": created_ts,
        })
    return rows, anomalies


def scan_home_skills():
    """Voci di ~/.claude/skills che non sono symlink verso SKILL/<nome>.

    - dir di sola lettura  -> skill ufficiali installate (informativo)
    - dir scrivibili       -> skill "fuori posto" rispetto alla convenzione SKILL/
    - symlink verso il repo dentro SKILL/ -> collegate da repo (informativo)
    """
    official, strays, repo_links = [], [], []
    if not HOME_SKILLS.is_dir():
        return official, strays, repo_links
    for e in sorted(HOME_SKILLS.iterdir(), key=lambda p: p.name.lower()):
        if e.name.startswith("."):
            continue
        if e.is_symlink():
            target = Path(os.path.realpath(e))
            direct = SKILL_DIR / e.name
            if direct.exists() and target == direct.resolve():
                continue  # già coperta da scan_skills
            if str(target).startswith(str(SKILL_DIR)):
                repo_links.append(e.name)
            elif not target.exists():
                strays.append(e.name + " (symlink rotto)")
            else:
                repo_links.append(e.name)
            continue
        if e.is_dir():
            (strays if os.access(e, os.W_OK) else official).append(e.name)
    return official, strays, repo_links


# -------------------------------------------------------------------- progetti

def scan_projects(indexed_names, chains=None):
    now = time.time()
    chains = chains or []
    project_divisions = {}
    for s in chains:
        for out in s.get("outs") or []:
            project_divisions.setdefault(out[0], set()).update(s.get("divisions") or [])
    rows = []
    if not PROJECTS_DIR.is_dir():
        return rows
    for d in sorted(PROJECTS_DIR.iterdir(), key=lambda p: p.name.lower()):
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
            "indexed": d.name in indexed_names,
            "mod7": mod7,
            "mod30": mod30,
            "last_file": latest_name or "—",
            "last_date": datefmt(latest_ts),
            "days_since": int((now - latest_ts) // 86400) if latest_ts else None,
            "divisions": sorted(project_divisions.get(d.name, set())),
        })
    return rows


# ------------------------------------------------------------------- MCP e plugin

CLAUDEAI_SKIP = {"claude.ai Claude Code Remote"}  # canale interno, non un servizio


def scan_mcp():
    """Inventario MCP completo, da tre fonti locali:

    1. i file .mcp.json (workspace e progetti) — server configurati a mano;
    2. ~/.claude.json — server a scope utente + connettori claude.ai già collegati;
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

    load(WS / ".mcp.json", "workspace")
    if PROJECTS_DIR.is_dir():
        for d in sorted(PROJECTS_DIR.iterdir()):
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
    """Le "sveglie" del workspace: i controlli periodici registrati e il loro esito.

    Ogni voce dice: cosa controlla, ogni quanto, com'è andata l'ultima volta e
    quando riparte. Le sveglie nuove si aggiungono qui.
    """
    checks = []

    # 1) Spec API AnswerThePublic — mensile, lanciata dall'hook a inizio sessione.
    #    Esiti nel log: 0 = invariato, 10 = API cambiata, 1 = errore di rete.
    last_run, last_code = "", None
    log = ATP_DIR / "spec_watch.log"
    if log.exists():
        for line in read_text(log).splitlines():
            m = RE_LOG_ENTRY.match(line)
            if m:
                d = m.group(1)
                last_run = f"{d[8:10]}/{d[5:7]}/{d[0:4]} {d[11:]}"
                last_code = int(m.group(2))
    level, outcome = {
        0: ("good", "ok — nessun cambiamento"),
        10: ("warning", "API cambiata: server.py da rivedere"),
        1: ("serious", "errore di rete: riprova alla prossima sessione"),
    }.get(last_code, ("warning", "mai eseguita"))
    now = datetime.now()
    stamp = ATP_DIR / ".last_spec_check"
    done_this_month = stamp.exists() and stamp.read_text().strip() == now.strftime("%Y-%m")
    next_due = f"prima sessione di {MESI[now.month % 12]}" if done_this_month \
        else "alla prossima sessione"
    checks.append({
        "name": "Spec API AnswerThePublic",
        "what": "l'API su cui poggia il server MCP è cambiata?",
        "cadence": "mensile (a inizio sessione)",
        "last_run": last_run or "—",
        "level": level,
        "outcome": outcome,
        "next": next_due,
    })
    return checks


# ---------------------------------------------------------------- orchestratori

ORCH_KEYWORDS = ("orchestr", "concaten", "filiera")


def scan_orchestrators(skill_names, chains):
    """Le skill-regia e i candidati suggeriti dai dati.

    Regia = skill il cui SKILL.md dichiara l'orchestrazione (parole chiave) e cita
    almeno 2 altre skill. Candidato = coppia di skill usate insieme in più sessioni
    senza che nessuna regia fosse in campo.
    """
    existing = []
    for name in skill_names:
        try:
            text = read_text(SKILL_DIR / name / "SKILL.md")[:8000].lower()
        except OSError:
            continue
        if not any(k in text for k in ORCH_KEYWORDS):
            continue
        refs = sorted((text.find(o), o) for o in skill_names if o != name and o in text)
        refs = [o for _, o in refs]
        if len(refs) >= 2:
            existing.append({"name": name,
                             "purpose": flows.FLOW_NAMES.get(name, ""),
                             "chains": refs[:6]})
    orch_names = {o["name"] for o in existing}
    pairs = {}
    for s in chains:
        used = sorted({ev[6:] for ev in s["chain"] if ev.startswith("skill:")
                       and ev[6:] not in flows.UTILITY_SKILLS})
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


# ------------------------------------------------------------------ uso (transcript)

def load_cache():
    try:
        cache = json.loads(read_text(CACHE))
    except Exception:
        cache = {}
    if cache.get("version") != 5:
        cache = {"version": 5, "files": {}}
    return cache


def scan_usage():
    """Legge i diari di sessione (con cache per mtime+size) e ritorna, sulla
    finestra di WINDOW_DAYS giorni: top skill, top MCP, n. sessioni e le
    catene-di-flusso per le mappe. L'estrazione vera è in flows.extract_session."""
    cache = load_cache()
    files_cache = cache["files"]
    cutoff = time.time() - WINDOW_DAYS * 86400
    skills, mcp = {}, {}
    sessions = 0
    chains = []

    dirs = []
    if TRANSCRIPTS_ROOT.is_dir():
        dirs = [p for p in TRANSCRIPTS_ROOT.iterdir()
                if p.is_dir() and p.name.startswith(TRANSCRIPT_PREFIX)]

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
        CACHE.write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        pass

    top = lambda d: [{"name": k, "n": v}
                     for k, v in sorted(d.items(), key=lambda kv: -kv[1])][:15]
    return top(skills), top(mcp), sessions, chains


# ------------------------------------------------------------------ telemetria

# La sonda vive in ~/.claude/otel (fuori da Documents: launchd non può leggere
# le cartelle protette dalla privacy di macOS). Lo scanner, che gira in sessione,
# può leggere entrambe le posizioni.
OTEL_DIR = Path.home() / ".claude" / "otel"
OTEL_LOGS = [OTEL_DIR / "otel_probe.1.jsonl", OTEL_DIR / "otel_probe.jsonl"]
EUR_CACHE = HERE / ".eur_rate.json"


def get_eur_rate():
    """Cambio USD→EUR del giorno (dati BCE via frankfurter.app). La telemetria
    riporta i costi solo in dollari: la conversione è di cortesia. Se la rete
    manca si riusa l'ultimo cambio salvato; senza mai un cambio, si resta in $."""
    saved = {}
    try:
        saved = json.loads(read_text(EUR_CACHE))
    except Exception:
        pass
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://api.frankfurter.app/latest?from=USD&to=EUR",
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
        with urllib.request.urlopen(req, timeout=4) as r:
            j = json.load(r)
        saved = {"rate": float(j["rates"]["EUR"]), "date": j.get("date", "")}
        EUR_CACHE.write_text(json.dumps(saved), encoding="utf-8")
    except Exception:
        pass
    return saved.get("rate"), saved.get("date", "")


def scan_telemetry():
    """Aggrega la telemetria locale (~/.claude/otel/otel_probe.jsonl, evento
    api_request) sulla finestra: token e costo reali per il periodo coperto.
    Ritorna None se la sonda non ha ancora prodotto nulla."""
    cutoff = time.time() - WINDOW_DAYS * 86400
    cost = 0.0
    tok = {"in": 0, "out": 0, "cache": 0}
    n = 0
    first_ts = last_ts = None
    for p in OTEL_LOGS:
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

MARGINE_SCRITTURA = 3600   # 1h: oltre, una modifica al file è considerata tua
RILAV_GIORNI = 7           # riscrittura in altra sessione entro 7gg = rilavorata


def scan_consegne(chains):
    """Registro delle consegne della finestra, per le metriche di attrito.

    Per ogni file consegnato (percorso relativo a Projects/): quante sessioni
    lo hanno scritto, se è stato CORRETTO IN CHAT (riscritto nella stessa
    sessione dopo un tuo messaggio successivo alla prima stesura), se è stato
    rilavorato (riscritto in un'altra sessione entro RILAV_GIORNI), se è stato
    corretto A MANO (mtime del file oltre l'ultima scrittura di Claude +
    margine) e se risulta riaperto dopo la consegna (data di ultimo uso di
    Spotlight). Solo percorsi e conteggi.
    """
    files = {}
    for i, s in enumerate(chains):
        for o in s.get("outs") or []:
            if len(o) < 4:
                continue
            rel, ts = o[2], o[3] or s.get("mtime")
            f = files.setdefault(rel, {"proj": o[0], "ext": o[1],
                                       "writes": [], "sess": set(),
                                       "rounds": {}, "divisions": set()})
            f["divisions"].update(s.get("divisions") or [])
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
        # corretta (o integrata) su tua richiesta in chat
        chat = any(len(r) >= 2 for r in f["rounds"].values())
        rilavorata = (len(f["sess"]) >= 2 and MARGINE_SCRITTURA <= span
                      <= RILAV_GIORNI * 86400)
        manuale = rivista = None
        path = PROJECTS_DIR / rel
        try:
            manuale = path.stat().st_mtime > last_w + MARGINE_SCRITTURA
        except OSError:
            pass   # file spostato o rinominato: nessun verdetto
        if manuale is not None and mdls_left > 0:
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
                    "divisions": sorted(f["divisions"]),
                    "first_ts": first_w, "last_ts": last_w,
                    "sessions": len(f["sess"]), "chat": chat,
                    "rilavorata": rilavorata,
                    "manuale": manuale, "rivista": rivista})
    return out


# ---------------------------------------------------------------------- alert

# Servizi MCP da cui dipende un flusso codificato (derivati dalle mappe curate):
# una loro auth scaduta È un problema di salute anche se l'uso recente è zero.
FLOW_DEPS = {d[4:] for phs in flows.FLOW_PHASES.values()
             for ph in phs for d in ph.get("detect", []) if d.startswith("mcp:")}

PESO_ALERT = {"critical": 40, "serious": 25, "warning": 10}
PESO_AUTH_USATO = 15


def build_alerts(skills, anomalies, strays, projects, mcp, checks, mcp_use):
    """Due liste: 'riparare' (tocca la salute, con peso per la formula Strumenti)
    e 'decidere' (pulizia: aspetta solo una scelta, non penalizza la salute)."""
    riparare, decidere = [], []
    mcp_divisions = {}
    for key, phases in flows.FLOW_PHASES.items():
        for phase in phases:
            for detect in phase.get("detect", []):
                if detect.startswith("mcp:"):
                    mcp_divisions.setdefault(detect[4:], set()).update(
                        flows._tags_for(key))

    def rip(level, text, peso=None, skill=None, project=None, divisions=None):
        riparare.append({"level": level, "text": text,
                         "peso": peso if peso is not None else PESO_ALERT[level],
                         "skill": skill, "project": project,
                         "divisions": sorted(set(divisions or []))})

    for s in skills:
        if s["symlink"] in ("mancante", "altrove", "dir reale"):
            rip("serious", f"Skill «{s['name']}»: symlink in ~/.claude/skills "
                           f"{s['symlink']} — la skill è invisibile o incoerente",
                skill=s["name"], divisions=flows._tags_for(s["name"]))
        if not s["indexed"]:
            rip("warning", f"Skill «{s['name']}» non è nell'indice di CLAUDE.md",
                skill=s["name"], divisions=flows._tags_for(s["name"]))
    for name in strays:
        rip("warning", f"«{name}» in ~/.claude/skills è una cartella reale fuori da SKILL/ — da migrare")
    for name in anomalies:
        rip("warning", f"«{name}» in SKILL/ non è una skill (manca SKILL.md)")
    for p in projects:
        if not p["claude_md"]:
            rip("warning", f"Progetto «{p['name']}» senza CLAUDE.md",
                project=p["name"], divisions=p.get("divisions"))
        if not p["indexed"]:
            rip("warning", f"Progetto «{p['name']}» non è linkato in CLAUDE.md",
                project=p["name"], divisions=p.get("divisions"))
    for srv in mcp:
        usato = mcp_use.get(srv.get("ukey", ""), 0) > 0
        dipendenza = srv.get("ukey", "") in FLOW_DEPS
        if srv.get("problem"):
            rip("serious", f"MCP «{srv['name']}» ({srv['scope']}): {srv['problem']}",
                divisions=mcp_divisions.get(srv.get("ukey", "")))
        if srv.get("status") == "riautorizzare":
            if usato or dipendenza:
                perche = "usato di recente" if usato else "un flusso ne dipende"
                rip("warning", f"MCP «{srv['name']}»: autorizzazione scaduta e {perche} "
                               "— riattivala con /mcp o dalle impostazioni claude.ai",
                    peso=PESO_AUTH_USATO,
                    divisions=mcp_divisions.get(srv.get("ukey", "")))
            else:
                decidere.append({"text": f"«{srv['name']}»: autorizzazione scaduta e mai "
                                         "usato in 30 giorni — collegalo o scollegalo",
                                  "skill": None, "project": None,
                                  "divisions": sorted(mcp_divisions.get(
                                      srv.get("ukey", ""), set()))})
        elif srv.get("scope") == "account claude.ai" and not usato:
            decidere.append({"text": f"«{srv['name']}»: collegato ma mai usato in "
                                     "30 giorni — serve davvero?",
                              "skill": None, "project": None,
                              "divisions": sorted(mcp_divisions.get(
                                  srv.get("ukey", ""), set()))})
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
    projects = None
    mcp = scan_mcp()
    plugins = scan_plugins()
    checks = scan_checks()
    use_skills, use_mcp, sessions, chains = scan_usage()
    flows.assign_session_divisions(chains)
    projects = scan_projects(indexed_projects, chains)
    use_skill_map = {u["name"]: u["n"] for u in use_skills}
    mcp_use = {u["name"]: u["n"] for u in use_mcp}
    flussi = flows.build(chains)
    orchestrators = scan_orchestrators([s["name"] for s in skills], chains)
    telemetry = scan_telemetry()
    consegne = scan_consegne(chains)
    eur_rate, eur_date = get_eur_rate() if telemetry else (None, "")
    alerts = build_alerts(skills, anomalies, strays, projects, mcp, checks, mcp_use)

    # sonda telemetria spenta? Le sessioni continuano ma i costi non arrivano più
    if telemetry and chains:
        ultimo_diario = max(s["mtime"] for s in chains)
        if ultimo_diario - telemetry["last_ts"] > 2 * 86400:
            alerts["riparare"].append({
                "level": "warning", "peso": PESO_ALERT["warning"],
                "skill": None, "project": None, "divisions": [],
                "text": "Sonda telemetria spenta: nessun dato di costo da "
                        f"{telemetry['last']} ma le sessioni continuano — riavvia il "
                        "LaunchAgent: launchctl load ~/Library/LaunchAgents/"
                        "com.salvatore.claude-otel-probe.plist"})
            alerts["riparare"].sort(key=lambda a: -a["peso"])

    met = metrics.compute(
        chains, projects, skills, use_skill_map, alerts["riparare"],
        {o["name"] for o in orchestrators["existing"]},
        flussi.get("agent_candidates", []), orchestrators["candidates"],
        consegne, telemetry, eur_rate, eur_date)

    # Consigli per la sezione dedicata: dinamici (nascono dai dati di oggi,
    # marcati "ora") + sempre validi. Ogni voce ha i tag per il filtro.
    advice = []

    def tip(tags, text, ora=False):
        advice.append({"tags": tags, "text": text, "ora": ora})

    for a in alerts["riparare"]:
        t = a["text"]
        tags = (["MCP"] if t.startswith("MCP") else
                ["Skill"] if t.startswith("Skill") else
                ["Claude Code"] if t.startswith("Controllo") else ["Pulizia"])
        tip(tags, t, ora=True)
    for a in alerts["decidere"]:
        tip(["Pulizia", "MCP"], a["text"], ora=True)
    mcard = met["metodo_card"]
    if mcard["ferme"]:
        tip(["Skill", "Metodo"],
            "Automazioni ferme (0 usi in 30 giorni): " + ", ".join(mcard["ferme"]) +
            ". Rilanciale con un caso reale o archiviale. La prima candidata è la "
            "pipeline personal brand: di' sì alla domanda a fine piano operativo.",
            ora=True)
    for v in mcard["vivaio"]:
        es = f", es. «{v['files'][0]}»" if v.get("files") else ""
        tip(["Skill", "Metodo"],
            f"Lavoro libero ricorrente ({v['label']}, {v['count']} volte{es}): maturo "
            "per diventare una skill — dillo in chat e la costruiamo.", ora=True)
    for a in flussi.get("agent_candidates", []):
        tip(["Agenti"],
            f"«{a['phase']}» in {a['family']}: {a['detail']}, senza un tuo checkpoint — "
            "quando il flusso torna a girare, valutiamo un agente in background.",
            ora=True)
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
            "nel CLAUDE.md del cliente o nella skill: costano meno che ripeterle.",
            ora=True)
    for s in att.get("sanguisughe") or []:
        tip(["Consumi", "Metodo"],
            f"Sessione sanguisuga il {s['date']} ({s['label']}): diario pesante, "
            f"{s['giri']} giri di richieste e nessuna consegna. Se il lavoro rinasce, "
            "spezzalo in sessioni più piccole o passa da una skill.", ora=True)
    for p in projects:
        if p["name"] != "Digital Strategist" and (p["days_since"] is None
                                                  or p["days_since"] > 14):
            giorni = p["days_since"] if p["days_since"] is not None else "30+"
            tip(["Clienti"],
                f"{p['name']} è fermo da {giorni} giorni: una call o un contenuto "
                "questa settimana vale più di qualsiasi metrica.", ora=True)
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
         "cache locali del Mac."),
        (["Plugin"],
         "Preferisci i plugin ufficiali del marketplace (skill-creator, plugin-dev, "
         "mcp-server-dev) ai cloni manuali: si aggiornano da soli."),
        (["Metodo", "Claude Code"],
         "Chiudi la settimana con «aggiorna la dashboard»: alimenta lo storico e ti "
         "consegna la fotografia del venerdì."),
    ]:
        tip(tags, text)

    n_riparare = len(alerts["riparare"])
    data = {
        "generated": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "window_days": WINDOW_DAYS,
        "window_from": datefmt(time.time() - WINDOW_DAYS * 86400),
        "window_to": datefmt(time.time()),
        "inventory": {
            "skills": len(skills),
            "projects": len(projects),
            "mcp": len(mcp),
            "sessions": sessions,
        },
        "decision_tiles": met["tiles"],
        "metodo": met["metodo_card"],
        "attrito": met.get("attrito"),
        "by_division": met.get("by_division"),
        "telemetry": ({"cost": telemetry["cost"], "requests": telemetry["requests"],
                       "tok": telemetry["tok"], "first": telemetry["first"],
                       "last": telemetry["last"],
                       "cost_eur": (round(telemetry["cost"] * eur_rate, 2)
                                    if eur_rate else None),
                       "rate_date": eur_date} if telemetry else None),
        "storico": metrics.load_storico()["days"],
        "trend": met["trend"],
        "advice": advice,
        "alerts": alerts,
        "skills": skills,
        "skill_areas": SKILL_AREAS,
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
            f"Uso e flussi leggono i diari di sessione conservati sul Mac (~{WINDOW_DAYS} giorni): fasi, conteggi e destinazioni — mai i contenuti.",
            "Token e costi vengono dalla telemetria locale (sonda otel_probe, attiva dal 7/7/2026): coprono solo il periodo in cui la sonda è accesa.",
            "Giri di revisione e correzioni sono stime dai metadati (ordine di messaggi e scritture, date dei file): indicano la tendenza, non i minuti esatti.",
            "La «prima stesura buona» non vede le modifiche fatte sulla copia caricata su Google Drive prima della condivisione: per quelle servirebbe la cronologia revisioni di Drive (estensione in valutazione).",
        ],
    }

    OUT_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    if TEMPLATE.exists():
        html = read_text(TEMPLATE).replace("__DATA__", json.dumps(data, ensure_ascii=False))
        OUT_HTML.write_text(html, encoding="utf-8")
        html_note = str(OUT_HTML)
    else:
        html_note = "template.html mancante: solo data.json"

    print(f"Scan ok {data['generated']} — Salute {met['salute']}/100 · "
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
        print("\n-- ORCHESTRATORI --")
        for o in orchestrators["existing"]:
            print(f"  {o['name']}: " + " > ".join(o["chains"]))
        for c in orchestrators["candidates"]:
            print(f"  candidato orchestratore: {c}")
        for a in flussi.get("agent_candidates", []):
            print(f"  fase da agente: «{a['phase']}» in {a['family']} — {a['detail']}")
        print("\n-- CONTROLLI PERIODICI --")
        for c in checks:
            print(f"  {c['name']:32} [{c['level']}] {c['outcome']}  "
                  f"ultimo={c['last_run']}  prossimo={c['next']}")
        print("\n-- USO SKILL (30gg) --")
        for u in use_skills:
            print(f"  {u['n']:4}  {u['name']}")
        print("\n-- USO MCP (30gg) --")
        for u in use_mcp:
            print(f"  {u['n']:4}  {u['name']}")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        import demo
        demo.main()
    else:
        main()
