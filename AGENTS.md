# AGENTS.md

## Progetto

Claude Studio Dashboard è un plugin open-source (MIT) per Claude Code.
Esegue uno scanner locale del workspace e genera una pagina HTML privata con
metriche operative. Il prodotto tratta metadati, non il contenuto del lavoro.
README.md è la documentazione completa; questo file serve solo a orientarsi.

## Vincoli non negoziabili

- `engine/` deve restare Python puro a zero dipendenze: solo libreria standard.
- Non aggiungere import di pacchetti esterni, dipendenze pip o passaggi di
  installazione.
- Non leggere il contenuto di deliverable o altri file di lavoro dei clienti.
- Non estrarre, conservare o pubblicare prompt o testo delle conversazioni.
- Non leggere né esportare valori di chiavi, token o variabili d'ambiente:
  degli ambienti sono ammessi soltanto i nomi delle variabili.
- L'output deve contenere soltanto metadati: nomi di file/cartelle, conteggi,
  date, estensioni, nomi di skill/MCP e indicatori derivati.
- L'unica chiamata di rete ammessa al motore è il cambio valuta tramite
  `https://api.frankfurter.app`; con valuta USD non serve.
- Il repository è pubblico: mai usare dati reali di clienti in esempi, demo,
  test, documentazione o commit.

Nota sull'implementazione attuale: lo scanner legge necessariamente file di
configurazione e controllo (`config.json`, indice `CLAUDE.md`, `SKILL.md`, log
dei check e JSONL di Claude Code). Nei diari `flows.py` ispeziona struttura,
tool call e, in modo minimo, l'inizio dei messaggi utente per escludere eventi
di sistema e contare i turni; quel testo non deve essere salvato né esportato.
Non ampliare questa ispezione semantica.

## Mappa del repository

- `engine/scan.py`: entrypoint, scansione locale, output e cambio valuta.
- `engine/metrics.py`: formule delle metriche decisionali e storico.
- `engine/flows.py`: estrazione dei flussi dai diari JSONL.
- `engine/config.py`: default e caricamento di `config.json`.
- `engine/demo.py`: dataset interamente di fantasia.
- `engine/template.html`: pagina HTML/CSS/JS generata.
- `skills/dashboard-setup/`: configurazione guidata iniziale.
- `skills/dashboard-update/`: scansione e ripubblicazione.
- `telemetry/`: costi/token reali opzionali; ricevitore stdlib, integrazione
  LaunchAgent documentata per macOS.
- `docs/index.html`: demo intenzionalmente committata per GitHub Pages.
- `config.example.json`: riferimento del formato di configurazione.

## Esecuzione

```bash
python3 engine/scan.py --config /percorso/config.json
python3 engine/scan.py --config /percorso/config.json --dump
python3 engine/scan.py --demo --out dashboard-demo.html
```

Nessun percorso, nome cliente, soglia o brand va cablato nel codice: ciò che
dipende dal workspace vive in `config.json`, secondo `config.example.json`.

## Convenzioni di lavoro

- UI, messaggi utente e documentazione sono in italiano.
- Mantieni formule e soglie esplicite e verificabili.
- `dashboard.html`, `dashboard-demo.html`, `data.json`, `config.json`,
  `storico.json`, `artifact-url.txt`, cache e rate locali sono gitignorati:
  non committarli.
- `docs/index.html` è l'eccezione deliberata: contiene soltanto la demo fittizia.
- Prima di modificare privacy, scansione, formule o formato config, verifica
  README.md, `config.example.json` e le due skill.
