# home-tracker

Unealtă personală care rulează local, în Docker. Colectează anunțuri de apartamente de pe
Storia, Publi24 și Lajumate, grupează anunțurile care descriu același apartament fizic, le dă
un scor de autenticitate și le arată într-o interfață web, filtrate și ordonate după
profilurile de căutare.

Planul e în [docs/plan.md](docs/plan.md), iar arhitectura în
[docs/architecture.md](docs/architecture.md).

## Cum funcționează

```
worker (periodic) ─► adaptere (storia, publi24, lajumate) ─► arhivă HTML brut (data/raw)
                          │
                          ▼
                  profil YAML: filtre + scor ─► poze + pHash ─► grupare pe apartament
                                                                    │
                                                                    ▼
                                                PostgreSQL ─► interfața web (feed, apartament)
```

- **Profilurile** (`profiles/*.yaml`) au filtre dure (preț, etaj, suprafață, cuvinte cheie),
  preferințe care dau scorul (de exemplu balconul) și URL-ul căutării de pe fiecare platformă.
- **Deduplicarea** leagă anunțurile aceluiași apartament după poze comune, text asemănător,
  telefon, suprafață, etaj, camere și distanță. Apartamentele foarte asemănătoare care nu pot fi
  legate sigur apar ca **posibil același apartament**, unde le poți uni sau separa.
- **Anunțurile potrivite** sunt reverificate zilnic: prețurile noi intră în istoric, iar cele
  șterse sunt marcate ca dispărute. Republicările sunt recunoscute.
- **Anul construcției** e estimat cu sursa lui (OpenStreetMap, anunțuri, tipologia blocului);
  vezi [docs/surse-an-constructie.md](docs/surse-an-constructie.md).
- **Datele** (poze, HTML, baza de date) rămân local, în `data/`. Telefoanele se păstrează doar
  ca hash.

OLX și Imobiliare.ro sunt dezactivate: răspund colectorului cu HTTP 403. Colectorul nu ocolește
protecțiile site-urilor; la un blocaj sau CAPTCHA se oprește pentru platforma respectivă și
reîncearcă la intervalul următor.

## Instalare

Ai nevoie de Docker (de exemplu Docker Desktop).

```sh
git clone https://github.com/dmdafsm/home-tracker.git
cd home-tracker
cp .env.example .env        # completează POSTGRES_PASSWORD
cp profiles/example.yaml profiles/cautarea-mea.yaml   # schimbă name, criteriile și pune active: true
docker compose up -d --build
```

Interfața e pe http://localhost:8080, publicată doar pe `127.0.0.1`, și nu are login propriu.

Profilurile tale rămân locale: git le ignoră, în repo e doar `example.yaml`, inactiv. Pentru
URL-urile căutărilor, setează filtrele direct pe site, sortează după cele mai noi și copiază
URL-ul în profil, la `sources.<platformă>.url`.

## Interfața web

- **Apartamente**: feed-ul cu apartamentele care trec de profil, cu carusel de poze, preț, preț
  pe mp, etaj, anul blocului și scorul de autenticitate. Filtre, sortări și acțiuni direct de pe
  card (salvează, ascunde, deschide anunțul).
- **Pagina unui apartament**: anunțurile lui de pe toate platformele, publicatorii, istoricul
  prețului, pozele și descrierea; marcaje (salvat, ascuns, real, fals) și unirea sau separarea
  apartamentelor.
- **Scorul de autenticitate** (0–100) arată semnalele care l-au scăzut: aceleași poze la mai
  mulți publicatori, preț mult sub piață, „proprietar” cu multe apartamente, republicări
  repetate. Nu dovedește nimic; arată ce merită verificat la telefon.
- **Publicatori**: anunțurile fiecărei agenții sau fiecărui proprietar și cât de des preia
  anunțurile altora.
- **Profiluri**: editarea profilurilor din interfață, cu verificare înainte de salvare.
- **Stare**: ultimele rulări pe fiecare platformă și problemele de colectare.

## Comenzi utile

```sh
docker compose logs -f worker                            # ce face acum
docker compose exec worker hometracker run               # o rulare imediată
docker compose exec worker hometracker property "<url>"  # istoricul apartamentului
docker compose exec worker hometracker dump "<url>"      # ce extrage un adapter dintr-o pagină
```

## Dezvoltare

Ai nevoie de [uv](https://docs.astral.sh/uv/).

```sh
uv sync                              # Python 3.12, dependențele și uneltele de dezvoltare
python3 scripts/install_hooks.py     # o dată pe clonă: blochează commit-urile direct pe main
python3 scripts/verify.py            # verificarea completă, ca în CI
```

Fără `DATABASE_URL`, aplicația folosește SQLite în `data/hometracker.db`. Pentru lucrul la
interfață fără date reale: `DATA_DIR=data/demo uv run hometracker demo`, apoi
`DATA_DIR=data/demo uv run hometracker web --port 8081`.

Când un adapter nu mai găsește anunțuri, pagina **Stare** arată problema. Rulează
`hometracker dump "<url>"` (pagina reală rămâne local, în `data/debug/`), scrie în
`tests/fixtures/` o copie sintetică, cu date inventate, și ajustează adapterul până trece
testul.

Regulile de lucru sunt în [AGENTS.md](AGENTS.md) și [CONTRIBUTING.md](CONTRIBUTING.md).
