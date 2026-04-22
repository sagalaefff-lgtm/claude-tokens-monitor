#!/usr/bin/env python3
"""Claude Code token counter — reads ~/.claude/projects/*/*.jsonl and shows:
- Current session: tokens and % of quota + time remaining (matches claude.ai/settings/usage)
- Weekly: tokens over 7 days + % of weekly quota

Session logic mirrors ccusage and claude.ai:
  Session = first request in a continuous block; block ends 5h after the first
  request OR when the pause between requests exceeds 5h.

Quotas are tuned by Claude plan (pro / max5 / max20). Choose interactively
on first run with --setup, or via the CLAUDE_PLAN env variable.

Modes:
  claude_tokens.py              — one-shot report
  claude_tokens.py --live       — live mode, refresh every 10s
  claude_tokens.py --swiftbar   — single-line output for SwiftBar plugin
  claude_tokens.py --setup      — interactive setup (plan + timezone)
  claude_tokens.py --no-api     — fallback to local JSONL only (skip Anthropic API)
"""
import json, sys, time, os, subprocess, urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta

PROJECTS = Path.home() / ".claude" / "projects"
WINDOW_H = 5           # Claude Code: 5-часовое сессионное окно
WEEKLY_DAYS = 7

# ─── API-режим: реальные данные с сервера Anthropic ────────────────────────
# Тот же endpoint что использует приложение Claude Usage.app и claude.ai/settings/usage.
# Возвращает utilization (%) и точное время сброса — без локального угадывания.

API_CACHE_FILE = Path.home() / ".claude" / "usage_api_cache.json"
API_CACHE_TTL = 120  # секунд — API жёстко rate-limit'ит 429, нужна пауза между запросами

def fetch_api_usage():
    """Дёргает Anthropic OAuth usage endpoint с кешем 60с. Возвращает dict или None."""
    # 1. Пробуем кеш
    try:
        cache = json.loads(API_CACHE_FILE.read_text())
        if time.time() - cache.get("fetched_at", 0) < API_CACHE_TTL:
            return cache.get("data")
    except Exception:
        pass
    # 2. Свежий запрос
    try:
        token_json = subprocess.check_output(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            stderr=subprocess.DEVNULL, timeout=5
        ).decode().strip()
        token = json.loads(token_json)["claudeAiOauth"]["accessToken"]
        req = urllib.request.Request(
            "https://api.anthropic.com/api/oauth/usage",
            headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
                "anthropic-version": "2023-06-01",
            }
        )
        data = json.loads(urllib.request.urlopen(req, timeout=5).read())
        API_CACHE_FILE.write_text(json.dumps({"fetched_at": time.time(), "data": data}))
        return data
    except Exception:
        # При 429/ошибке — возвращаем stale-кеш если есть
        try:
            return json.loads(API_CACHE_FILE.read_text()).get("data")
        except Exception:
            return None


def build_report_api(api_data, rows_week, limits, now):
    """Строит отчёт из API-данных (точные %) + локальные абсолюты из JSONL."""
    five = api_data.get("five_hour") or {}
    seven = api_data.get("seven_day") or {}
    seven_sonnet = api_data.get("seven_day_sonnet") or {}

    # Локальный подсчёт — для абсолютных чисел токенов (API их не даёт)
    rep_local = build_report(rows_week, limits, now)
    q = get_quotas()

    def parse_reset(s):
        if not s: return now
        return datetime.fromisoformat(s.replace("Z", "+00:00"))

    s_reset_at = parse_reset(five.get("resets_at"))
    s_delta = s_reset_at - now
    wa_reset_at = parse_reset(seven.get("resets_at"))
    ws_reset_at = parse_reset(seven_sonnet.get("resets_at"))

    def pct_to_fields(util, reset_at, quota, local_eff):
        # Если utilization >0 — используем его для расчёта eff; иначе берём локальный
        eff = int(util * quota / 100) if util and util > 0 else local_eff
        return {
            "pct": float(util or 0.0),
            "eff": eff,
            "quota": quota,
            "reset": reset_at,
        }

    s_fields = pct_to_fields(five.get("utilization", 0), s_reset_at, q["session"], rep_local["session"]["eff"])
    s_fields["reset_h"] = max(0, int(s_delta.total_seconds()) // 3600)
    s_fields["reset_m"] = max(0, (int(s_delta.total_seconds()) % 3600) // 60)
    s_fields["start"] = rep_local["session"].get("start", "—")

    return {
        "session": s_fields,
        "w_all":    pct_to_fields(seven.get("utilization", 0), wa_reset_at, q["weekly"], rep_local["w_all"]["eff"]),
        "w_sonnet": pct_to_fields(seven_sonnet.get("utilization", 0), ws_reset_at, q["weekly_sonnet"], rep_local["w_sonnet"]["eff"]),
        "models":   rep_local["models"],
        "plan":     f"{PLAN} · api",
    }

# Approximate token quotas per plan (effective = input + cache_creation + output, excluding cache_read).
# Numbers based on community calibration. Run --calibrate-session/weekly/sonnet <pct> to fine-tune
# for your actual quota; calibrated values are stored in ~/.claude/usage_calibration.json
# and override these defaults.
PLAN_QUOTAS = {
    "pro":   {"session":    940_000, "weekly":  26_300_000, "weekly_sonnet":  23_100_000},
    "max5":  {"session":  4_700_000, "weekly": 131_500_000, "weekly_sonnet": 115_600_000},
    "max20": {"session": 18_800_000, "weekly": 526_000_000, "weekly_sonnet": 462_400_000},
}

# ─── User configuration ────────────────────────────────────────────────────
# Stored in ~/.config/claude-tokens/config.json after running --setup.
# Lookup precedence:
#   1. ~/.config/claude-tokens/config.json   (set by --setup)
#   2. CLAUDE_PLAN / CLAUDE_TZ_OFFSET_H env vars
#   3. defaults (max5, UTC+0)
CONFIG_FILE = Path.home() / ".config" / "claude-tokens" / "config.json"


def load_user_config():
    """Returns dict with user-set 'plan' and 'tz_offset_h'. Empty if no config."""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    return {}


def save_user_config(data):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, indent=2))


_USER_CFG = load_user_config()
PLAN = _USER_CFG.get("plan") or os.environ.get("CLAUDE_PLAN", "max5")

# Калибровка: ~/.claude/usage_calibration.json хранит откалиброванные квоты
# (если есть — используется вместо PLAN_QUOTAS).
CALIB_FILE = Path.home() / ".claude" / "usage_calibration.json"

def load_calibration():
    if CALIB_FILE.exists():
        try: return json.loads(CALIB_FILE.read_text())
        except: pass
    return {}

def save_calibration(data):
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    CALIB_FILE.write_text(json.dumps(data, indent=2))

def get_quotas():
    default = PLAN_QUOTAS.get(PLAN, PLAN_QUOTAS["max5"])
    calib = load_calibration()
    return {
        "session":       calib.get("session", default["session"]),
        "weekly":        calib.get("weekly", default["weekly"]),
        "weekly_sonnet": calib.get("weekly_sonnet", default["weekly_sonnet"]),
    }

# UTC offset (in hours) for the user's local timezone — used to align weekly reset
# with claude.ai (Sat 23:00 / Sun 00:00 local time). Set via --setup or env CLAUDE_TZ_OFFSET_H.
try:
    USER_TZ_OFFSET_H = int(_USER_CFG.get("tz_offset_h", os.environ.get("CLAUDE_TZ_OFFSET_H", 0)))
except (ValueError, TypeError):
    USER_TZ_OFFSET_H = 0


def load_all_rows(since_utc):
    """Возвращает (rows, limit_events): усовые запросы и моменты hit-limit."""
    rows = []; limits = []
    for jsonl in PROJECTS.rglob("*.jsonl"):
        try:
            for line in jsonl.read_text(encoding="utf-8", errors="ignore").splitlines():
                try: d = json.loads(line)
                except: continue
                ts = d.get("timestamp", "")
                if not ts: continue
                try:
                    t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except: continue
                if t < since_utc: continue
                msg = d.get("message", {})

                # synthetic hit-limit событие
                if msg.get("model") == "<synthetic>":
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        content = " ".join(c.get("text","") for c in content if isinstance(c,dict))
                    if "hit your limit" in str(content).lower():
                        limits.append(t)
                    continue

                u = msg.get("usage") or {}
                if not u: continue
                eff = (u.get("input_tokens", 0) or 0) \
                      + (u.get("cache_creation_input_tokens", 0) or 0) \
                      + (u.get("output_tokens", 0) or 0)
                if eff == 0: continue
                rows.append({
                    "t": t,
                    "in":    u.get("input_tokens", 0) or 0,
                    "out":   u.get("output_tokens", 0) or 0,
                    "cache_c": u.get("cache_creation_input_tokens", 0) or 0,
                    "cache_r": u.get("cache_read_input_tokens", 0) or 0,
                    "eff":   eff,
                    "model": (msg.get("model") or d.get("model") or "").split("/")[-1],
                })
        except Exception:
            continue
    rows.sort(key=lambda r: r["t"])
    limits.sort()
    return rows, limits


def find_current_session_block(rows, limits, now):
    """Сессия начинается с первого реального запроса ПОСЛЕ последнего hit-limit
    (если был), иначе — от первого запроса в непрерывном блоке с паузой ≤ 5ч."""
    if not rows: return None

    offset = timedelta(seconds=load_calibration().get("timer_offset_seconds", 0))

    # Если был hit limit — сессия с первого запроса после него
    if limits:
        last_limit = limits[-1]
        after = [r for r in rows if r["t"] > last_limit]
        if after:
            start = after[0]["t"]
            end = start + timedelta(hours=WINDOW_H) + offset
            if now <= end:
                return {"start": start, "end": end, "rows": after}

    # Иначе — скользящий блок от последнего большого gap
    block_start = rows[0]["t"]; block_rows = []
    for r in rows:
        if r["t"] - block_start > timedelta(hours=WINDOW_H):
            block_start = r["t"]; block_rows = []
        block_rows.append(r)

    end = block_start + timedelta(hours=WINDOW_H) + offset
    if now > end: return None
    return {"start": block_start, "end": end, "rows": block_rows}


def fmt(n):
    if n >= 1_000_000_000: return f"{n/1_000_000_000:.2f}B"
    if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
    if n >= 1_000: return f"{n/1_000:.1f}K"
    return str(n)


def pct(used, quota):
    return 100.0 * used / quota if quota else 0.0


def next_weekly_reset(now, weekday, hour):
    """Ближайший {weekday}(0=Mon..6=Sun) {hour}:00 в tz пользователя, возвращает UTC."""
    user_now = now + timedelta(hours=USER_TZ_OFFSET_H)
    days_ahead = (weekday - user_now.weekday()) % 7
    target = user_now.replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=days_ahead)
    if target <= user_now: target += timedelta(days=7)
    return target - timedelta(hours=USER_TZ_OFFSET_H)


def fmt_reset(delta):
    total = int(delta.total_seconds())
    if total < 0: return "сейчас"
    d = total // 86400
    h = (total % 86400) // 3600
    m = (total % 3600) // 60
    if d > 0: return f"{d}д {h}ч"
    if h > 0: return f"{h}ч {m:02d}м"
    return f"{m}м"


def build_report(rows_week, limits, now):
    session = find_current_session_block(rows_week, limits, now)
    quotas = get_quotas()

    # Session aggregate
    if session:
        s_eff = sum(r["eff"] for r in session["rows"])
        s_pct = pct(s_eff, quotas["session"])
        reset_in = session["end"] - now
        s_reset_h = int(reset_in.total_seconds() // 3600)
        s_reset_m = int((reset_in.total_seconds() % 3600) // 60)
        s_start = session["start"].astimezone().strftime("%H:%M")
    else:
        s_eff = 0; s_pct = 0; s_reset_h = 0; s_reset_m = 0; s_start = "—"

    # Weekly aggregate — All models (Sat 23:00 local reset)
    w_all_reset = next_weekly_reset(now, weekday=5, hour=23)
    w_all_start = w_all_reset - timedelta(days=7)
    w_all_rows = [r for r in rows_week if r["t"] >= w_all_start]
    w_all_eff = sum(r["eff"] for r in w_all_rows)
    w_all_pct = pct(w_all_eff, quotas["weekly"])

    # Weekly Sonnet only (Sun 00:00 local reset)
    w_sonnet_reset = next_weekly_reset(now, weekday=6, hour=0)
    w_sonnet_start = w_sonnet_reset - timedelta(days=7)
    w_sonnet_rows = [r for r in rows_week if r["t"] >= w_sonnet_start and "sonnet" in r["model"].lower()]
    w_sonnet_eff = sum(r["eff"] for r in w_sonnet_rows)
    w_sonnet_pct = pct(w_sonnet_eff, quotas["weekly_sonnet"])

    # by model (session)
    models = {}
    if session:
        for r in session["rows"]:
            k = r["model"].replace("claude-", "") or "?"
            models[k] = models.get(k, 0) + r["eff"]

    return {
        "plan": PLAN,
        "session": {"eff": s_eff, "pct": s_pct, "reset_h": s_reset_h, "reset_m": s_reset_m, "start": s_start, "quota": quotas["session"]},
        "w_all":   {"eff": w_all_eff,    "pct": w_all_pct,    "quota": quotas["weekly"],        "reset": w_all_reset},
        "w_sonnet":{"eff": w_sonnet_eff, "pct": w_sonnet_pct, "quota": quotas["weekly_sonnet"], "reset": w_sonnet_reset},
        "models": models,
    }


def render(rep, compact=False):
    s = rep["session"]; wa = rep["w_all"]; ws = rep["w_sonnet"]
    now = datetime.now(timezone.utc)
    if compact:
        return f"⚡ {s['pct']:.1f}% · {fmt(s['eff'])} · {s['reset_h']}ч{s['reset_m']:02d}м"
    lines = [
        f"═══ Claude Code Usage · план {rep['plan']} ═══",
        f"",
        f"Current session ({s['start']} → +5ч):",
        f"  Использовано:  {s['pct']:5.1f}%   {fmt(s['eff'])} / {fmt(s['quota'])}",
        f"  Сброс через:   {s['reset_h']}ч {s['reset_m']:02d}м",
        f"",
        f"Weekly · All models (сброс {wa['reset'].astimezone().strftime('%a %H:%M')}):",
        f"  Использовано:  {wa['pct']:5.1f}%   {fmt(wa['eff'])} / {fmt(wa['quota'])}",
        f"  Сброс через:   {fmt_reset(wa['reset'] - now)}",
        f"",
        f"Weekly · Sonnet only (сброс {ws['reset'].astimezone().strftime('%a %H:%M')}):",
        f"  Использовано:  {ws['pct']:5.1f}%   {fmt(ws['eff'])} / {fmt(ws['quota'])}",
        f"  Сброс через:   {fmt_reset(ws['reset'] - now)}",
        f"",
        f"По моделям (сессия):",
    ]
    for m, v in sorted(rep["models"].items(), key=lambda x: -x[1]):
        lines.append(f"  {m:<28} {fmt(v):>8}  ({pct(v, s['quota']):.1f}%)")
    return "\n".join(lines)


def cmd_setup():
    """Interactive first-run setup: pick your Claude plan and timezone."""
    print("─── claude-tokens-monitor setup ────────────────────────────")
    print()
    print("1) Choose your Claude plan:")
    print("   1. Pro            (~940K session, ~26.3M weekly)")
    print("   2. Max 5x         (~4.7M session, ~131.5M weekly)")
    print("   3. Max 20x        (~18.8M session, ~526M weekly)")
    plans = {"1": "pro", "2": "max5", "3": "max20"}
    while True:
        choice = input("   > [1-3] (default 2 / Max 5x): ").strip() or "2"
        if choice in plans:
            plan = plans[choice]
            break
        print("   Please enter 1, 2 or 3.")

    print()
    print("2) Choose your timezone (UTC offset in hours):")
    print("   Examples:  London=0   Paris/Berlin=1   Moscow=3   Dubai=4")
    print("              Karachi=5  Delhi=5.5 (use 5)  Bangkok=7   Beijing=8")
    print("              Tokyo=9    Sydney=10   New York=-5   Los Angeles=-8")
    while True:
        raw = input("   > UTC offset in hours (e.g. 0, 3, -5): ").strip() or "0"
        try:
            tz = int(raw)
            if -12 <= tz <= 14:
                break
        except ValueError:
            pass
        print("   Please enter an integer between -12 and 14.")

    save_user_config({"plan": plan, "tz_offset_h": tz})
    print()
    print(f"✔ Saved to {CONFIG_FILE}")
    print(f"  plan         = {plan}")
    print(f"  tz_offset_h  = {tz}")
    print()
    print("Re-run without --setup to see your token report.")
    print("If your real quota differs from the defaults, fine-tune with:")
    print("  claude_tokens.py --calibrate-session <%>")
    print("  claude_tokens.py --calibrate-weekly  <%>")
    print("  claude_tokens.py --calibrate-sonnet  <%>")
    print("(read the percentage shown on https://claude.ai/settings/usage")
    print(" while you have ~10%+ of session/weekly used).")


def main():
    argv = sys.argv[1:]
    args = set(argv)

    # --setup must run before quota lookups (it writes the config)
    if "--setup" in args:
        cmd_setup()
        return

    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=WEEKLY_DAYS + 1)
    rows, limits = load_all_rows(week_ago)

    # --calibrate-timer 4:34 — смещение session-reset (сайт показывает X, мы рассчитываем Y, offset = X-Y)
    if "--calibrate-timer" in argv:
        i = argv.index("--calibrate-timer")
        try:
            hh, mm = argv[i+1].split(":")
            target_remaining = timedelta(hours=int(hh), minutes=int(mm))
        except:
            print("Использование: --calibrate-timer Hч:ММ  (например 4:34)"); return
        rep0 = build_report(rows, limits, now)
        if not rep0["session"]["eff"]:
            print("Нет активной сессии для калибровки"); return
        # Наш расчёт reset времени в минутах
        my_remaining = timedelta(hours=rep0["session"]["reset_h"], minutes=rep0["session"]["reset_m"])
        offset = target_remaining - my_remaining
        calib = load_calibration()
        calib["timer_offset_seconds"] = int(offset.total_seconds())
        save_calibration(calib)
        print(f"✓ Калибровка таймера: offset = {int(offset.total_seconds())}с ({offset})")
        print(f"  Сайт: {target_remaining}, скрипт: {my_remaining}")
        return

    # Команды калибровки: --calibrate-session 13
    for key, calib_key in [("--calibrate-session", "session"),
                           ("--calibrate-weekly", "weekly"),
                           ("--calibrate-sonnet", "weekly_sonnet")]:
        if key in argv:
            i = argv.index(key)
            try: target_pct = float(argv[i+1])
            except: print(f"Использование: {key} <процент>"); return
            rep0 = build_report(rows, limits, now)
            if calib_key == "session":
                eff = rep0["session"]["eff"]
            elif calib_key == "weekly":
                eff = rep0["w_all"]["eff"]
            else:
                eff = rep0["w_sonnet"]["eff"]
            new_quota = int(eff / (target_pct / 100))
            calib = load_calibration()
            calib[calib_key] = new_quota
            save_calibration(calib)
            print(f"✓ {calib_key} quota = {new_quota:,} (для {eff:,} tokens = {target_pct}%)")
            print(f"  Сохранено в {CALIB_FILE}")
            return

    # По умолчанию пытаемся взять реальные данные с API (как Claude Usage.app).
    # Fallback на локальный JSONL-подсчёт если API недоступен или передан --no-api.
    rep = None
    if "--no-api" not in args:
        api_data = fetch_api_usage()
        if api_data:
            rep = build_report_api(api_data, rows, limits, now)
    if rep is None:
        rep = build_report(rows, limits, now)

    if "--swiftbar" in args:
        s = rep["session"]; wa = rep["w_all"]; ws = rep["w_sonnet"]
        print(render(rep, compact=True))
        print("---")
        print(f"Session: {s['pct']:.1f}% · {fmt(s['eff'])} / {fmt(s['quota'])} · {s['reset_h']}ч{s['reset_m']:02d}м | size=12")
        print(f"Weekly all: {wa['pct']:.1f}% · {fmt(wa['eff'])} / {fmt(wa['quota'])} · {fmt_reset(wa['reset']-now)} | size=12")
        print(f"Weekly sonnet: {ws['pct']:.1f}% · {fmt(ws['eff'])} / {fmt(ws['quota'])} · {fmt_reset(ws['reset']-now)} | size=12")
        print(f"План: {rep['plan']} | color=gray size=11")
        print("---")
        for m, v in sorted(rep["models"].items(), key=lambda x: -x[1]):
            print(f"  {m}: {fmt(v)} ({pct(v, s['quota']):.1f}%) | size=11 color=gray")
        print("---")
        print("Обновить | refresh=true")
        print(f"Подробный отчёт | shell=python3 param1={Path(__file__).resolve()} terminal=true")
        return

    if "--live" in args:
        try:
            while True:
                now = datetime.now(timezone.utc)
                rows, limits = load_all_rows(now - timedelta(days=WEEKLY_DAYS + 1))
                rep = None
                if "--no-api" not in args:
                    api_data = fetch_api_usage()
                    if api_data:
                        rep = build_report_api(api_data, rows, limits, now)
                if rep is None:
                    rep = build_report(rows, limits, now)
                os.system("clear")
                print(render(rep))
                print(f"\n⟳ 10с. Ctrl+C — выход. ({datetime.now().strftime('%H:%M:%S')})")
                time.sleep(10)
        except KeyboardInterrupt:
            print("\nВыход.")
        return

    print(render(rep))


if __name__ == "__main__":
    main()
