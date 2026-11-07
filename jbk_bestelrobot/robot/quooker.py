"""Quooker-dealerwebshop (dealerwebshop.quooker.nl).

Werkwijze, vastgesteld op 6-11-2026:
- Artikelen in de winkelwagen zetten gaat via POST /ajax/shoppingcart/update met
  update[model]=base64(artikelcode), update[color]=base64("NONE"), update[size]=base64("NONE"),
  update[order_steps]=1, update[value]=<aantal>. Het antwoord bevat o.a. "quantity" (totaal in winkelwagen).
- Afrekenen: /nl/shoppingcart/checkout. Referentie verplicht (max. 30 tekens), afleveradres kiezen via
  /nl/customeraddressbook (alleen adressen die Quooker in het adresboek heeft gezet), betaalmethode
  "Op rekening" of "Direct betalen", knop "Bestelling afronden".
- Prijzen zijn exclusief btw.
"""
import base64
import re

BASIS = "https://dealerwebshop.quooker.nl"
ZEKERHEID_EURO = 0.50  # maximaal verschil tussen prijslijst en webshop voordat de robot stopt


async def ga_naar(page, url, pogingen=3):
    """page.goto met herhaling: vlak na het starten van de add-on geeft het netwerk soms ERR_NETWORK_CHANGED."""
    for i in range(pogingen):
        try:
            return await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            if i == pogingen - 1 or not re.search(r"ERR_NETWORK_CHANGED|ERR_NAME_NOT_RESOLVED|ERR_CONNECTION|ERR_TIMED_OUT|Timeout", str(e)):
                raise
            await page.wait_for_timeout(5000 * (i + 1))


class InlogFout(Exception):
    pass


class Stop(Exception):
    """Netjes stoppen met een melding voor Johnny (status 'controle' of 'mislukt')."""

    def __init__(self, melding, soort="mislukt"):
        super().__init__(melding)
        self.melding = melding
        self.soort = soort


def b64(s):
    return base64.b64encode(s.encode()).decode()


def bedrag(tekst):
    """'€ 1.234,56' → 1234.56"""
    m = re.search(r"€\s*-?\s*([\d.]+,\d{2})", tekst or "")
    return float(m.group(1).replace(".", "").replace(",", ".")) if m else None


def postcode(s):
    return re.sub(r"\s+", "", str(s or "")).upper()


async def is_ingelogd(page):
    """Ingelogd = op de dealerwebshop en het accountmenu (Uitloggen / Mijn bestellingen) staat in de pagina."""
    if "dealerwebshop.quooker.nl" not in page.url:
        return False
    html = await page.content()
    return ("Uitloggen" in html or "Mijn bestellingen" in html) and "type=\"password\"" not in html


class AndereSessie(Exception):
    """Iemand anders (meestal Johnny in Chrome) is ingelogd; Quooker staat maar één sessie per account toe."""


async def ingelogd(page, kluis, overnemen=True):
    """Zorgt dat de browser is ingelogd. Gebruikt de opgeslagen gegevens als het nodig is.
    Quooker staat maar één sessie per account toe. overnemen=True: de andere sessie uitloggen (bij bestellen/prijzen).
    overnemen=False: stoppen met AndereSessie (bij de dagelijkse inlogcontrole, zodat Johnny niet steeds wordt uitgelogd)."""
    await ga_naar(page, f"{BASIS}/nl/shoppingcart")
    if await is_ingelogd(page):
        return True
    gegevens = (kluis or {}).get("quooker") or {}
    if not gegevens.get("email") or not gegevens.get("wachtwoord"):
        raise InlogFout("Niet ingelogd en geen inloggegevens opgeslagen. Vul ze in op het scherm van de add-on.")
    # inlogpagina (login.quooker.com of een eigen formulier)
    email = page.locator("input[type=email], input[name=username], input[name=email]").first
    await email.wait_for(timeout=30000)
    await email.fill(gegevens["email"])
    ww = page.locator("input[type=password]").first
    if not await ww.count() or not await ww.is_visible():
        # sommige inlogschermen vragen eerst alleen het e-mailadres
        await email.press("Enter")
        await ww.wait_for(timeout=15000)
    await ww.fill(gegevens["wachtwoord"])
    # "Onthoud mij" aanzetten, dan blijft de robot langer ingelogd
    try:
        onthoud = page.get_by_label(re.compile("onthoud", re.I)).first
        if await onthoud.count() and not await onthoud.is_checked():
            await onthoud.check(timeout=3000)
    except Exception:
        pass
    await ww.press("Enter")
    # Melding "Andere sessie gevonden"?
    try:
        knop = page.get_by_role("button", name=re.compile("andere sessie uit", re.I)).first
        await knop.wait_for(state="visible", timeout=6000)
        if not overnemen:
            raise AndereSessie("Er is al iemand anders ingelogd met dit Quooker-account (waarschijnlijk jij in Chrome).")
        await knop.click()
    except AndereSessie:
        raise
    except Exception:
        pass  # geen melding: gewoon doorgaan
    try:
        await page.wait_for_url(re.compile(r"dealerwebshop\.quooker\.nl"), timeout=45000)
        await page.wait_for_load_state("domcontentloaded")
        if not await is_ingelogd(page):
            await ga_naar(page, f"{BASIS}/nl/shoppingcart")
            if not await is_ingelogd(page):
                raise RuntimeError("na inloggen niet herkend")
    except Exception:
        tekst = (await page.inner_text("body"))[:300]
        if re.search(r"captcha|robot", tekst, re.I):
            raise InlogFout("Quooker vraagt om een captcha. Log één keer zelf in via het scherm van de add-on.")
        raise InlogFout("Inloggen bij Quooker lukt niet (wachtwoord gewijzigd?).")
    return True


async def zet_aantal(page, code, aantal):
    j = await page.evaluate(
        """async ([m, n]) => {
          const b = new URLSearchParams({'update[model]': m, 'update[color]': btoa('NONE'), 'update[size]': btoa('NONE'),
                                         'update[order_steps]': '1', 'update[value]': String(n), 'use_live_prices': 'false'});
          const r = await fetch('/ajax/shoppingcart/update', {method: 'POST', body: b, credentials: 'include',
            headers: {'x-requested-with': 'XMLHttpRequest', 'content-type': 'application/x-www-form-urlencoded; charset=UTF-8'}});
          if (!r.ok) return {fout: r.status};
          return await r.json();
        }""",
        [b64(code), int(aantal)],
    )
    if not isinstance(j, dict) or j.get("fout"):
        raise Stop(f"Artikel {code} kon niet in de winkelwagen worden gezet (fout {j.get('fout') if isinstance(j, dict) else '?'}).")
    return j


async def aantal_in_winkelwagen(page):
    await ga_naar(page, f"{BASIS}/nl/shoppingcart")
    tekst = await page.inner_text("body")
    if re.search(r"winkelwagen is leeg", tekst, re.I):
        return 0, tekst
    m = re.search(r"Totaal aantal artikelen:\s*(\d+)", tekst)
    return (int(m.group(1)) if m else -1), tekst


async def leeg_eigen_artikelen(page, regels):
    for r in regels:
        await zet_aantal(page, r["code"], 0)


async def kies_adres(page, order):
    """Kiest het afleveradres. 'johnny' = het standaardadres laten staan."""
    if order.get("aflever_keuze") == "johnny" or (order.get("aflever") or {}).get("bij_johnny"):
        return "standaardadres (Johnny)"
    a = order.get("aflever") or {}
    pc, straat = postcode(a.get("postcode")), str(a.get("straat") or "").strip()
    if not pc:
        raise Stop("Er is geen afleveradres ingevuld voor deze klant.", "controle")
    await ga_naar(page, f"{BASIS}/nl/customeraddressbook")
    huisnr = (re.findall(r"\d+\s*[A-Za-z]?", straat) or [""])[-1].replace(" ", "").lower()
    # De tabel wordt na het laden van de pagina pas gevuld ("Laden..."): wachten tot er adressen staan
    kies = "button:has-text('Kies'), a:has-text('Kies'), input[value='Kies']"
    try:
        await page.locator(f"tbody tr :is({kies})").first.wait_for(state="visible", timeout=30000)
    except Exception:
        pass
    # Zoeken op postcode, zodat ook adressen buiten de eerste 25 regels zichtbaar worden
    try:
        zoek = page.locator("input[type=search]").first
        if await zoek.count():
            await zoek.fill(a.get("postcode") or "", timeout=5000)
            await page.wait_for_timeout(1500)
    except Exception:
        pass
    for poging in range(2):
        rijen = page.locator("tbody tr")
        for i in range(await rijen.count()):
            rij = rijen.nth(i)
            t = await rij.inner_text()
            if pc in postcode(t) and (not huisnr or huisnr in re.sub(r"\s+", "", t).lower()):
                await rij.locator(kies).first.click()
                await page.wait_for_url(re.compile(r"/shoppingcart/checkout"), timeout=30000)
                return f"{straat}, {a.get('postcode')}"
        if poging == 0:
            # misschien stond er een zoekfilter verkeerd: alles tonen en nog een keer
            try:
                await page.locator("input[type=search]").first.fill("", timeout=5000)
                await page.evaluate("""() => { const s = document.querySelector("select[name$='_length']"); if (s) { s.value = s.options[s.options.length - 1].value; s.dispatchEvent(new Event('change', {bubbles: true})); } }""")
            except Exception:
                pass
            await page.wait_for_timeout(2000)
    raise Stop(f"Het afleveradres {straat}, {a.get('postcode')} {a.get('plaats', '')} staat niet in je Quooker-adresboek. Vraag Quooker het toe te voegen, of laat bij jou afleveren.", "controle")


async def kies_verzenddatum(page, datum):
    """Vult "Voorkeur verzenddag vanuit magazijn Quooker" in (veld #preferred_delivery_date, jQuery-datepicker,
    formaat dd-mm-jjjj, alleen-lezen, standaard = morgen = zo snel mogelijk). datum = "" of JJJJ-MM-DD.
    Lukt het niet zeker, dan stopt de robot met 'controle' (er wordt dan niets besteld)."""
    veld = page.locator("#preferred_delivery_date")
    if not await veld.count():
        if datum:
            raise Stop(f"Het veld voor de verzenddag staat niet op de afrekenpagina; verzenddatum {datum} kon niet worden ingevuld.", "controle")
        return "geen verzenddag-veld (Quooker kiest)"
    standaard = await veld.input_value()
    if not datum:
        return f"zo snel mogelijk ({standaard})"
    j, m, d = datum.split("-")
    gewenst = f"{d}-{m}-{j}"
    uit = await page.evaluate(
        """(w) => { const e = document.getElementById('preferred_delivery_date'); const $ = window.jQuery;
                   if ($ && $(e).datepicker) { $(e).datepicker('setDate', w); } else { e.value = w; }
                   e.dispatchEvent(new Event('change', {bubbles: true})); return e.value; }""",
        gewenst,
    )
    if uit != gewenst:
        # datum ligt vóór de vroegst mogelijke dag (bijv. order te laat verwerkt): dan zo snel mogelijk
        if uit and re.match(r"\d{2}-\d{2}-\d{4}$", uit) and (uit[6:] + uit[3:5] + uit[:2]) > (j + m + d):
            return f"zo snel mogelijk ({uit}); gevraagde {gewenst} was al voorbij"
        raise Stop(f"Verzenddag {gewenst} kon niet worden ingesteld (veld toont '{uit}').", "controle")
    return gewenst


async def bewaar_afrekenpagina(page):
    """Bewaart HTML en screenshot van de afrekenpagina (te bekijken op het scherm van de add-on)."""
    import os
    data = os.environ.get("JBK_DATA", "/data")
    try:
        with open(f"{data}/afrekenpagina.html", "w") as f:
            f.write(await page.content())
        with open(f"{data}/afrekenpagina.jpg", "wb") as f:
            f.write(await page.screenshot(type="jpeg", quality=60, full_page=True))
    except Exception:
        pass


async def bestel(page, kluis, order, max_bedrag, schermafdruk):
    """Voert één order uit. Geeft dict {resultaat, melding, totaal, ordernr, screenshot}."""
    fase = order["fase"]  # "vullen" (stopt vóór de laatste knop) of "bestellen"
    await ingelogd(page, kluis)
    # 1. Winkelwagen moet leeg zijn (afgezien van eventuele restanten van deze order)
    await leeg_eigen_artikelen(page, order["regels"])
    n, _ = await aantal_in_winkelwagen(page)
    if n != 0:
        raise Stop("In de Quooker-winkelwagen staan nog andere artikelen (handmatig toegevoegd?). De robot bestelt daar niet overheen; maak de winkelwagen leeg en probeer opnieuw.", "mislukt")
    try:
        # 2. Artikelen erin
        for r in order["regels"]:
            await zet_aantal(page, r["code"], r["aantal"])
        n, tekst = await aantal_in_winkelwagen(page)
        verwacht_n = sum(int(r["aantal"]) for r in order["regels"])
        if n != verwacht_n:
            raise Stop(f"Winkelwagen bevat {n} artikelen in plaats van {verwacht_n}. Mogelijk is een artikelcode niet meer leverbaar.")
        # 3. Afrekenen invullen
        await ga_naar(page, f"{BASIS}/nl/shoppingcart/checkout")
        adres = await kies_adres(page, order)
        ref = page.get_by_placeholder(re.compile("referentie", re.I)).first
        await ref.fill(order["referentie"][:30])
        await bewaar_afrekenpagina(page)
        verzenden = await kies_verzenddatum(page, order.get("verzenddatum") or "")
        op_rekening = page.get_by_text("Op rekening", exact=True).first
        await op_rekening.click()
        radio = page.locator("input[type=radio]:checked")
        gekozen = " ".join([(await radio.nth(i).evaluate("e => (e.closest('label')||e.parentElement).innerText")) for i in range(await radio.count())])
        if "rekening" not in gekozen.lower():
            raise Stop("Kon betaalmethode 'Op rekening' niet aanzetten. De robot betaalt nooit direct.", "controle")
        body = await page.inner_text("body")
        m = re.search(r"Subtotaal \(incl\. korting, excl\. BTW\)\s*(€\s*[\d.,]+)", body)
        totaal = bedrag(m.group(1)) if m else None
        verwacht = float(order["verwacht_inkoop"])
        shot = await schermafdruk(page)
        # 4. Controles
        if totaal is None:
            raise Stop("Kon het subtotaal op de afrekenpagina niet lezen.", "controle")
        if abs(totaal - verwacht) > ZEKERHEID_EURO:
            return {"resultaat": "controle", "melding": f"Prijsverschil: webshop {totaal:.2f}, prijslijst {verwacht:.2f} (ex btw). Werk de prijslijst bij of bestel definitief.", "totaal": totaal, "screenshot": shot}
        if fase != "bestellen":
            return {"resultaat": "controle", "melding": f"Proefmodus: winkelwagen en afrekenpagina staan klaar (referentie {order['referentie']}, afleveren: {adres}, verzenden: {verzenden}, op rekening).", "totaal": totaal, "screenshot": shot}
        if totaal > float(max_bedrag):
            return {"resultaat": "controle", "melding": f"Bedrag {totaal:.2f} is hoger dan het maximum van {float(max_bedrag):.2f}.", "totaal": totaal, "screenshot": shot}
        # 5. Definitief bestellen
        return {"resultaat": "klik", "totaal": totaal, "screenshot": shot}
    finally:
        if fase != "bestellen":
            try:
                await leeg_eigen_artikelen(page, order["regels"])
            except Exception:
                pass


async def afronden(page, schermafdruk):
    """Klikt op 'Bestelling afronden' en leest het ordernummer. Alleen aanroepen na bestel() met resultaat 'klik'."""
    knop = page.get_by_role("button", name=re.compile("Bestelling afronden", re.I)).last
    if not await knop.count():
        knop = page.locator("text=Bestelling afronden").last
    await knop.click()
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
    except Exception:
        pass
    body = await page.inner_text("body")
    shot = await schermafdruk(page)
    m = re.search(r"(?:order|bestel)(?:nummer|nr\.?)\s*[:#]?\s*([A-Z0-9][A-Z0-9-]{3,})", body, re.I)
    if m or re.search(r"bedankt|orderbevestiging|bestelling is (ontvangen|geplaatst)", body, re.I):
        return {"resultaat": "besteld", "ordernr": m.group(1) if m else "", "screenshot": shot}
    return {"resultaat": "onzeker", "melding": "Na 'Bestelling afronden' kwam geen bevestiging in beeld.", "screenshot": shot}


async def prijzen(page, kluis):
    """Leest de hele dealerprijslijst uit de webshop (ex btw), met bestelcode (bijv. FNSSST) en EAN per artikel."""
    await ingelogd(page, kluis)
    await ga_naar(page, f"{BASIS}/nl/catalog")
    lijst = await page.evaluate(
        """async () => {
          function parse(doc){ return [...doc.querySelectorAll('input[type=number]')].map(inp => {
            let el = inp; for (let i = 0; i < 12 && el; i++) { el = el.parentElement; const t = el.textContent; if (/€/.test(t) && /\\d{2}\\.\\d{3}\\.\\d{2}/.test(t)) break; }
            const t = (el ? el.textContent : '').replace(/\\s+/g, ' ').trim();
            const code = (t.match(/\\d{2}\\.\\d{3}\\.\\d{2}/) || [''])[0];
            const pr = (t.match(/€\\s*([\\d.]+,\\d{2})/) || ['', ''])[1];
            const naam = t.replace(code, '').replace(/€\\s*[\\d.,]+/, '').replace(/\\s+/g, ' ').trim().slice(0, 90);
            return {code, naam, inkoop: pr ? Number(pr.replace(/\\./g, '').replace(',', '.')) : 0}; }); }
          const alle = {};
          for (let p = 1; p <= 30; p++) {
            const h = await fetch(location.pathname + '?page=' + p, {credentials: 'include'}).then(r => r.text());
            const r = parse(new DOMParser().parseFromString(h, 'text/html'));
            if (!r.length) break;
            r.forEach(x => { if (x.code && x.inkoop > 0) alle[x.code] = x; });
          }
          const lijst = Object.values(alle);
          for (let i = 0; i < lijst.length; i += 8) {
            await Promise.all(lijst.slice(i, i + 8).map(async (a) => {
              try {
                const h = await fetch('/nl/catalog/item/' + a.code, {credentials: 'include'}).then(r => r.text());
                const b = h.match(/Bestelcode:\\s*(?:<[^>]*>\\s*)*([A-Za-z0-9.\\-\\/+]+)/);
                const e = h.match(/EAN Code:\\s*(?:<[^>]*>\\s*)*(\\d{8,14})/);
                a.bestelcode = b ? b[1] : ''; a.ean = e ? e[1] : '';
              } catch (x) {}
            }));
          }
          return lijst;
        }"""
    )
    if len(lijst) < 5:
        tekst = (await page.inner_text("body"))[:200].replace("\n", " ")
        raise RuntimeError(f"maar {len(lijst)} artikelen gevonden op {page.url} ({await page.title()}): {tekst}")
    return lijst
