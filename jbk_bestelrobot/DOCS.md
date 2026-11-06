# JBK Bestelrobot

Plaatst bestellingen die Johnny in het dashboard (Bestellingen) akkoord heeft gegeven bij de webshop van de leverancier. Eerste leverancier: de Quooker-dealerwebshop.

## Instellen
1. **Configuratie**: plak bij *Robotsleutel* de sleutel uit het dashboard (Bestellingen → Bestelrobot → Robotsleutel maken). Vul bij *Meldingsdienst* je telefoon in, bijvoorbeeld `notify.mobile_app_iphone_van_johnny` (te vinden onder Ontwikkelhulpmiddelen → Acties, zoek op "notify").
2. Start de add-on en open **Bestelrobot** in de zijbalk.
3. Vul daar één keer het e-mailadres en wachtwoord van de Quooker-dealerwebshop in en klik op *Inloggen nu testen*.

## Hoe het werkt
- Elke minuut vraagt de robot het portaal of er goedgekeurde bestellingen klaarstaan.
- **Proefmodus** (standaard, in het dashboard aan/uit te zetten): de robot vult de winkelwagen en de afrekenpagina, maakt een screenshot en stopt vóór *Bestelling afronden*. Jij klikt in het dashboard op *Definitief bestellen*.
- **Automatisch**: de robot rondt de bestelling zelf af, tot het maximumbedrag uit het dashboard.
- De robot betaalt altijd **op rekening** en stopt bij een prijsverschil met de prijslijst, een te hoog bedrag, een afleveradres dat niet in je Quooker-adresboek staat of een winkelwagen met andere artikelen.
- Elke ochtend rond 7:30 test de robot of het inloggen nog werkt; zo niet, dan krijg je een pushbericht.

## Privacy
De inloggegevens van de webshops staan alleen in de map van deze add-on (en in je Home Assistant-back-ups). Ze gaan niet naar het dashboard of naar Claude.
