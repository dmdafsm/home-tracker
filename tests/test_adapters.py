"""Testele adapterelor pe pagini sintetice care reproduc structura așteptată.

Paginile reale se schimbă des. Când un adapter nu mai găsește nimic, salvează o pagină
reală cu `hometracker dump <url>`, pune-o în tests/fixtures/ și adaugă un test pe ea.
"""

import json

from hometracker.adapters.base import merge, with_query_param
from hometracker.adapters.imobiliare import ImobiliareAdapter
from hometracker.adapters.olx import OlxAdapter
from hometracker.adapters.storia import StoriaAdapter
from hometracker.config import Settings

SETTINGS = Settings()


def next_data_page(page_props: dict) -> str:
    payload = json.dumps({"props": {"pageProps": page_props}})
    return f'<html><body><script id="__NEXT_DATA__" type="application/json">{payload}</script></body></html>'


def olx_page(state: dict) -> str:
    encoded = json.dumps(json.dumps(state))
    return f"<html><script>window.__PRERENDERED_STATE__= {encoded};</script></html>"


# ------------------------------------------------------------------ Storia

STORIA_SEARCH = next_data_page({
    "data": {"searchAds": {"items": [
        {
            "id": 7001, "title": "Apartament 2 camere, parter înalt, Gheorgheni",
            "slug": "apartament-2-camere-gheorgheni-IDabc1",
            "totalPrice": {"value": 149000, "currency": "EUR"},
            "areaInSquareMeters": 51, "roomsNumber": "TWO", "floorNumber": None,
            "isPrivateOwner": True, "agency": None, "dateCreated": "2026-10-20 09:15:00",
            "location": {"reverseGeocoding": {"locations": [{"fullName": "Gheorgheni, Cluj-Napoca, Cluj"}]}},
            "images": [{"medium": "https://img.test/a-m.jpg", "large": "https://img.test/a-l.jpg"}],
        },
        {
            "id": 7002, "title": "3 camere Gheorgheni", "slug": "trei-camere-IDabc2",
            "totalPrice": {"value": 735000, "currency": "RON"},
            "areaInSquareMeters": 68, "roomsNumber": "THREE", "floorNumber": "FOURTH",
            "isPrivateOwner": False, "agency": {"name": "Agenția X"},
            "images": [],
        },
    ]}},
})

STORIA_DETAIL = next_data_page({
    "ad": {
        "id": 7001, "title": "Apartament 2 camere, parter înalt, Gheorgheni",
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
        "advertiserType": "private", "owner": {"name": "Ion"},
        "createdAt": "2026-10-20T09:15:00Z",
    },
})


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

OLX_SEARCH = olx_page({"listing": {"listing": {"ads": [
    {
        "id": 301, "url": "https://www.olx.ro/d/oferta/ap-2-cam-gheorgheni-IDx1.html",
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
        "user": {"name": "Maria"}, "isBusiness": False, "createdTime": "2026-10-25T10:00:00+03:00",
    },
    {"id": 302, "url": "https://www.olx.ro/d/oferta/x.html", "title": "Din Storia",
     "externalUrl": "https://www.storia.ro/ro/oferta/x", "params": []},
]}}})


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
        IMOBILIARE_DETAIL, "https://www.imobiliare.ro/oferta/apartament-2-camere-123456789")
    assert detail.price_eur == 139900 and detail.area_m2 == 49 and detail.rooms == 2
    assert detail.floor == "raised_ground" and detail.balcony is False


def test_with_query_param_replaces_page():
    url = with_query_param("https://x.test/a?priceMin=1&page=2", "page", 3)
    assert url == "https://x.test/a?priceMin=1&page=3"
