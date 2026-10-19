"""Builds the fictional hospital test site under tests/site/. All people and hospitals are invented.

Each page imitates a layout pattern seen on real Indian hospital websites:
label/value divs, JSON-LD, Next.js data, microdata, faculty tables, card grids
with and without the 'Dr.' prefix, related-doctor carousels and noisy nav/footer.
"""
from pathlib import Path

SITE = Path(__file__).parent / "site"
HEAD = (SITE / "_layout_head.txt").read_text()
FOOT = (SITE / "_layout_foot.txt").read_text()


def page(title: str, body: str, head_extra: str = "") -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title}</title>
<meta property="og:site_name" content="Sunrise Super Speciality Hospital">{head_extra}</head>
<body>{HEAD}<main>{body}</main>{FOOT}</body></html>"""


FILES: dict[str, str] = {}

FILES["index.html"] = page("Sunrise Hospital | Best Multi-speciality Hospital in Delhi", """
<section class="hero"><h1>Care that comes first</h1><p>Over 40 specialities under one roof.</p></section>
<section><h2>Centres of excellence</h2><ul>
<li><a href="/specialities/cancer-care">Cancer Care</a></li><li><a href="/specialities/cardiology">Heart Institute</a></li></ul>
<a href="/find-a-doctor" class="btn">Find a Doctor</a> <a href="/doctors-app">Doctors (new app)</a></section>""")

FILES["robots.txt"] = """User-agent: *
Allow:
Disallow: /private/
Disallow: *?speciality=*
Disallow: /book-an-appointment/doctor
Sitemap: /sitemap.xml
"""

FILES["sitemap.xml"] = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>{BASE}/</loc></url>
<url><loc>{BASE}/doctor/dr-asha-rao</loc></url>
<url><loc>{BASE}/doctor/dr-priya-nair</loc></url>
<url><loc>{BASE}/blog/what-is-chemotherapy</loc></url>
</urlset>"""

# ---- listing page 1: cards with Dr. prefix, mixed specialities, pagination
CARD = """<div class="doctor-card"><a href="/doctor/{slug}"><img src="/img/{slug}.jpg" alt="{name}"></a>
<div class="doctor-card__body"><h3 class="doctor-card__name"><a href="/doctor/{slug}">{name}</a></h3>
<p class="designation">{desig}</p><p class="spec">{spec}</p><p>{exp} Years of Experience</p>
<p class="loc">{hosp}</p><a class="btn" href="/book-an-appointment/doctor/{slug}">Book Appointment</a>
<a class="btn-outline" href="/doctor/{slug}">View Profile</a></div></div>"""

def cards(items):
    return "\n".join(CARD.format(**i) for i in items)

FILES["find-a-doctor.html"] = page("Find a Doctor | Sunrise Hospital", f"""
<h1>Find a Doctor</h1><form><select><option>All specialities</option></select></form>
<div class="doctor-grid">{cards([
    dict(slug="dr-meenu-walia", name="Dr. Meenu Walia", desig="Senior Director", spec="Medical Oncology", exp=28, hosp="Sunrise Hospital, Saket"),
    dict(slug="dr-sunil-kumar", name="Dr. Sunil Kumar", desig="Principal Director", spec="Interventional Cardiology", exp=30, hosp="Sunrise Hospital, Saket"),
    dict(slug="dr-rahul-verma", name="Dr. Rahul Verma", desig="Associate Director", spec="Surgical Oncology - Head &amp; Neck", exp=15, hosp="Sunrise Hospital, Gurugram"),
    dict(slug="dr-neha-joshi", name="Dr. Neha Joshi", desig="Consultant", spec="Dermatology", exp=9, hosp="Sunrise Hospital, Noida"),
])}</div>
<ul class="pagination"><li class="active"><a href="/find-a-doctor">1</a></li><li><a href="/find-a-doctor?page=2">2</a></li><li><a href="/find-a-doctor?page=2" rel="next">Next ›</a></li></ul>
<a href="/find-a-doctor?speciality=oncology">Oncology doctors</a>""")

FILES["find-a-doctor__page=2.html"] = page("Find a Doctor - Page 2 | Sunrise Hospital", f"""
<h1>Find a Doctor</h1><div class="doctor-grid">{cards([
    dict(slug="dr-kavita-menon", name="Dr. (Col) Kavita Menon", desig="Senior Consultant", spec="Haemato Oncology &amp; BMT", exp=18, hosp="Sunrise Hospital, Saket"),
    dict(slug="dr-arjun-shah", name="Dr. Arjun Shah", desig="Consultant", spec="Orthopaedics", exp=12, hosp="Sunrise Hospital, Gurugram"),
])}</div><ul class="pagination"><li><a href="/find-a-doctor">1</a></li><li class="active"><a href="/find-a-doctor?page=2">2</a></li></ul>""")

# ---- department page: team cards WITHOUT the Dr. prefix
FILES["specialities/cancer-care.html"] = page("Cancer Care | Oncology | Sunrise Hospital", """
<nav aria-label="breadcrumb" class="breadcrumb"><ol><li><a href="/">Home</a></li><li>Specialities</li><li>Cancer Care</li></ol></nav>
<h1>Cancer Care</h1><p>Comprehensive cancer treatment: chemotherapy, radiotherapy, surgery and bone marrow transplant.</p>
<h2>Our Team</h2><div class="team-list">
<div class="team-member"><a href="/doctor/dr-meenu-walia"><span class="tm-name">Meenu Walia</span></a><span class="tm-role">Senior Director, Medical Oncology</span><span>MBBS, MD, DM</span></div>
<div class="team-member"><a href="/doctor/dr-asha-rao"><span class="tm-name">Asha Rao</span></a><span class="tm-role">Director, Radiation Oncology</span><span>MBBS, MD (Radiotherapy)</span></div>
<div class="team-member"><a href="/doctor/dr-priya-nair"><span class="tm-name">Priya Nair</span></a><span class="tm-role">Consultant, Gynae Oncology</span><span>MBBS, MS, MCh</span></div>
<div class="team-member"><a href="/doctor/dr-anil-gupta"><span class="tm-name">Anil Gupta</span></a><span class="tm-role">Head, Paediatric Haemato-Oncology</span><span>MBBS, MD, FNB</span></div>
</div><p><a href="/doctors-json">Browse all oncologists</a></p>""")

FILES["specialities/cardiology.html"] = page("Cardiology | Sunrise Hospital", """
<h1>Heart Institute</h1><p>Interventional cardiology, cardiac surgery.</p>
<div class="team"><div class="member"><h4>Dr. Sunil Kumar</h4><p>Principal Director, Interventional Cardiology</p><a href="/doctor/dr-sunil-kumar">Know more</a></div>
<div class="member"><h4>Dr. Ritu Bansal</h4><p>Consultant, Cardiac Anaesthesia</p><a href="/doctor/dr-ritu-bansal">Know more</a></div></div>""")

# ---- profile: label/value divs, sections, related doctors, noisy nav
FILES["doctor/dr-meenu-walia.html"] = page("Dr. Meenu Walia - Best Medical Oncologist in Delhi | Sunrise Hospital", """
<div class="breadcrumb"><a href="/">Home</a> &gt; <a href="/find-a-doctor">Doctors</a> &gt; <span>Dr. Meenu Walia</span></div>
<section class="doctor-banner"><img src="/img/dr-meenu-walia.jpg" alt="Dr. Meenu Walia">
<h1>Dr. Meenu Walia</h1>
<p class="doc-designation">Senior Director - Medical Oncology</p>
<p class="doc-quali">MBBS, MD (Medicine), DM (Medical Oncology), ECMO</p>
<div class="doc-meta"><div class="item"><div class="label">Experience</div><div class="value">28+ Years</div></div>
<div class="item"><div class="label">Hospital</div><div class="value">Sunrise Super Speciality Hospital, Saket, New Delhi</div></div>
<div class="item"><div class="label">Languages Spoken</div><div class="value">English, Hindi, Punjabi</div></div>
<div class="item"><span class="label">Consultation Fee</span> <span>₹ 1,800</span></div></div>
<a class="btn" href="/book-an-appointment/doctor/dr-meenu-walia">Book an Appointment</a></section>
<section><h2>About Dr. Meenu Walia</h2><p>Dr. Meenu Walia is a renowned medical oncologist with over 28 years of experience in the treatment of breast, lung and gastrointestinal cancers. She has administered more than 10,000 cycles of chemotherapy and leads the immunotherapy programme.</p></section>
<section><h2>Areas of Expertise</h2><ul><li>Breast Cancer</li><li>Lung Cancer</li><li>Immunotherapy &amp; Targeted Therapy</li><li>GI Cancers</li></ul></section>
<section><h3>Education</h3><ul><li>MBBS - Maulana Azad Medical College, 1990</li><li>DM (Medical Oncology) - AIIMS, New Delhi, 1999</li></ul></section>
<section><h3>Awards &amp; Recognition</h3><ul><li>Best Oncologist Award, Delhi Medical Association 2015</li></ul></section>
<section><h3>OPD Timings</h3><p>Mon - Fri: 10:00 AM - 4:00 PM</p><p>Sat: 10:00 AM - 1:00 PM</p></section>
<section class="related-doctors"><h2>Other Doctors in Medical Oncology</h2>
<div class="rel"><a href="/doctor/dr-rahul-verma"><h4>Dr. Rahul Verma</h4></a><p>Associate Director - Surgical Oncology</p></div>
<div class="rel"><a href="/doctor/dr-kavita-menon"><h4>Dr. Kavita Menon</h4></a><p>Senior Consultant - Haemato Oncology</p></div></section>""")

# ---- profile: JSON-LD Physician, little HTML structure
FILES["doctor/dr-asha-rao.html"] = page("Dr. Asha Rao | Radiation Oncologist", """
<h1>Dr. Asha Rao</h1><p>Director, Radiation Oncology</p>
<p>Dr Asha Rao has 22 years of experience in IMRT, IGRT and brachytherapy.</p>
<p>Reg. No. DMC-45821</p>""", head_extra="""
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
 {"@type":"Physician","name":"Dr. Asha Rao","medicalSpecialty":["https://schema.org/Oncologic","Radiation Oncology"],
  "jobTitle":"Director - Radiation Oncology","image":"/img/asha.jpg","knowsLanguage":["English","Kannada","Hindi"],
  "hospitalAffiliation":{"@type":"Hospital","name":"Sunrise Hospital, Gurugram"},
  "address":{"@type":"PostalAddress","addressLocality":"Gurugram"},
  "description":"Radiation oncologist specialising in stereotactic radiosurgery and brachytherapy.",
  "hasCredential":"MBBS, MD (Radiotherapy), DNB",},
 {"@type":"BreadcrumbList","itemListElement":[{"@type":"ListItem","position":1,"name":"Home"},{"@type":"ListItem","position":2,"name":"Radiation Oncology"}]}
]}</script>""")

# ---- profile: Next.js app shell, data only in __NEXT_DATA__
FILES["doctor/dr-rahul-verma.html"] = """<!doctype html><html><head><title>Sunrise Hospital</title></head><body><div id="__next"></div>
<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"doctor":{"id":812,"doctorName":"Dr. Rahul Verma",
"designation":"Associate Director","speciality":{"name":"Surgical Oncology"},"subSpeciality":"Head & Neck Oncology",
"qualification":"MBBS, MS (General Surgery), MCh (Surgical Oncology)","experience":"15","hospitalName":"Sunrise Hospital, Gurugram",
"languages":"English, Hindi, Gujarati","about":"<p>Dr. Rahul Verma is a head and neck onco-surgeon trained at Tata Memorial Hospital.</p>",
"expertise":[{"title":"Oral cancer surgery"},{"title":"Thyroid cancer"},{"title":"Robotic surgery"}],"slug":"dr-rahul-verma",
"profileImage":"https://cdn.sunrise.example/rv.jpg"},
"relatedDoctors":[{"doctorName":"Dr. Meenu Walia","designation":"Senior Director","speciality":{"name":"Medical Oncology"},"slug":"/doctor/dr-meenu-walia"}]}},
"page":"/doctor/[slug]"}</script></body></html>"""

# ---- profile: non-oncology
FILES["doctor/dr-sunil-kumar.html"] = page("Dr. Sunil Kumar | Cardiologist", """
<h1>Dr. Sunil Kumar</h1><p>Principal Director - Interventional Cardiology</p><p>MBBS, MD, DM (Cardiology)</p>
<p>30 years of experience</p><h2>About</h2><p>Dr. Sunil Kumar has performed 15,000 angioplasties. He also works with oncology teams on cardio-oncology cases.</p>""")

FILES["doctor/dr-ritu-bansal.html"] = page("Dr. Ritu Bansal", "<h1>Dr. Ritu Bansal</h1><p>Consultant - Cardiac Anaesthesia</p><p>MBBS, MD (Anaesthesia)</p>")

# ---- profile: messy, name only in <title>, dt/dd details, plain h1
FILES["doctor/dr-priya-nair.html"] = page("Dr. Priya Nair - Gynaecologic Oncologist in Kochi | Sunrise", """
<div class="profile"><h1>Priya Nair</h1>
<p>M.B.B.S., M.S. (Obst. &amp; Gynae), M.Ch. (Gynaecologic Oncology)</p>
<dl><dt>Department</dt><dd>Gynaecologic Oncology</dd><dt>Designation</dt><dd>Consultant</dd>
<dt>Location</dt><dd>Sunrise Medicity, Kochi</dd><dt>Timings</dt><dd>Tue, Thu 9 am - 1 pm</dd></dl>
<p>Having more than 12 years of experience, she treats ovarian, cervical and endometrial cancers using laparoscopic and robotic approaches.</p>
<p>Email: <a href="mailto:priya.nair@sunrise-hospital.example">priya.nair@sunrise-hospital.example</a></p></div>""")

# ---- profile: microdata
FILES["doctor/dr-anil-gupta.html"] = page("Paediatric Haemato-Oncology | Anil Gupta", """
<div itemscope itemtype="https://schema.org/Physician"><h1 itemprop="name">Dr. Anil Gupta</h1>
<span itemprop="jobTitle">Head - Paediatric Haemato-Oncology &amp; BMT</span>
<span itemprop="medicalSpecialty">Paediatric Oncology</span>
<div itemprop="hospitalAffiliation" itemscope itemtype="https://schema.org/Hospital"><span itemprop="name">Sunrise Hospital, Noida</span></div>
<p>Qualification: MBBS, MD (Paediatrics), FNB (Paediatric Haemato-Oncology)</p>
<p>Experience: 17 years</p></div>""")

# ---- faculty table (government institute style)
FILES["faculty/radiation-oncology.html"] = page("Department of Radiation Oncology - Faculty", """
<h2>Department of Radiation Oncology</h2><table class="faculty"><tr><th>S.No.</th><th>Name</th><th>Designation</th><th>Qualification</th><th>Email</th></tr>
<tr><td>1</td><td>Dr. Harish Chandra</td><td>Professor &amp; Head</td><td>MD, DNB</td><td>hchandra@inst.example</td></tr>
<tr><td>2</td><td>Dr. Lata Krishnan</td><td>Additional Professor</td><td>MD (Radiotherapy)</td><td>lk@inst.example</td></tr>
<tr><td>3</td><td>Dr. Imran Qureshi</td><td>Assistant Professor</td><td>MD, DNB, PDCC</td><td></td></tr></table>""")

# ---- JS app listing with embedded JSON state
FILES["doctors-json.html"] = """<!doctype html><html><head><title>Our Oncologists</title></head><body><div id="root"></div>
<script>window.__INITIAL_STATE__ = {"doctors":{"list":[
{"name":"Dr. Farah Siddiqui","designation":"Consultant","department":"Medical Oncology","qualification":"MBBS, MD, DM","experience":11,"url":"/doctor/dr-farah-siddiqui","location":"Sunrise Hospital, Noida"},
{"name":"Dr. Vikas Malhotra","designation":"Senior Consultant","department":"Uro-Oncology","qualification":"MBBS, MS, MCh","experience":19,"url":"/doctor/dr-vikas-malhotra","location":"Sunrise Hospital, Saket"},
{"name":"Dr. Neha Joshi","designation":"Consultant","department":"Dermatology","qualification":"MBBS, MD","experience":9,"url":"/doctor/dr-neha-joshi"}
]},"ui":{"loading":false}};</script></body></html>"""

# ---- client-side rendered page: data arrives by XHR, so only a browser can see it
FILES["js-team.html"] = """<!doctype html><html><head><title>Oncology Team</title></head><body><div id="root">Loading...</div>
<script>fetch('/api/team.json').then(r => r.json()).then(list => {
  document.getElementById('root').innerHTML = '<h1>Oncology Team</h1>' + list.map(d =>
    `<div class="doc"><h3><a href="${d.url}">${d.name}</a></h3><p>${d.role}</p><p>${d.quals}</p></div>`).join('');
});</script></body></html>"""
FILES["api/team.json"] = '[{"name":"Dr. Sameer Kulkarni","role":"Consultant - Medical Oncology","quals":"MBBS, MD, DM","url":"/doctor/dr-sameer-kulkarni"},' \
    '{"name":"Dr. Ritika Bose","role":"Senior Consultant - Radiation Oncology","quals":"MBBS, MD (Radiotherapy)","url":"/doctor/dr-ritika-bose"}]'

FILES["private/staff.html"] = page("Staff", "<h1>Dr. Secret Person</h1><p>Consultant - Medical Oncology</p>")
FILES["blog/what-is-chemotherapy.html"] = page("What is chemotherapy?", "<h1>What is chemotherapy?</h1><p>Dr. Meenu Walia explains chemotherapy.</p>")


def build() -> None:
    for rel, html in FILES.items():
        path = SITE / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")


if __name__ == "__main__":
    build()
    print(f"wrote {len(FILES)} files to {SITE}")
