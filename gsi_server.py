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
# CS2_GSI_IGNORE_SPECTATOR=0 — включить фильтр «только мой игрок» (в спеке не подменять на тиммейта).
# По умолчанию 1: фильтр выключен, статы обновляются как раньше (в спеке будет показывать того, на кого смотришь).
IGNORE_SPECTATOR_FILTER = os.environ.get("CS2_GSI_IGNORE_SPECTATOR", "1").strip().lower() in ("1", "true", "yes")
# Текущее состояние (мержим приходящие JSON от CS2)
_state = {}
_lock = threading.Lock()
# SteamID локального игрока: оверлей показывает только его статы (при спекте не подменяем на тиммейта)
# Всегда храним и сравниваем как строку — игра может присылать число или строку
_local_steamid = None


def deep_merge(base: dict, update: dict) -> None:
    """Рекурсивно мержит update в base (in-place)."""
    for k, v in update.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            deep_merge(base[k], v)
        else:
            base[k] = v


def _normalize_steamid(sid):
    """Приводит steamid к строке для единообразного сравнения и поиска в allplayers."""
    if sid is None:
        return None
    return str(sid).strip() or None


def _get_my_match_stats():
    """Возвращает match_stats только локального игрока (из player или allplayers)."""
    with _lock:
        allplayers = _state.get("allplayers") or {}
        pid = _local_steamid
        if pid and isinstance(allplayers, dict):
            # allplayers может быть ключ по строке или по числу
            p = allplayers.get(pid)
            if not p and str(pid).isdigit():
                try:
                    p = allplayers.get(int(pid))
                except (TypeError, ValueError):
                    pass
            if p:
                ms = p.get("match_stats") or {}
                if ms:
                    return dict(ms)
        p = _state.get("player") or {}
        return dict(p.get("match_stats") or {})


def _int_or_zero(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def get_stats():
    """Из текущего state достаёт kills, deaths и KD только локального игрока."""
    ms = _get_my_match_stats()
    try:
        kills = _int_or_zero(ms.get("kills", 0))
        deaths = _int_or_zero(ms.get("deaths", 0))
    except (TypeError, ValueError):
        kills = deaths = 0
    kd = (kills / deaths) if deaths else float(kills)
    return {
        "kills": kills,
        "deaths": deaths,
        "kd": round(kd, 2),
    }


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        client = self.client_address[0] if self.client_address else "?"
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        print(f"[POST] от {client} путь: {self.path} размер: {len(body)} байт")
        try:
            data = json.loads(body.decode("utf-8", errors="ignore"))
            incoming_player = data.get("player") or {}
            raw_steamid = incoming_player.get("steamid") or incoming_player.get("steamid64")
            incoming_steamid = _normalize_steamid(raw_steamid)
            with _lock:
                global _local_steamid
                if _local_steamid is None and incoming_steamid:
                    _local_steamid = incoming_steamid
                    print(f"[GSI] сохранён SteamID локального игрока: {_local_steamid}")
                # При спекте игра присылает статы наблюдаемого — не перезаписываем своего игрока
                if not IGNORE_SPECTATOR_FILTER and incoming_steamid and _local_steamid and incoming_steamid != _local_steamid:
                    data = {k: v for k, v in data.items() if k != "player"}
                deep_merge(_state, data)
            ms = _get_my_match_stats()
            k = ms.get("kills", "?")
            d = ms.get("deaths", "?")
            print(f"[GSI] данные от игры (только свои): kills={k}, deaths={d}")
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
                sid = _local_steamid
                allplayers = _state.get("allplayers") or {}
                ms = (player.get("match_stats") or {}) if isinstance(player, dict) else {}
                my_all = (allplayers.get(sid) or (allplayers.get(int(sid)) if sid and str(sid).isdigit() else {})) if isinstance(allplayers, dict) else {}
                ms_all = my_all.get("match_stats") or {} if isinstance(my_all, dict) else {}
            snapshot = {
                "local_steamid": sid,
                "player": player,
                "stats": get_stats(),
                "debug_keys": {
                    "player_match_stats_keys": list(ms.keys()) if isinstance(ms, dict) else [],
                    "allplayers_me_match_stats_keys": list(ms_all.keys()) if isinstance(ms_all, dict) else [],
                },
            }
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
    .label { font-size: 12px; font-weight: 600; opacity: 0.85; text-transform: uppercase; letter-spacing: 0.05em; }
    #kills, #deaths, #kd { font-family: "Orbitron", Arial, sans-serif; }
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
    if IGNORE_SPECTATOR_FILTER:
        print("Режим: статы того, кого присылает игра (в спеке — тиммейт). Чтобы только свои — CS2_GSI_IGNORE_SPECTATOR=0")
    else:
        print("Режим: только статы локального игрока (в спеке не подменяем).")
    server.serve_forever()


if __name__ == "__main__":
    main()
