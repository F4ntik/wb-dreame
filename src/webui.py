"""Private Unix socket service behind the Wiren Board service gate."""
import json
import mimetypes
import re
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from shutil import chown
from urllib.parse import urlsplit

from config import Refused

DREAME_PHOTOS = "https://api.ru.dreametech.com/media/catalog/product/cache/74c1057f7991b4edb2bc7bdaa94de933/s/"

ACCESSORY_FILES = (
    "RollerBrush-_02_1_160x.jpg", "output_160x.jpg", "output_7ecbea13-05d4-4e0e-8147-3d161e37029a_160x.jpg",
    "1_d09c295f-0c12-4a23-9a99-50c7e22121d5_160x.jpg", "output_2_edff9d8a-7f55-4426-af76-7935a11acdda_160x.jpg",
    "14_160x.png", "Scale_inhibitor_160x.jpg",
)
ACCESSORY_PHOTOS = " ".join("https://fr.dreametech.com/cdn/shop/files/" + name for name in ACCESSORY_FILES)


class WebConflict(Exception):
    pass


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(8)

    def reply(self, code, data, mime="application/json; charset=utf-8"):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; object-src 'none'; "
                         "frame-ancestors 'self'; base-uri 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data: "
                         f"{DREAME_PHOTOS}xaAkaRJMGwWN5Bx/download/03.jpg "
                         f"{DREAME_PHOTOS}EPRqR47CysdkPBc/download/05.jpg {ACCESSORY_PHOTOS}")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/":
            self.send_response(302)
            self.send_header("Location", "/dreame/")
            self.end_headers()
            return
        if path == "/dreame/api/state":
            self.reply(200, self.server.bridge.web_state())
            return
        if path == "/dreame/api/entrance":
            try:
                self.reply(200, self.server.bridge.web_entrance())
            except Refused as exc:
                self.reply(409, {"message": str(exc)})
            return
        shortcuts = re.fullmatch(r"/dreame/api/shortcuts(?:/([1-9][0-9]{0,2}))?", path)
        if shortcuts:
            try:
                sid = shortcuts.group(1)
                result = self.server.bridge.web_shortcuts(int(sid) if sid is not None else None)
            except (Refused, WebConflict) as exc:
                self.reply(409, {"message": str(exc)})
            except Exception:
                self.reply(503, {"message": "Не удалось прочитать сценарии Dreamehome."})
            else:
                self.reply(200, result)
            return
        if path == "/dreame/api/schedules":
            try:
                self.reply(200, self.server.bridge.web_schedules())
            except (Refused, WebConflict) as exc:
                self.reply(503, {"message": str(exc)})
            return
        history = re.fullmatch(r"/dreame/api/history/([1-9][0-9]{0,10})(?:/obstacles/([a-zA-Z0-9_-]{1,96})/photo)?", path)
        if history:
            timestamp, obstacle_id = history.groups()
            try:
                result = self.server.bridge.web_history(int(timestamp), obstacle_id)
            except KeyError:
                self.reply(404, {"message": "Эта запись больше не входит в историю уборок."})
            except (LookupError, Refused) as exc:
                self.reply(410, {"message": str(exc)})
            except WebConflict as exc:
                self.reply(503, {"message": str(exc)})
            except Exception:
                self.reply(503, {"message": "Не удалось прочитать архив из Dreamehome. Повторите позже."})
            else:
                if obstacle_id:
                    self.reply(200, *result)
                else:
                    self.reply(200, result)
            return
        if path == "/dreame/api/underlay":
            bridge = self.server.bridge
            if bridge.underlay.for_map(bridge.latest.get("map")):
                self.reply(200, bridge.underlay.data, "image/jpeg")
            else:
                self.reply(404, {"message": "Для этой карты подложка не настроена"})
            return
        files = self.server.files
        if path not in files:
            self.reply(404, {"message": "Страница не найдена"})
            return
        file = files[path]
        self.reply(200, file.read_bytes(), (mimetypes.guess_type(file.name)[0] or "application/octet-stream") + "; charset=utf-8")

    def do_POST(self):
        path = urlsplit(self.path).path
        if path not in ("/dreame/api/job", "/dreame/api/preview", "/dreame/api/preference", "/dreame/api/underlay",
                        "/dreame/api/schedules", "/dreame/api/entrance"):
            self.reply(404, {"status": "error", "message": "Действие не найдено"})
            return
        try:
            size = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            size = 0
        # Read the bounded body before replying; an early close can reset the proxy's upload.
        body = None
        if 0 < size <= 8192 and not self.headers.get("Transfer-Encoding"):
            body = self.rfile.read(size)
        if self.headers.get("X-Dreame-Request") != "1" or self.headers.get_content_type() != "application/json":
            self.reply(403, {"status": "error", "message": "Откройте управление через интерфейс WB"})
            return
        origin = self.headers.get("Origin")
        if origin:
            source = urlsplit(origin)
            if source.scheme not in ("http", "https") or source.hostname != urlsplit("//" + self.headers.get("Host", "")).hostname:
                self.reply(403, {"status": "error", "message": "Запрос с другого сайта отклонён"})
                return
        try:
            if body is None or len(body) != size:
                raise ValueError
            request = json.loads(body)
            if not isinstance(request, dict):
                raise ValueError
        except (ValueError, RecursionError):
            self.reply(400, {"status": "error", "message": "Проверьте формат задания"})
            return
        try:
            if path.endswith("/entrance"):
                outcome = self.server.bridge.web_entrance(request)
            elif path.endswith("/underlay"):
                outcome = self.server.bridge.web_adjust(request)
            elif path.endswith("/preference"):
                outcome = self.server.bridge.web_preference(request)
            elif path.endswith("/schedules"):
                outcome = self.server.bridge.web_schedules(request)
            else:
                outcome = self.server.bridge.web_job(request, preview=path.endswith("/preview"))
            self.reply(200, outcome)
        except Refused as exc:
            self.reply(409, {"status": "refused", "message": str(exc)})
        except WebConflict as exc:
            self.reply(503, {"status": "unavailable", "message": str(exc)})
        except Exception:
            self.reply(503, {"status": "unknown", "message": "Ответ робота не подтверждён. Проверьте его состояние."})


def start_ui(bridge, directory=Path("/usr/share/wb-mqtt-dreame/web"),
             socket_path=Path("/run/wb-mqtt-dreame/web.sock")):
    from socketserver import ThreadingUnixStreamServer

    files = {"/dreame/" + f.name: f for f in directory.iterdir() if f.is_file() and f.suffix in (".html", ".js", ".css")}
    files["/dreame/"] = directory / "index.html"
    server = ThreadingUnixStreamServer(str(socket_path), Handler, bind_and_activate=False)
    server.daemon_threads = True
    server.bridge = bridge
    server.files = files
    try:
        server.server_bind()
        chown(socket_path, group="www-data")
        socket_path.chmod(0o660)
        server.server_activate()
        threading.Thread(target=server.serve_forever, name="dreame-ui", daemon=True).start()
    except Exception:
        server.server_close()
        raise
    return server
