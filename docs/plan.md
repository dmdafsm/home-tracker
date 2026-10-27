# Tracker imobiliar — plan

Oct 27, 2026 · @Daniel

## Scop și criterii

Tool-ul adună anunțurile de apartamente de pe toate platformele românești într-un singur loc. Grupează anunțurile care descriu același apartament și dă fiecăruia un scor de încredere. Te anunță când apare ceva care se potrivește unui profil de căutare. Rulează local, pe MacBook, în Docker.

Primul profil, pentru apartamentul mamei:

| Criteriu | Valoare | Tip |
| --- | --- | --- |
| Oraș / cartier | Cluj-Napoca, Gheorgheni | obligatoriu |
| Preț | 130.000–165.000 € (țintă \~150.000 €) | obligatoriu |
| Etaj | parter (simplu sau înalt) sau etajul 1 | obligatoriu |
| Balcon | da | preferință (crește scorul) |
| Orizont | următoarele 6 luni | — |

Profilurile sunt fișiere de configurare (YAML), nu cod. Fiecare are filtre dure, care exclud anunțul, și preferințe ponderate, care îl ordonează. Un profil nou pentru altcineva înseamnă doar un fișier nou.

## Arhitectură

Totul pornește cu un singur `docker compose up` pe MacBook. Sunt patru containere: `db` (PostgreSQL), `worker` (scheduler, colectoare, procesare), `web` (interfața) și `ntfy`. Limbajul e Python 3.12, cu httpx, Playwright, imagehash și FastAPI.

```
platforme ──> worker (colectoare, parsare, potrivire, deduplicare) ──> db (PostgreSQL)
                 │                                                     │
                 └──> data/ (HTML brut, poze)                          ├──> web ──> Cloudflare Tunnel + Access
                                                                       └──> ntfy ──> telefon
```

Colectoarele scriu în arhivă, iar parsarea alimentează baza de date. Celelalte servicii lucrează doar cu baza. Spre exterior ies doar notificările și interfața web.

## Surse de date și colectare

Fiecare platformă are propriul colector (adapter), cu o interfață comună: `search(profil)` întoarce o listă de URL-uri, iar `fetch(url)` întoarce anunțul brut. Adăugarea unei platforme noi înseamnă un singur fișier nou.

| Platformă | Metodă probabilă | Prioritate |
| --- | --- | --- |
| Storia | JSON-ul embed din pagină (Next.js) | MVP |
| OLX | API-ul intern folosit de site, altfel HTML | MVP |
| Imobiliare.ro | HTML; Playwright dacă e nevoie | MVP |
| Publi24 | HTML | etapa 2 |
| Lajumate | HTML | etapa 2 |
| Site-uri de agenții din Cluj | HTML, câte un adapter pe agenție | etapa 3 |

Reguli de colectare:

- Pagina de căutare se rulează la 30–60 de minute, cu o pauză aleatoare și un singur request pe rând pe fiecare platformă.
- Pagina de detaliu se descarcă o singură dată pe anunț nou. Se reverifică zilnic, pentru preț și pentru a vedea dacă a dispărut.
- Se salvează HTML-ul brut și pozele, comprimate. Așa poți reprocesa tot istoricul când îmbunătățești parserul.
- Fiecare adapter are un test pe o pagină salvată, ca să afli imediat când platforma își schimbă structura.
- Metodele din tabel trebuie verificate la implementare, pentru că platformele se schimbă des.

## Model de date

Distincția centrală e între **anunț** și **apartament**. Un apartament fizic are un singur cluster, care poate conține zeci de anunțuri, de pe mai multe platforme, de la mai mulți publicatori, de-a lungul mai multor luni.

| Tabel | Ce ține | Câmpuri cheie |
| --- | --- | --- |
| `listing` | o apariție a unui anunț pe o platformă | platformă, URL, id extern, titlu, text, preț, mp, camere, etaj, balcon, adresă aproximativă, coordonate, prima/ultima văzută, activ |
| `listing_snapshot` | versiunile în timp ale aceluiași anunț | listing, data, preț, text hash, nr. poze |
| `photo` | fiecare poză descărcată | listing, fișier, pHash, dHash, dimensiuni |
| `publisher` | cine publică | tip declarat (proprietar/agenție), nume, telefon (hash), nr. anunțuri active |
| `property` | apartamentul fizic (cluster) | adresă estimată, bloc, etaj, mp, an construcție estimat, scor autenticitate |
| `property_listing` | legătura anunț–apartament | scor potrivire, motiv (poze, text, telefon) |
| `profile` / `match` / `alert` | criterii, potriviri, notificări trimise | profil, apartament, scor, trimis la |

Baza de date e PostgreSQL, cu PostGIS pentru coordonate (cartier, distanțe) și pgvector pentru căutarea rapidă de poze similare. Pozele stau pe disc, într-un volum Docker.

## Deduplicare și istoric

Două anunțuri sunt considerate același apartament când scorul lor de potrivire trece de un prag. Pozele cântăresc cel mai mult, pentru că agențiile copiază pozele proprietarului.

1. **Poze:** se calculează pHash și dHash pentru fiecare poză. Distanța Hamming sub \~8 biți înseamnă aceeași poză, chiar și redimensionată, recomprimată sau cu watermark mic. Un crop puternic sau un watermark mare se prind cu un embedding (CLIP), comparat prin pgvector.
2. **Text:** după normalizare (diacritice, majuscule, cifre), se compară cu shingles și MinHash. O agenție care rescrie textul tot păstrează fragmente: „bloc de cărămidă”, „lângă Iulius”, numărul de mp.
3. **Atribute:** mp ±2, același etaj, același număr de camere, coordonate la sub \~300 m. Aceste atribute nu decid singure, dar elimină potrivirile false.
4. **Telefon:** se compară hash-ul numărului extras din text sau din anunț.

Scorul combinat se calculează cu ponderi fixe la început. Mai târziu, ponderile se pot învăța din potrivirile pe care le confirmi sau le respingi în interfață.

Din cluster ies direct informațiile pe care le-ai cerut:

- ce agenții și ce proprietari publică apartamentul, cu prețul fiecăruia;
- istoricul prețului pe fiecare anunț, din snapshot-uri;
- de câte ori a fost republicat, inclusiv sub id-uri noi, cu aceleași poze;
- prima apariție, care e de obicei sursa originală, adesea proprietarul;
- câte zile stă pe piață.

## Scor de autenticitate

Tool-ul nu poate dovedi că un anunț e real. Poate în schimb să adune semnale și să arate de ce un anunț e suspect. Scorul are valori de la 0 la 100 și vine mereu însoțit de lista semnalelor care l-au produs, ca să poți judeca tu.

| Semnal | Indică | Pondere |
| --- | --- | --- |
| Aceleași poze la 3+ publicatori diferiți | anunț preluat de agenții | mare |
| Publicatorul agenție nu e prima apariție a pozelor | hijack de la proprietar | mare |
| Preț cu >15% sub mediana clusterelor similare din Gheorgheni | momeală | mare |
| „Proprietar” al cărui telefon apare pe 5+ apartamente diferite | agent deghizat | mare |
| Poze găsite într-un anunț vechi, vândut sau dispărut | anunț fictiv, reciclat | mare |
| Republicat periodic, timp de luni de zile, fără schimbări | anunț-vitrină | mediu |
| Lipsesc etajul, mp sau adresa, dar există poze multe | atragere de clienți | mic |
| Același publicator cu prețuri diferite pe platforme diferite | inconsecvență | mic |

Mediana de preț pe mp se calculează din datele proprii, pe tipologii comparabile (camere, etaj, bloc vechi sau nou). Ea devine mai corectă pe măsură ce tool-ul rulează mai mult timp.

Din interfață poți marca un anunț ca „confirmat real” sau „fals”, după ce ai sunat la el. Aceste etichete vor servi mai târziu la calibrarea ponderilor.

## Estimarea anului construcției

Nu există o sursă publică unică și gratuită cu anul fiecărui bloc. Tool-ul combină mai multe surse și afișează un interval, împreună cu sursa lui, de exemplu „1975–1982, din tipologie și din 3 anunțuri”.

| Sursă | Ce dă | Cost | Încredere |
| --- | --- | --- | --- |
| Textul anunțurilor din același bloc | „bloc din 1978”, „construcție 2008” | gratuit | medie, se confirmă la 2+ anunțuri |
| Tipologia blocului (P+4 / P+10, cărămidă / panou) | decadă probabilă; Gheorgheni e în mare parte construit în anii '70–'80 | gratuit | mică–medie |
| OpenStreetMap (`start_date`, `building:levels`) | an exact pe unele clădiri | gratuit | mare unde există, acoperire mică |
| Imagini istorice (ortofoto, hărți vechi) | blocul există sau nu într-un anumit an | gratuit, manual | mare pentru interval |
| Geoportalul Primăriei Cluj-Napoca / ANCPI | date cadastrale; extrasul CF poate conține anul | extras CF plătit | mare |

Pentru apartamentul ales, extrasul CF de la ANCPI e oricum necesar înainte de cumpărare. Pentru anunțurile de pe piață, tool-ul folosește estimarea. Ce date publică exact geoportalul Primăriei trebuie verificat în etapa 4.

## Notificări

Notificările pornesc din motorul de potrivire și pleacă pe canalele alese în profil. Recomandarea e ntfy pentru instant și email pentru rezumat.

| Canal | Cum funcționează | Avantaj | Limită |
| --- | --- | --- | --- |
| **ntfy** (self-hosted) | container în același Docker; aplicația ntfy pe telefon se abonează la un topic | open-source, fără cont, push instant | telefonul ajunge la Mac prin Cloudflare Tunnel; pe iOS, push-ul instant cere ca serverul să trimită prin ntfy.sh (\`upstream-base-url\`) |
| **Email** (SMTP) | un cont Gmail sau Fastmail cu parolă de aplicație | universal, bun pentru rezumatul zilnic | nu e instant pe telefon |
| **Notificare macOS** | un mic script pe Mac care citește din API-ul local | zero configurare | doar când stai la laptop |
| **Web Push** din interfață | notificări din browser | fără aplicație extra | cere HTTPS și browserul pornit |

Regulile de trimitere:

- **Instant:** apartament nou, cu potrivire peste prag și scor de autenticitate peste prag.
- **Instant:** scădere de preț peste 3% la un apartament salvat.
- **Zilnic:** rezumat cu anunțurile noi slab potrivite, cu cele dispărute (posibil vândute) și cu republicările.
- Un apartament declanșează o singură alertă, chiar dacă apare pe 5 platforme. Alertele noi vin doar pentru schimbări.

## Interfață web

Interfața e o aplicație web locală, disponibilă la `localhost:8080`. Prin Cloudflare Tunnel, protejată cu Cloudflare Access, o poți deschide și de pe telefon. Are cinci ecrane:

- **Feed:** apartamentele potrivite pentru profilul activ, ordonate după scor. Fiecare card arată poza principală, prețul minim și maxim, etajul, scorul de autenticitate și numărul de anunțuri.
- **Apartament:** toate anunțurile din cluster, pe o axă a timpului, cu graficul prețului, publicatorii, semnalele de autenticitate și anul estimat. Butoanele: salvează, ascunde, „confirmat real”, „fals”, „nu e același apartament”.
- **Hartă:** clusterele din cartier, cu filtre.
- **Publicatori:** agențiile și „proprietarii”, cu numărul de anunțuri, ce procent au preluat de la alții și diferențele medii de preț.
- **Profiluri:** editarea criteriilor și a canalelor de notificare.

Stack-ul propus: backend FastAPI (Python), care servește și API-ul; frontend în HTMX sau un React mic. HTMX e mai simplu de întreținut pentru un singur utilizator.

### Acces din exterior

Tracker-ul folosește tunelul Cloudflare care rulează deja pe Mac. Nu creează unul nou, ci adaugă două reguli de ingress în configurarea existentă:

| Hostname | Duce la | Protecție |
| --- | --- | --- |
| `tracker.romdata.ro` | `web`, pe portul publicat local (de exemplu `localhost:8080`) | Cloudflare Access, doar emailul tău |
| `ntfy.romdata.ro` | `ntfy`, pe portul publicat local (de exemplu `localhost:8090`) | autentificarea proprie ntfy (`auth-default-access: deny-all` și un token pentru telefon), fără Access |

ntfy rămâne în afara Cloudflare Access pentru că aplicația de pe telefon nu poate trece prin login-ul Access. Containerele publică porturile doar pe `127.0.0.1`, așa că rămân inaccesibile din rețeaua locală. Celelalte aplicații private de pe tunel nu sunt afectate.

## Aspecte legale și riscuri

Pentru uz personal, la volum mic, riscul principal e tehnic, nu juridic. Lucrurile se schimbă dacă tool-ul devine mai târziu un serviciu pentru alții.

| Risc | Ce înseamnă | Măsură |
| --- | --- | --- |
| Termenii platformelor | OLX și Storia interzic colectarea automată | volum mic, ritm uman, fără ocolirea protecțiilor; date doar pentru uz propriu |
| Blocări anti-bot | IP-ul de acasă poate fi limitat temporar | pauze, cache, Playwright doar unde e necesar; niciodată rezolvare de CAPTCHA |
| Date personale (GDPR) | telefoane și nume ale proprietarilor | telefonul se stochează doar ca hash; textul brut rămâne local |
| Drepturi asupra pozelor | pozele aparțin publicatorilor | stocate doar local pentru comparare, nerepublicate |
| Schimbări de structură | un parser se strică fără avertisment | teste pe pagini salvate și o alertă când un adapter întoarce 0 rezultate |
| Mac oprit sau în sleep | nu rulează colectarea | la pornire rulează o recuperare: mai multe pagini de căutare, sortate după cele mai noi; anunțurile apărute și dispărute cât e Mac-ul închis se pierd; rezumatul zilnic arată golurile; dacă devine o problemă, \`worker\` se mută pe un mini-server |

Dacă vrei să-l deschizi altor utilizatori, trebuie un aviz juridic despre termenii platformelor și despre GDPR. Atunci ar fi preferabile parteneriatele sau API-urile oficiale, unde există.

## Etape de livrare

Ordinea e gândită așa încât tool-ul să-ți fie util pentru căutarea mamei încă din prima etapă. Restul îl construiești în paralel cu căutarea, în cele 6 luni.

1. **MVP: alerte utile**, ținta fiind primele 1–2 weekenduri.
   - Docker compose cu `db`, `worker` și `ntfy`.
   - Adaptere pentru Storia, OLX și Imobiliare.ro, plus profilul Gheorgheni.
   - Deduplicare simplă: aceeași platformă și id, apoi pHash pe poze.
   - Alertă ntfy pentru fiecare apartament nou potrivit.
   - Gata când primești pe telefon un anunț real, înaintea emailului de la Storia.
2. **Clustere și istoric**
   - Deduplicare completă (poze, text, atribute, telefon), istoricul prețurilor, detectarea republicărilor.
   - Interfață web cu feed și pagina de apartament.
   - Rezumat zilnic pe email.
3. **Autenticitate și publicatori**
   - Scorul cu semnale explicate, pagina de publicatori, butoanele de etichetare.
   - Încă două platforme (Publi24, Lajumate).
4. **Anul construcției și profiluri multiple**
   - Estimarea anului din text, tipologie și OSM, plus verificarea surselor Primăriei.
   - Editor de profiluri din interfață și canale de notificare per profil.

Ce trebuie decis înainte de codul pentru etapa 1:

- [x] Acces de pe telefon: Cloudflare Tunnel către `ntfy` și `web`, cu Cloudflare Access în fața interfeței.
- [x] Etaj: parter simplu, parter înalt sau etajul 1.
- [x] Mac-ul nu stă mereu pornit: colectarea rulează când e deschis și recuperează la pornire.
- [x] Domeniu: romdata.ro, prin tunelul Cloudflare existent pe Mac.
- [x] Interval de preț: 130.000–165.000 €.
