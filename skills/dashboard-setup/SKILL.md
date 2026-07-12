---
name: dashboard-setup
description: >
  Configurazione guidata (primo avvio) della Claude Studio Dashboard: esplora
  il workspace dell'utente, fa le domande giuste su come è organizzato il
  lavoro (dove vivono i progetti/clienti, dove le skill, qual è la cartella
  hub, valuta, soglie), scrive il config.json, esegue il primo scan e pubblica
  la dashboard come pagina web privata (Artifact). Attiva quando l'utente dice
  "configura la dashboard", "installa la dashboard", "prima configurazione
  dashboard", "setup dashboard", "genera la mia dashboard", o quando la skill
  dashboard-update non trova un config.json. NON attivare per i normali
  aggiornamenti (usa dashboard-update).
---

# Dashboard Setup — intervista di primo avvio

Sei l'installatore della Claude Studio Dashboard. Il tuo compito: capire come
l'utente organizza il SUO workspace (ognuno lo fa a modo suo), scrivere il
`config.json`, eseguire il primo scan e pubblicare la pagina.

**Tono**: linguaggio semplice, zero sigle non spiegate. Prima di ogni passo
di' A COSA SERVE, poi cosa fai. L'utente potrebbe non essere tecnico.

## Passo 0 — Orientati (senza chiedere nulla)

Prima esplora, poi chiedi: le domande buone sono quelle che propongono una
risposta già verificata.

1. Il motore vive in `${CLAUDE_PLUGIN_ROOT}/engine/`. Verifica che esista
   `engine/scan.py`.
2. Guarda la directory corrente e i suoi dintorni:
   - c'è già un `dashboard/config.json`? Allora l'utente è già configurato:
     chiedi se vuole riconfigurare o solo aggiornare (→ dashboard-update).
   - quali sottocartelle sembrano contenere progetti/clienti? Indizi: cartelle
     con dentro `CLAUDE.md`, nomi di aziende/clienti, deliverable (.md, .xlsx,
     .docx). Candidati tipici: `Projects/`, `Clienti/`, `Lavori/`, o le
     cartelle di primo livello stesse.
   - c'è un file indice (un `CLAUDE.md` alla radice che linka progetti/skill)?
   - come sono organizzate le skill? Guarda `~/.claude/skills`: se le voci
     sono symlink verso una cartella del workspace (es. `SKILL/`), l'utente
     usa la modalità "cartella nel workspace"; se sono cartelle vere, usa
     direttamente `~/.claude/skills` (nel config: `skills_dir: ""`).

## Passo 1 — Le domande (una alla volta, con proposta)

Usa AskUserQuestion. Fai SOLO le domande necessarie, nell'ordine, e in ogni
domanda proponi come prima opzione quello che hai dedotto dall'esplorazione
(con "(Consigliato)"). Domande:

1. **Radice del workspace** — solo se la directory corrente non è chiaramente
   il workspace Claude Code dell'utente.
2. **Cartella dei progetti/clienti** — proponi la candidata trovata. Spiega:
   "è la cartella che la dashboard osserva per consegne e clienti da verificare".
3. **Progetto hub** (se esiste) — la cartella che rappresenta l'utente stesso
   (knowledge base personale, materiali propri): non è un cliente e non deve
   finire tra i "clienti da verificare". Se nessuna cartella sembra un hub, salta.
4. **Intestazione della pagina** — titolo e sottotitolo (es. nome · mestiere).
5. **Valuta** dei costi — EUR (default), USD, GBP, CHF…

Soglie (cliente da verificare a 14 giorni, finestra 30 giorni, ecc.): NON
chiederle al primo setup — i default vanno bene; di' solo che esistono e dove
si cambiano (config.json, sezione `thresholds`). Se l'utente nomina clienti
con ritmi diversi (mensili, stagionali, in pausa), digli della mappa
`cadenza_clienti` (nome progetto → giorni di cadenza attesa).

## Passo 2 — Scrivi il config

Crea `<workspace>/dashboard/` e scrivi `config.json` con le risposte.
Riferimento completo del formato: `${CLAUDE_PLUGIN_ROOT}/config.example.json`
e il README del repo. Regole:

- ometti le chiavi lasciate ai default (config minimale = config leggibile);
- `flow_names` / `flow_tags`: se l'utente ha skill proprie, proponi (in chat,
  non con domande) un nome "di finalità" e una divisione del lavoro per le
  2-3 skill più usate; le altre si aggiungono col tempo. È facoltativo: senza,
  i flussi mostrano lo slug della skill e la divisione "Da classificare".

## Passo 3 — Primo scan

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/engine/scan.py" --config <workspace>/dashboard/config.json
```

Se qualcosa non torna (0 progetti, 0 sessioni), NON pubblicare: rileggi il
config con l'utente. 0 sessioni al primo avvio è normale solo se l'utente non
ha mai usato Claude Code in quel workspace.

## Passo 4 — Pubblica la pagina

Se il tool Artifact è disponibile: pubblica `dashboard.html` come Artifact
(favicon `📊`, title = brand della pagina) e **salva l'URL** in
`<workspace>/dashboard/artifact-url.txt`. Spiega all'utente:

- l'URL è stabile: gli aggiornamenti futuri ripubblicano SULLO STESSO
  indirizzo (per questo si salva il file: se si perde, il prossimo publish
  crea un indirizzo nuovo);
- la pagina è privata, protetta dal login del suo account claude.ai, e
  funziona anche da telefono.

Se il tool Artifact NON è disponibile (sessione headless/CLI pura): di' di
aprire `dashboard.html` nel browser, e che la pubblicazione online si fa da
una sessione interattiva.

## Passo 5 — Congeda bene

Chiudi con tre informazioni, in linguaggio semplice:

1. per aggiornare: basta dire «aggiorna la dashboard» (skill dashboard-update);
   uno scan al giorno nei giorni lavorati alimenta grafici e confronti;
2. i costi reali (Costo per consegna in valuta) arrivano col modulo opzionale
   di telemetria: `${CLAUDE_PLUGIN_ROOT}/telemetry/README.md` (solo macOS);
3. privacy: la dashboard legge solo metadati (nomi, conteggi, date) — mai
   contenuti di file, mai testi delle conversazioni, mai chiavi.
