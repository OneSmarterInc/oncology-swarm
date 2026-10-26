"""Exact, deliberately narrow grammar for count/type/city doctor-page requests.

Unmatched wording goes through the semantic planner. Never partially parse a
request: doing so could silently discard exclusions or person filters.
"""
import re

from .prompt_discovery import Criterion, Plan

_PATTERN = re.compile(
    r"\s*(?:find|list|show|I\s+want|I\s+need)\s+"
    r"(?P<count>[1-9][0-9]{0,3}?)\s+"
    r"(?P<kind>(?:(?:cancer|oncology|multi-?speciality|multi-?specialty|super-?speciality|private|government)\s+)?"
    r"(?:hospitals?|cancer\s+cent(?:re|er)s?|cancer\s+institutes?))\s+"
    r"(?:in|from|located\s+in)\s+"
    r"(?P<city>[^\W\d_]+(?:[ '\-][^\W\d_]+){0,3}?)\s+"
    r"(?:(?:and|with)\s+(?:(?:return|give|provide|show|list)\s+)?)?"
    r"(?:(?:their|its|the)\s+)?(?:official\s+)?"
    r"(?P<who>oncologists?|cancer\s+specialists?|doctors?)\b\s*"
    r"(?:(?:directory|list|page|pages|profiles?)\s+)?(?:URLs?|pages?|links?)?\s*[.!]?\s*", re.I)
# Ambiguous modifiers in the location span need semantic planning.
_AMBIGUOUS = set("and or not no excluding except exclude only near around with without all any top best "
                 "private public government emails email patients doctors oncologists".split())


def simple_doctor_plan(prompt):
    match = _PATTERN.fullmatch(prompt or "")
    if match is None:
        return None
    city, kind = match["city"], match["kind"]
    if set(re.findall(r"\w+", city.casefold())) & _AMBIGUOUS:
        return None
    target = "doctors" if match["who"].lower().startswith("doctor") and not re.search(
        r"cancer|oncolog", kind, re.I) else "oncologists"
    who = "oncologists" if target == "oncologists" else "doctors"
    return Plan(action="search", scope="hospital_doctors", objective=prompt,
                requested_institutions=int(match["count"]), requested_people=None,
                requested_people_per_institution=None, target=target,
                criteria=[Criterion(id="institution_type", requirement=kind, prompt_quote=kind, applies_to="institution"),
                          Criterion(id="location", requirement="Located in " + city, prompt_quote=city,
                                    applies_to="institution")],
                queries=[f"{kind} {city} {who}", f"{kind} {city} find a doctor oncology",
                         f"{kind} {city} official website"],
                explanation="Parsed a complete count/type/city doctor-page request using the bounded grammar.")
