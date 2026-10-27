"""Testele adapterelor pe pagini sintetice care reproduc structura așteptată.

Paginile reale se schimbă des. Când un adapter nu mai găsește nimic, salvează o pagină
reală cu `hometracker dump <url>` (rămâne local, în data/debug/), apoi pune în tests/fixtures/
o copie sintetică: aceeași structură, cu valori inventate (ADR 0003).
"""

import json
from pathlib import Path

import pytest

from hometracker.adapters import ListingRemoved
from hometracker.adapters.base import merge, with_query_param
from hometracker.adapters.imobiliare import ImobiliareAdapter
from hometracker.adapters.lajumate import LajumateAdapter
from hometracker.adapters.olx import OlxAdapter
from hometracker.adapters.publi24 import Publi24Adapter
from hometracker.adapters.storia import StoriaAdapter
from hometracker.config import Settings
from hometracker.schema import ListingData

SETTINGS = Settings()


def next_data_page(page_props: dict) -> str:
    payload = json.dumps({"props": {"pageProps": page_props}})
    return f'<html><body><script id="__NEXT_DATA__" type="application/json">{payload}</script></body></html>'


def olx_page(state: dict) -> str:
    encoded = json.dumps(json.dumps(state))
    return f"<html><script>window.__PRERENDERED_STATE__= {encoded};</script></html>"


# ------------------------------------------------------------------ Storia

STORIA_SEARCH = next_data_page(
    {
        "data": {
            "searchAds": {
                "items": [
                    {
                        "id": 7001,
                        "title": "Apartament 2 camere, parter înalt, Gheorgheni",
                        "slug": "apartament-2-camere-gheorgheni-IDabc1",
                        "totalPrice": {"value": 149000, "currency": "EUR"},
                        "areaInSquareMeters": 51,
                        "roomsNumber": "TWO",
                        "floorNumber": None,
                        "isPrivateOwner": True,
                        "agency": None,
                        "dateCreated": "2026-10-20 09:15:00",
                        "location": {
                            "reverseGeocoding": {"locations": [{"fullName": "Gheorgheni, Cluj-Napoca, Cluj"}]}
                        },
                        "images": [{"medium": "https://img.test/a-m.jpg", "large": "https://img.test/a-l.jpg"}],
                    },
                    {
                        "id": 7002,
                        "title": "3 camere Gheorgheni",
                        "slug": "trei-camere-IDabc2",
                        "totalPrice": {"value": 735000, "currency": "RON"},
                        "areaInSquareMeters": 68,
                        "roomsNumber": "THREE",
                        "floorNumber": "FOURTH",
                        "isPrivateOwner": False,
                        "agency": {"name": "Agenția X"},
                        "images": [],
                    },
                ]
            }
        },
    }
)

STORIA_DETAIL = next_data_page(
    {
        "ad": {
            "id": 7001,
            "title": "Apartament 2 camere, parter înalt, Gheorgheni",
            "description": "<p>Apartament renovat, <b>balcon închis</b>, bloc de cărămidă.</p>",
            "characteristics": [
                {"key": "price", "value": "149000", "currency": "EUR"},
                {"key": "m", "value": "51"},
                {"key": "rooms_num", "value": "2"},
                {"key": "floor_no", "value": "ground_floor", "localizedValue": "parter"},
                {"key": "build_year", "value": "1978"},
            ],
            "images": [{"large": "https://img.test/a-l.jpg"}, {"large": "https://img.test/b-l.jpg"}],
            "location": {"coordinates": {"latitude": 46.77, "longitude": 23.63}},
            "advertiserType": "private",
            "owner": {"name": "Ion"},
            "createdAt": "2026-10-20T09:15:00Z",
        },
    }
)


def test_storia_search():
    items = list(StoriaAdapter(None, SETTINGS).parse_search(STORIA_SEARCH, "https://www.storia.ro/x"))
    assert [i.external_id for i in items] == ["7001", "7002"]
    first, second = items
    assert first.url == "https://www.storia.ro/ro/oferta/apartament-2-camere-gheorgheni-IDabc1"
    assert first.floor == "raised_ground"  # din titlu, câmpul lipsește
    assert first.price_eur == 149000 and first.rooms == 2 and first.publisher_type == "owner"
    assert "Gheorgheni" in first.location_text
    assert first.photo_urls == ["https://img.test/a-l.jpg"]
    assert second.price_eur == round(735000 / 4.97, 2)
    assert second.floor == "4" and second.publisher_type == "agency" and second.publisher_name == "Agenția X"


def test_storia_detail_and_merge():
    adapter = StoriaAdapter(None, SETTINGS)
    search_item = next(adapter.parse_search(STORIA_SEARCH, "https://www.storia.ro/x"))
    detail = adapter.parse_detail(STORIA_DETAIL, search_item.url)
    assert detail.balcony is True
    assert "balcon închis" in detail.description
    assert detail.floor == "ground"  # câmpul dedicat are prioritate față de titlu
    assert detail.extra["build_year"] == "1978"
    merged = merge(search_item, detail)
    assert merged.is_detail and merged.latitude == 46.77
    assert merged.external_id == "7001"
    assert len(merged.photo_urls) == 2


# --------------------------------------------------------------------- OLX

OLX_SEARCH = olx_page(
    {
        "listing": {
            "listing": {
                "ads": [
                    {
                        "id": 301,
                        "url": "https://www.olx.ro/d/oferta/ap-2-cam-gheorgheni-IDx1.html",
                        "title": "Proprietar vând ap. 2 cam Gheorgheni",
                        "description": "Etaj 1/4, balcon, aproape de Iulius. Tel 0722 123 456<br/>",
                        "params": [
                            {"key": "floor", "name": "Etaj", "value": "1", "normalizedValue": "1"},
                            {"key": "m", "name": "Suprafata utila", "value": "54 m²", "normalizedValue": "54"},
                            {"key": "rooms", "name": "Compartimentare", "value": "2 camere"},
                        ],
                        "price": {"regularPrice": {"value": 152000, "currencyCode": "EUR"}},
                        "photos": ["https://ireland.apollo.olxcdn.com/v1/files/abc/image;s={width}x{height}"],
                        "location": {"cityName": "Cluj-Napoca", "districtName": "Gheorgheni"},
                        "map": {"lat": 46.76, "lon": 23.62},
                        "user": {"name": "Maria"},
                        "isBusiness": False,
                        "createdTime": "2026-10-25T10:00:00+03:00",
                    },
                    {
                        "id": 302,
                        "url": "https://www.olx.ro/d/oferta/x.html",
                        "title": "Din Storia",
                        "externalUrl": "https://www.storia.ro/ro/oferta/x",
                        "params": [],
                    },
                ]
            }
        }
    }
)


def test_olx_search_skips_storia_mirrors():
    items = list(OlxAdapter(None, SETTINGS).parse_search(OLX_SEARCH, "https://www.olx.ro/x"))
    assert len(items) == 1
    item = items[0]
    assert item.floor == "1" and item.area_m2 == 54 and item.rooms == 2
    assert item.balcony is True and item.price_eur == 152000
    assert item.location_text.startswith("Gheorgheni")
    assert item.phone == "0722 123 456"
    assert item.photo_urls[0].endswith("image;s=1280x960")


def test_olx_detail_reads_ad_state():
    ad = json.loads(json.loads(OLX_SEARCH.split("= ", 1)[1].rsplit(";", 1)[0]))["listing"]["listing"]["ads"][0]
    html = olx_page({"ad": {"ad": ad}})
    detail = OlxAdapter(None, SETTINGS).parse_detail(html, ad["url"])
    assert detail.is_detail and detail.external_id == "301" and detail.floor == "1"


# ---------------------------------------------------------- Imobiliare.ro

IMOBILIARE_SEARCH = """
<html><body>
<a href="/vanzare-apartamente/cluj-napoca/gheorgheni">Gheorgheni</a>
<a href="/vanzare-apartamente/cluj-napoca/gheorgheni/apartament-de-vanzare-2-camere-X7GV1001A">2 camere, parter</a>
<a href="https://www.imobiliare.ro/oferta/apartament-3-camere-gheorgheni-123456789">3 camere</a>
<a href="https://www.imobiliare.ro/oferta/apartament-3-camere-gheorgheni-123456789?utm=x">duplicat</a>
<a href="https://altsite.ro/oferta/1234567">străin</a>
</body></html>
"""

IMOBILIARE_DETAIL = """
<html><head><script type="application/ld+json">
{"@type": "Product", "name": "Apartament 2 camere Gheorgheni", "description": "Fără balcon, renovat.",
 "offers": {"price": "139900", "priceCurrency": "EUR"}, "image": ["https://img.test/i1.jpg"]}
</script></head><body>
<h1>Apartament 2 camere Gheorgheni</h1>
<ul><li>Suprafață utilă: 49 mp</li><li>Etaj: parter înalt</li></ul>
</body></html>
"""


def test_imobiliare_search_uses_offer_links_only():
    items = list(ImobiliareAdapter(None, SETTINGS).parse_search(IMOBILIARE_SEARCH, "https://www.imobiliare.ro/x"))
    assert sorted(i.external_id for i in items) == ["123456789", "X7GV1001A"]
    assert all("?" not in i.url for i in items)


def test_imobiliare_detail():
    detail = ImobiliareAdapter(None, SETTINGS).parse_detail(
        IMOBILIARE_DETAIL, "https://www.imobiliare.ro/oferta/apartament-2-camere-123456789"
    )
    assert detail.price_eur == 139900 and detail.area_m2 == 49 and detail.rooms == 2
    assert detail.floor == "raised_ground" and detail.balcony is False


def test_with_query_param_replaces_page():
    url = with_query_param("https://x.test/a?priceMin=1&page=2", "page", 3)
    assert url == "https://x.test/a?priceMin=1&page=3"


# ------------------------------------------------------- anunțuri expirate

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_storia_removed_and_not_found_pages_are_detected():
    adapter = StoriaAdapter(None, SETTINGS)
    url = "https://www.storia.ro/ro/oferta/apartament-2-camere-50-mp-strada-exemplu-IDtest1"
    assert adapter.is_removed(fixture("storia_detail_removed_synthetic.html"), url)
    assert adapter.is_removed(fixture("storia_not_found_synthetic.html"), url)


def test_storia_active_pages_are_not_removed():
    adapter = StoriaAdapter(None, SETTINGS)
    assert not adapter.is_removed(fixture("storia_detail_active_synthetic.html"), "https://www.storia.ro/x")
    assert not adapter.is_removed(STORIA_DETAIL, "https://www.storia.ro/x")  # fără câmpul status
    assert not adapter.is_removed("<html><body>altceva</body></html>", "https://www.storia.ro/x")


def test_fetch_detail_raises_for_a_removed_page_served_with_200():
    class Response:
        def __init__(self, text, url):
            self.text, self.url = text, url

    class Client:
        def get(self, url):
            return Response(fixture("storia_detail_removed_synthetic.html"), url)

    adapter = StoriaAdapter(Client(), SETTINGS)
    item = ListingData(platform="storia", external_id="1000001", url="https://www.storia.ro/ro/oferta/x-IDtest1")
    with pytest.raises(ListingRemoved):
        adapter.fetch_detail(item)


def test_unverified_adapters_never_report_removed():
    # OLX și Imobiliare.ro nu au încă o pagină reală de anunț expirat verificată; până atunci rămân pe 404/410.
    for adapter in (OlxAdapter(None, SETTINGS), ImobiliareAdapter(None, SETTINGS)):
        assert not adapter.is_removed(fixture("storia_detail_removed_synthetic.html"), "https://x.test/")


# ------------------------------------------------------------- Publi24


def test_publi24_search_reads_json_ld_with_raw_newlines():
    adapter = Publi24Adapter(None, SETTINGS)
    items = list(adapter.parse_search(fixture("publi24_search_synthetic.html"), "https://www.publi24.ro/x"))
    assert [item.external_id for item in items] == [
        "0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d",
        "1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e",
    ]
    first, second = items
    assert first.url.startswith("https://www.publi24.ro/anunturi/") and first.title == "Apartament 2 camere, Gheorgheni"
    assert (first.price_eur, first.rooms, first.floor, first.balcony) == (148000, 2, "1", True)
    assert first.area_m2 == 51  # „0” în JSON-LD: luat din descriere
    assert "1 din 4'" in first.description  # entitatea HTML a fost decodată
    assert first.location_text == "Cluj-Napoca, Cluj" and first.phone == "0722 000 333"
    assert (second.rooms, second.floor, second.area_m2, second.currency) == (1, "ground", 28, "RON")
    assert abs(second.price_eur - 540000 / SETTINGS.eur_ron) < 1


def test_publi24_detail_reads_attributes_and_publisher():
    adapter = Publi24Adapter(None, SETTINGS)
    url = "https://www.publi24.ro/anunturi/imobiliare/de-vanzare/apartamente/apartamente-2-camere/anunt/apartament-2-camere-gheorgheni/0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d.html"
    detail = adapter.parse_detail(fixture("publi24_detail_synthetic.html"), url)
    assert detail.is_detail and detail.external_id == "0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d"
    assert (detail.area_m2, detail.rooms, detail.floor) == (51, 2, "1")
    assert detail.publisher_name == "Ion Exemplu" and detail.extra["build_year"] == "1978"
    assert len(detail.photo_urls) == 3


def test_publi24_page_without_json_ld_yields_nothing():
    assert list(Publi24Adapter(None, SETTINGS).parse_search("<html></html>", "x")) == []


# ------------------------------------------------------------ Lajumate


def test_lajumate_search_reads_next_data():
    adapter = LajumateAdapter(None, SETTINGS)
    first, second = adapter.parse_search(fixture("lajumate_search_synthetic.html"), "https://lajumate.ro/x")
    assert first.url == "https://lajumate.ro/ad/apartament-2-camere-gheorgheni-test-1-9000001"
    assert (first.price_eur, first.area_m2, first.rooms, first.floor) == (149000, 52, 2, "ground")
    assert first.phone == "0722000444" and first.publisher_name == "Ana Exemplu"
    assert first.extra["build_year"] == "1980" and first.extra["copy_of"] == "homezz"
    assert first.photo_urls == []  # serviciul de imagini nu le servește în afara site-ului
    assert first.extra["photo_urls"] == [
        "https://api-preprod.lajumate.ro/opt-image/media/i/big/0/0/9000001_test_1.webp"
    ]
    assert second.area_m2 is None  # „2” mp e o greșeală a publicatorului
    assert second.floor == "13" and second.phone is None  # telefon ascuns


def test_lajumate_detail_reads_publisher_type_and_html_description():
    adapter = LajumateAdapter(None, SETTINGS)
    detail = adapter.parse_detail(fixture("lajumate_detail_synthetic.html"), "https://lajumate.ro/x")
    assert detail.is_detail and detail.publisher_type == "agency" and detail.publisher_name == "Agenția Test"
    assert detail.description == "Apartament la parter, cu balcon." and detail.balcony is True
    assert detail.extra["photo_urls"] == [
        "https://api-preprod.lajumate.ro/opt-image/media/i/big/0/0/9000001_test_7.webp"
    ]
    assert detail.posted_at is not None


def test_lajumate_page_without_next_data_yields_nothing():
    adapter = LajumateAdapter(None, SETTINGS)
    assert list(adapter.parse_search("<html></html>", "x")) == []
    assert adapter.parse_detail("<html></html>", "x").external_id == ""
