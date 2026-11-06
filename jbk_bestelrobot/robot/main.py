"""JBK Bestelrobot – Home Assistant add-on.

Vraagt elke minuut aan het portaal (mijn.keukenadviesaanhuis-assen.nl/api/robot) of er goedgekeurde
bestellingen klaarstaan en plaatst die in de webshop van de leverancier. Stuurt pushberichten via Home Assistant.

Veiligheid:
- Alleen bestellingen die Johnny in het dashboard akkoord heeft gegeven.
- Betaalt altijd "op rekening"; raakt iDEAL/creditcard nooit aan.
- Proefmodus (instelling in het dashboard): stopt vóór "Bestelling afronden" en stuurt een screenshot.
- Stopt bij een prijsverschil of een bedrag boven het maximum.
- Een order die al op "afronden" stond wordt na een herstart nooit opnieuw geprobeerd (dagboek).
"""
import asyncio
import base64
import datetime as dt
import json
import os
import time
import traceback

import aiohttp
from aiohttp import web
from playwright.async_api import async_playwright

import quooker

VERSIE = "0.1.2"
DATA = os.environ.get("JBK_DATA", "/data")
KLUIS = f"{DATA}/kluis.json"
DAGBOEK = f"{DATA}/dagboek.json"
GEMELD = f"{DATA}/gemeld.json"
PROFIEL = f"{DATA}/browser"
LEVERANCIERS = {"quooker": quooker}

staat = {"laatst": None, "fout": "", "bezig": "", "log": [], "inlog": {}}


def logregel(t):
    regel = f"{dt.datetime.now().strftime('%d-%m %H:%M:%S')}  {t}"
    print(regel, flush=True)
    staat["log"] = (staat["log"] + [regel])[-80:]


def lees(pad, standaard):
    try:
        with open(pad) as f:
            return json.load(f)
    except Exception:
        return standaard


def schrijf(pad, data):
    tmp = pad + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, pad)


def opties():
    return lees(f"{DATA}/options.json", {})


# ---------------- Home Assistant pushberichten ----------------
async def push(sessie, titel, tekst, url=None):
    svc = (opties().get("melding_service") or "").strip()
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not svc or not token or "." not in svc:
        return
    domein, naam = svc.split(".", 1)
    data = {"title": titel, "message": tekst}
    if url:
        data["data"] = {"url": url, "clickAction": url}
    try:
        async with sessie.post(f"http://supervisor/core/api/services/{domein}/{naam}", json=data,
                               headers={"Authorization": f"Bearer {token}"}, timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status >= 300:
                logregel(f"pushbericht mislukt ({r.status})")
    except Exception as e:
        logregel(f"pushbericht mislukt: {e}")


# ---------------- Portaal ----------------
def api_kop():
    o = opties()
    return o.get("dashboard_api", "https://mijn.keukenadviesaanhuis-assen.nl").rstrip("/"), {
        "Authorization": f"Bearer {o.get('robot_sleutel', '')}", "x-robot-versie": VERSIE}


async def api(sessie, methode, pad, body=None):
    basis, kop = api_kop()
    async with sessie.request(methode, basis + pad, json=body, headers=kop, timeout=aiohttp.ClientTimeout(total=60)) as r:
        try:
            j = await r.json(content_type=None)
        except Exception:
            j = {"ok": False, "fout": f"HTTP {r.status}"}
        if r.status == 401:
            raise RuntimeError("Robotsleutel klopt niet. Maak een nieuwe in het dashboard en zet hem in de configuratie van de add-on.")
        return j


async def schermafdruk(page):
    try:
        b = await page.screenshot(type="jpeg", quality=55, full_page=True)
        return base64.b64encode(b).decode() if len(b) < 2_000_000 else base64.b64encode(await page.screenshot(type="jpeg", quality=45)).decode()
    except Exception:
        return ""


async def bewaar_debug(page):
    """Laatste screenshot bij een fout, te bekijken op het scherm van de add-on."""
    try:
        with open(f"{DATA}/laatste_fout.jpg", "wb") as f:
            f.write(await page.screenshot(type="jpeg", quality=60, full_page=True))
        staat["debug_t"] = dt.datetime.now().strftime("%d-%m %H:%M:%S")
    except Exception:
        pass


# ---------------- Werk ----------------
async def verwerk_order(sessie, ctx, order, instellingen):
    mod = LEVERANCIERS.get(order["leverancier"])
    if not mod:
        await api(sessie, "POST", f"/api/robot/order/{order['id']}", {"resultaat": "mislukt", "melding": f"Leverancier {order['leverancier']} wordt nog niet ondersteund."})
        return
    dagboek = lees(DAGBOEK, {})
    if dagboek.get(order["id"]) == "afronden":
        await api(sessie, "POST", f"/api/robot/order/{order['id']}", {"resultaat": "onzeker", "melding": "De robot werd onderbroken tijdens het afronden."})
        return
    staat["bezig"] = f"{order['referentie']} ({order['fase']})"
    logregel(f"order {order['referentie']}: {order['fase']}")
    page = await ctx.new_page()
    uitkomst = None
    try:
        r = await mod.bestel(page, lees(KLUIS, {}), order, instellingen.get("max_bedrag", 2500), schermafdruk)
        if r["resultaat"] == "klik":
            dagboek[order["id"]] = "afronden"
            schrijf(DAGBOEK, dagboek)
            r2 = await mod.afronden(page, schermafdruk)
            r = {**r, **r2}
        uitkomst = r
    except quooker.InlogFout as e:
        uitkomst = {"resultaat": "mislukt", "melding": str(e)}
        staat["inlog"][order["leverancier"]] = {"ok": False, "melding": str(e)}
    except quooker.Stop as e:
        uitkomst = {"resultaat": e.soort, "melding": e.melding, "screenshot": await schermafdruk(page)}
    except Exception as e:
        logregel(traceback.format_exc()[-600:])
        uitkomst = {"resultaat": "mislukt" if dagboek.get(order["id"]) != "afronden" else "onzeker", "melding": f"Technische fout: {str(e)[:200]}", "screenshot": await schermafdruk(page)}
        await bewaar_debug(page)
    finally:
        if uitkomst and uitkomst.get("resultaat") in ("controle", "mislukt"):
            try:
                await mod.leeg_eigen_artikelen(page, order["regels"])
            except Exception:
                pass
        await page.close()
        staat["bezig"] = ""
    body = {k: uitkomst.get(k) for k in ("resultaat", "melding", "totaal", "ordernr", "screenshot") if uitkomst.get(k) is not None}
    await api(sessie, "POST", f"/api/robot/order/{order['id']}", body)
    if uitkomst["resultaat"] in ("besteld", "onzeker"):
        dagboek[order["id"]] = uitkomst["resultaat"]
        schrijf(DAGBOEK, dagboek)
    logregel(f"order {order['referentie']}: {uitkomst['resultaat']} {uitkomst.get('melding', '') or uitkomst.get('ordernr', '')}")
    titel = {"besteld": f"Besteld: {order['referentie']}", "controle": f"Controleren: {order['referentie']}", "mislukt": f"Niet gelukt: {order['referentie']}", "onzeker": f"Controleer: {order['referentie']}"}[uitkomst["resultaat"]]
    await push(sessie, titel, uitkomst.get("melding") or (f"Ordernummer {uitkomst.get('ordernr')}" if uitkomst.get("ordernr") else "Klaar."), instellingen.get("dashboard"))


async def inlogcontrole(sessie, ctx):
    uit = {}
    for naam, mod in LEVERANCIERS.items():
        page = await ctx.new_page()
        try:
            await mod.ingelogd(page, lees(KLUIS, {}))
            uit[naam] = {"ok": True}
        except Exception as e:
            uit[naam] = {"ok": False, "melding": str(e)[:200]}
            await bewaar_debug(page)
        finally:
            await page.close()
    staat["inlog"] = uit
    await api(sessie, "POST", "/api/robot/status", {"leveranciers": uit})
    for naam, s in uit.items():
        if not s["ok"]:
            await push(sessie, f"Bestelrobot: inloggen bij {naam} lukt niet", s["melding"])
    return uit


async def lus():
    gemeld = lees(GEMELD, {})
    laatste_controle = 0
    async with aiohttp.ClientSession() as sessie, async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(PROFIEL, headless=True, locale="nl-NL", timezone_id="Europe/Amsterdam",
                                                          viewport={"width": 1366, "height": 900}, args=["--disable-dev-shm-usage"],
                                                          **({"executable_path": os.environ["JBK_CHROME"]} if os.environ.get("JBK_CHROME") else {}))
        try:
            while True:
                try:
                    if not opties().get("robot_sleutel"):
                        staat["fout"] = "Nog geen robotsleutel ingesteld (configuratie van de add-on)."
                    else:
                        w = await api(sessie, "GET", "/api/robot/werk")
                        staat["laatst"] = time.time()
                        staat["fout"] = "" if w.get("ok") else w.get("fout", "onbekende fout")
                        # pushbericht voor nieuwe orders die op akkoord wachten
                        for o in w.get("wacht_op_akkoord", []):
                            if o["id"] not in gemeld:
                                await push(sessie, "Bestelling wacht op akkoord", o["tekst"], w.get("dashboard"))
                                gemeld[o["id"]] = int(time.time())
                        if len(gemeld) > 500:
                            gemeld = dict(sorted(gemeld.items(), key=lambda x: x[1])[-300:])
                        schrijf(GEMELD, gemeld)
                        for order in w.get("orders", []):
                            await verwerk_order(sessie, ctx, order, w)
                        for taak in w.get("taken", []):
                            if taak.startswith("prijzen:"):
                                lev = taak.split(":", 1)[1]
                                page = await ctx.new_page()
                                try:
                                    art = await LEVERANCIERS[lev].prijzen(page, lees(KLUIS, {}))
                                    r = await api(sessie, "POST", "/api/robot/prijzen", {"leverancier": lev, "artikelen": art})
                                    logregel(f"prijzen {lev}: {r}")
                                except Exception as e:
                                    logregel(f"prijzen {lev} mislukt: {str(e)[:400]}")
                                    await bewaar_debug(page)
                                finally:
                                    await page.close()
                        # inlogcontrole: bij de start en elke ochtend rond 7:30
                        nu = dt.datetime.now()
                        dagelijks = time.time() - laatste_controle > 23 * 3600 and (laatste_controle == 0 or (nu.hour, nu.minute) >= (7, 30))
                        if staat.pop("forceer_controle", False) or dagelijks:
                            await inlogcontrole(sessie, ctx)
                            laatste_controle = time.time()
                except Exception as e:
                    staat["fout"] = str(e)[:300]
                    logregel(f"fout: {e}")
                await asyncio.sleep(max(30, int(opties().get("interval_seconden", 60))))
        finally:
            await ctx.close()


# ---------------- Scherm in Home Assistant (ingress) ----------------
STIJL = "body{font-family:system-ui,sans-serif;max-width:760px;margin:20px auto;padding:0 14px;color:#2e2e2e;background:#f4f3f0}.k{background:#fff;border:1px solid #e6e3dd;border-radius:12px;padding:16px;margin:12px 0}input{width:100%;padding:10px;margin:4px 0 10px;border:1px solid #dcd8d0;border-radius:8px;font:inherit;box-sizing:border-box}button{background:#c22a12;color:#fff;border:0;border-radius:8px;padding:10px 16px;font-weight:700;cursor:pointer}.ok{color:#1f7a4d}.no{color:#c22a12}pre{white-space:pre-wrap;font-size:12px;background:#efede8;padding:10px;border-radius:8px;max-height:320px;overflow:auto}"


def pagina(melding=""):
    k = lees(KLUIS, {})
    laatst = dt.datetime.fromtimestamp(staat["laatst"]).strftime("%d-%m %H:%M:%S") if staat["laatst"] else "nog niet"
    inlog = "".join(f"<div>{n}: <b class='{'ok' if s.get('ok') else 'no'}'>{'ingelogd' if s.get('ok') else s.get('melding', 'probleem')}</b></div>" for n, s in staat["inlog"].items()) or "<div>nog niet gecontroleerd</div>"
    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")
    return f"""<!doctype html><html lang=nl><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Bestelrobot</title><style>{STIJL}</style></head><body>
<h1>JBK Bestelrobot</h1>{f'<div class=k><b>{esc(melding)}</b></div>' if melding else ''}
<div class=k><div>Laatst bij het portaal: <b>{laatst}</b></div><div>{'<span class=no>' + esc(staat['fout']) + '</span>' if staat['fout'] else '<span class=ok>verbinding in orde</span>'}</div>{f"<div>Bezig met: {esc(staat['bezig'])}</div>" if staat['bezig'] else ''}{inlog}
<form method=post action=controle><button>Inloggen nu testen</button></form></div>
<div class=k><h2>Quooker-dealerwebshop</h2><p>Inloggegevens worden alleen hier in Home Assistant bewaard (map van de add-on, ook in je back-ups). Ze gaan nooit naar het dashboard of naar Claude.</p>
<form method=post action=kluis><input type=hidden name=lev value=quooker><label>E-mailadres</label><input name=email type=email value="{esc((k.get('quooker') or {}).get('email', ''))}" autocomplete=off>
<label>Wachtwoord {'(opgeslagen; leeg laten = niet wijzigen)' if (k.get('quooker') or {}).get('wachtwoord') else ''}</label><input name=wachtwoord type=password autocomplete=new-password><button>Opslaan</button></form></div>
{f"<div class=k><h2>Laatste fout ({staat['debug_t']})</h2><a href=laatste_fout.jpg target=_blank>Screenshot bekijken</a></div>" if staat.get('debug_t') else ''}
<div class=k><h2>Logboek</h2><pre>{esc(chr(10).join(reversed(staat['log'][-40:])))}</pre></div></body></html>"""


def alleen_ha(handler):
    async def binnen(req):
        # ingress-verkeer komt altijd van de Supervisor (172.30.32.2); de watchdog van localhost
        if req.remote not in ("172.30.32.2", "127.0.0.1", "::1"):
            return web.Response(status=403, text="Alleen via Home Assistant")
        return await handler(req)
    return binnen


async def start_web(app_state):
    app = web.Application()

    async def index(req):
        return web.Response(text=pagina(req.query.get("m", "")), content_type="text/html")

    async def gezond(req):
        return web.Response(text="ok")

    async def kluis(req):
        f = await req.post()
        k = lees(KLUIS, {})
        lev = f.get("lev", "quooker")
        cur = k.get(lev) or {}
        cur["email"] = (f.get("email") or "").strip()
        if f.get("wachtwoord"):
            cur["wachtwoord"] = f.get("wachtwoord")
        k[lev] = cur
        schrijf(KLUIS, k)
        logregel(f"inloggegevens {lev} opgeslagen")
        raise web.HTTPSeeOther("./?m=Opgeslagen.%20Klik%20op%20Inloggen%20nu%20testen.")

    async def controle(req):
        app_state["controle_nu"] = True
        raise web.HTTPSeeOther("./?m=De%20inlogtest%20start%20bij%20de%20volgende%20ronde%20(binnen%20een%20minuut).%20Ververs%20daarna%20deze%20pagina.")

    app.router.add_get("/", alleen_ha(index))
    app.router.add_get("/gezond", gezond)

    async def debugfoto(req):
        try:
            with open(f"{DATA}/laatste_fout.jpg", "rb") as f:
                return web.Response(body=f.read(), content_type="image/jpeg")
        except Exception:
            return web.Response(status=404, text="geen screenshot")

    app.router.add_get("/laatste_fout.jpg", alleen_ha(debugfoto))
    app.router.add_post("/kluis", alleen_ha(kluis))
    app.router.add_post("/controle", alleen_ha(controle))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8099).start()


async def hoofd():
    os.makedirs(PROFIEL, exist_ok=True)
    app_state = {}
    await start_web(app_state)
    logregel(f"Bestelrobot {VERSIE} gestart")

    async def controle_wacht():
        # "Inloggen nu testen": zet de dagelijkse controle terug zodat hij in de volgende ronde draait
        while True:
            if app_state.pop("controle_nu", False):
                staat["inlog"] = {}
                staat["forceer_controle"] = True
            await asyncio.sleep(2)

    asyncio.create_task(controle_wacht())
    await lus()


if __name__ == "__main__":
    asyncio.run(hoofd())
