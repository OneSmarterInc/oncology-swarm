import io

import pytest
from openpyxl import Workbook

from attorneyscraper.export import parse_sources
from attorneyscraper.models import RunConfig, Source


def test_domain_labels_and_explicit_names():
    config = RunConfig(
        sources=[{"url": "https://www.firm.com/one"}, {"url": "https://firm.com/two"}]
    )
    assert [source.organization for source in config.sources] == [
        "firm.com",
        "firm.com",
    ]
    assert (
        Source(url="https://firm.com", organization=" Firm Name ").organization
        == "Firm Name"
    )


@pytest.mark.parametrize(
    "url", ["invalid", "javascript:alert(1)", "https://user:pass@firm.com"]
)
def test_url_only_invalid_sources(url):
    with pytest.raises(ValueError):
        Source(url=url)


@pytest.mark.parametrize("extension", ["csv", "xlsx"])
def test_url_only_upload(extension):
    data = b"URL\nhttps://www.firm.com/people/\n"
    if extension == "xlsx":
        book = Workbook()
        book.active.append(["URL"])
        book.active.append(["https://www.firm.com/people/"])
        stream = io.BytesIO()
        book.save(stream)
        data = stream.getvalue()
    sources = parse_sources("sources." + extension, data)
    assert sources[0].organization == "firm.com"
