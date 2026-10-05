p = "scripts/10b_classify_v2.py"
s = open(p, encoding="utf-8").read()
s = s.replace('yb = pd.to_numeric(col(g, "year_built"), errors="coerce")',
              'yb = pd.to_numeric(col(g, "year_built"), errors="coerce").astype("float64")')
s = s.replace('ya = pd.to_numeric(col(g, "year_abandoned"), errors="coerce")',
              'ya = pd.to_numeric(col(g, "year_abandoned"), errors="coerce").astype("float64")')
s = s.replace('st = col(g, "status").fillna("")', 'st = col(g, "status").astype("object").fillna("")')
s = s.replace("return g[keep.to_numpy()]", "return g[np.asarray(keep.fillna(False), dtype=bool)]")
open(p, "w", encoding="utf-8").write(s)
print("patched")
