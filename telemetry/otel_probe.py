#!/usr/bin/env python3
"""
Ricevitore OTLP minimale per la VERIFICA della telemetria di Claude Code.

Scopo: capire se le sessioni (in particolare quelle avviate da VS Code, i cui
diari riportano usage=0) emettono i conteggi token veri via OpenTelemetry.
Ascolta su http://localhost:4318 (endpoint standard OTLP http/json) e appende
ogni metrica/evento ricevuto a otel_probe.jsonl, in questa stessa cartella
(~/.claude/otel: fuori da Documents, così il LaunchAgent può leggerla e scriverla).

Solo stdlib. In produzione la avvia il LaunchAgent un LaunchAgent (vedi README accanto);
a mano: python3 ~/.claude/otel/otel_probe.py
Nessun dato lascia il computer: il ricevitore è in ascolto solo su localhost.
"""
import gzip
import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "otel_probe.jsonl"
PORT = 4318


def _attrs(lst):
    out = {}
    for a in lst or []:
        v = a.get("value") or {}
        out[a.get("key")] = next(iter(v.values()), None)
    return out


class Handler(BaseHTTPRequestHandler):
    def _read_body(self):
        if (self.headers.get("Transfer-Encoding") or "").lower() == "chunked":
            chunks = []
            while True:
                size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    self.rfile.readline()  # trailer/CRLF finale
                    break
                chunks.append(self.rfile.read(size))
                self.rfile.readline()  # CRLF dopo ogni blocco
            return b"".join(chunks)
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n)

    def do_POST(self):
        raw = self._read_body()
        if self.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        rows = []
        try:
            payload = json.loads(raw)
            if self.path.endswith("/metrics"):
                for rm in payload.get("resourceMetrics", []):
                    for sm in rm.get("scopeMetrics", []):
                        for m in sm.get("metrics", []):
                            data = m.get("sum") or m.get("gauge") or {}
                            for dp in data.get("dataPoints", []):
                                rows.append({
                                    "kind": "metric", "name": m.get("name"),
                                    "value": dp.get("asInt", dp.get("asDouble")),
                                    "attrs": _attrs(dp.get("attributes")),
                                })
            elif self.path.endswith("/logs"):
                for rl in payload.get("resourceLogs", []):
                    for sl in rl.get("scopeLogs", []):
                        for rec in sl.get("logRecords", []):
                            rows.append({
                                "kind": "event",
                                "name": (rec.get("body") or {}).get("stringValue"),
                                "attrs": _attrs(rec.get("attributes")),
                            })
            if not rows:
                rows = [{"kind": "vuoto", "path": self.path}]
        except Exception as exc:
            rows = [{"kind": "raw", "path": self.path, "err": str(exc)[:100],
                     "content_type": self.headers.get("Content-Type"),
                     "content_encoding": self.headers.get("Content-Encoding"),
                     "bytes": len(raw), "head_hex": raw[:40].hex()}]
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:  # rotazione: oltre 15 MB il file scala a .1 (lo scanner legge entrambi)
            if OUT.exists() and OUT.stat().st_size > 15_000_000:
                OUT.replace(OUT.with_name("otel_probe.1.jsonl"))
        except OSError:
            pass
        with OUT.open("a", encoding="utf-8") as fh:
            for r in rows:
                r["ts"] = stamp
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass  # niente rumore sul terminale


if __name__ == "__main__":
    print(f"Ricevitore OTLP in ascolto su http://localhost:{PORT} → {OUT.name}")
    print("Lascia questa finestra aperta durante la prova. Ctrl+C per fermare.")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
