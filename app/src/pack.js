// Reading the explorer's data packs (written by app/tools/packs.py).
//
// A pack is gzip of: "H3LP", uint32 format version, uint32 header length, a JSON header, then the
// arrays the header lists, little-endian, each starting on a multiple of 8 bytes.

export const FORMAT = 2;
const READS = [1, 2];          // format 2 adds target_id and target_region

const TYPES = {
  "<i8": BigInt64Array, "<u8": BigUint64Array, "<f4": Float32Array, "<f8": Float64Array,
  "<i4": Int32Array, "<u4": Uint32Array, "|u1": Uint8Array, "|b1": Uint8Array,
};

export async function gunzip(bytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

export function parsePack(bytes) {
  const buf = bytes.byteOffset % 8 === 0 ? bytes : bytes.slice();
  const view = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
  const magic = String.fromCharCode(...buf.subarray(0, 4));
  if (magic !== "H3LP") throw new Error("not an h3-lowpoints pack");
  const version = view.getUint32(4, true);
  if (!READS.includes(version)) throw new Error(`pack format ${version}, this reader reads ${READS.join(" and ")}`);
  const headLength = view.getUint32(8, true);
  const header = JSON.parse(new TextDecoder().decode(buf.subarray(12, 12 + headLength)));
  const body = 12 + headLength;
  const arrays = {};
  for (const s of header.sections) {
    const Type = TYPES[s.dtype];
    if (!Type) throw new Error(`unknown dtype ${s.dtype}`);
    const length = s.shape.reduce((a, b) => a * b, 1);
    arrays[s.name] = new Type(buf.buffer, buf.byteOffset + body + s.offset, length);
    arrays[s.name].shape = s.shape;
  }
  return { header, format: version, ...arrays, n: arrays.id.length };
}

// One pack from the combined file by a range request; `fetchRange(url, start, length)` returns bytes.
export async function loadPack(index, region, baseUrl, fetchRange) {
  const entry = index.packs[region];
  if (!entry) return null;
  const [offset, length] = entry;
  const bytes = await fetchRange(`${baseUrl}/${index.file}`, offset, length);
  return parsePack(await gunzip(bytes));
}

export async function httpRange(url, start, length) {
  const r = await fetch(url, { headers: { Range: `bytes=${start}-${start + length - 1}` } });
  if (r.status !== 206 && r.status !== 200) throw new Error(`HTTP ${r.status} for ${url}`);
  const all = new Uint8Array(await r.arrayBuffer());
  if (r.status === 200) return all.subarray(start, start + length);   // server ignored the range
  if (all.length !== length) throw new Error(`expected ${length} bytes, got ${all.length}`);
  return all;
}
