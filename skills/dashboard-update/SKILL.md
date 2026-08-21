---
name: dashboard-update
description: >
  Aggiorna e ripubblica la Claude Studio Dashboard: esegue lo scanner locale
  (engine/scan.py) sul config.json dell'utente e ripubblica la pagina Artifact
  sullo stesso URL stabile. Attiva quando l'utente dice "aggiorna la
  dashboard", "ripubblica la dashboard", "stato del workspace", "com'è messo
  lo studio", "dashboard del workspace", o a fine settimana per la fotografia
  periodica. Se non esiste ancora un config.json, NON improvvisare: rimanda
  alla skill dashboard-setup.
---

# Dashboard Update — scan e ripubblicazione

## Passo 1 — Trova il config

Cerca `dashboard/config.json` nella directory corrente, poi nella radice del
workspace. Se non esiste: fermati e proponi la skill **dashboard-setup**
(prima configurazione guidata). Non inventare un config.

## Passo 2 — Scan

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/engine/scan.py" --config <path>/dashboard/config.json
```

Lo scanner scrive `data.json` + `dashboard.html` nell'output_dir (di default
la stessa cartella del config) e aggiorna lo storico giornaliero. Con `--dump`
stampa anche il riepilogo a terminale, utile per il report in chat.

## Passo 3 — Ripubblica SULLO STESSO URL

L'URL stabile è in `artifact-url.txt` accanto al config.

- Se il file esiste: pubblica `dashboard.html` con il tool Artifact passando
  quell'URL come `url` (ripubblicazione in place). Favicon `📊`, stessa di
  sempre.
- Se non esiste: pubblica come nuovo Artifact e salva l'URL nel file.
- Se il tool Artifact non è disponibile (headless): di' che lo scan è fatto e
  la pagina locale è aggiornata; la ripubblicazione richiede una sessione
  interattiva.

## Passo 4 — Racconta cosa è cambiato

Riporta in chat, in linguaggio semplice: le 5 tile (Indice operativo, Quota di
metodo, Clienti senza Claude Code, Costo/Peso per consegna, Da riparare) con le
variazioni, e
l'alert più importante se c'è. Non incollare tutto il dump: seleziona.

## Screenshot e condivisione

La dashboard mostra i nomi delle tue cartelle cliente in chiaro (tabella
Progetti, note delle tile, destinazioni dei flussi). Prima di condividere
uno screenshot su LinkedIn o in una presentazione, ricordalo all'utente:
sono dati suoi e dei suoi clienti.
