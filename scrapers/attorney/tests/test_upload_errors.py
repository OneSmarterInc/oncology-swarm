import base64
import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from attorneyscraper.export import parse_sources
from attorneyscraper.web import create_app


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"filename": 7, "data": ""},
        {
            "filename": "broken.xlsx",
            "data": base64.b64encode(b"not a workbook").decode(),
        },
        {"filename": "broken.xlsx", "data": "not base64"},
    ],
)
def test_invalid_upload_returns_client_error(tmp_path, payload):
    with TestClient(create_app(tmp_path / "db")) as c:
        token = c.get("/api/session").json()["token"]
        result = c.post(
            "/api/sources/validate", json=payload, headers={"x-session-token": token}
        )
        assert result.status_code == 400
        assert result.json()["detail"]


@pytest.mark.parametrize("disabled", [False, 0, "false", "no"])
def test_excel_disabled_rows_are_not_imported(disabled):
    wb = Workbook()
    wb.active.append(["Organization", "URL", "Enabled"])
    wb.active.append(["Excluded firm", "https://excluded.example/", disabled])
    wb.active.append(["Included firm", "https://included.example/", "yes"])
    output = io.BytesIO()
    wb.save(output)
    result = parse_sources("sources.xlsx", output.getvalue())
    assert len(result) == 1
    assert result[0].organization == "Included firm"
