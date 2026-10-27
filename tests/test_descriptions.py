"""Descrierile: HTML-ul platformelor ca text cu paragrafe, re-parsarea din arhivă și afișarea în pagină."""

import gzip

from helpers import listing
from test_adapters import next_data_page

from hometracker.adapters.base import html_to_text
from hometracker.config import Settings
from hometracker.models import Listing
from hometracker.reparse import archived_pages, reparse_descriptions
from hometracker.web import paragraphs


def test_html_keeps_paragraphs_and_lists():
    html = "<p>Apartament <b>renovat</b>.</p><p>Dotări:</p><ul><li>centrală</li><li>balcon</li></ul><p>Sunați.</p>"
    assert html_to_text(html) == "Apartament renovat.\n\nDotări:\n• centrală\n• balcon\n\nSunați."
    assert html_to_text("Primul rând<br/>al doilea") == "Primul rând\nal doilea"
    assert html_to_text(None) == ""


def test_paragraphs_formats_text_and_escapes_it():
    text = "Prima frază.\nA doua linie.\n\nDotări:\n- centrală\n• balcon <b>\n2) parcare\n\nFinal."
    assert str(paragraphs(text)) == (
        "<p>Prima frază.<br>A doua linie.</p>"
        "<p>Dotări:</p><ul><li>centrală</li><li>balcon &lt;b&gt;</li><li>parcare</li></ul>"
        "<p>Final.</p>"
    )
    assert str(paragraphs("")) == "" and str(paragraphs(None)) == ""


def archive(raw_dir, url, page, name="a"):
    path = raw_dir / "www.storia.ro" / "2026-10-27" / f"{name}.html.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(f"<!-- {url} -->\n{page}")
    return path


def test_reparse_updates_only_the_description_from_the_archive(setup, tmp_path):
    catalog, sessions, run = setup
    flat = "Apartament renovat. Dotări: centrală balcon"
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], description=flat)]
    run()
    settings = Settings(data_dir=tmp_path)
    page = next_data_page(
        {"ad": {"id": 1, "title": "Alt titlu", "description": "<p>Apartament renovat.</p><ul><li>centrală</li></ul>"}}
    )
    archive(settings.raw_dir, "https://storia.test/s1", page)
    archive(settings.raw_dir, "https://storia.test/search?page=1", "<html></html>", name="b")  # nu e un anunț
    (settings.raw_dir / "www.storia.ro" / "2026-10-27" / "stricat.html.gz").write_bytes(b"nu e gzip")
    assert set(archived_pages(settings.raw_dir)) == {"https://storia.test/s1", "https://storia.test/search?page=1"}
    with sessions() as session:
        assert reparse_descriptions(session, settings) == 1
        session.commit()
        row = session.query(Listing).one()
        assert row.description == "Apartament renovat.\n• centrală"
        assert row.title == "Apartament 2 camere Gheorgheni"  # celelalte câmpuri rămân
        assert reparse_descriptions(session, settings) == 0  # a doua oară nu mai e nimic de schimbat
