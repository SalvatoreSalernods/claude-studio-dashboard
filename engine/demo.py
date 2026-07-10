#!/usr/bin/env python3
"""
Dashboard Workspace — dataset demo per gli screenshot.

Costruisce una dashboard interamente di fantasia (il «freelance ideale»:
clienti archetipo, skill generiche, numeri plausibili e coerenti tra loro)
e la renderizza con lo stesso template.html della dashboard vera.

Non legge il workspace, non tocca data.json, storico.json, la cache né
l'Artifact pubblicato: l'unico output è dashboard-demo.html, da aprire in
locale per gli screenshot. Nessun dato reale entra qui per costruzione.

Uso:  python3 scan.py --demo [--out FILE]    (oppure: python3 demo.py)
      default output: ./dashboard-demo.html nella cartella corrente

Le date sono relative a oggi, così gli screenshot risultano sempre freschi;
lo storico sintetico è deterministico (niente random): rigenerare il file
nello stesso giorno produce numeri identici.
"""

import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "template.html"


def _out_path():
    if "--out" in sys.argv:
        i = sys.argv.index("--out")
        if i + 1 < len(sys.argv):
            return Path(sys.argv[i + 1]).expanduser().resolve()
        raise SystemExit("--out richiede un percorso")
    return Path.cwd() / "dashboard-demo.html"

OGGI = datetime.now()


def dmy(days_ago=0):
    return (OGGI - timedelta(days=days_ago)).strftime("%d/%m/%Y")


def iso(days_ago=0):
    return (OGGI - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def fmt_tok(n):
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1000:
        return f"{round(n / 1000)}k"
    return str(n)


def build_storico():
    """Serie giornaliera sintetica (solo feriali) degli ultimi ~56 giorni.

    Andamento in lieve miglioramento con una piccola oscillazione
    deterministica (seno), così grafico e Confronto periodi hanno
    qualcosa di credibile da mostrare. L'ultima riga coincide con i
    valori del giorno mostrati nelle tile.
    """
    giorni = [d for d in range(56, -1, -1)
              if (OGGI - timedelta(days=d)).weekday() < 5]
    n = len(giorni) - 1
    rows = []
    for i, back in enumerate(giorni):
        t = i / n  # 0 → inizio serie, 1 → oggi
        metodo = round(68 + 8 * t + 1.4 * math.sin(i * 0.9))
        strumenti, copertura = 85, 83
        row = {
            "d": iso(back),
            "salute": round(0.40 * metodo + 0.30 * strumenti + 0.30 * copertura),
            "metodo": metodo,
            "strumenti": strumenti,
            "copertura": copertura,
            "freddi": 1,
            "peso": round(205 - 18 * t + 6 * math.sin(i * 0.6)),
            "riparare": 1 if t > 0.6 else 0,
            "sessioni": round(50 + 11 * t),
            "costo": round(3.4 - 0.45 * t + 0.22 * math.sin(i * 0.8), 2),
            "autonomia": round(62 + 7 * t + 2 * math.sin(i * 0.7)),
            "giri": round(1.35 - 0.15 * t + 0.08 * math.sin(i * 1.7), 1),
            "conformita": round(80 + 5 * t + 1.5 * math.sin(i * 1.1)),
            "consegne30": round(32 + 8 * t + 1.2 * math.sin(i * 0.5)),
        }
        rows.append(row)
    # l'oggi delle tile, esatto
    rows[-1].update({"metodo": 76, "salute": 81, "costo": 2.99, "autonomia": 69,
                     "giri": 1.2, "conformita": 85, "consegne30": 40,
                     "sessioni": 61, "peso": 188, "riparare": 1})
    return rows


def build():
    storico = build_storico()

    # ---- trend: 3 settimane ricostruite (backfill) + campioni dallo storico
    first_hist = datetime.strptime(storico[0]["d"], "%Y-%m-%d")
    points = [{"d": (first_hist - timedelta(days=7 * (3 - k))).strftime("%Y-%m-%d"),
               "metodo": 63 + k * 2, "backfill": True} for k in range(3)]
    campioni = storico[::5]
    if campioni[-1] is not storico[-1]:
        campioni.append(storico[-1])
    points += [{"d": r["d"], "salute": r["salute"], "metodo": r["metodo"],
                "copertura": r["copertura"]} for r in campioni]

    # ---- ingredienti del giorno (tutti derivati, mai scritti due volte)
    con_metodo, operative = 37, 49
    metodo = round(con_metodo / operative * 100)          # 76
    strumenti, copertura = 85, 83                          # 1 alert −15 · 5 clienti su 6
    salute = round(0.40 * metodo + 0.30 * strumenti + 0.30 * copertura)  # 81

    cost, requests = 41.85, 132
    tok = {"in": 214300, "out": 168400, "cache": 15600000}
    eur_rate, rate_date = 0.88, iso(1)
    cost_eur = round(cost * eur_rate, 2)
    consegne_coperte = 14
    costo_consegna = round(cost_eur / consegne_coperte, 2)

    prev, ultimo = storico[-2], storico[-1]

    def delta(key, up_is_good=True, dec=0):
        diff = round(ultimo[key] - prev[key], dec if dec else None)
        if abs(diff) < 0.5:
            return {"text": "stabile", "dir": "flat"}
        buono = (diff > 0) == up_is_good
        segno = "+" if diff > 0 else "−"
        return {"text": f"{segno}{abs(diff)}", "dir": "good" if buono else "bad"}

    tiles = [
        {"id": "salute", "label": "Salute dello studio", "value": salute,
         "unit": "/100", "delta": delta("salute"),
         "note": f"0,40·Metodo {metodo} + 0,30·Strumenti {strumenti} "
                 f"+ 0,30·Copertura {copertura}"},
        {"id": "metodo", "label": "Quota di metodo", "value": metodo,
         "unit": "%", "delta": delta("metodo"),
         "note": f"{con_metodo} sessioni con flusso su {operative} operative"},
        {"id": "freddi", "label": "Clienti freddi", "value": 1, "unit": "",
         "delta": {"text": "stabile", "dir": "flat"}, "note": "B&B Riviera"},
        {"id": "peso", "label": "Costo per consegna", "value": costo_consegna,
         "unit": " €", "delta": delta("costo", up_is_good=False, dec=2),
         "note": f"{cost:.2f} $ ≈ {cost_eur:.2f} € (cambio BCE {rate_date}) per "
                 f"{consegne_coperte} consegne dal {dmy(30)} · token: "
                 f"{fmt_tok(tok['in'])} in · {fmt_tok(tok['out'])} out · "
                 f"{fmt_tok(tok['cache'])} cache"},
        {"id": "riparare", "label": "Da riparare", "value": 1, "unit": "",
         "delta": {"text": "stabile", "dir": "flat"},
         "note": "alert che toccano la salute"},
    ]

    # ---- inventario del freelance ideale
    skills = [
        ("articolo-blog-seo",       dmy(9)),
        ("report-mensile-cliente",  dmy(4)),
        ("piano-editoriale-social", dmy(12)),
        ("newsletter-mensile",      dmy(18)),
        ("audit-sito-web",          dmy(40)),
        ("analisi-competitor",      dmy(55)),
        ("preventivo-da-brief",     dmy(33)),
        ("rassegna-stampa-settore", dmy(70)),
    ]
    skills = [{"name": n, "symlink": "ok", "indexed": True, "updated": u,
               "created_ts": (OGGI - timedelta(days=90)).timestamp()}
              for n, u in skills]

    projects = [
        ("E-commerce Moda",     5, 18, "Descrizioni prodotto — collezione estate.md", 1),
        ("Studio Dentistico",   3, 11, "Articolo blog — igiene dentale bambini.md",   2),
        ("Ristorante Tipico",   2,  9, "Piano editoriale social — mese prossimo.xlsx", 3),
        ("Palestra & Wellness", 1,  7, "Newsletter — sfida d'estate.md",              5),
        ("Artigiano del Legno", 1,  5, "Audit sito — riepilogo interventi.md",        6),
        ("B&B Riviera",         0,  2, "Report mensile — risultati e prossimi passi.docx", 21),
    ]
    projects = [{"name": n, "claude_md": True, "indexed": True, "mod7": m7,
                 "mod30": m30, "last_file": f, "last_date": dmy(g),
                 "days_since": g} for n, m7, m30, f, g in projects]

    mcp = [
        {"name": "SEO Tool (keyword)", "scope": "utente", "type": "http",
         "env": [], "status": "riautorizzare", "problem": "",
         "ukey": "SEO Tool (keyword)"},
        {"name": "Google Calendar", "scope": "account claude.ai",
         "type": "connettore", "env": [], "status": "", "problem": "",
         "ukey": "Google Calendar"},
        {"name": "Email marketing", "scope": "workspace", "type": "stdio",
         "env": ["EMAIL_API_KEY"], "status": "", "problem": "",
         "ukey": "Email marketing"},
        {"name": "Analytics", "scope": "account claude.ai", "type": "connettore",
         "env": [], "status": "", "problem": "", "ukey": "Analytics"},
        {"name": "Canva", "scope": "account claude.ai", "type": "connettore",
         "env": [], "status": "", "problem": "", "ukey": "Canva"},
    ]

    alerts = {
        "riparare": [{"level": "warning", "peso": 15,
                      "text": "MCP «SEO Tool (keyword)»: autorizzazione scaduta e "
                              "usato di recente — riattivala con /mcp o dalle "
                              "impostazioni claude.ai"}],
        "decidere": [{"text": "«Canva»: collegato ma mai usato in 30 giorni — "
                              "serve davvero?"}],
    }

    metodo_card = {
        "quota": metodo, "operative": operative, "con_metodo": con_metodo,
        "vivaio": [{"label": "E-commerce Moda · consegne .md", "count": 4,
                    "files": ["Descrizioni prodotto — collezione estate.md",
                              "Guida alle taglie — pagina informativa.md",
                              "FAQ resi e spedizioni.md"]}],
        "ferme": ["rassegna-stampa-settore"],
        "banco": [{"name": "newsletter-mensile", "giorni": 18, "usi": 4},
                  {"name": "preventivo-da-brief", "giorni": 33, "usi": 2}],
        "delega": 9,
        "coda": {"orchestratori": 1, "agenti": 1},
    }

    attrito = {
        "autonomia": {"pct": 69, "auto": 9, "tot": 13},
        "giri": {"avg": 1.2, "sessioni": 34},
        "conformita": {"pct": 85, "ok": 34, "tot": 40, "chat": 5,
                       "riprese": 1, "manuali": 0},
        "riviste": {"n": 5, "tot": 7},
        "consegne": {"week": 11, "media": 10.0, "tot": 40},
        "sanguisughe": [],
    }

    flows = {
        "families": [
            {"name": "Dal brief all'articolo del blog", "slug": "articolo-blog-seo",
             "sessions": 8, "tags": ["Content", "SEO & AEO"],
             "steps": [
                 {"label": "Brief del cliente e scelta della keyword", "kind": "tu",
                  "tool": "", "share": None, "count": None, "mcp": [], "avg": 0,
                  "agent": False},
                 {"label": "Raccolta dati keyword: volumi e domande", "kind": "mcp",
                  "tool": "", "share": 1.0, "count": 8,
                  "mcp": [{"name": "SEO Tool", "count": 8}], "avg": 18,
                  "agent": True},
                 {"label": "Stesura dell'articolo ottimizzato", "kind": "skill",
                  "tool": "articolo-blog-seo", "share": 1.0, "count": 8,
                  "mcp": [], "avg": 1, "agent": False},
                 {"label": "Consegna nella cartella cliente (.md)", "kind": "out",
                  "tool": "", "share": 1.0, "count": 8, "mcp": [], "avg": 2,
                  "agent": False},
             ],
             "deliver": "file .md per Studio Dentistico, E-commerce Moda e altri 2",
             "mcp_extra": []},
            {"name": "Report mensile per il cliente", "slug": "report-mensile-cliente",
             "sessions": 6, "tags": ["Gestione clienti"],
             "steps": [
                 {"label": "Raccolta dei risultati del mese", "kind": "mcp",
                  "tool": "", "share": 0.83, "count": 5,
                  "mcp": [{"name": "Analytics", "count": 5}], "avg": 6,
                  "agent": False},
                 {"label": "Redazione del report coi numeri commentati",
                  "kind": "skill", "tool": "report-mensile-cliente", "share": 1.0,
                  "count": 6, "mcp": [], "avg": 1, "agent": False},
                 {"label": "Consegna (.docx) pronta per l'invio", "kind": "out",
                  "tool": "", "share": 1.0, "count": 6, "mcp": [], "avg": 2,
                  "agent": False},
             ],
             "deliver": "file .docx per B&B Riviera, Palestra & Wellness e altri 3",
             "mcp_extra": []},
            {"name": "Lavoro libero (senza ricetta codificata)", "slug": "",
             "sessions": 11, "tags": ["Lavoro libero"],
             "steps": [
                 {"label": "ricerca web", "kind": "web", "tool": "", "share": 0.45,
                  "count": 5, "mcp": [], "avg": 2, "agent": False},
                 {"label": "raccolta dati: Google Calendar", "kind": "mcp",
                  "tool": "", "share": 0.18, "count": 2,
                  "mcp": [{"name": "Google Calendar", "count": 2}], "avg": 2,
                  "agent": False},
                 {"label": "consegna nel progetto", "kind": "out", "tool": "",
                  "share": 0.73, "count": 8, "mcp": [], "avg": 0, "agent": False},
             ],
             "deliver": "file .md + .xlsx per Ristorante Tipico, Artigiano del "
                        "Legno e altri 2",
             "mcp_extra": []},
            {"name": "Piano editoriale social del mese",
             "slug": "piano-editoriale-social", "sessions": 5, "tags": ["Social"],
             "steps": [
                 {"label": "Obiettivi del mese concordati col cliente", "kind": "tu",
                  "tool": "", "share": None, "count": None, "mcp": [], "avg": 0,
                  "agent": False},
                 {"label": "Costruzione del piano: rubriche, formati, date",
                  "kind": "skill", "tool": "piano-editoriale-social", "share": 1.0,
                  "count": 5, "mcp": [], "avg": 1, "agent": False},
                 {"label": "Consegna del calendario (.xlsx)", "kind": "out",
                  "tool": "", "share": 1.0, "count": 5, "mcp": [], "avg": 3,
                  "agent": False},
             ],
             "deliver": "file .xlsx per Ristorante Tipico, E-commerce Moda e altri 1",
             "mcp_extra": []},
            {"name": "Newsletter del mese", "slug": "newsletter-mensile",
             "sessions": 4, "tags": ["Content"],
             "steps": [
                 {"label": "Stesura della newsletter", "kind": "skill",
                  "tool": "newsletter-mensile", "share": 1.0, "count": 4,
                  "mcp": [], "avg": 1, "agent": False},
                 {"label": "Caricamento della bozza sulla piattaforma email",
                  "kind": "mcp", "tool": "", "share": 0.75, "count": 3,
                  "mcp": [{"name": "Email marketing", "count": 3}], "avg": 4,
                  "agent": False},
                 {"label": "Consegna del testo nella cartella cliente", "kind": "out",
                  "tool": "", "share": 1.0, "count": 4, "mcp": [], "avg": 1,
                  "agent": False},
             ],
             "deliver": "file .md per Palestra & Wellness, E-commerce Moda",
             "mcp_extra": []},
            {"name": "Officina: costruzione di nuove skill", "slug": "skill-creator",
             "sessions": 2, "tags": ["Officina del metodo"],
             "steps": [
                 {"label": "Individuazione di un lavoro ripetuto da codificare",
                  "kind": "tu", "tool": "", "share": None, "count": None,
                  "mcp": [], "avg": 0, "agent": False},
                 {"label": "Progettazione e scrittura della skill", "kind": "skill",
                  "tool": "skill-creator", "share": 1.0, "count": 2, "mcp": [],
                  "avg": 1, "agent": False},
                 {"label": "Collaudo su un caso reale", "kind": "tu", "tool": "",
                  "share": None, "count": None, "mcp": [], "avg": 0, "agent": False},
                 {"label": "Collegamento al workspace", "kind": "out", "tool": "",
                  "share": 1.0, "count": 2, "mcp": [], "avg": 1, "agent": False},
             ],
             "deliver": "", "mcp_extra": []},
        ],
        "chat_sessions": 12,
        "divisions": [
            {"name": "Content", "sessions": 12},
            {"name": "Lavoro libero", "sessions": 11},
            {"name": "SEO & AEO", "sessions": 8},
            {"name": "Gestione clienti", "sessions": 6},
            {"name": "Social", "sessions": 5},
            {"name": "Officina del metodo", "sessions": 2},
        ],
        "agent_candidates": [
            {"family": "Dal brief all'articolo del blog",
             "phase": "Raccolta dati keyword: volumi e domande",
             "detail": "in media 18 azioni a sessione"},
        ],
    }

    orchestrators = {
        "existing": [
            {"name": "kit-contenuti-mese",
             "purpose": "Dal calendario editoriale al kit contenuti del mese",
             "chains": ["piano-editoriale-social", "articolo-blog-seo",
                        "newsletter-mensile"]},
        ],
        "candidates": ["articolo-blog-seo + audit-sito-web (insieme in 2 sessioni)"],
    }

    advice = [
        {"tags": ["MCP"], "text": alerts["riparare"][0]["text"], "ora": True},
        {"tags": ["Pulizia", "MCP"], "text": alerts["decidere"][0]["text"],
         "ora": True},
        {"tags": ["Skill", "Metodo"],
         "text": "Automazioni ferme (0 usi in 30 giorni): rassegna-stampa-settore. "
                 "Rilanciala con un caso reale o archiviala.", "ora": True},
        {"tags": ["Skill", "Metodo"],
         "text": "Lavoro libero ricorrente (E-commerce Moda · consegne .md, 4 "
                 "volte, es. «Descrizioni prodotto — collezione estate.md»): "
                 "maturo per diventare una skill — dillo in chat e la costruiamo.",
         "ora": True},
        {"tags": ["Agenti"],
         "text": "«Raccolta dati keyword: volumi e domande» in Dal brief "
                 "all'articolo del blog: in media 18 azioni a sessione, senza un "
                 "tuo checkpoint — valutiamo un agente in background.", "ora": True},
        {"tags": ["Skill", "Metodo"],
         "text": "Orchestratore da creare: articolo-blog-seo + audit-sito-web "
                 "(insieme in 2 sessioni)", "ora": True},
        {"tags": ["Clienti"],
         "text": "B&B Riviera è fermo da 21 giorni: una call o un contenuto "
                 "questa settimana vale più di qualsiasi metrica.", "ora": True},
        {"tags": ["Claude Code", "Consumi"],
         "text": "Una sessione = un lavoro: apri una sessione nuova per ogni "
                 "lavoro diverso invece di allungarne una sola. Contesti più "
                 "precisi, diari più leggeri, consumi più bassi."},
        {"tags": ["Claude Code", "Skill"],
         "text": "Dopo aver creato o modificato una skill o un MCP, ricarica la "
                 "sessione: gli strumenti nuovi compaiono solo da lì."},
        {"tags": ["Metodo"],
         "text": "Se ti accorgi che stai rifacendo a mano un lavoro già "
                 "codificato, invoca la skill: la Quota di metodo misura proprio "
                 "questa abitudine."},
        {"tags": ["Consumi"],
         "text": "Per i file grandi (trascrizioni, CSV) passa il percorso del "
                 "file invece di incollarne il contenuto in chat: Claude lo legge "
                 "a pezzi e il contesto resta leggero."},
        {"tags": ["Agenti"],
         "text": "Gli agenti servono per le fasi pesanti e ripetitive senza tue "
                 "decisioni in mezzo; i checkpoint (ok al titolo, ok al brief) "
                 "restano tuoi: non delegarli."},
        {"tags": ["Metodo", "Claude Code"],
         "text": "Chiudi la settimana con «aggiorna la dashboard»: alimenta lo "
                 "storico e ti consegna la fotografia del venerdì."},
    ]
    for a in advice:
        a.setdefault("ora", False)

    return {
        "generated": OGGI.strftime("%d/%m/%Y %H:%M") + " · dati di esempio",
        "window_days": 30,
        "window_from": dmy(30),
        "window_to": dmy(0),
        "brand": {"title": "Dashboard Workspace",
                  "subtitle": "Demo · dati di esempio"},
        "hub": "",
        "inventory": {"skills": len(skills), "projects": len(projects),
                      "mcp": len(mcp), "sessions": 61},
        "decision_tiles": tiles,
        "metodo": metodo_card,
        "attrito": attrito,
        "telemetry": {"cost": cost, "requests": requests, "tok": tok,
                      "first": dmy(30), "last": dmy(0), "cost_eur": cost_eur,
                      "rate_date": rate_date},
        "storico": storico,
        "trend": {"points": points,
                  "series": [{"key": "salute", "label": "Salute", "color": "acc"},
                             {"key": "metodo", "label": "Metodo %", "color": "blu"},
                             {"key": "copertura", "label": "Copertura clienti %",
                              "color": "aqua"}]},
        "advice": advice,
        "alerts": alerts,
        "skills": skills,
        "home_extra": {"official": ["docx", "pdf", "pptx", "xlsx"],
                       "repo_links": [], "anomalies": []},
        "projects": projects,
        "mcp": mcp,
        "plugins": [{"name": "skill-creator",
                     "marketplace": "claude-plugins-official", "scope": "user",
                     "updated": iso(3)}],
        "checks": [{"name": "Accesso API del tool SEO",
                    "what": "il piano e la chiave API del tool keyword sono ancora validi?",
                    "cadence": "mensile (a inizio sessione)",
                    "last_run": dmy(5) + " 08:30", "level": "good",
                    "outcome": "ok — nessun cambiamento",
                    "next": "prima sessione del mese prossimo"}],
        "flows": flows,
        "orchestrators": orchestrators,
        "usage": {
            "skills": [{"name": "articolo-blog-seo", "n": 8},
                       {"name": "report-mensile-cliente", "n": 6},
                       {"name": "piano-editoriale-social", "n": 5},
                       {"name": "xlsx", "n": 5},
                       {"name": "newsletter-mensile", "n": 4},
                       {"name": "analisi-competitor", "n": 3},
                       {"name": "audit-sito-web", "n": 3},
                       {"name": "docx", "n": 3},
                       {"name": "skill-creator", "n": 2},
                       {"name": "preventivo-da-brief", "n": 2}],
            "mcp": [{"name": "SEO Tool (keyword)", "n": 96},
                    {"name": "Google Calendar", "n": 22},
                    {"name": "Email marketing", "n": 15},
                    {"name": "Analytics", "n": 9}],
            "sessions": 61},
        "limits": [
            "Connettori claude.ai e stati di autorizzazione letti dalle cache locali: il dettaglio live è in /mcp.",
            "Uso e flussi leggono i diari di sessione conservati in locale (~30 giorni): fasi, conteggi e destinazioni — mai i contenuti.",
            "Token e costi vengono dalla telemetria locale: coprono solo il periodo in cui la sonda è accesa.",
            "Giri di revisione e correzioni sono stime dai metadati (ordine di messaggi e scritture, date dei file): indicano la tendenza, non i minuti esatti.",
            "La «prima stesura buona» non vede le modifiche fatte sulla copia caricata su Google Drive prima della condivisione.",
        ],
    }


def main():
    out = _out_path()
    data = build()
    html = TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    out.write_text(html, encoding="utf-8")
    print(f"Demo ok — dataset di fantasia («freelance ideale»), nessun dato "
          f"reale letto o toccato.\nHTML: {out}\n"
          f"Aprilo nel browser: open {out}")


if __name__ == "__main__":
    main()
