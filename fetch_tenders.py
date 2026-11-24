"""Fetch UK and EU telecoms tenders into tenders.json (standard library only)."""
import json, re, sys, urllib.request, urllib.parse
from datetime import datetime, timedelta, timezone

DAYS, MAX_PAGES = 30, 120
NOW = datetime.now(timezone.utc)
START = NOW - timedelta(days=DAYS)
CPV = ("642", "3240", "3241", "3242", "325", "45314", "7271", "7272")
WORDS = ("broadband", "fibre", "fiber", "gigabit", "telecom", "connectivity", "subsea",
         "submarine cable", "leased line", "sd-wan", "voip", "wide area network", "mobile network")
ISLANDS = ("isle of man", "jersey", "guernsey", "alderney", "channel islands")

def get(url, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
        headers={"Content-Type": "application/json", "User-Agent": "connectivity-tenders/0.1"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)

def cpvs(o, out):
    if isinstance(o, dict):
        for k, v in o.items():
            if k in ("classification", "additionalClassifications"):
                for c in v if isinstance(v, list) else [v]:
                    if isinstance(c, dict) and str(c.get("id", "")).isdigit():
                        out.add(str(c["id"])[:8])
            cpvs(v, out)
    elif isinstance(o, list):
        for x in o:
            cpvs(x, out)
    return out

def ocds(source, first_url, link):
    items, url, pages = {}, first_url, 0
    while url and pages < MAX_PAGES:
        d = get(url); pages += 1
        for r in d.get("releases", []):
            t, title = r.get("tender") or {}, (r.get("tender") or {}).get("title") or ""
            if not (any(c.startswith(CPV) for c in cpvs(r, set())) or any(w in title.lower() for w in WORDS)):
                continue
            tags = r.get("tag", [])
            stage = ("award" if any(x.startswith(("award", "contract")) for x in tags)
                     else "planning" if any(x.startswith("planning") for x in tags) else "tender")
            aw = (r.get("awards") or [{}])[0]
            val = (aw.get("value") if stage == "award" else t.get("value")) or {}
            docs = [x["url"] for x in t.get("documents", []) if x.get("format") == "text/html" and x.get("url")]
            item = {"id": r.get("ocid"), "source": source, "stage": stage, "title": title,
                    "buyer": (r.get("buyer") or {}).get("name", ""), "value": val.get("amount"),
                    "currency": val.get("currency", "GBP"), "published": r.get("date") or "",
                    "deadline": ((t.get("tenderPeriod") or {}).get("endDate") or "")[:10] or None,
                    "country": "UK", "url": docs[0] if docs else link(r),
                    "islands": any(k in json.dumps(r).lower() for k in ISLANDS)}
            if item["id"] not in items or item["published"] > items[item["id"]]["published"]:
                items[item["id"]] = item
        url = (d.get("links") or {}).get("next")
    return list(items.values())

def fts():
    q = urllib.parse.urlencode({"limit": 100, "stages": "tender,award", "updatedFrom": START.strftime("%Y-%m-%dT%H:%M:%S")})
    return ocds("Find a Tender", "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages?" + q,
                lambda r: "https://www.find-tender.service.gov.uk/procurement/" + str(r.get("ocid")))

def cf():
    q = urllib.parse.urlencode({"limit": 100, "stages": "tender,award", "publishedFrom": START.strftime("%Y-%m-%dT%H:%M:%SZ")})
    return ocds("Contracts Finder", "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search?" + q,
                lambda r: "https://www.contractsfinder.service.gov.uk/Notice/" + str(r.get("id")))

def first(v):
    if isinstance(v, dict) and "eng" in v:
        v = v["eng"]
    while isinstance(v, (dict, list)) and v:
        v = next(iter(v.values())) if isinstance(v, dict) else v[0]
    return v if isinstance(v, str) else ""

def day(s):
    m = re.match(r"\d{4}-\d{2}-\d{2}", s or "")
    return m[0] if m else None

def ted():
    codes = "64200000 64210000 64212000 32400000 32412000 32420000 32500000 45314000 72720000"
    fields = ["publication-number", "notice-title", "buyer-name", "buyer-country", "publication-date",
              "deadline-receipt-tender-date-lot", "notice-type"]
    out = []
    for page in range(1, 11):
        d = get("https://api.ted.europa.eu/v3/notices/search", {
            "query": f"classification-cpv IN ({codes}) AND publication-date>={START:%Y%m%d}",
            "fields": fields, "page": page, "limit": 100, "scope": "ALL"})
        ns = d.get("notices", [])
        for n in ns:
            nt, num = first(n.get("notice-type")).lower(), first(n.get("publication-number"))
            out.append({"id": num, "source": "TED", "title": first(n.get("notice-title")),
                        "stage": "award" if nt.startswith("can") else "planning" if nt.startswith("pin") else "tender",
                        "buyer": first(n.get("buyer-name")), "value": None, "currency": "EUR",
                        "published": day(first(n.get("publication-date"))) or "",
                        "deadline": day(first(n.get("deadline-receipt-tender-date-lot"))),
                        "country": first(n.get("buyer-country")) or "EU",
                        "url": "https://ted.europa.eu/en/notice/-/detail/" + num,
                        "islands": any(k in json.dumps(n).lower() for k in ISLANDS)})
        if len(ns) < 100:
            break
    return out

items = []
for name, fn in (("Find a Tender", fts), ("Contracts Finder", cf), ("TED", ted)):
    try:
        got = fn(); items += got; print(name, len(got), "notices")
    except Exception as e:
        print("WARN", name, "failed:", e)
if not items:
    sys.exit("No data fetched; keeping the previous tenders.json")
items.sort(key=lambda i: i["deadline"] or "9999")
json.dump({"updated": NOW.isoformat(), "items": items}, open("tenders.json", "w"))
