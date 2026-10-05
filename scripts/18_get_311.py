import pandas as pd
import requests

BBOX = (-97.76, 30.29, -97.72, 30.32)
where = ("upper(sr_type_desc) like '%FLOOD%' OR upper(sr_type_desc) like '%STANDING WATER%' "
         "OR upper(sr_type_desc) like '%DRAIN%' OR upper(sr_type_desc) like '%STORM%'")
r = requests.get("https://data.austintexas.gov/resource/xwdj-i9he.json",
                 params={"$where": where, "$limit": 200000}, timeout=600)
r.raise_for_status()
df = pd.DataFrame(r.json())
print(len(df), "rows; columns:", list(df.columns))
print("\nticket types:\n", df["sr_type_desc"].value_counts().head(25).to_string())
df.to_csv("data/austin311_drainage_raw.csv", index=False)

latc = [c for c in df.columns if "lat" in c.lower()]
lonc = [c for c in df.columns if "lon" in c.lower()]
datec = [c for c in df.columns if "creat" in c.lower() or "date" in c.lower()]
print("\nlat/lon/date columns found:", latc, lonc, datec)
if latc and lonc:
    la = pd.to_numeric(df[latc[0]], errors="coerce")
    lo = pd.to_numeric(df[lonc[0]], errors="coerce")
    inside = df[(lo >= BBOX[0]) & (lo <= BBOX[2]) & (la >= BBOX[1]) & (la <= BBOX[3])].copy()
    print("\nin pilot box:", len(inside))
    print(inside["sr_type_desc"].value_counts().to_string())
    if datec:
        inside["year"] = pd.to_datetime(inside[datec[0]], errors="coerce").dt.year
        print("\nby year:\n", inside["year"].value_counts().sort_index().to_string())
