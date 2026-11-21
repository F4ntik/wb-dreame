import argparse
import getpass
import json
import logging
import os
import subprocess
import sys
import uuid

from config import ACCOUNT, CONFIG, MODELS, Refused, atomic, read, validate

# Upstream can log account-scoped cloud response bodies and signed map URLs.
logging.getLogger("vendor").addHandler(logging.NullHandler())
logging.getLogger("vendor").propagate = False


def setup():
    from robot import discovery
    if os.geteuid() != 0 or not sys.stdin.isatty():
        raise Refused("Запустите wb-dreame setup от root в интерактивном SSH")
    country = input("Облачный регион Dreamehome (eu, ru, cn, us, sg, kr): ").strip().lower()
    if country not in ("eu", "ru", "cn", "us", "sg", "kr"):
        raise Refused("Проверьте регион аккаунта")
    username = input("Электронная почта Dreamehome: ").strip()
    password = getpass.getpass("Пароль Dreamehome: ")
    devices, token = discovery(username, password, country)
    for n, device in enumerate(devices, 1):
        print(n, device.get("customName") or MODELS[device["model"]], device["model"], "DID", device["did"])
    value = input("Номер вашего пылесоса (Enter для отмены): ").strip()
    if not value:
        return
    if not value.isdecimal() or not 1 <= int(value) <= len(devices):
        raise Refused("Недопустимый номер устройства")
    device = devices[int(value)-1]
    atomic(ACCOUNT, {"country": country, "username": username, "password": password, "did": str(device["did"]),
                     "model": device["model"], "auth_key": token, "binding": uuid.uuid4().hex})
    subprocess.run(["systemctl", "restart", "wb-mqtt-dreame.service"], check=True)
    print("Привязка сохранена. Откройте «Устройства» в Wiren Board.")


def main():
    p = argparse.ArgumentParser(description="Dreame для Wiren Board")
    p.add_argument("action", choices=["setup", "check", "panel", "install-panel", "remove-panel", "service",
                                      "clear-retained", "confed-tojson", "confed-fromjson"])
    args = p.parse_args()
    if args.action == "setup":
        setup()
    elif args.action == "check":
        validate(read(CONFIG))
        from vendor.device import DreameVacuumDevice
        print("Конфигурация и импорт ядра в порядке")
    elif args.action == "panel":
        from panel import make_panel
        sys.stdout.buffer.write((json.dumps(make_panel(), ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
    elif args.action in ("confed-tojson", "confed-fromjson"):
        from room_names import confed_save, confed_view, read_room_catalog, room_aliases
        form = json.load(sys.stdin.buffer)
        rooms = read_room_catalog()
        if args.action == "confed-tojson":
            config = validate(form)
            result = confed_view(config, rooms, room_aliases(config))
        else:
            config = validate(read(CONFIG))
            result = confed_save(form, config, rooms, room_aliases(config))
        sys.stdout.buffer.write((json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    elif args.action == "install-panel":
        from registration import register
        print(register())
    elif args.action == "remove-panel":
        from registration import unregister
        print(unregister())
    elif args.action == "clear-retained":
        from bridge import clear_retained
        clear_retained()
    else:
        from bridge import Bridge
        Bridge().run()


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        sys.exit(1)
    except Refused as ex:
        sys.exit(str(ex))
    except Exception as ex:
        sys.exit("Операция прервана (" + type(ex).__name__ + ")")
