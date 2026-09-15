#!/usr/bin/env python3
"""
Flight price watcher — GRU -> multiple destinations.

Weekly job:
  1. Uses the free Travelpayouts "cheap prices calendar" API to scan every
     route across the whole configured date window (cheap, no quota worries)
     and find this week's cheapest round-trip found per route.
  2. Compares that price against the route's local price history to decide
     if it's a new low / a real drop worth alerting on.
  3. For routes that look like a deal, spends ONE SerpAPI (Google Flights)
     call to fetch a live, bookable confirmation of the price for that date.
  4. Prints a JSON summary to stdout: {"deals": [...], "checked": [...]}
     The scheduled task reads this and, only if "deals" is non-empty,
     sends an email through Gmail.

No third-party dependencies (uses urllib from the stdlib) so it runs with
plain `python3` on macOS with no extra install step.
"""
import html
import json
import os
import re
import sys
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
HISTORY_PATH = os.path.join(BASE_DIR, "price_history.json")
LOG_PATH = os.path.join(BASE_DIR, "run_log.txt")

TRAVELPAYOUTS_URL = "https://api.travelpayouts.com/v1/prices/calendar"
SERPAPI_URL = "https://serpapi.com/search.json"
MELHORESDESTINOS_URL = "https://www.melhoresdestinos.com.br/wp-json/wp/v2/promocao"


def log(msg):
    line = f"[{datetime.now(timezone.utc).isoformat()}] {msg}"
    print(line, file=sys.stderr)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def http_get_json(url, params, timeout=20):
    full_url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full_url, headers={"User-Agent": "flight-price-watch/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code} on {url}: {e.read()[:300]}"
    except Exception as e:
        return None, f"error on {url}: {e}"


def is_serpapi_check_week(route):
    """Budget-saving cadence: routes with a verified non-stop from their
    origin (the priority list) get a SerpAPI fallback attempt every week.
    Routes without a confirmed non-stop (long-haul/no-direct extras like
    Japão, Quênia, Grécia, Polônia, Bolívia, Marrocos, Cairo, Tel Aviv,
    Islândia, Noronha via CGH) only get one every OTHER week, freeing up
    budget for the routes that matter most. Travelpayouts (free) is still
    checked every week regardless — this only gates the paid-quota
    SerpAPI fallback."""
    if route.get("verified_nonstop", False):
        return True
    iso_week = datetime.now(timezone.utc).isocalendar()[1]
    return iso_week % 2 == 0


def window_bounds(window_months):
    first = datetime.strptime(window_months[0], "%Y-%m").date()
    last_month = datetime.strptime(window_months[-1], "%Y-%m").date()
    if last_month.month == 12:
        last_bound = last_month.replace(year=last_month.year + 1, month=1, day=1)
    else:
        last_bound = last_month.replace(month=last_month.month + 1, day=1)
    last_bound = last_bound - timedelta(days=1)
    return first, last_bound


def find_cheapest_for_route(cfg, origin, arrival_codes):
    """Scan Travelpayouts calendar across all configured months and arrival
    codes for one route; return the single cheapest entry found within the
    configured date window, or None. NOTE: Travelpayouts reports a
    "transfers" count per fare — we keep it as "stops" so downstream code
    never has to *assume* a price is nonstop just because the route is on
    our verified-nonstop list (the cheapest cached fare can still be a
    connecting itinerary on a different carrier)."""
    win_start, win_end = window_bounds(cfg["window_months"])
    best = None
    for code in arrival_codes:
        for month in cfg["window_months"]:
            params = {
                "origin": origin,
                "destination": code,
                "depart_date": month,
                "calendar_type": "departure_date",
                "length": cfg["calendar_length_days"],
                "currency": cfg["currency"],
                "token": cfg["travelpayouts_token"],
            }
            data, err = http_get_json(TRAVELPAYOUTS_URL, params)
            if err:
                log(f"  travelpayouts warn ({code} {month}): {err}")
                continue
            if not data or not data.get("success") or not data.get("data"):
                continue
            for date_str, entry in data["data"].items():
                price = entry.get("price")
                if price is None:
                    continue
                try:
                    d = datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError:
                    continue
                if not (win_start <= d <= win_end):
                    continue  # Travelpayouts sometimes suggests dates outside the requested window
                if best is None or price < best["price"]:
                    best = {
                        "price": price,
                        "date": date_str,
                        "arrival_code": code,
                        "departure_at": entry.get("departure_at"),
                        "return_at": entry.get("return_at"),
                        "stops": entry.get("transfers"),
                        "source": "travelpayouts",
                    }
    return best


# ---------------------------------------------------------------------------
# SerpAPI budget guard — the free plan is capped at 100 searches/month with
# no metered pay-as-you-go (running out forces an early paid plan renewal),
# so we track usage locally and HARD-STOP well before that ceiling instead
# of ever risking an unexpected charge.
# ---------------------------------------------------------------------------

def log_events(history, key, events, days=60):
    """Appends events (deals or editorial mentions) to a rolling log used by
    build_dashboard.py, pruning anything older than `days`. Each event must
    carry a "logged_at" (YYYY-MM-DD) field."""
    log = history.setdefault(key, [])
    log.extend(events)
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
    history[key] = [e for e in log if e.get("logged_at", "0000-00-00") >= cutoff]


def _usage_bucket(history):
    month_key = datetime.now(timezone.utc).strftime("%Y-%m")
    usage = history.setdefault("_serpapi_usage", {"month": month_key, "count": 0})
    if usage["month"] != month_key:
        usage["month"] = month_key
        usage["count"] = 0
    return usage


def serpapi_budget_remaining(cfg, history):
    usage = _usage_bucket(history)
    cap = cfg.get("serpapi_monthly_cap", 80)
    return max(0, cap - usage["count"])


def spend_serpapi_call(cfg, history, origin, arrival_code, outbound_date, return_date):
    """Checks the local monthly budget, spends 1 unit, and calls SerpAPI.
    Returns (result_dict_or_None, budget_blocked_bool)."""
    usage = _usage_bucket(history)
    cap = cfg.get("serpapi_monthly_cap", 80)
    if usage["count"] >= cap:
        log(f"  serpapi SKIPPED for {origin}->{arrival_code}: monthly budget reached ({usage['count']}/{cap})")
        return None, True
    usage["count"] += 1
    result = confirm_with_serpapi(cfg, origin, arrival_code, outbound_date, return_date)
    return result, False


def fallback_serpapi_sample(cfg, route, r_hist, history):
    """Used when Travelpayouts has no cached data for a route: spend ONE
    SerpAPI call (subject to the monthly budget guard) on a sample date,
    rotating through window_months each week so the full window gets
    covered over time."""
    months = cfg["window_months"]
    idx = r_hist.get("fallback_cursor", 0) % len(months)
    month = months[idx]
    r_hist["fallback_cursor"] = idx + 1
    outbound = f"{month}-15"
    return_dt = (datetime.strptime(outbound, "%Y-%m-%d") + timedelta(days=cfg["calendar_length_days"])).date().isoformat()
    origin = route.get("origin", cfg["origin"])
    arrival_code = route["arrival"][0]
    result, blocked = spend_serpapi_call(cfg, history, origin, arrival_code, outbound, return_dt)
    if blocked:
        return None, True
    if result is None:
        return None, False
    return {
        "price": result["price"],
        "date": outbound,
        "arrival_code": arrival_code,
        "stops": result["stops"],
        "source": "serpapi_fallback",
    }, False


def confirm_with_serpapi(cfg, origin, arrival_code, outbound_date, return_date):
    """Raw SerpAPI call to get a live, bookable price for this date. Callers
    must go through spend_serpapi_call() so usage is tracked against the
    monthly budget — never call this directly."""
    params = {
        "engine": "google_flights",
        "departure_id": origin,
        "arrival_id": arrival_code,
        "outbound_date": outbound_date,
        "return_date": return_date,
        "currency": cfg["currency"],
        "hl": cfg["language"],
        "adults": cfg["adults"],
        "travel_class": cfg["travel_class"],
        "type": 1,
        "api_key": cfg["serpapi_key"],
    }
    data, err = http_get_json(SERPAPI_URL, params, timeout=30)
    if err:
        log(f"  serpapi warn ({arrival_code}): {err}")
        return None
    if not data:
        return None
    candidates = (data.get("best_flights") or []) + (data.get("other_flights") or [])
    candidates = [c for c in candidates if c.get("price")]
    if not candidates:
        return None
    cheapest = min(candidates, key=lambda c: c["price"])
    stops = max(0, len(cheapest.get("flights") or []) - 1)
    return {"price": cheapest["price"], "stops": stops}


def flights_link(origin, arrival_code, outbound_date, return_date):
    q = f"Voos de {origin} para {arrival_code} saindo {outbound_date} voltando {return_date}"
    return "https://www.google.com/travel/flights?q=" + urllib.parse.quote(q)


# ---------------------------------------------------------------------------
# Discord / Telegram delivery — sent directly by this script (plain HTTP,
# no MCP tool needed) so the message content is deterministic and never
# depends on an LLM re-composing it correctly. Email still goes through the
# scheduled task's Gmail tool, since Gmail has no simple webhook/bot API.
# ---------------------------------------------------------------------------

def format_deal_block(cfg, deal):
    price = deal["serpapi_confirmed_price_brl"]
    price_note = "preço confirmado ao vivo"
    if price is None:
        price = deal["travelpayouts_price_brl"]
        price_note = "preço estimado, não confirmado ao vivo"
        if deal.get("serpapi_budget_reached"):
            price_note += " (limite mensal do SerpAPI atingido)"

    stops = deal["serpapi_confirmed_stops"] if deal["serpapi_confirmed_stops"] is not None else deal["travelpayouts_stops"]
    if deal.get("verified_nonstop"):
        if stops == 0:
            stops_line = "✅ voo direto/non-stop"
        else:
            stops_line = "⚠️ ATENÇÃO: essa tarifa tem conexão, não é o voo direto que essa rota costuma ter"
    else:
        stops_line = f"{stops} conexão(ões)" if stops else "paradas não informadas"

    origin_prefix = f"[{deal['origin']}] " if deal.get("origin") and deal["origin"] != cfg.get("origin") else ""
    lines = [
        f"**{origin_prefix}{deal['label']}** — R$ {price:,.0f} ({price_note})".replace(",", "."),
        f"Ida {deal['outbound_date']} · Volta {deal['return_date']} · {stops_line}",
    ]
    if deal.get("carriers"):
        lines.append("Companhias: " + ", ".join(deal["carriers"]))
    if deal.get("single_carrier_risk"):
        lines.append("⚠️ Rota operada por uma única companhia aérea — sem alternativa direta em caso de cancelamento/remanejo")
    if deal.get("route_note"):
        lines.append(f"ℹ️ {deal['route_note']}")
    lines.append(f"Motivo: {deal['reason']}")
    lines.append(deal["link"])
    return "\n".join(lines)


def build_alert_message(cfg, deals):
    header = f"✈️ Queda de preço encontrada em {len(deals)} rota(s) — GRU"
    blocks = [format_deal_block(cfg, d) for d in deals]
    return header + "\n\n" + "\n\n".join(blocks)


def send_discord(webhook_url, message):
    # Discord hard-caps message content at 2000 chars; split if needed.
    chunks = [message[i:i + 1900] for i in range(0, len(message), 1900)] or [message]
    for chunk in chunks:
        body = json.dumps({"content": chunk}).encode("utf-8")
        req = urllib.request.Request(
            webhook_url, data=body,
            headers={"Content-Type": "application/json", "User-Agent": "flight-price-watch/1.0"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
        except Exception as e:
            log(f"  discord send warn: {e}")
            return False
    return True


def send_telegram(bot_token, chat_id, message):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    # Telegram hard-caps messages at 4096 chars; split if needed.
    chunks = [message[i:i + 3800] for i in range(0, len(message), 3800)] or [message]
    ok = True
    for chunk in chunks:
        params = {"chat_id": chat_id, "text": chunk, "disable_web_page_preview": "true"}
        data, err = http_get_json(url, params)
        if err or not data or not data.get("ok"):
            log(f"  telegram send warn: {err or data}")
            ok = False
    return ok


PRICE_HINT_RE = re.compile(r"R\$\s*([\d.,]+)")


def scan_melhoresdestinos(cfg, history):
    """Free, public WordPress REST endpoint (not blocked by robots.txt) that
    lists melhoresdestinos.com.br's individual deal posts — each post is one
    offer, often with a price right in the title (e.g. "a partir de R$X").
    We match titles against each route's `keywords` and surface NEW posts
    (by id, tracked in history) since the last run as editorial mentions.

    IMPORTANT: this is NOT a verified price for our search window/dates —
    it's a third party's "starting from" headline for an unspecified date.
    Always labeled as such, never merged into the real `deals` detection."""
    state = history.setdefault("_melhoresdestinos", {"last_seen_id": 0})
    params = {"per_page": 30, "orderby": "date", "order": "desc"}
    data, err = http_get_json(MELHORESDESTINOS_URL, params, timeout=20)
    if err or not data:
        log(f"  melhoresdestinos warn: {err}")
        return []

    mentions = []
    max_id_seen = state["last_seen_id"]
    for post in data:
        pid = post.get("id", 0)
        max_id_seen = max(max_id_seen, pid)
        if pid <= state["last_seen_id"]:
            continue
        title = html.unescape(post.get("title", {}).get("rendered", ""))
        link = post.get("link")
        for route in cfg["routes"]:
            keywords = route.get("keywords") or []
            if any(kw.lower() in title.lower() for kw in keywords):
                price_match = PRICE_HINT_RE.search(title)
                mentions.append({
                    "route": route["id"],
                    "label": route["label"],
                    "title": title,
                    "link": link,
                    "price_hint_brl": price_match.group(1) if price_match else None,
                })
                break  # one route match is enough per post
    state["last_seen_id"] = max_id_seen
    return mentions


MENTION_SOURCE_LABEL = "MELHORES DESTINOS (fonte externa — preço aproximado, NÃO confirmado)"


def format_mention_block(mention):
    price = f" — a partir de R$ {mention['price_hint_brl']}" if mention.get("price_hint_brl") else ""
    return (
        f"🔶 **[MELHORES DESTINOS]** {mention['label']}{price}\n"
        f"{mention['title']}\n"
        f"{mention['link']}"
    )


def build_mentions_section(mentions):
    divider = "━━━━━━━━━━━━━━━━━━━━"
    header = f"{divider}\n📰 **{MENTION_SOURCE_LABEL}**\n{divider}"
    return header + "\n\n" + "\n\n".join(format_mention_block(m) for m in mentions)


def dispatch_alerts(cfg, deals, mentions=None):
    """Sends the deal summary (and, if any, editorial mentions) to
    Discord/Telegram directly (if configured). Email is intentionally left
    to the scheduled task's Gmail tool."""
    mentions = mentions or []
    if not deals and not mentions:
        return {}
    message = build_alert_message(cfg, deals) if deals else "✈️ Nenhuma queda de preço confirmada esta semana, mas há uma menção externa abaixo:"
    if mentions:
        message += "\n\n" + build_mentions_section(mentions)
    result = {}
    webhook = cfg.get("discord_webhook_url")
    if webhook:
        result["discord_sent"] = send_discord(webhook, message)
    bot_token = cfg.get("telegram_bot_token")
    chat_id = cfg.get("telegram_chat_id")
    if bot_token and chat_id:
        result["telegram_sent"] = send_telegram(bot_token, chat_id, message)
    return result


def main():
    cfg = load_json(CONFIG_PATH, None)
    if cfg is None:
        print(json.dumps({"error": f"config not found at {CONFIG_PATH}"}))
        sys.exit(1)

    history = load_json(HISTORY_PATH, {})
    today = datetime.now(timezone.utc).date().isoformat()

    checked = []
    deals = []

    for route in cfg["routes"]:
        rid = route["id"]
        origin = route.get("origin", cfg["origin"])
        log(f"Checking {rid} ({route['label']})...")
        r_hist = history.setdefault(rid, {"observations": [], "last_alert": None, "fallback_cursor": 0})

        budget_blocked_here = False
        best = find_cheapest_for_route(cfg, origin, route["arrival"])
        if best is None:
            if is_serpapi_check_week(route):
                log(f"  no travelpayouts data for {rid}, falling back to a SerpAPI sample date")
                best, budget_blocked_here = fallback_serpapi_sample(cfg, route, r_hist, history)
            else:
                log(f"  no travelpayouts data for {rid}; low-priority route, skipping SerpAPI this week (checked every 2 weeks)")
                checked.append({"route": rid, "label": route["label"], "origin": origin, "status": "skipped_low_priority_week"})
                continue
        if best is None:
            status = "serpapi_budget_reached" if budget_blocked_here else "no_data"
            log(f"  no data returned for {rid} ({status})")
            checked.append({"route": rid, "label": route["label"], "status": status})
            continue

        prior_obs = r_hist["observations"]
        prior_prices = [o["price"] for o in prior_obs]

        is_new_low = bool(prior_prices) and best["price"] < min(prior_prices)
        recent = prior_prices[-5:]
        avg_recent = sum(recent) / len(recent) if recent else None
        is_big_drop = avg_recent is not None and best["price"] <= avg_recent * (1 - cfg["drop_threshold_pct"] / 100)
        has_enough_history = len(prior_obs) >= cfg["min_history_for_alert"]

        candidate = has_enough_history and (is_new_low or is_big_drop)

        last_alert = r_hist.get("last_alert")
        in_cooldown = False
        if candidate and last_alert:
            days_since = (datetime.fromisoformat(today) - datetime.fromisoformat(last_alert["date"])).days
            if best["price"] >= last_alert["price"] and days_since < cfg["renotify_cooldown_days"]:
                in_cooldown = True

        # record this observation for future baselines
        r_hist["observations"].append({
            "checked_at": today,
            "price": best["price"],
            "date": best["date"],
            "arrival_code": best["arrival_code"],
        })
        # keep history from growing forever
        r_hist["observations"] = r_hist["observations"][-52:]

        entry_summary = {
            "route": rid,
            "label": route["label"],
            "origin": origin,
            "price_brl": best["price"],
            "date": best["date"],
            "arrival_code": best["arrival_code"],
            "stops": best.get("stops"),
            "verified_nonstop": route.get("verified_nonstop", False),
            "status": "candidate_deal" if (candidate and not in_cooldown) else "ok",
        }
        checked.append(entry_summary)

        if candidate and not in_cooldown:
            outbound = best["date"]
            try:
                return_dt = (datetime.fromisoformat(outbound) + timedelta(days=cfg["calendar_length_days"])).date().isoformat()
            except ValueError:
                return_dt = outbound
            confirmed, blocked = spend_serpapi_call(cfg, history, origin, best["arrival_code"], outbound, return_dt)
            deal = {
                "route": rid,
                "label": route["label"],
                "origin": origin,
                "arrival_code": best["arrival_code"],
                "carriers": route.get("carriers", []),
                "single_carrier_risk": route.get("single_carrier", False),
                "verified_nonstop": route.get("verified_nonstop", False),
                "route_note": route.get("note"),
                "outbound_date": outbound,
                "return_date": return_dt,
                "travelpayouts_price_brl": best["price"],
                "travelpayouts_stops": best.get("stops"),
                "serpapi_confirmed_price_brl": confirmed["price"] if confirmed else None,
                "serpapi_confirmed_stops": confirmed["stops"] if confirmed else None,
                "serpapi_budget_reached": blocked,
                "reason": "menor preço já visto" if is_new_low else f"queda de {cfg['drop_threshold_pct']}%+ vs média recente",
                "link": flights_link(origin, best["arrival_code"], outbound, return_dt),
            }
            deals.append(deal)
            r_hist["last_alert"] = {
                "date": today,
                "price": confirmed["price"] if confirmed else best["price"],
            }

    mentions = scan_melhoresdestinos(cfg, history)

    # Log deals/mentions for the 60-day dashboard (build_dashboard.py reads
    # these). Kept separate from the per-route "observations" used for
    # baseline comparison, since a dashboard cares about DETECTED events
    # (deals/mentions), not every raw price sample.
    log_events(history, "_deals_log", [dict(d, logged_at=today) for d in deals])
    log_events(history, "_mentions_log", [dict(m, logged_at=today) for m in mentions])

    save_json(HISTORY_PATH, history)

    dispatch_result = dispatch_alerts(cfg, deals, mentions)

    usage = _usage_bucket(history)
    print(json.dumps({
        "checked_at": today,
        "routes_checked": len(checked),
        "serpapi_calls_this_month": usage["count"],
        "serpapi_monthly_cap": cfg.get("serpapi_monthly_cap", 80),
        "email_enabled": cfg.get("email_enabled", True),
        "checked": checked,
        "deals": deals,
        "editorial_mentions": mentions,
        "dispatch_result": dispatch_result,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
