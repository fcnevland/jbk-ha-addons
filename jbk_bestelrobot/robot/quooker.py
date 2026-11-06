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


async def ingelogd(page, kluis):
    """Zorgt dat de browser is ingelogd. Gebruikt de opgeslagen gegevens als het nodig is."""
    await page.goto(f"{BASIS}/nl/shoppingcart", wait_until="domcontentloaded", timeout=45000)
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
        await page.locator("button[type=submit], button[name=action]").first.click()
        await ww.wait_for(timeout=15000)
    await ww.fill(gegevens["wachtwoord"])
    await page.locator("button[type=submit], button[name=action]").first.click()
    try:
        await page.wait_for_url(re.compile(r"dealerwebshop\.quooker\.nl"), timeout=45000)
        await page.wait_for_load_state("domcontentloaded")
        if not await is_ingelogd(page):
            await page.goto(f"{BASIS}/nl/shoppingcart", wait_until="domcontentloaded", timeout=45000)
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
    await page.goto(f"{BASIS}/nl/shoppingcart", wait_until="domcontentloaded", timeout=45000)
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
    await page.goto(f"{BASIS}/nl/customeraddressbook", wait_until="domcontentloaded", timeout=45000)
    huisnr = (re.findall(r"\d+\s*[A-Za-z]?", straat) or [""])[-1].replace(" ", "").lower()
    rijen = page.locator("tr")
    for i in range(await rijen.count()):
        rij = rijen.nth(i)
        t = await rij.inner_text()
        if pc in postcode(t) and (not huisnr or huisnr in re.sub(r"\s+", "", t).lower()):
            await rij.locator("button:has-text('Kies')").first.click()
            await page.wait_for_url(re.compile(r"/shoppingcart/checkout"), timeout=30000)
            return f"{straat}, {a.get('postcode')}"
    raise Stop(f"Het afleveradres {straat}, {a.get('postcode')} {a.get('plaats', '')} staat niet in je Quooker-adresboek. Vraag Quooker het toe te voegen, of laat bij jou afleveren.", "controle")


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
        await page.goto(f"{BASIS}/nl/shoppingcart/checkout", wait_until="domcontentloaded", timeout=45000)
        adres = await kies_adres(page, order)
        ref = page.get_by_placeholder(re.compile("referentie", re.I)).first
        await ref.fill(order["referentie"][:30])
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
            return {"resultaat": "controle", "melding": f"Proefmodus: winkelwagen en afrekenpagina staan klaar (referentie {order['referentie']}, afleveren: {adres}, op rekening).", "totaal": totaal, "screenshot": shot}
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
    await page.goto(f"{BASIS}/nl/catalog", wait_until="domcontentloaded", timeout=45000)
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
