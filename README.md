# home-tracker

Tracker de piață imobiliară care rulează local, în Docker. Colectează anunțuri de pe
Storia, Imobiliare.ro, Publi24 și Lajumate (OLX e dezactivat, vezi mai jos) și grupează pe apartamente fizice anunțurile care au
aceleași poze. Când apare un apartament care se potrivește unui profil de căutare,
trimite o notificare pe telefon prin [ntfy](https://ntfy.sh) și, opțional, pe email.

Starea actuală e **etapa 1 (MVP)**: colectare, filtrare după profil, deduplicare după
poze, alerte pentru apartamentele noi și pentru scăderile de preț. Planul complet e în
[docs/plan.md](docs/plan.md), iar arhitectura actuală în [docs/architecture.md](docs/architecture.md).

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
- **Deduplicarea** leagă două anunțuri de același apartament după un scor combinat: poze
  comune (pHash și dHash), text asemănător (fără textul-șablon al agențiilor), același telefon
  de proprietar, suprafață, etaj, camere și distanță. Două poze comune ajung; o singură poză
  (de obicei fațada blocului) cere aceeași suprafață și același etaj. O contradicție clară
  (alt etaj, altă suprafață, la peste 1 km) ține anunțurile separate. `hometracker property`
  arată pentru fiecare anunț scorul și semnalele care l-au legat.
- **Alertele** se trimit o singură dată pe apartament și profil, oricâte platforme îl
  publică. O scădere de preț de peste 3% generează o alertă separată.
- **Reverificarea zilnică**: anunțurile potrivite care nu mai apar în primele pagini de
  căutare sunt redeschise o dată la 24 de ore (cel mult `RECHECK_PER_RUN` pe rulare). O
  scădere de preț găsită așa trimite alerta obișnuită; un anunț șters (404/410) e marcat ca
  dispărut.
- **Republicările**: un anunț nou pentru același apartament, pe aceeași platformă, de la
  același publicator (telefon sau nume), e marcat ca republicare și nu trimite o alertă nouă.
- **Rezumatul zilnic** pleacă pe email la prima rulare de după ora `SUMMARY_HOUR` (implicit
  20:00): anunțurile noi care trec de filtre dar n-au atins pragul de alertă, cele dispărute
  (posibil vândute), republicările și intervalele în care nu s-a colectat nimic.
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
cp .env.example .env        # completează POSTGRES_PASSWORD și restul
docker compose up -d --build
```

### Notificări pe telefon (ntfy)

Serverul ntfy are accesul închis implicit (`deny-all`). Creează un utilizator pentru
telefon și un token pentru worker:

```sh
docker compose exec ntfy ntfy user add --role=admin <utilizator>
docker compose exec ntfy ntfy token add <utilizator> # copiază tokenul în .env, la NTFY_TOKEN
docker compose up -d worker                        # repornește worker-ul cu tokenul nou
docker compose exec worker hometracker test-notify ntfy
```

În aplicația ntfy de pe telefon: adaugă serverul din `NTFY_PUBLIC_URL`, autentifică-te
cu utilizatorul creat mai sus și abonează-te la topicul din `NTFY_TOPIC` (implicit
`apartamente`).

### Acces din exterior (Cloudflare Tunnel)

Proiectul nu configurează accesul din exterior: expune doar serviciul HTTP al ntfy pe
`127.0.0.1:8090` (portul din `NTFY_LOCAL_PORT`). Ca telefonul să ajungă la el, poți folosi
un tunel Cloudflare. Adaugă în configurarea tunelului o regulă de ingress, înaintea regulii
finale `http_status:404`, cu hostname-ul tău:

```yaml
  - hostname: ntfy.example.com
    service: http://localhost:8090
```

Dacă `cloudflared` rulează la rândul lui într-un container Docker, folosește
`http://host.docker.internal:8090` în loc de `localhost`.

Nu pune hostname-ul ntfy în spatele Cloudflare Access: aplicația ntfy nu poate trece
prin login-ul Access și se bazează pe autentificarea proprie a serverului.

### Interfața web

Containerul `web` servește feed-ul apartamentelor potrivite și pagina fiecărui apartament
pe `127.0.0.1:8080` (portul din `WEB_LOCAL_PORT`): http://localhost:8080. Interfața nu are
login propriu, deci din exterior se deschide doar printr-un hostname separat al tunelului,
**cu Cloudflare Access în față**, restricționat la contul tău:

```yaml
  - hostname: tracker.example.com
    service: http://localhost:8080
```

Pe pagina unui apartament poți să-l **salvezi**, să-l **ascunzi**, să-l marchezi **confirmat real**
sau **fals** după ce ai sunat, iar la fiecare anunț poți spune **nu e același apartament** (anunțul
trece într-un apartament separat). Scăderile de preț trimit alertă doar pentru apartamentele
salvate (`notify.price_drop_scope` în profil); cele ascunse sau false nu mai trimit alerte și nu
mai apar în rezumat. Feed-ul are filtre pentru salvate, ascunse și dispărute.

Fiecare apartament are un **scor de autenticitate** (0–100) cu semnalele care l-au scăzut:
aceleași poze la mai mulți publicatori, o agenție care a publicat după proprietar, un preț mult
sub mediana apartamentelor asemănătoare, un „proprietar” cu telefonul la multe apartamente, poze
dintr-un anunț dispărut, republicări luni de zile la același preț, detalii lipsă. Scorul nu
dovedește nimic; arată ce merită verificat la telefon. În profil, `notify.min_authenticity`
poate opri alertele instant sub un prag.

Pagina **Publicatori** arată, pentru fiecare agenție sau proprietar, câte anunțuri active are, câte
apartamente, ce procent a preluat de la alții (apartamentul apăruse întâi la alt publicator) și
cu cât e în medie mai scump sau mai ieftin decât ceilalți pentru aceleași apartamente. Un
„proprietar” al cărui telefon apare la 5 sau mai multe apartamente e marcat ca posibil agent.

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
docker compose exec worker hometracker matches      # ultimele alerte
docker compose exec worker hometracker summary --print   # rezumatul ultimelor 24 de ore, fără trimitere
docker compose exec worker hometracker property "<url>"  # istoricul apartamentului: anunțuri, prețuri, republicări
docker compose exec worker hometracker dump "<url>" # verifică ce extrage un adapter
```

## Când un adapter nu mai merge

Platformele își schimbă des structura paginilor. Când se întâmplă asta, adapterul nu
mai găsește anunțuri, iar tu primești pe ntfy un avertisment (cel mult unul la 12 ore
pe platformă). Pașii de urmat:

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
| `src/hometracker/dedup.py` | gruparea anunțurilor pe apartamente, după poze, și republicările |
| `src/hometracker/history.py` | istoricul unui apartament |
| `src/hometracker/publishers.py` | publicatorii: anunțuri preluate, diferențe de preț, posibili agenți |
| `src/hometracker/web.py` | interfața web: apartamente, publicatori |
| `src/hometracker/summary.py` | rezumatul zilnic |
| `src/hometracker/pipeline.py` | o rulare completă: căutare, detaliu, salvare, alerte |
| `src/hometracker/notify.py` | ntfy și email |
| `src/hometracker/cli.py` | comenzile `hometracker` |
| `profiles/` | profilurile de căutare |
| `tests/` | teste pe pagini sintetice și pe fluxul complet (SQLite) |
| `docs/` | planul, arhitectura, regulile de engineering și deciziile (ADR) |
| `scripts/` | verificarea repository-ului, hook-urile git și `verify.py` |
