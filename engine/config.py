#!/usr/bin/env python3
"""
Configurazione del motore — tutto ciò che dipende da COME è organizzato
il workspace dell'utente vive qui, mai nel codice.

Il file config.json viene scritto dalla skill `dashboard-setup` (l'intervista
guidata al primo avvio) e può essere ritoccato a mano in qualsiasi momento.
Vedi config.example.json nella radice del repo per il formato completo.

Uso dagli altri moduli:

    import config
    config.init(path_del_config)   # una volta, all'avvio (lo fa scan.py)
    config.C.ws                    # poi ovunque

Zero dipendenze: solo libreria standard.
"""

import json
import re
from pathlib import Path

DEFAULTS = {
    # --- dove vive il lavoro (obbligatorio solo "workspace")
    "workspace": None,            # radice del workspace Claude Code
    "projects_dir": "Projects",   # cartella dei progetti/clienti (relativa al workspace)
    "skills_dir": "SKILL",        # cartella delle skill ("" = uso diretto di ~/.claude/skills)
    "index_file": "CLAUDE.md",    # indice del workspace ("" = nessun indice, niente alert)
    "hub_project": "",            # progetto-hub personale: non conta come cliente
    "output_dir": "",             # dove scrivere output ("" = cartella del config.json)

    # --- identità della pagina
    "brand": {"title": "Dashboard Workspace", "subtitle": ""},

    # --- misura
    "window_days": 30,
    "currency": "EUR",            # valuta di vetrina per i costi ("USD" = niente cambio)
    "deliverable_exts": [".md", ".xlsx", ".docx", ".pdf", ".csv", ".pptx", ".html"],
    "thresholds": {
        "freddo_giorni": 14,        # cliente senza attività da più di N giorni = freddo
        "grace_skill_giorni": 7,    # una skill appena nata non è "ferma"
        "banco_giorni": 60,         # skill nuove sotto osservazione ROI
        "soglia_vivaio": 3,         # consegne libere simili per proporre una skill
        "agent_min_events": 8,      # azioni medie per candidare una fase ad agente
        "margine_scrittura_sec": 3600,   # oltre 1h dopo Claude, la modifica è tua
        "rilavorazione_giorni": 7,  # ripresa in altra sessione entro N giorni
        "sanguisuga_kb": 250,       # diario oltre N KB...
        "sanguisuga_giri": 3,       # ...con almeno N giri e 0 consegne
    },

    # --- personalizzazioni del racconto (tutte opzionali)
    "mcp_labels": {},             # chiave-diario MCP -> etichetta leggibile
    "utility_skills": [],         # skill-attrezzo extra (oltre alle built-in note)
    "flow_names": {},             # slug skill -> nome "di finalità" del flusso
    "flow_tags": {},              # slug skill -> [divisioni del lavoro] (1-2)
    "flow_phases": {},            # mappe-processo curate (formato in README)

    # --- controlli periodici (log watcher generico, formato in README)
    "checks": [],

    # --- telemetria locale (opzionale: senza, resta il proxy in KB)
    "telemetry": {
        "enabled": True,
        "logs": ["~/.claude/otel/otel_probe.1.jsonl",
                 "~/.claude/otel/otel_probe.jsonl"],
        "restart_hint": "riavvia la sonda di telemetria",
    },
}


class Config:
    def __init__(self, raw, config_path):
        merged = _merge(DEFAULTS, raw)
        if not merged.get("workspace"):
            raise SystemExit(
                "config.json: manca \"workspace\" (la radice del tuo workspace "
                "Claude Code). Esegui la skill dashboard-setup per generarlo.")

        self.path = Path(config_path).resolve()
        self.ws = Path(merged["workspace"]).expanduser().resolve()
        self.projects_dir = self.ws / merged["projects_dir"] \
            if merged["projects_dir"] else None
        self.skill_dir = self.ws / merged["skills_dir"] \
            if merged["skills_dir"] else None
        self.index_md = self.ws / merged["index_file"] \
            if merged["index_file"] else None
        self.hub = merged["hub_project"]
        self.out_dir = (Path(merged["output_dir"]).expanduser().resolve()
                        if merged["output_dir"] else self.path.parent)

        self.brand = merged["brand"]
        self.window_days = int(merged["window_days"])
        self.currency = (merged["currency"] or "USD").upper()
        self.deliv_exts = {e.lower() for e in merged["deliverable_exts"]}
        self.th = merged["thresholds"]

        self.mcp_labels = merged["mcp_labels"]
        self.utility_skills = set(merged["utility_skills"])
        self.flow_names = merged["flow_names"]
        self.flow_tags = merged["flow_tags"]
        self.flow_phases = merged["flow_phases"]
        self.checks = merged["checks"]

        tel = merged["telemetry"]
        self.telemetry_enabled = bool(tel.get("enabled", True))
        self.telemetry_logs = [Path(p).expanduser() for p in tel.get("logs", [])]
        self.telemetry_restart_hint = tel.get("restart_hint") or \
            DEFAULTS["telemetry"]["restart_hint"]

        # I diari di Claude Code vivono in ~/.claude/projects/<percorso-con-trattini>:
        # il prefisso si deriva dal percorso del workspace, non si configura.
        self.transcripts_root = Path.home() / ".claude" / "projects"
        self.transcript_prefix = re.sub(r"[^A-Za-z0-9-]", "-",
                                        str(self.ws).replace("/", "-"))

        # Percorsi di lavoro nell'output_dir (mai dentro il repo del motore).
        self.out_json = self.out_dir / "data.json"
        self.out_html = self.out_dir / "dashboard.html"
        self.cache_file = self.out_dir / ".cache.json"
        self.storico_file = self.out_dir / "storico.json"
        self.rate_cache = self.out_dir / ".rate.json"

        self.proj_prefix = (str(self.projects_dir) + "/") if self.projects_dir else ""


def _merge(base, over):
    """Merge ricorsivo: i dict con default si fondono, il resto si sovrascrive.
    Un default {} è un dizionario aperto (flow_names, mcp_labels…): lì il
    valore dell'utente passa intero, senza filtri sulle chiavi."""
    out = {}
    for k, v in base.items():
        w = (over or {}).get(k, v)
        out[k] = _merge(v, w) if isinstance(v, dict) and isinstance(w, dict) and v \
            else w
    return out


C = None


def init(path):
    """Carica il config e lo espone come config.C. Ritorna l'oggetto."""
    global C
    p = Path(path).expanduser()
    if not p.exists():
        raise SystemExit(
            f"Config non trovato: {p}\n"
            "Primo avvio? Esegui la skill dashboard-setup (o copia "
            "config.example.json e adattalo).")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"Config illeggibile ({p}): {exc}")
    C = Config(raw, p)
    C.out_dir.mkdir(parents=True, exist_ok=True)
    return C
