# home-tracker

Tracker de piață imobiliară care rulează local, în Docker. Colectează anunțuri de pe
Storia, OLX și Imobiliare.ro și grupează pe apartamente fizice anunțurile care au
aceleași poze. Când apare un apartament care se potrivește unui profil de căutare,
trimite o notificare pe telefon prin [ntfy](https://ntfy.sh) și, opțional, pe email.

Starea actuală e **etapa 1 (MVP)**: colectare, filtrare după profil, deduplicare după
poze, alerte pentru apartamentele noi și pentru scăderile de preț. Planul complet se
află în documentul de plan al proiectului.

## Cum funcționează

```
scheduler ─► adaptere (storia, olx, imobiliare) ─► arhivă HTML brut (data/raw)
                    │
                    ▼
            profil YAML: filtre + scor ─► poze + pHash ─► grupare pe apartament
                                                              │
                                                              ▼
                                               PostgreSQL ─► alertă ntfy / email
```

- **Profilurile** stau în `profiles/*.yaml`. Fiecare are filtre dure (preț, etaj,
  suprafață, cuvinte cheie), preferințe care dau scorul (de exemplu balconul) și
  URL-urile de căutare de pe fiecare platformă.
- **Deduplicarea** leagă două anunțuri de același apartament când au cel puțin 2 poze
  comune (pHash). O singură poză comună e acceptată doar dacă suprafața și etajul se
  potrivesc, pentru că agențiile refolosesc poze cu fațada blocului.
- **Alertele** se trimit o singură dată pe apartament și profil, oricâte platforme îl
  publică. O scădere de preț de peste 3% generează o alertă separată.
- **Cu Mac-ul închis** nu se colectează nimic. La pornire și după o pauză mai lungă de
  două intervale, rularea citește mai multe pagini (`PAGES_CATCHUP`), ca să recupereze
  ce a apărut între timp.
- **Poze și HTML** se salvează în `data/`, local, doar pentru comparare și reprocesare.
  Telefoanele găsite în anunțuri se păstrează doar ca hash.

## Instalare

Ai nevoie de Docker Desktop pe Mac.

```sh
git clone https://github.com/dmdafsm/home-tracker.git
cd home-tracker
cp .env.example .env        # completează POSTGRES_PASSWORD și restul
docker compose up -d --build
```

### Notificări pe telefon (ntfy)

Serverul ntfy are accesul închis implicit (`deny-all`). Creează un utilizator pentru
telefon și un token pentru worker:

```sh
docker compose exec ntfy ntfy user add --role=admin daniel
docker compose exec ntfy ntfy token add daniel     # copiază tokenul în .env, la NTFY_TOKEN
docker compose up -d worker                        # repornește worker-ul cu tokenul nou
docker compose exec worker hometracker test-notify ntfy
```

În aplicația ntfy de pe telefon: adaugă serverul `https://ntfy.romdata.ro`, autentifică-te
cu utilizatorul creat mai sus și abonează-te la topicul din `NTFY_TOPIC` (implicit
`apartamente`).

### Tunelul Cloudflare

Tunelul există deja pe Mac. Proiectul nu îl configurează: expune doar serviciul HTTP
al ntfy pe `127.0.0.1:8090` (portul din `NTFY_LOCAL_PORT`). Adaugă în configurarea
tunelului o regulă de ingress, înaintea regulii finale `http_status:404`:

```yaml
  - hostname: ntfy.romdata.ro
    service: http://localhost:8090
```

Dacă `cloudflared` rulează la rândul lui într-un container Docker, folosește
`http://host.docker.internal:8090` în loc de `localhost`.

Nu pune `ntfy.romdata.ro` în spatele Cloudflare Access: aplicația ntfy nu poate trece
prin login-ul Access și se bazează pe autentificarea proprie a serverului. Interfața
web (etapa 2) va primi un hostname separat, protejat cu Access.

## Ajustarea căutărilor

Cel mai sigur mod de a configura o căutare: setezi filtrele direct pe site, în browser,
sortezi după cele mai noi și copiezi URL-ul în profil, la `sources.<platformă>.url`.
URL-urile din profilul inclus sunt un punct de plecare și trebuie verificate.

## Comenzi utile

```sh
docker compose logs -f worker                       # ce face acum
docker compose exec worker hometracker run          # o rulare imediată
docker compose exec worker hometracker run --catchup
docker compose exec worker hometracker matches      # ultimele alerte
docker compose exec worker hometracker dump "<url>" # verifică ce extrage un adapter
```

## Când un adapter nu mai merge

Platformele își schimbă des structura paginilor. Când se întâmplă asta, adapterul nu
mai găsește anunțuri, iar tu primești pe ntfy un avertisment (cel mult unul la 12 ore
pe platformă). Pașii de urmat:

1. Rulează `hometracker dump "<url-ul căutării>"`. Pagina se salvează în `data/debug/`,
   iar comanda arată ce a reușit adapterul să extragă.
2. Pune pagina salvată în `tests/fixtures/` și scrie un test care o folosește.
3. Ajustează adapterul din `src/hometracker/adapters/` până trece testul.

Adapterele nu ocolesc protecțiile site-urilor. Dacă un site cere o verificare (CAPTCHA),
rularea se oprește pentru platforma respectivă și reîncearcă la intervalul următor.

## Dezvoltare

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest
```

Fără `DATABASE_URL`, aplicația folosește SQLite în `data/hometracker.db`, util pentru
rulări locale în afara Docker.

## Structura

| Cale | Ce conține |
| --- | --- |
| `src/hometracker/adapters/` | câte un adapter pe platformă, cu interfața din `base.py` |
| `src/hometracker/parsing.py` | interpretarea textelor în română: etaj, preț, suprafață, balcon |
| `src/hometracker/profiles.py` | profilurile YAML și calculul scorului |
| `src/hometracker/dedup.py` | gruparea anunțurilor pe apartamente, după poze |
| `src/hometracker/pipeline.py` | o rulare completă: căutare, detaliu, salvare, alerte |
| `src/hometracker/notify.py` | ntfy și email |
| `src/hometracker/cli.py` | comenzile `hometracker` |
| `profiles/` | profilurile de căutare |
| `tests/` | teste pe pagini sintetice și pe fluxul complet (SQLite) |
