#!/usr/bin/env python3
"""
CS2 GSI server: принимает Game State Integration от CS2, отдаёт статы (K/D, средний KD) для OBS.
Запуск: python gsi_server.py
OBS: добавить Browser Source → URL http://localhost:3000/overlay
"""

import json
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

PORT = int(os.environ.get("CS2_GSI_PORT", "3002"))
# Текущее состояние (мержим приходящие JSON от CS2)
_state = {}
_lock = threading.Lock()


def deep_merge(base: dict, update: dict) -> None:
    """Рекурсивно мержит update в base (in-place)."""
    for k, v in update.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            deep_merge(base[k], v)
        else:
            base[k] = v


# Карты, на которых есть курицы (по имени карты, без учёта регистра)
MAPS_WITH_CHICKENS = (
    "inferno", "italy", "militia", "nuke", "anubis", "ancient", "cobblestone", "rush"
)


def get_stats():
    """Из текущего state достаёт kills, deaths, KD и убийства куриц (если есть)."""
    with _lock:
        try:
            p = _state.get("player") or {}
            ms = p.get("match_stats") or {}
            kills = int(ms.get("kills", 0))
            deaths = int(ms.get("deaths", 0))
            chicken_kills = int(ms.get("chicken_kills") or ms.get("chickenKills") or 0)
            map_name = (_state.get("map") or {}).get("name") or ""
        except (TypeError, ValueError):
            kills = deaths = chicken_kills = 0
            map_name = ""
    kd = (kills / deaths) if deaths else float(kills)
    map_lower = map_name.lower()
    has_chickens = any(m in map_lower for m in MAPS_WITH_CHICKENS) or chicken_kills > 0
    return {
        "kills": kills,
        "deaths": deaths,
        "kd": round(kd, 2),
        "chicken_kills": chicken_kills,
        "show_chickens": has_chickens,
    }


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        client = self.client_address[0] if self.client_address else "?"
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        print(f"[POST] от {client} путь: {self.path} размер: {len(body)} байт")
        try:
            data = json.loads(body.decode("utf-8", errors="ignore"))
            with _lock:
                deep_merge(_state, data)
            ms = (data.get("player") or {}).get("match_stats") or {}
            k = ms.get("kills", "?")
            d = ms.get("deaths", "?")
            c = ms.get("chicken_kills", ms.get("chickenKills", "?"))
            print(f"[GSI] данные от игры: kills={k}, deaths={d}, chickens={c}")
        except Exception as e:
            print(f"[GSI] ошибка разбора: {e}")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        client = self.client_address[0] if self.client_address else "?"
        if path not in ("/stats", "/stats/"):
            print(f"[GET] от {client} путь: {path}")
        if path == "/stats" or path == "/stats/":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(get_stats()).encode())
            return
        if path == "/overlay" or path == "/overlay/" or path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(OVERLAY_HTML.encode("utf-8"))
            return
        if path == "/debug" or path == "/debug/":
            with _lock:
                player = _state.get("player") or {}
            snapshot = {"player": player, "stats": get_stats()}
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(snapshot, ensure_ascii=False, indent=2).encode("utf-8"))
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        pass  # отключаем лог каждого запроса


OVERLAY_HTML = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>CS2 K/D Overlay</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@600;700&display=swap" rel="stylesheet">
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    html, body {
      font-family: "Orbitron", "Segoe UI", system-ui, sans-serif;
      background: transparent;
      color: #fff;
      padding: 0;
      margin: 0;
      text-shadow: 0 0 8px rgba(0,0,0,0.9), 0 1px 2px #000;
      width: max-content;
      min-width: 0;
    }
    .stats {
      display: inline-flex;
      align-items: center;
      gap: 16px;
      font-size: 28px;
      font-weight: 700;
      background: linear-gradient(135deg,
        rgba(55, 50, 85, 0.6) 0%,
        rgba(85, 55, 80, 0.55) 40%,
        rgba(75, 60, 45, 0.6) 100%);
      padding: 10px 22px;
      border-radius: 8px;
      width: max-content;
    }
    .stats > div {
      white-space: nowrap;
    }
    .kills { color: #4ade80; }
    .deaths { color: #f87171; }
    .kd { color: #fbbf24; }
    .chickens { color: #f59e0b; }
    .label { font-size: 12px; font-weight: 600; opacity: 0.85; text-transform: uppercase; letter-spacing: 0.05em; }
    #chickens-block { display: none; }
    #chickens-block.visible { display: block; }
    /* Цифры всегда читаемые: в OBS внешний шрифт может не подгрузиться */
    #kills, #deaths, #kd, #chicken_kills { font-family: "Orbitron", Arial, sans-serif; }
  </style>
</head>
<body>
  <div class="stats">
    <div>
      <div class="label">Kills</div>
      <span class="kills" id="kills">0</span>
    </div>
    <div>
      <div class="label">Deaths</div>
      <span class="deaths" id="deaths">0</span>
    </div>
    <div>
      <div class="label">K/D</div>
      <span class="kd" id="kd">0.00</span>
    </div>
    <div id="chickens-block">
      <div class="label">Chickens</div>
      <span class="chickens" id="chicken_kills">0</span>
    </div>
  </div>
  <script>
    function update() {
      fetch("/stats")
        .then(r => r.json())
        .then(d => {
          var k = Number(d.kills);
          var v = Number(d.deaths);
          var kd = v > 0 ? (k / v) : k;
          document.getElementById("kills").textContent = isNaN(k) ? "0" : k;
          document.getElementById("deaths").textContent = isNaN(v) ? "0" : v;
          document.getElementById("kd").textContent = isNaN(kd) ? "0.00" : kd.toFixed(2);
          var block = document.getElementById("chickens-block");
          var c = Number(d.chicken_kills);
          if (d.show_chickens) {
            block.classList.add("visible");
            document.getElementById("chicken_kills").textContent = isNaN(c) ? "0" : c;
          } else {
            block.classList.remove("visible");
          }
        })
        .catch(function() {});
    }
    update();
    setInterval(update, 1000);
  </script>
</body>
</html>
"""


def main():
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"CS2 GSI server: http://localhost:{PORT}/")
    print(f"Overlay for OBS: http://localhost:{PORT}/overlay")
    print(f"Отладка (что пришло от игры): http://localhost:{PORT}/debug")
    print("Если в оверлее нули — смотри в консоль: приходят ли строки [GSI] от игры.")
    server.serve_forever()


if __name__ == "__main__":
    main()
