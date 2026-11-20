"""Register the embedded control page through the WB dashboard API."""
import json
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from config import Refused


def embedded_panel():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="100%" aria-label="Управление пылесосом">'
           '<foreignObject width="100%" height="100%"><iframe xmlns="http://www.w3.org/1999/xhtml" '
           'title="Карта и управление Dreame" src="/dreame/" style="display:block;width:100%;height:100%;'
           'border:0;border-radius:12px;background:white"></iframe></foreignObject></svg>')
    return {"id": "dreame-control", "name": "Пылесос Dreame", "isSvg": True, "widgets": [],
            "swipe": {"enable": False, "left": None, "right": None}, "svg_fullwidth": True,
            "svg_url": "local", "svg": {"current": svg, "params": []}}


def register():
    panel = embedded_panel()
    url = "http://127.0.0.1/api/dashboards"
    with urlopen(url, timeout=5) as response:
        index = json.load(response)
    if any(d["id"] == panel["id"] for d in index["dashboards"]):
        with urlopen(url + "/" + panel["id"] + "/svg", timeout=5) as response:
            if b'src="/dreame/"' not in response.read():
                raise Refused("ID dreame-control занят пользовательской панелью.")
        return "Панель Dreame уже зарегистрирована"
    body = json.dumps(panel, ensure_ascii=False).encode()
    req = Request(url + "/dreame-create-" + uuid.uuid4().hex, data=body, method="PUT",
                  headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=8) as response:
            if response.status != 201:
                raise Refused("Web UI не подтвердил создание панели")
    except HTTPError as exc:
        raise Refused(f"Web UI отклонил регистрацию панели (HTTP {exc.code})") from None
    return "Панель Dreame добавлена в веб-интерфейс"


def unregister():
    panel = embedded_panel()
    url = "http://127.0.0.1/api/dashboards"
    with urlopen(url, timeout=5) as response:
        entry = next((d for d in json.load(response)["dashboards"] if d.get("id") == panel["id"]), None)
    if entry is None:
        return "Панель уже снята"
    # The index can omit the SVG body. Read the actual file and preserve any owner edits.
    from pathlib import Path
    stored = json.loads(Path("/etc/wb-webui.conf").read_bytes())
    entry = next((d for d in stored["dashboards"] if d.get("id") == panel["id"]), None)
    if entry != panel:
        return "Изменённая пользователем панель сохранена"
    with urlopen(Request(url + "/" + panel["id"], method="DELETE"), timeout=5):
        return "Штатная панель Dreame снята"
