"""The prompt is read like a person reads it: any case, typos, Hinglish, exclusions, names."""
import pytest

from oncoscraper.understand import ModelReading, understand


def read(prompt, model=None):
    return understand(prompt, model)[0].as_dict()


@pytest.mark.parametrize("prompt,city", [
    ("find the cancer hospitals in mumbai and their urls", "Mumbai"),
    ("FIND CANCER HOSPITALS IN MUMBAI", "Mumbai"),
    ("cancer hospitals in mumabi", "Mumbai"),
    ("Cancer Hospitals In Bombay", "Mumbai"),
    ("mumbai mein cancer hospital chahiye", "Mumbai"),
    ("cancr hospitls in banglore", "Bengaluru"),
    ("find cancer hospitals in dilli", "Delhi"),
    ("oncologists in kolkatta", "Kolkata"),
    ("cancer hospital in hydrabad", "Hyderabad"),
    ("cancer hospitals in gurgoan", "Gurugram"),
])
def test_city_in_any_case_or_spelling(prompt, city):
    assert read(prompt)["locations"] == [city]


def test_several_places_states_and_exclusions():
    assert read("govt cancer hospitals in pune and nagpur")["locations"] == ["Pune", "Nagpur"]
    assert read("cancer hospitals in maharashtra")["states"] == ["Maharashtra"]
    r = read("5 cancer hospitals in mumbai except navi mumbai")
    assert r["locations"] == ["Mumbai"] and r["excluded_locations"] == ["Navi Mumbai"] and r["count"] == 5
    r = read("mumbai mein cancer hospital chahiye, navi mumbai nahi")
    assert r["locations"] == ["Mumbai"] and r["excluded_locations"] == ["Navi Mumbai"] and not r["names"]


def test_every_kind_named_is_kept():
    assert set(read("govt cancer hospitals in pune")["kind"].split()) == {"cancer", "government"}
    assert read("top five multispeciality hospitals in chennai")["kind"] == "multispeciality"
    assert read("top five multispeciality hospitals in chennai")["count"] == 5
    r = read("paediatric oncology hospitals in kolkata")
    assert r["specialties"] == ["Paediatric Oncology"] and "children's" not in r["kind"]


@pytest.mark.parametrize("prompt,names", [
    ("get oncologists of tata memorial hospital and kokilaben hospital", ["Tata Memorial Hospital", "Kokilaben Hospital"]),
    ("Get doctors of Tata Memorial Hospital and Rajiv Gandhi Cancer Institute",
     ["Tata Memorial Hospital", "Rajiv Gandhi Cancer Institute"]),
    ("apollo and fortis oncologists in chennai", ["Apollo", "Fortis"]),
    ("AIIMS delhi oncologists", ["AIIMS"]),
    ("find max 5 cancer hospitals in pune", []),
    ("top 10 cancer hospitals in mumbai", []),
])
def test_named_hospitals_any_case(prompt, names):
    assert read(prompt)["names"] == names


def test_excluded_hospital_names():
    assert read("oncologists in hyderabad except apollo")["excluded_names"] == ["Apollo"]
    r = read("cancer hospitals in mumbai except tata memorial hospital")
    assert r["excluded_names"] == ["Tata Memorial Hospital"] and not r["names"]


class Model:
    def __init__(self, reading=None, error=None):
        self.reading, self.error = reading, error

    def ask(self, schema, instruction, payload):
        if self.error:
            raise self.error
        return self.reading


def test_model_fills_gaps_but_cannot_invent():
    # The model reads 'mumbaai' and 'tmh' style text; values not in the prompt are dropped.
    reading = ModelReading(cities=["Mumbai", "Chennai"], hospital_names=["Kokilaben", "Apollo"])
    r, notes = understand("oncologists at kokilaben, mumbai", Model(reading))
    d = r.as_dict()
    assert d["locations"] == ["Mumbai"] and "Chennai" not in d["locations"]
    assert "Apollo" not in d["names"]


def test_model_down_falls_back_to_rules():
    r, notes = understand("cancer hospitals in mumbai", Model(error=ConnectionError("ollama off")))
    assert r.location == "Mumbai" and any("built-in reader" in n for n in notes)
