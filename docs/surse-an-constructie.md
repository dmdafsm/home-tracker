# Surse pentru anul construcției

Nu există o sursă publică, gratuită și interogabilă automat cu anul fiecărui bloc din Cluj-Napoca.
`hometracker` estimează un interval din ce găsește singur (vezi `buildyear.py` și
[ADR 0005](engineering/decisions/0005-openstreetmap-buildings.md)); pentru apartamentul ales,
anul se confirmă manual din documentele de mai jos. Informații verificate în octombrie 2026, din
surse publice; tarifele și procedurile se pot schimba.

## Ce folosește aplicația automat

| Sursă | Încredere | Acoperire | Observații |
| --- | --- | --- | --- |
| OpenStreetMap (`start_date` pe clădire, prin Overpass) | mare | mică: puține blocuri din Cluj au anul completat | cel mult `OSM_LOOKUPS_PER_RUN` interogări pe rulare, rezultat păstrat în `osm_building`; datele © OpenStreetMap contributors (ODbL) |
| Anii declarați în anunțuri (câmpul platformei, textul) | medie dacă două anunțuri diferite se confirmă (±2 ani), altfel mică | medie, mai ales Storia | se folosesc și anunțurile altor apartamente aflate la cel mult 40 m (același bloc); anii care nu se potrivesc apar ca „conflicte” |
| Tipologia blocului (panouri, cărămidă P+4, P+8 sau mai înalt, bloc nou) | mică | mare | doar un interval larg, de exemplu 1962–1990 pentru panouri |

## Ce se verifică manual, pentru apartamentul ales

- **Certificatul de performanță energetică.** Vânzătorul trebuie să îl prezinte la vânzare;
  formularul are anul construcției clădirii. E cea mai la îndemână sursă: se cere vânzătorului
  sau agentului înainte de vizionare.
- **Extrasul de carte funciară pentru informare (ANCPI).** Se comandă online pe
  [epay.ancpi.ro](https://epay.ancpi.ro) cu numărul cadastral sau numărul cărții funciare
  (20 lei online, 25 lei la ghișeul OCPI Cluj; gratuit pentru proprietar). Partea I descrie
  clădirea (destinație, suprafață) și, la apartamente, unitatea individuală (`C1-U…`). Anul
  construirii nu apare în toate extrasele; când lipsește, extrasul confirmă totuși suprafața,
  proprietarul și sarcinile, deci merită oricum înaintea unei oferte. Numărul cadastral se cere
  vânzătorului; geoportalul ANCPI arată imobilele pe hartă, dar nu întotdeauna numărul.
- **Extrasul de plan cadastral** (15 lei online, același portal) arată conturul clădirii pe
  parcelă, fără an.
- **Cartea tehnică a construcției** se păstrează la asociația de proprietari și are anul
  recepției. Se poate cere prin vânzător sau administrator.
- **Primăria Cluj-Napoca.** Secțiunea GIS de pe site (layere PUG, autorizații de construire)
  arată ce se poate construi și autorizațiile recente; nu am găsit un câmp cu anul construcției
  pentru blocurile existente. Decizia de impunere a proprietarului folosește anul construcției
  (impozitul pe clădiri se reduce pentru clădirile vechi), deci vânzătorul îl poate confirma
  din documentele lui fiscale.

## Ce nu facem

- Nu interogăm automat ePay/ANCPI sau portalul primăriei: cer cont, plată sau nu oferă o
  interfață pentru interogări automate, iar un extras se comandă doar pentru un apartament
  concret.
- Nu descărcăm geoportaluri în masă și nu combinăm date personale din cartea funciară
  (proprietari) cu anunțurile.

## Surse

- [Capital: extrasul de carte funciară online, 20 lei](https://www.capital.ro/cum-afli-cine-e-proprietarul-unui-imobil.html)
- [Profit.ro: eliminarea taxei pentru proprietari](https://profit.ro/taxe-si-consultanta/taxele-platite-de-proprietari-pentru-extrasele-de-carte-funciara-eliminate-21925037)
- [Agerpres: extrasul de plan cadastral online din 2020](https://agerpres.ro/administratie/2020/01/08/ancpi-extrasul-de-plan-cadastral-poate-fi-obtinut-online-incepand-cu-8-ianuarie-2020--428755)
- [Actual de Cluj: butonul GIS al Primăriei Cluj-Napoca](https://actualdecluj.ro/primaria-sfatuieste-clujenii-sa-verifice-ce-se-poate-construi-pe-terenurile-pe-care-vor-sa-le-cumpere-cu-butonul-gis-si-certificat-de-urbanism/)
- [Legeaz: extrasul de carte funciară](https://legeaz.net/dictionar-juridic/extras-de-carte-funciara)
