# home-tracker

Tracker de piață imobiliară care rulează local, în Docker. Colectează anunțuri de pe
Storia, Imobiliare.ro, Publi24 și Lajumate (OLX e dezactivat, vezi mai jos), grupează pe
apartamente fizice anunțurile care descriu același apartament și le arată într-o interfață
web, filtrate și ordonate după profilurile de căutare. Nu trimite notificări: totul se
urmărește în interfață.

Planul e în [docs/plan.md](docs/plan.md), iar arhitectura actuală în
[docs/architecture.md](docs/architecture.md).

## Cum funcționează

```
worker (periodic) ─► adaptere (storia, imobiliare, publi24, lajumate) ─► arhivă HTML brut (data/raw)
                          │
                          ▼
                  profil YAML: filtre + scor ─► poze + pHash ─► grupare pe apartament
                                                                    │
                                                                    ▼
                                                PostgreSQL ─► interfața web (feed, apartament)
```

- **Profilurile** stau în `profiles/*.yaml`. Fiecare are filtre dure (preț, etaj,
  suprafață, cuvinte cheie), preferințe care dau scorul (de exemplu balconul) și
  URL-urile de căutare de pe fiecare platformă. Se editează și din interfață.
- **Deduplicarea** leagă două anunțuri de același apartament după un scor combinat: poze
  comune (pHash și dHash), text asemănător (fără textul-șablon al agențiilor), același telefon
  de proprietar, suprafață, etaj, camere și distanță. Două poze comune ajung; o singură poză
  (de obicei fațada blocului) cere aceeași suprafață și același etaj. O contradicție clară
  (alt etaj, altă suprafață, la peste 1 km) ține anunțurile separate. `hometracker property`
  arată pentru fiecare anunț scorul și semnalele care l-au legat.
- **Posibil același apartament**: mai multe agenții care fotografiază fiecare apartamentul
  rămân separate (pozele diferă). Apartamentele cu același etaj și număr de camere, suprafață
  la cel mult 5 mp, preț la cel mult 2% și aproape pe hartă sunt sugerate în interfață, unde
  le poți uni sau marca drept diferite.
- **Reverificarea zilnică**: anunțurile potrivite care nu mai apar în primele pagini de
  căutare sunt redeschise o dată la 24 de ore (cel mult `RECHECK_PER_RUN` pe rulare). Un preț
  nou intră în istoric; un anunț șters (404/410) e marcat ca dispărut.
- **Republicările**: un anunț nou pentru același apartament, pe aceeași platformă, de la
  același publicator (telefon sau nume), e marcat ca republicare.
- **Anul construcției** e un interval cu sursa lui: anul din OpenStreetMap pentru clădirea
  de la coordonatele anunțului (cel mult `OSM_LOOKUPS_PER_RUN` interogări pe rulare, păstrate
  local), altfel anii declarați în anunțuri (și ale altor apartamente din același bloc), apoi
  tipologia blocului. Pentru apartamentul ales, anul se confirmă din documente; vezi
  [docs/surse-an-constructie.md](docs/surse-an-constructie.md).
- **Cu calculatorul oprit sau în sleep** nu se colectează nimic. La pornire și după o pauză mai lungă de
  două intervale, rularea citește mai multe pagini (`PAGES_CATCHUP`), ca să recupereze
  ce a apărut între timp.
- **Poze și HTML** se salvează în `data/`, local, doar pentru comparare și reprocesare.
  Telefoanele găsite în anunțuri se păstrează doar ca hash.

## Instalare

Ai nevoie de Docker (de exemplu Docker Desktop).

```sh
git clone https://github.com/dmdafsm/home-tracker.git
cd home-tracker
cp .env.example .env        # completează POSTGRES_PASSWORD
docker compose up -d --build
```

Interfața e pe http://localhost:8080 (portul din `WEB_LOCAL_PORT`, publicat doar pe
`127.0.0.1`).

### Acces din exterior (Cloudflare Tunnel)

Proiectul nu configurează accesul din exterior. Interfața nu are login propriu, deci se
deschide din exterior doar printr-un tunel, **cu Cloudflare Access în față**, restricționat la
contul tău. Regula de ingress din configurarea tunelului, înaintea regulii finale
`http_status:404`:

```yaml
  - hostname: tracker.example.com
    service: http://localhost:8080
```

Dacă `cloudflared` rulează la rândul lui într-un container Docker, folosește
`http://host.docker.internal:8080` în loc de `localhost`.

## Interfața web

- **Apartamente** (feed-ul): apartamentele care trec de profilul ales, cu un carusel de poze,
  prețul, prețul pe mp, etajul, anul blocului, scorul de autenticitate și etichetele. Filtre
  (text, preț, suprafață, camere, platforme, proprietar sau agenție, salvate, ascunse,
  dispărute) și sortări (potrivire, cele mai noi, preț, preț pe mp, suprafață, autenticitate).
  De pe card: detalii, anunțul cel mai ieftin, salvează, ascunde. Un banner apare când
  colectarea are probleme. Interfața e în stil Material 3, fără JavaScript.
- **Pagina unui apartament**: anunțurile lui de pe toate platformele, publicatorii, semnalele
  care le-au legat, anul construcției cu dovezile, istoricul prețului, pozele și descrierea.
  Poți să-l **salvezi**, să-l **ascunzi**, să-l marchezi **confirmat real** sau **fals** după ce ai
  sunat, iar la fiecare anunț poți spune **nu e același apartament**. Secțiunea **posibil același
  apartament** are butoanele **e același apartament** (le unește) și **nu e același**.
- **Scorul de autenticitate** (0–100) vine cu semnalele care l-au scăzut: aceleași poze la mai
  mulți publicatori, o agenție care a publicat după proprietar, un preț mult sub mediana
  apartamentelor asemănătoare, un „proprietar” cu telefonul la multe apartamente, poze dintr-un
  anunț dispărut, republicări luni de zile la același preț, detalii lipsă. Scorul nu dovedește
  nimic; arată ce merită verificat la telefon.
- **Publicatori**: pentru fiecare agenție sau proprietar, câte anunțuri active are, câte
  apartamente, ce procent a preluat de la alții și cu cât e în medie mai scump sau mai ieftin
  decât ceilalți pentru aceleași apartamente.
- **Profiluri**: editarea profilurilor ca text YAML, cu comentariile păstrate. **Verifică**
  validează și arată câte apartamente deja grupate ar trece de filtre, **Salvează** scrie
  fișierul (versiunea veche rămâne în `profiles/.history/`). Un profil nou pornește dintr-o
  copie a unuia existent. Worker-ul citește profilurile la fiecare rulare. Profilurile noi nu
  intră în git; în repo rămâne doar exemplul.
- **Stare**: ultimele rulări pe fiecare platformă, ultima reușită și problema de rezolvat
  (blocaj, niciun rezultat, worker oprit).

Pentru lucrul la interfață fără date reale: `DATA_DIR=data/demo uv run hometracker demo`,
apoi `DATA_DIR=data/demo uv run hometracker web --port 8081`.

## Ajustarea căutărilor

Cel mai sigur mod de a configura o căutare: setezi filtrele direct pe site, în browser,
sortezi după cele mai noi și copiezi URL-ul în profil, la `sources.<platformă>.url`.
URL-urile din profilul inclus sunt un punct de plecare și trebuie verificate.

## Comenzi utile

```sh
docker compose logs -f worker                       # ce face acum
docker compose exec worker hometracker run          # o rulare imediată
docker compose exec worker hometracker run --catchup
docker compose exec worker hometracker property "<url>"  # istoricul apartamentului: anunțuri, prețuri, republicări
docker compose exec worker hometracker dump "<url>" # verifică ce extrage un adapter
```

## Când un adapter nu mai merge

Platformele își schimbă des structura paginilor. Când se întâmplă asta, adapterul nu
mai găsește anunțuri, iar pagina **Stare** (și bannerul din feed) arată problema. Pașii:

1. Rulează `hometracker dump "<url-ul căutării>"`. Pagina se salvează în `data/debug/`,
   iar comanda arată ce a reușit adapterul să extragă.
2. Scrie în `tests/fixtures/` o copie sintetică a paginii: aceeași structură, cu titluri,
   prețuri, nume și telefoane inventate. Repository-ul e public, deci pagina reală rămâne
   doar local, în `data/debug/`.
3. Ajustează adapterul din `src/hometracker/adapters/` până trece testul.

**OLX e dezactivat** în profilul inclus: din octombrie 2026 răspunde colectorului cu HTTP 403
(„Request blocked”, de la CloudFront), chiar și pentru căutare. Adapterul rămâne în cod, dar
sursa are `enabled: false` până există o cale permisă de colectare.

Adapterele nu ocolesc protecțiile site-urilor. Dacă un site cere o verificare (CAPTCHA),
rularea se oprește pentru platforma respectivă și reîncearcă la intervalul următor.

## Dezvoltare

Ai nevoie de [uv](https://docs.astral.sh/uv/).

```sh
uv sync                              # Python 3.12, dependențele și uneltele de dezvoltare
python3 scripts/install_hooks.py     # o dată pe clonă: blochează commit-urile direct pe main
uv run pytest
python3 scripts/verify.py            # verificarea completă, ca în CI
```

Regulile de lucru (branch-uri, teste, review, date personale) sunt în [AGENTS.md](AGENTS.md)
și [CONTRIBUTING.md](CONTRIBUTING.md).

Fără `DATABASE_URL`, aplicația folosește SQLite în `data/hometracker.db`, util pentru
rulări locale în afara Docker.

## Structura

| Cale | Ce conține |
| --- | --- |
| `src/hometracker/adapters/` | câte un adapter pe platformă, cu interfața din `base.py` |
| `src/hometracker/parsing.py` | interpretarea textelor în română: etaj, preț, suprafață, balcon |
| `src/hometracker/profiles.py` | profilurile YAML și calculul scorului |
| `src/hometracker/dedup.py` | gruparea anunțurilor pe apartamente și republicările |
| `src/hometracker/merge.py` | „posibil același apartament” și unirea manuală |
| `src/hometracker/history.py` | istoricul unui apartament |
| `src/hometracker/publishers.py` | publicatorii: anunțuri preluate, diferențe de preț, posibili agenți |
| `src/hometracker/authenticity.py` | scorul de autenticitate și semnalele lui |
| `src/hometracker/buildyear.py`, `osm.py` | anul construcției |
| `src/hometracker/status.py` | starea colectării pe platformă |
| `src/hometracker/web.py` | interfața web |
| `src/hometracker/pipeline.py` | o rulare completă: căutare, detaliu, salvare, grupare |
| `src/hometracker/cli.py` | comenzile `hometracker` |
| `profiles/` | profilurile de căutare |
| `tests/` | teste pe pagini sintetice și pe fluxul complet (SQLite) |
| `docs/` | planul, arhitectura, regulile de engineering și deciziile (ADR) |
| `scripts/` | verificarea repository-ului, hook-urile git și `verify.py` |
