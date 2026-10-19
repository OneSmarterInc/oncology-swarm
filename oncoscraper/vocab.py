"""Domain vocabulary and regular expressions for Indian doctor profile pages."""
from __future__ import annotations

import re

# --------------------------------------------------------------------------
# Oncology relevance. Weight 2 = clearly oncology, 1 = strongly cancer related.
ONCOLOGY_TERMS: dict[str, float] = {
    "oncology": 2, "oncologist": 2, "onco": 1.5, "cancer": 1.5, "haemato-oncology": 2,
    "hemato-oncology": 2, "hemato oncology": 2, "haemato oncology": 2,
    "radiotherapy": 2, "radiation therapy": 1.5, "chemotherapy": 1.5, "tumour": 1, "tumor": 1,
    "bone marrow transplant": 1.5, "bmt": 1, "stem cell transplant": 1.5,
    "leukemia": 1, "leukaemia": 1, "lymphoma": 1, "myeloma": 1, "carcinoma": 1,
    "sarcoma": 1, "malignan": 1, "immunotherapy": 1, "brachytherapy": 2,
    "cyberknife": 1.5, "tomotherapy": 2, "linac": 1, "radiosurgery": 1.5,
    "mastectomy": 1, "oncoplastic": 2, "hipec": 2, "palliative": 0.5,
    "nuclear medicine": 0.5, "pet-ct": 0.5, "pet ct": 0.5,
}

# Sub-specialty canonical names and the phrases that point to them.
SUB_SPECIALTIES: dict[str, tuple[str, ...]] = {
    "Medical Oncology": ("medical oncology", "medical oncologist", "clinical oncology"),
    "Surgical Oncology": ("surgical oncology", "surgical oncologist", "onco surgery", "onco-surgery", "oncosurgery", "cancer surgery", "onco surgeon"),
    "Radiation Oncology": ("radiation oncology", "radiation oncologist", "radiotherapy", "radiation therapy"),
    "Haemato-Oncology": ("haemato-oncology", "hemato-oncology", "hemato oncology", "haemato oncology", "hematology oncology", "haematology oncology", "haematologist", "hematologist", "clinical haematology", "clinical hematology"),
    "Bone Marrow Transplant": ("bone marrow transplant", "bmt", "stem cell transplant", "hematopoietic"),
    "Paediatric Oncology": ("paediatric oncology", "pediatric oncology", "paediatric haemato", "pediatric hemato", "childhood cancer"),
    "Gynaecologic Oncology": ("gynaecologic oncology", "gynecologic oncology", "gynae oncology", "gyne oncology", "gynaec oncology", "gynae-oncology", "gyn onc"),
    "Head and Neck Oncology": ("head and neck oncology", "head & neck oncology", "head and neck cancer", "head & neck surgical"),
    "Breast Oncology": ("breast oncology", "breast cancer surgery", "breast surgical oncology", "oncoplastic breast"),
    "Uro-Oncology": ("uro-oncology", "uro oncology", "urologic oncology", "urological oncology"),
    "Neuro-Oncology": ("neuro-oncology", "neuro oncology", "brain tumour", "brain tumor"),
    "Musculoskeletal Oncology": ("musculoskeletal oncology", "orthopaedic oncology", "orthopedic oncology", "bone and soft tissue", "ortho-oncology"),
    "Thoracic Oncology": ("thoracic oncology", "thoracic surgical oncology", "lung cancer"),
    "GI Oncology": ("gi oncology", "gastrointestinal oncology", "gi surgical oncology", "hpb oncology", "hepatobiliary"),
    "Thoracic/Lung Cancer Surgery": ("thoracic onco",),
    "Nuclear Medicine": ("nuclear medicine",),
    "Onco-Pathology": ("oncopathology", "onco-pathology", "onco pathology"),
    "Palliative Medicine": ("palliative medicine", "palliative care", "pain and palliative"),
    "Psycho-Oncology": ("psycho-oncology", "psycho oncology"),
    "Interventional Oncology": ("interventional oncology", "interventional radiology oncology"),
}

# --------------------------------------------------------------------------
# Qualifications. Dotted forms are normalised before matching.
QUALIFICATIONS = [
    "MBBS", "MD", "MS", "DM", "MCh", "DNB", "DrNB", "FNB", "PDCC", "PhD", "DMRT", "DMRD", "DMRE",
    "DGO", "DCH", "DA", "DCP", "DPM", "DLO", "DOMS", "DPB", "MRCP", "FRCP", "FRCS", "FRCR", "MRCS",
    "FACS", "FICS", "FACP", "FICP", "FAMS", "MNAMS", "FIAGES", "FMAS", "FALS", "MRCOG", "FRCOG",
    "FICOG", "ECFMG", "USMLE", "FAAP", "MRCPCH", "FRCPC", "FRACP", "FRANZCR",
    "MPH", "MHA", "MBA", "MSc", "BDS", "MDS", "FDS", "CCT", "CCST", "FEBS", "FEBU", "FRCPath",
    "DipNB", "PGDHHM", "FCPS", "MMed", "FCCP", "FSCAI", "FESC", "FUICC", "FAGE", "FIAMS",
]
_QUAL_ALT = "|".join(sorted((re.escape(q) for q in QUALIFICATIONS), key=len, reverse=True))
QUAL_RE = re.compile(rf"(?<![A-Za-z])({_QUAL_ALT})(?![A-Za-z])")
# Words that turn a bare "MS"/"MD"/"DA" into a qualification line.
QUAL_STRONG = {"MBBS", "MCh", "DNB", "DrNB", "DM", "FRCS", "FRCR", "MRCP", "PDCC", "FNB", "DMRT", "DMRD", "PhD", "MRCS", "FRCP", "FACS"}

DOTTED_QUALS = [
    (re.compile(r"M\.\s*B\.\s*B\.\s*S\.?", re.I), "MBBS"),
    (re.compile(r"M\.\s*Ch\.?(?=[\s,(/]|$)", re.I), "MCh"),
    (re.compile(r"(?<![A-Za-z])M\.\s*D\.?(?=[\s,(/]|$)"), "MD"),
    (re.compile(r"(?<![A-Za-z])M\.\s*S\.?(?=[\s,(/]|$)"), "MS"),
    (re.compile(r"(?<![A-Za-z])D\.\s*M\.?(?=[\s,(/]|$)"), "DM"),
    (re.compile(r"D\.\s*N\.\s*B\.?", re.I), "DNB"),
    (re.compile(r"Ph\.\s*D\.?", re.I), "PhD"),
    (re.compile(r"F\.\s*R\.\s*C\.\s*S\.?", re.I), "FRCS"),
    (re.compile(r"M\.\s*R\.\s*C\.\s*P\.?", re.I), "MRCP"),
    (re.compile(r"D\.\s*M\.\s*R\.\s*T\.?", re.I), "DMRT"),
]


def normalize_quals(text: str) -> str:
    for rx, rep in DOTTED_QUALS:
        text = rx.sub(rep, text)
    return text


# --------------------------------------------------------------------------
# Names
HONORIFIC = r"(?:Dr|DR|Prof|PROF|Professor|PROFESSOR|Doctor(?=\s)|DOCTOR(?=\s))"
_RANK = r"(?:Brig|Col|Lt\.?\s*Col|Maj|Gen|Lt\.?\s*Gen|Maj\.?\s*Gen|Surg\.?\s*(?:Cdr|Capt|Cmde)|Capt|Wg\.?\s*Cdr|Air\s*Cmde|Cdr|Retd|Prof|Dr)"
_PREFIX_EXTRA = rf"(?:\(\s*{_RANK}\.?\s*(?:\(?retd\.?\)?)?\s*\)\s*|{_RANK}\.?\s+|{HONORIFIC}\.?\s+)"
NAME_TOKEN = r"[A-Z](?:[a-zA-Z'\u2019\-]|\.(?=[A-Z]))*\.?"
NAME_RE = re.compile(
    rf"(?<![A-Za-z]){HONORIFIC}\.?\s*{_PREFIX_EXTRA}{{0,3}}"
    rf"((?:{NAME_TOKEN}\s+){{0,4}}{NAME_TOKEN})"
)
NAME_STOP = {
    "MBBS", "MD", "MS", "DM", "MCh", "DNB", "Consultant", "Senior", "Director", "Head", "Department",
    "Oncology", "Oncologist", "Cancer", "Hospital", "Hospitals", "Profile", "Book", "Appointment",
    "Associate", "Assistant", "Professor", "Chairman", "Chief", "Unit", "Specialist", "Surgeon",
    "Medical", "Surgical", "Radiation", "Best", "Top", "In", "At", "Of", "And", "The", "Is", "Has",
    "Experience", "Years", "View", "Know", "More", "Read", "Call", "Contact", "Clinic", "Centre",
    "Center", "Institute", "Speciality", "Specialty", "Haemato", "Hemato", "Paediatric", "Pediatric",
    "Max", "Apollo", "Fortis", "Manipal", "Medanta", "Narayana", "Tata", "Aiims", "AIIMS", "With",
    "Her", "His", "He", "She", "Who", "Was", "For", "To", "On", "Doctor", "Doctors", "Team", "Our",
    "Name", "Details", "Search", "Find", "Online", "Consultation", "List", "Speak", "Visit", "Timings",
    "Qualification", "Qualifications", "Designation", "Department", "Speciality", "Specialities", "Location",
    "Get", "Ask", "Talk", "Meet", "Your", "My", "Here", "Now", "Today", "Video", "Opinion", "Second",
    "Laboratories", "Labs", "Lab", "Pharma", "Pharmaceuticals", "Foundation", "Trust", "Charitable", "Retd",
}


def clean_name(raw: str) -> str | None:
    """Trim trailing non-name words and validate a captured name."""
    tokens = raw.replace("’", "'").split()
    if tokens and all(t.isupper() or len(t.strip(".")) <= 1 for t in tokens):
        tokens = [t if len(t.strip(".")) <= 1 or "." in t.strip(".") else t.capitalize() for t in tokens]
    out: list[str] = []
    for tok in tokens:
        bare = tok.strip(".,")
        if bare.endswith("'s") and bare[:-2] in NAME_STOP:
            break
        if bare in NAME_STOP or bare.upper() in {q.upper() for q in QUALIFICATIONS}:
            break
        out.append(tok.rstrip(","))
    while out and len(out[-1].strip(".")) <= 1 and len(out) > 1 and not out[-1].endswith("."):
        out.pop()
    name = " ".join(out).strip(" .,-")
    letters = [t for t in out if len(t.strip(".")) > 1]
    if not letters or len(name) < 3 or len(name) > 60:
        return None
    return name


def format_name(name: str, honorific: str = "Dr.") -> str:
    return f"{honorific} {name}" if not re.match(rf"^{HONORIFIC}\b", name, re.I) else name


# --------------------------------------------------------------------------
DESIGNATIONS = [
    "Principal Director", "Senior Director", "Associate Director", "Director", "Chairman", "Vice Chairman",
    "Co-Chairman", "Chairperson", "Head of Department", "Head - ", "Head &", "Head and", "HOD", "Unit Head",
    "Chief", "Clinical Lead", "Clinical Director", "Senior Consultant", "Associate Consultant",
    "Attending Consultant", "Junior Consultant", "Visiting Consultant", "Consultant", "Professor",
    "Associate Professor", "Assistant Professor", "Additional Professor", "Lecturer", "Senior Resident",
    "Fellow", "Medical Director", "Dean", "Founder", "Specialist", "Registrar", "Physician", "Surgeon",
]
DESIGNATION_RE = re.compile(
    r"\b(?:Principal Director|Senior Director|Associate Director|Director|Vice[- ]Chairman|Co[- ]Chairman|"
    r"Chairman|Chairperson|Head of (?:the )?Department|HOD|Unit Head|Head|Chief(?: of [A-Za-z ]+)?|Clinical (?:Lead|Director)|"
    r"(?:Senior|Associate|Attending|Junior|Visiting|Principal|Sr\.?)\s+Consultant|Consultant|"
    r"(?:Associate|Assistant|Additional)\s+Professor|Professor|Senior Resident|Medical Director|Dean|Lecturer)\b"
    r"(?:\s*(?:[-–&,:]|and|of|in|&)\s*[A-Za-z&,\-–() /]{2,90})?",
    re.I,
)

EXPERIENCE_RE = re.compile(
    r"(?:(?:experience|exp\.?)\s*(?:of|:|-|–)?\s*(?:over|more than|about|nearly|around)?\s*(\d{1,2})\s*\+?\s*(?:years|yrs))"
    r"|(?:(\d{1,2})\s*\+?\s*(?:years|yrs)\.?\s*(?:of\s+)?(?:\w+\s+){0,3}?(?:experience|exp\b))"
    r"|(?:(?:over|more than|nearly|around)\s+(\d{1,2})\s+years)",
    re.I,
)

FEE_RE = re.compile(r"(?:consultation\s*fee|fees?|consultation charges?)\s*[:\-–]?\s*(?:₹|rs\.?|inr)\s*([\d,]{2,7})", re.I)
FEE_RE2 = re.compile(r"(?:₹|rs\.?|inr)\s*([\d,]{3,7})\s*(?:/-)?\s*(?:consultation|fee)", re.I)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:(?:\+|00)91[\s\-]?)?(?:\(?0?\d{2,5}\)?[\s\-]?)?\d{3,5}[\s\-]?\d{4,6}")

REG_RE = re.compile(
    r"(?:(?:medical\s+)?(?:council\s+)?reg(?:istration|n|d)?\.?\s*(?:no|number|#)\.?|"
    r"(?:MCI|NMC|DMC|MMC|KMC|TNMC|WBMC|UPMC|GMC|APMC|TSMC|RMC|PMC|HMC|BMC|OMC|KSMC|MPMC|CGMC)\s*(?:reg(?:istration|n)?\.?\s*(?:no|number)?\.?)?)"
    r"\s*[:#\-–.]?\s*([A-Z]{0,8}[\-/ ]?\d{2,7}(?:[\-/ ]\d{1,6})?(?:[\-/ ][A-Z]{1,4})?)",
    re.I,
)

LANGUAGES = [
    "English", "Hindi", "Bengali", "Bangla", "Marathi", "Telugu", "Tamil", "Gujarati", "Urdu", "Kannada",
    "Odia", "Oriya", "Malayalam", "Punjabi", "Assamese", "Maithili", "Konkani", "Nepali", "Sindhi",
    "Kashmiri", "Dogri", "Manipuri", "Bhojpuri", "Rajasthani", "Haryanvi", "Tulu", "Arabic", "French",
    "German", "Russian", "Spanish", "Persian", "Dari", "Pashto", "Swahili", "Sinhala", "Burmese", "Uzbek",
]
LANG_RE = re.compile(r"\b(" + "|".join(LANGUAGES) + r")\b", re.I)

DAYS = r"(?:Mon(?:day)?|Tue(?:s|sday)?|Wed(?:nesday)?|Thu(?:r|rs|rsday)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?|Daily|All days|Weekdays)"
TIME = r"\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.|hrs)?"
OPD_RE = re.compile(
    rf"{DAYS}(?:\s*(?:-|–|to|,|&|and)\s*{DAYS})*\s*[:\-–(]?\s*{TIME}\s*(?:-|–|to)\s*{TIME}",
    re.I,
)

INDIAN_CITIES = [
    "New Delhi", "Delhi", "Gurugram", "Gurgaon", "Noida", "Greater Noida", "Ghaziabad", "Faridabad", "Mumbai",
    "Navi Mumbai", "Thane", "Pune", "Nagpur", "Nashik", "Aurangabad", "Bengaluru", "Bangalore", "Mysuru",
    "Mysore", "Mangaluru", "Mangalore", "Hubli", "Chennai", "Coimbatore", "Madurai", "Trichy", "Tiruchirappalli",
    "Vellore", "Salem", "Hyderabad", "Secunderabad", "Visakhapatnam", "Vizag", "Vijayawada", "Guntur",
    "Tirupati", "Kolkata", "Howrah", "Siliguri", "Durgapur", "Bhubaneswar", "Cuttack", "Ahmedabad", "Surat",
    "Vadodara", "Rajkot", "Gandhinagar", "Jaipur", "Jodhpur", "Udaipur", "Kota", "Bikaner", "Lucknow", "Kanpur",
    "Varanasi", "Prayagraj", "Allahabad", "Agra", "Meerut", "Bareilly", "Gorakhpur", "Patna", "Muzaffarpur",
    "Ranchi", "Jamshedpur", "Dhanbad", "Bhopal", "Indore", "Gwalior", "Jabalpur", "Raipur", "Bilaspur",
    "Chandigarh", "Mohali", "Panchkula", "Ludhiana", "Amritsar", "Jalandhar", "Bathinda", "Dehradun",
    "Haridwar", "Rishikesh", "Shimla", "Jammu", "Srinagar", "Kochi", "Cochin", "Thiruvananthapuram",
    "Trivandrum", "Kozhikode", "Calicut", "Thrissur", "Kollam", "Kannur", "Guwahati", "Shillong", "Imphal",
    "Agartala", "Goa", "Panaji", "Margao", "Puducherry", "Pondicherry", "Karnal", "Panipat", "Sonipat",
    "Hisar", "Rohtak", "Ambala", "Saket", "Vaishali", "Patparganj", "Shalimar Bagh", "Dwarka", "Rajinder Nagar",
]
CITY_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, INDIAN_CITIES), key=len, reverse=True)) + r")\b")
CITY_CANON = {"Gurgaon": "Gurugram", "Bangalore": "Bengaluru", "Bombay": "Mumbai", "Calcutta": "Kolkata",
              "Madras": "Chennai", "Cochin": "Kochi", "Trivandrum": "Thiruvananthapuram", "Mysore": "Mysuru",
              "Mangalore": "Mangaluru", "Calicut": "Kozhikode", "Allahabad": "Prayagraj", "Vizag": "Visakhapatnam",
              "Pondicherry": "Puducherry", "Trichy": "Tiruchirappalli",
              "Saket": "New Delhi", "Vaishali": "Ghaziabad", "Patparganj": "New Delhi", "Shalimar Bagh": "New Delhi",
              "Dwarka": "New Delhi", "Rajinder Nagar": "New Delhi"}

HOSPITAL_WORDS = re.compile(
    r"\b(?:Hospital|Hospitals|Healthcare|Health City|Medical (?:College|Centre|Center|Institute)|Institute|"
    r"Cancer (?:Centre|Center|Institute|Hospital)|Medicity|Clinic|Memorial|AIIMS|Nursing Home|Health Care)\b",
    re.I,
)

# --------------------------------------------------------------------------
# Labels used on profile pages (lower-case, punctuation stripped) -> field
LABELS: dict[str, str] = {
    "name": "name", "doctor name": "name", "doctor": "name",
    "designation": "designation", "position": "designation", "title": "designation", "role": "designation",
    "speciality": "specialty", "specialty": "specialty", "specialities": "specialty", "specialties": "specialty",
    "specialization": "specialty", "specialisation": "specialty", "primary speciality": "specialty",
    "primary specialty": "specialty", "super speciality": "specialty", "super specialty": "specialty",
    "department": "department", "dept": "department", "division": "department", "unit": "department",
    "hospital": "hospital", "hospitals": "hospital", "location": "hospital", "locations": "hospital",
    "facility": "hospital", "practices at": "hospital", "available at": "hospital", "consults at": "hospital",
    "centre": "hospital", "center": "hospital", "branch": "hospital", "works at": "hospital",
    "city": "city",
    "experience": "experience_years", "total experience": "experience_years", "years of experience": "experience_years",
    "qualification": "qualifications", "qualifications": "qualifications", "degree": "qualifications",
    "degrees": "qualifications", "educational qualification": "qualifications", "academic qualification": "qualifications",
    "education": "education", "education & training": "education", "education and training": "education",
    "training": "education", "fellowships": "education", "fellowship": "education",
    "languages": "languages", "languages spoken": "languages", "language": "languages", "language spoken": "languages",
    "languages known": "languages",
    "consultation fee": "consultation_fee", "consultation fees": "consultation_fee", "fee": "consultation_fee", "fees": "consultation_fee",
    "opd timings": "opd_timings", "opd timing": "opd_timings", "timings": "opd_timings", "timing": "opd_timings",
    "opd schedule": "opd_timings", "availability": "opd_timings", "opd days": "opd_timings", "schedule": "opd_timings",
    "consultation timings": "opd_timings",
    "registration": "registration", "registration no": "registration", "registration number": "registration",
    "reg no": "registration", "medical registration": "registration", "mci registration": "registration",
    "nmc registration": "registration",
    "phone": "phone", "contact": "phone", "contact number": "phone", "telephone": "phone", "mobile": "phone", "tel": "phone",
    "email": "email", "e-mail": "email", "email id": "email", "e mail": "email", "mail": "email",
    "email address": "email", "mail id": "email", "contact email": "email",
    "expertise": "expertise", "areas of expertise": "expertise", "area of expertise": "expertise",
    "clinical interests": "expertise", "clinical interest": "expertise", "areas of interest": "expertise",
    "area of interest": "expertise", "special interests": "expertise", "special interest": "expertise",
    "procedures": "expertise", "procedures performed": "expertise", "treatments": "expertise",
    "conditions treated": "expertise", "services": "expertise", "skills": "expertise", "focus areas": "expertise",
    "areas of specialisation": "expertise", "areas of specialization": "expertise", "specialized in": "expertise",
    "awards": "awards", "awards & recognition": "awards", "awards and recognition": "awards", "achievements": "awards",
    "awards & achievements": "awards", "honours": "awards", "honors": "awards",
    "about": "about", "about doctor": "about", "profile": "about", "overview": "about", "biography": "about",
    "summary": "about", "brief profile": "about", "professional summary": "about",
}


def label_key(text: str) -> str:
    text = text.strip().lower().rstrip(":-–").strip()
    text = re.sub(r"^about\s+(?:dr\.?|doctor)\b.*$", "about", text)
    text = re.sub(r"[^a-z&\- ]+", " ", text)
    return " ".join(text.split())


def label_field(text: str) -> str | None:
    key = label_key(text)
    if key in LABELS:
        return LABELS[key]
    if len(key) > 40:
        return None
    for k, v in LABELS.items():
        if len(k) > 4 and (key.startswith(k + " ") or key.endswith(" " + k)):
            return v
    return None


# --------------------------------------------------------------------------
# URL hints
PROFILE_URL_HINTS = re.compile(
    r"/(?:doctors?|physicians?|consultants?|specialists?|experts?|our-doctors|our-experts|faculty|team|people|"
    r"profile|profiles|find-a-doctor|doctor-profile|staff|surgeons?|oncologists?)/[^/?#]+/?$"
    r"|/dr[-_.][a-z]|/doctor[-_][a-z]",
    re.I,
)
LISTING_URL_HINTS = re.compile(
    r"(?:find-a-doctor|find-doctor|our-doctors|doctors?(?:[-_/]|$|\?)|doctor-list|meet-our|our-team|our-experts|"
    r"specialists|consultants|faculty|medical-team|team|physicians|experts|oncologists?|department|speciality|specialty|"
    r"specialities|specialties|oncology|cancer|haematology|hematology|radiation|bmt|bone-marrow)",
    re.I,
)
ONCO_URL_HINTS = re.compile(r"onco|cancer|tumou?r|radiation|radiotherapy|haemat|hemat|bmt|bone-marrow|chemo", re.I)
SKIP_URL = re.compile(
    r"\.(?:pdf|jpe?g|png|gif|svg|webp|ico|css|js|mp4|mp3|zip|docx?|xlsx?|pptx?)(?:$|\?)|"
    r"/(?:blogs?|news|media|press|events?|careers?|jobs|login|signin|sign-in|register|cart|checkout|"
    r"search|tag|tags|author|feed|wp-json|wp-admin|wp-content|cdn-cgi|share|print|amp|"
    r"health-packages?|packages?|health-check|lab-tests?|pharmacy|patient-stories|testimonials?|videos?|"
    r"gallery|investors?|csr|hi|mr|bn|ta|te|kn|gu|ml|book[-_]?an?[-_]?appointment|book[-_]appointment|appointments?|"
    r"enquiry|enquire|request-a-call(?:back)?|callback|online-consultation|video-consultation|second-opinion)(?:/|$)|"
    r"^(?:mailto|tel|javascript|whatsapp):",
    re.I,
)
LINK_TEXT_HINTS = re.compile(
    r"doctor|oncolog|cancer|specialist|consultant|faculty|our team|meet the team|experts?|physicians|view all|see all|"
    r"browse|department|speciality|specialty|haematolog|hematolog|radiation|bone marrow|tumou?r", re.I)
PAGINATION_RE = re.compile(r"(?:[?&](?:page|p|pg|paged|start|offset)=\d+|/page/\d+/?$)", re.I)
