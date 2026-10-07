from pathlib import Path

NEW = '''def touches_edge(labels, ids, margin=1):
    """True for depressions within `margin` cells of the raster border.

    The fill algorithm treats the border as a drain, so border cells never hold depth: a
    depression whose true extent is cut off by the border shows up one cell inside it.
    """
    h, w = labels.shape
    objs = ndimage.find_objects(labels)
    out = []
    for i in ids:
        rows, cols = objs[i - 1]
        out.append(rows.start <= margin or cols.start <= margin
                   or rows.stop >= h - margin or cols.stop >= w - margin)
    return np.array(out, dtype=bool)'''

p = Path("src/h3lowpoints/classify.py")
s = p.read_text(encoding="utf-8")
start = s.index("def touches_edge(")
end = s.index("\n\n\ndef ", start)
p.write_text(s[:start] + NEW + s[end:], encoding="utf-8")
print("patched classify.py")
