# Telemetria — costi e token reali (modulo opzionale, solo macOS per ora)

Senza questo modulo la dashboard funziona lo stesso: la tile dei consumi mostra
il **proxy in KB** (peso del diario ÷ consegne). Con la telemetria attiva, la
tile diventa **Costo per consegna** in valuta reale, con il dettaglio dei token
(input · output · cache).

## A cosa serve

I diari delle sessioni Claude Code avviate da VS Code/Cowork riportano
`usage = 0`: i token veri viaggiano solo via **OpenTelemetry**. Questo modulo
è una "cassetta delle lettere" locale: un piccolo ricevitore che ascolta su
`localhost:4318`, riceve gli eventi di consumo che Claude Code emette e li
appende a un file. Lo scanner li aggrega da lì. **Nessun dato lascia il
computer**: il ricevitore ascolta solo su localhost.

## Installazione (3 passi)

### 1. Di' a Claude Code di emettere la telemetria

Aggiungi al blocco `env` di `~/.claude/settings.json`:

```json
{
  "env": {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_METRICS_EXPORTER": "otlp",
    "OTEL_LOGS_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318",
    "OTEL_METRIC_EXPORT_INTERVAL": "10000",
    "OTEL_LOGS_EXPORT_INTERVAL": "5000"
  }
}
```

### 2. Installa la sonda

```bash
mkdir -p ~/.claude/otel
cp otel_probe.py ~/.claude/otel/
```

La sonda vive in `~/.claude/otel` e NON dentro Documents: launchd (il
gestore di processi di macOS) non può leggere le cartelle protette dalla
privacy di macOS (Documents, Desktop…).

### 3. Falla partire da sola a ogni login (LaunchAgent)

```bash
sed "s|SOSTITUISCI_CON_HOME|$HOME|" com.example.claude-otel-probe.plist \
  > ~/Library/LaunchAgents/com.example.claude-otel-probe.plist
launchctl load ~/Library/LaunchAgents/com.example.claude-otel-probe.plist
```

Da questo momento la sonda parte al login e riparte da sola se cade
(`KeepAlive`). Riavvio manuale, se serve:

```bash
launchctl unload ~/Library/LaunchAgents/com.example.claude-otel-probe.plist
launchctl load   ~/Library/LaunchAgents/com.example.claude-otel-probe.plist
```

Nel `config.json` della dashboard puoi personalizzare il suggerimento che
compare nell'alert quando la sonda tace (`telemetry.restart_hint`).

## Come verificare che funziona

1. Riavvia la sessione Claude Code (le env si leggono all'avvio).
2. Lavora qualche minuto, poi:

```bash
grep -c api_request ~/.claude/otel/otel_probe.jsonl
```

Se il numero cresce, i costi stanno arrivando. Al prossimo scan la tile
«Peso per consegna» diventa «Costo per consegna».

## Dettagli

- Il file `otel_probe.jsonl` ruota da solo oltre i 15 MB (scala a `.1`;
  lo scanner legge entrambi).
- Lo scanner aggrega gli eventi `claude_code.api_request` (costo in USD e
  token per richiesta). La conversione nella valuta del config usa il cambio
  BCE del giorno (api.frankfurter.app) con cache offline dell'ultimo cambio.
- Se la sonda tace da più di 2 giorni mentre le sessioni continuano, la
  dashboard mostra un alert con il comando di riavvio.
- **Linux**: la sonda è puro Python stdlib e funziona anche lì; al posto del
  LaunchAgent serve un servizio systemd user (`systemctl --user`). Contributi
  benvenuti per il file di servizio.
