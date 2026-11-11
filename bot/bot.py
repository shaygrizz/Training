#!/usr/bin/env python3
"""Телеграм-бот «Форма»: анкета при старте и кнопка запуска мини-приложения.

Без внешних зависимостей — нужен только Python 3.8+.
Настройки берутся из переменных окружения или из файла bot/.env:
    BOT_TOKEN   — токен от @BotFather
    WEBAPP_URL  — https-адрес мини-приложения (index.html), например GitHub Pages
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("DATA_FILE", HERE / "data" / "users.json"))


def load_env():
    f = HERE / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


load_env()
TOKEN = os.environ.get("BOT_TOKEN", "")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "")
API = "https://api.telegram.org/bot%s/" % TOKEN

# ---------- справочники (те же коды, что в мини-приложении) ----------
SEX = {"m": "Мужской", "f": "Женский"}
GOALS = {"gain": "Набор мышечной массы", "lose": "Похудение", "fit": "Форма и здоровье", "str": "Сила"}
PLACES = {"gym": "Тренажёрный зал", "home": "Дом"}
EQS = {"d": "Гантели", "k": "Гиря", "b": "Турник", "r": "Резинки"}
EXPS = {"0": "Новичок", "1": "Немного занимался", "2": "Уверенно"}
ACTS = {"0": "Сидячий", "1": "Средний", "2": "Высокий (на ногах, физ. работа)"}
LIMS = {"b": "Поясница", "s": "Плечи", "k": "Колени", "n": "Шея", "p": "Осанка, прогиб в пояснице"}
DAYS = {str(i): str(i) for i in range(2, 7)}

# шаг анкеты: (поле, тип, вопрос, варианты | (мин, макс))
STEPS = [
    ("s", "choice", "Твой пол?", SEX),
    ("a", "num", "Сколько тебе лет?", (10, 95)),
    ("h", "num", "Рост в сантиметрах?", (120, 230)),
    ("w", "num", "Вес в килограммах? Можно с десятыми: 72.5", (30, 250)),
    ("g", "choice", "Какая цель?", GOALS),
    ("tw", "num", "Какой вес хочешь? Напиши число в кг или нажми «Пропустить».", (30, 250)),
    ("p", "choice", "Где будешь заниматься?", PLACES),
    ("q", "multi", "Какой инвентарь есть дома? Отметь всё, что есть, и нажми «Готово».", EQS),
    ("e", "choice", "Какой у тебя опыт тренировок?", EXPS),
    ("d", "choice", "Сколько тренировок в неделю?", DAYS),
    ("af", "choice", "Какой у тебя образ жизни вне тренировок?", ACTS),
    ("l", "multi", "Что беспокоит? Отметь всё подходящее — план это учтёт. Если ничего, просто нажми «Готово».", LIMS),
]
FIELDS = [s[0] for s in STEPS]
INT_FIELDS = {"a", "h", "e", "d", "af"}
EDIT_LABELS = {"s": "Пол", "a": "Возраст", "h": "Рост", "w": "Вес", "g": "Цель", "tw": "Желаемый вес", "p": "Место",
               "q": "Инвентарь", "e": "Опыт", "d": "Дней в неделю", "af": "Образ жизни", "l": "Ограничения"}


# ---------- Telegram API ----------
def api(method, **params):
    req = urllib.request.Request(API + method, data=json.dumps(params).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=70) as r:
            return json.load(r).get("result")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        if "message is not modified" not in body:
            print("API %s: %s %s" % (method, e.code, body), file=sys.stderr)
    except Exception as e:  # сеть, таймаут
        print("API %s: %s" % (method, e), file=sys.stderr)
    return None


def kb(rows):
    return {"inline_keyboard": rows}


def send(chat, text, markup=None):
    p = {"chat_id": chat, "text": text, "parse_mode": "HTML"}
    if markup:
        p["reply_markup"] = markup
    return api("sendMessage", **p)


def edit(chat, mid, text, markup=None):
    p = {"chat_id": chat, "message_id": mid, "text": text, "parse_mode": "HTML"}
    if markup:
        p["reply_markup"] = markup
    return api("editMessageText", **p)


# ---------- хранилище ----------
def load_db():
    try:
        return json.loads(DATA.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_db():
    DATA.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA.with_suffix(".tmp")
    tmp.write_text(json.dumps(DB, ensure_ascii=False), encoding="utf-8")
    tmp.replace(DATA)


DB = load_db()


def user(uid):
    return DB.setdefault(str(uid), {"profile": None, "draft": None, "step": None, "one": False})


# ---------- анкета ----------
def skip_step(field, draft):
    if field == "tw":
        return draft.get("g") not in ("gain", "lose")
    if field == "q":
        return draft.get("p") != "home"
    return False


def ask(chat, u, mid=None):
    """Показать текущий вопрос анкеты."""
    field, kind, text, opts = STEPS[u["step"]]
    d = u["draft"]
    if kind == "choice":
        items = list(opts.items())
        per = 5 if field == "d" else 1 if max(len(t) for _, t in items) > 14 else 2
        btns = [{"text": t, "callback_data": "c:%s:%s" % (field, v)} for v, t in items]
        rows = [btns[i:i + per] for i in range(0, len(btns), per)]
    elif kind == "multi":
        cur = d.get(field, "")
        rows = [[{"text": ("✅ " if v in cur else "") + t, "callback_data": "m:%s:%s" % (field, v)}] for v, t in opts.items()]
        rows.append([{"text": "Готово →", "callback_data": "done:" + field}])
    else:
        rows = [[{"text": "Пропустить", "callback_data": "skip:tw"}]] if field == "tw" else None
    markup = kb(rows) if rows else None
    if mid and markup:
        edit(chat, mid, text, markup)
    else:
        send(chat, text, markup)


def advance(chat, u, tg_user, mid=None):
    """Перейти к следующему вопросу или завершить анкету."""
    d = u["draft"]
    if u["one"]:
        # после смены места/цели нужно доспросить зависимое поле
        field = STEPS[u["step"]][0]
        dep = {"p": "q", "g": "tw"}.get(field)
        if dep and not skip_step(dep, d):
            u["step"] = FIELDS.index(dep)
            save_db()
            return ask(chat, u, mid)
        return finish(chat, u, tg_user, mid)
    step = u["step"] + 1
    while step < len(STEPS) and skip_step(STEPS[step][0], d):
        step += 1
    if step >= len(STEPS):
        return finish(chat, u, tg_user, mid)
    u["step"] = step
    save_db()
    ask(chat, u, mid)


def finish(chat, u, tg_user, mid=None):
    d = u["draft"]
    if d.get("p") != "home":
        d["q"] = ""
    if d.get("g") not in ("gain", "lose"):
        d["tw"] = 0
    d.setdefault("q", "")
    d.setdefault("l", "")
    d.setdefault("tw", 0)
    d["n"] = (tg_user.get("first_name") or "")[:40]
    d["ts"] = int(time.time() * 1000)
    u.update(profile=d, draft=None, step=None, one=False)
    save_db()
    text = "Готово, анкета сохранена.\n\n" + summary(d) + "\n\nПлан собран под тебя — открывай приложение."
    if mid:
        edit(chat, mid, text, main_kb(d))
    else:
        send(chat, text, main_kb(d))
    set_menu(chat, d)


def summary(p):
    lines = [
        "<b>%s</b>, %s лет, %s см, %s кг" % (SEX[p["s"]], p["a"], p["h"], fmt(p["w"])),
        "Цель: <b>%s</b>%s" % (GOALS[p["g"]], " → %s кг" % fmt(p["tw"]) if p.get("tw") else ""),
        "Место: %s%s" % (PLACES[p["p"]], " (%s)" % ", ".join(EQS[x].lower() for x in p["q"]) if p.get("q") else ""),
        "Опыт: %s" % EXPS[str(p["e"])],
        "Тренировок в неделю: %s" % p["d"],
        "Образ жизни: %s" % ACTS[str(p["af"])].lower(),
        "Учитываю: %s" % (", ".join(LIMS[x].lower() for x in p["l"]) if p.get("l") else "ограничений нет"),
    ]
    return "\n".join(lines)


def fmt(v):
    return ("%g" % v).replace(".", ",")


def app_url(p):
    raw = json.dumps(p, ensure_ascii=False, separators=(",", ":")).encode()
    q = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return WEBAPP_URL + ("&" if "?" in WEBAPP_URL else "?") + "p=" + q


def main_kb(p):
    rows = []
    if WEBAPP_URL.startswith("https://"):
        rows.append([{"text": "🏋️ Открыть приложение", "web_app": {"url": app_url(p)}}])
    rows.append([{"text": "Изменить анкету", "callback_data": "edit"}])
    return kb(rows)


def set_menu(chat, p):
    """Кнопка меню слева от поля ввода — тоже открывает приложение с актуальной анкетой."""
    if WEBAPP_URL.startswith("https://"):
        api("setChatMenuButton", chat_id=chat,
            menu_button={"type": "web_app", "text": "Тренировка", "web_app": {"url": app_url(p)}})


def start_form(chat, u, tg_user):
    u.update(draft={}, step=0, one=False)
    save_db()
    ask(chat, u)


def edit_menu(chat, u, mid=None):
    p = u["profile"]
    btns = [{"text": EDIT_LABELS[f], "callback_data": "e:" + f} for f in FIELDS
            if not skip_step(f, p)]
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    rows.append([{"text": "Пройти анкету заново", "callback_data": "redo"}])
    rows.append([{"text": "← Назад", "callback_data": "back"}])
    text = summary(p) + "\n\nЧто изменить?"
    if mid:
        edit(chat, mid, text, kb(rows))
    else:
        send(chat, text, kb(rows))


def show_profile(chat, u, mid=None):
    text = summary(u["profile"]) + "\n\nВесь прогресс и план — в приложении."
    if mid:
        edit(chat, mid, text, main_kb(u["profile"]))
    else:
        send(chat, text, main_kb(u["profile"]))


# ---------- обработчики ----------
HELP = ("/start — анкета и запуск\n/app — открыть приложение\n/profile — моя анкета, изменить данные\n"
        "/reset — удалить анкету из бота\n/help — эта справка")


def on_message(m):
    chat = m["chat"]["id"]
    if m["chat"].get("type") != "private":
        return
    tg_user = m.get("from", {})
    u = user(tg_user.get("id", chat))
    text = (m.get("text") or "").strip()
    cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""

    if cmd == "/start":
        if u["profile"]:
            send(chat, "С возвращением, %s! План ждёт в приложении." % esc(tg_user.get("first_name") or "атлет"),
                 main_kb(u["profile"]))
            set_menu(chat, u["profile"])
        else:
            send(chat, "Привет, %s! Я соберу тебе тренировочный план и буду вести прогресс: подходы, веса, "
                       "рекорды, питание.\n\nСначала несколько вопросов — это займёт минуту."
                 % esc(tg_user.get("first_name") or "атлет"))
            start_form(chat, u, tg_user)
        return
    if cmd == "/help":
        return send(chat, HELP)
    if cmd in ("/app", "/profile", "/plan"):
        if not u["profile"]:
            return send(chat, "Сначала заполни анкету: /start")
        return show_profile(chat, u)
    if cmd == "/reset":
        u.update(profile=None, draft=None, step=None, one=False)
        save_db()
        api("setChatMenuButton", chat_id=chat, menu_button={"type": "default"})
        return send(chat, "Анкета удалена. Прогресс тренировок хранится в приложении и не тронут.\n"
                          "Заполнить заново: /start")
    if cmd:
        return send(chat, "Не знаю такой команды.\n\n" + HELP)

    # ответ числом на вопрос анкеты
    if u["step"] is not None and u["draft"] is not None:
        field, kind, _, opts = STEPS[u["step"]]
        if kind != "num":
            return send(chat, "Выбери вариант кнопкой выше 👆")
        try:
            v = float(text.replace(",", ".").split()[0])
        except (ValueError, IndexError):
            return send(chat, "Напиши просто число, например: %s" % {"a": 25, "h": 180}.get(field, 75))
        lo, hi = opts
        if not lo <= v <= hi:
            return send(chat, "Похоже на опечатку. Нужно число от %s до %s." % (lo, hi))
        u["draft"][field] = int(round(v)) if field in INT_FIELDS else round(v, 1)
        return advance(chat, u, tg_user)
    if u["profile"]:
        return show_profile(chat, u)
    send(chat, "Нажми /start, чтобы начать.")


def on_callback(c):
    api("answerCallbackQuery", callback_query_id=c["id"])
    msg = c.get("message") or {}
    chat = msg.get("chat", {}).get("id")
    mid = msg.get("message_id")
    if not chat:
        return
    tg_user = c.get("from", {})
    u = user(tg_user.get("id", chat))
    parts = (c.get("data") or "").split(":")
    act = parts[0]

    if act == "edit" and u["profile"]:
        return edit_menu(chat, u, mid)
    if act == "back" and u["profile"]:
        u.update(draft=None, step=None, one=False)
        save_db()
        return show_profile(chat, u, mid)
    if act == "redo":
        edit(chat, mid, "Хорошо, пройдём анкету заново.")
        return start_form(chat, u, tg_user)
    if act == "e" and u["profile"] and len(parts) == 2 and parts[1] in FIELDS:
        u.update(draft=dict(u["profile"]), step=FIELDS.index(parts[1]), one=True)
        save_db()
        return ask(chat, u, mid if STEPS[u["step"]][1] != "num" or parts[1] == "tw" else None)

    # дальше — только ответы на текущий вопрос анкеты
    if u["step"] is None or u["draft"] is None or len(parts) < 2:
        return
    field, kind, text, opts = STEPS[u["step"]]
    if parts[1] != field:
        return
    d = u["draft"]
    if act == "c" and kind == "choice" and len(parts) == 3 and parts[2] in opts:
        d[field] = int(parts[2]) if field in INT_FIELDS else parts[2]
        if not u["one"]:
            edit(chat, mid, "%s\n<b>%s</b>" % (text.split("?")[0] + ":", opts[parts[2]]))
            mid = None
        return advance(chat, u, tg_user, mid)
    if act == "m" and kind == "multi" and len(parts) == 3 and parts[2] in opts:
        cur = d.get(field, "")
        d[field] = cur.replace(parts[2], "") if parts[2] in cur else cur + parts[2]
        save_db()
        return ask(chat, u, mid)
    if act == "done" and kind == "multi":
        d.setdefault(field, "")
        if not u["one"]:
            chosen = ", ".join(opts[x].lower() for x in d[field]) or "ничего"
            edit(chat, mid, "%s <b>%s</b>" % ({"q": "Инвентарь:", "l": "Учитываю:"}[field], chosen))
            mid = None
        return advance(chat, u, tg_user, mid)
    if act == "skip" and field == "tw":
        d["tw"] = 0
        if not u["one"]:
            edit(chat, mid, "Желаемый вес: не задан")
            mid = None
        return advance(chat, u, tg_user, mid)


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def main():
    if not TOKEN:
        sys.exit("Не задан BOT_TOKEN. Впиши токен в bot/.env (см. bot/.env.example).")
    me = api("getMe")
    if not me:
        sys.exit("Telegram не принял токен — проверь BOT_TOKEN.")
    if not WEBAPP_URL.startswith("https://"):
        print("ВНИМАНИЕ: WEBAPP_URL не задан или не https — кнопка приложения показана не будет.", file=sys.stderr)
    api("setMyCommands", commands=[
        {"command": "start", "description": "Анкета и запуск"},
        {"command": "app", "description": "Открыть приложение"},
        {"command": "profile", "description": "Моя анкета"},
        {"command": "help", "description": "Справка"},
    ])
    print("Бот @%s запущен. Остановить: Ctrl+C" % me.get("username"))
    offset = 0
    while True:
        updates = api("getUpdates", offset=offset, timeout=50, allowed_updates=["message", "callback_query"])
        if updates is None:
            time.sleep(3)
            continue
        for up in updates:
            offset = up["update_id"] + 1
            try:
                if "message" in up:
                    on_message(up["message"])
                elif "callback_query" in up:
                    on_callback(up["callback_query"])
            except Exception as e:  # один сбойный апдейт не должен ронять бота
                print("Ошибка обработки: %r" % e, file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nОстановлен.")
