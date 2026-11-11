"""Build the canonical district list (data/reference/districts.csv).

Source: DataMeet "India - District Map", Census 2011 district boundaries
(https://github.com/datameet/maps, licence CC BY 2.5 India).

Why Census 2011 districts? IDSP reports span 2009-2026, during which many
districts were split or renamed. Using the 641 Census 2011 districts gives one
stable geography for the whole period; newer districts are mapped to their
2011 parent through data/reference/district_aliases.csv.

State names are updated to today's states (Telangana, Ladakh, the merged
Dadra & Nagar Haveli and Daman & Diu), so reports can be matched by state.

This script needs two extra packages that the app itself does not use:
    pip install pyshp shapely
Usage:
    python scripts/build_district_master.py path/to/2011_Dist   (shapefile without extension)
"""

import csv
import sys
from pathlib import Path

import shapefile  # pyshp
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "reference" / "districts.csv"

STATE_FIX = {
    "Andaman & Nicobar Island": "Andaman and Nicobar Islands",
    "Arunanchal Pradesh": "Arunachal Pradesh",
    "Dadara & Nagar Havelli": "Dadra and Nagar Haveli and Daman and Diu",
    "Daman & Diu": "Dadra and Nagar Haveli and Daman and Diu",
    "Jammu & Kashmir": "Jammu and Kashmir",
    "NCT of Delhi": "Delhi",
}
# Census 2011 districts that are now in Telangana (formed 2014) or Ladakh (UT since 2019)
TELANGANA = {"Adilabad", "Nizamabad", "Karimnagar", "Medak", "Hyderabad", "Rangareddy",
             "Mahbubnagar", "Nalgonda", "Warangal", "Khammam"}
LADAKH = {"Kargil", "Leh"}
NAME_FIX = {"Leh (ladakh)": "Leh", "Leh (Ladakh)": "Leh"}

STATE_CODES = {
    "Andaman and Nicobar Islands": "AN", "Andhra Pradesh": "AP", "Arunachal Pradesh": "AR",
    "Assam": "AS", "Bihar": "BR", "Chandigarh": "CH", "Chhattisgarh": "CG",
    "Dadra and Nagar Haveli and Daman and Diu": "DH", "Delhi": "DL", "Goa": "GA",
    "Gujarat": "GJ", "Haryana": "HR", "Himachal Pradesh": "HP", "Jammu and Kashmir": "JK",
    "Jharkhand": "JH", "Karnataka": "KA", "Kerala": "KL", "Ladakh": "LA", "Lakshadweep": "LD",
    "Madhya Pradesh": "MP", "Maharashtra": "MH", "Manipur": "MN", "Meghalaya": "ML",
    "Mizoram": "MZ", "Nagaland": "NL", "Odisha": "OD", "Puducherry": "PY", "Punjab": "PB",
    "Rajasthan": "RJ", "Sikkim": "SK", "Tamil Nadu": "TN", "Telangana": "TS", "Tripura": "TR",
    "Uttar Pradesh": "UP", "Uttarakhand": "UK", "West Bengal": "WB",
}


def main(shp_path: str) -> None:
    reader = shapefile.Reader(shp_path)
    rows = []
    for sr in reader.shapeRecords():
        rec = sr.record.as_dict()
        if int(rec["censuscode"]) == 0:  # polygon labelled "Data Not Available" in the source map
            continue
        name = NAME_FIX.get(rec["DISTRICT"].strip(), rec["DISTRICT"].strip())
        state = STATE_FIX.get(rec["ST_NM"].strip(), rec["ST_NM"].strip())
        if state == "Andhra Pradesh" and name in TELANGANA:
            state = "Telangana"
        if state == "Jammu and Kashmir" and name in LADAKH:
            state = "Ladakh"
        geom = shape(sr.shape.__geo_interface__)
        point = geom.centroid if geom.contains(geom.centroid) else geom.representative_point()
        rows.append({
            "census_code": int(rec["censuscode"]),
            "district_name": name,
            "state_name": state,
            "state_code": STATE_CODES[state],
            "latitude": round(point.y, 4),
            "longitude": round(point.x, 4),
        })
    rows.sort(key=lambda r: (r["state_name"], r["district_name"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} districts to {OUT}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
