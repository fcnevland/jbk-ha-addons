## 0.1.0
- Eerste versie: Quooker-dealerwebshop, proefmodus, inlogcontrole, prijzen ophalen, pushberichten.

## 0.1.1
- Kleinere basis (python slim + alleen Chromium) zodat de add-on minder schijfruimte gebruikt.

## 0.1.2
- Strengere inlogcontrole bij Quooker, screenshot van de laatste fout op het scherm van de add-on, duidelijkere foutmelding bij prijzen ophalen.

## 0.1.3
- Inloggen bij Quooker via Enter (eigen inlogformulier van de dealerwebshop), "Onthoud mij" aan, opnieuw proberen bij netwerkfoutjes direct na het opstarten.

## 0.1.4
- Quooker staat maar één sessie per account toe: bij bestellen/prijzen neemt de robot de sessie over ("Log andere sessie uit"), bij de dagelijkse inlogcontrole niet.

## 0.1.5
- Adresboek van Quooker wordt na het laden pas gevuld: de robot wacht nu op de adressen en zoekt op postcode (ook voorbij de eerste 25 regels).

## 0.1.6
- Verzenddatum op de afrekenpagina: "zo snel mogelijk" of de datum die de klant in het portaal/API kiest. Twijfel = stoppen met controle.
- Afrekenpagina wordt bewaard (afrekenpagina.jpg/.html op het scherm van de add-on).

## 0.1.7
- "Bestelling afronden": eerste zichtbare knop, wacht tot de webshop echt doorgaat (max. 90 s), toont foutmeldingen en bewaart de pagina daarna (na_afronden.html).

## 0.1.8
- Na "Bestelling afronden" stuurt Quooker je terug naar een lege winkelwagen: dat telt nu als besteld (voorheen "onzeker", wat tot dubbel bestellen leidde).
