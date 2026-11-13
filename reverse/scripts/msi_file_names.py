"""Real file names from an MSI (the File table) for a folder unpacked by 7-Zip.

7-Zip unpacks the MSI streams ('!File', '!_StringPool', '!_StringData') and the CAB,
where the files sit under the keys of the File table. The script reads the File table and
copies the files under their long names.

    python msi_file_names.py <msi streams folder> <cab folder> <output folder>
"""
import shutil
import struct
import sys
from pathlib import Path


def strings(msi: Path):
    pool = (msi / "!_StringPool").read_bytes()
    data = (msi / "!_StringData").read_bytes()
    codepage = struct.unpack_from("<I", pool, 0)[0]
    big = bool(codepage & 0x80000000)          # string references are 3 bytes
    out, pos, i = [""], 0, 4
    while i + 4 <= len(pool):
        ln, refs = struct.unpack_from("<HH", pool, i)
        i += 4
        if ln == 0 and refs:                   # long string: the length is in the next 4 bytes
            ln = struct.unpack_from("<I", pool, i)[0]
            i += 4
        out.append(data[pos:pos + ln].decode("cp1252", "replace"))
        pos += ln
    return out, (3 if big else 2)


def file_table(msi: Path):
    st, sref = strings(msi)
    raw = (msi / "!File").read_bytes()
    # File, Component_, FileName, FileSize(i4), Version, Language, Attributes(i2), Sequence(i2|i4)
    for seq in (2, 4):
        row = sref * 5 + 4 + 2 + seq
        if len(raw) % row:
            continue
        n = len(raw) // row
        cols, off = [], 0
        for w in (sref, sref, sref, 4, sref, sref, 2, seq):
            cols.append([int.from_bytes(raw[off + k * w: off + (k + 1) * w], "little") for k in range(n)])
            off += n * w
        res = []
        for k in range(n):
            key, name = st[cols[0][k]], st[cols[2][k]]
            long_name = name.split("|")[-1]
            res.append((key, long_name, cols[3][k] ^ 0x80000000 if cols[3][k] & 0x80000000 else cols[3][k]))
        if all(r[0] for r in res):
            return res
    raise ValueError("could not parse the File table")


def main(msi_dir, cab_dir, dest):
    msi, cab, dest = Path(msi_dir), Path(cab_dir), Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    seen = {}
    for key, name, size in file_table(msi):
        src = cab / key
        if not src.exists():
            continue
        target = dest / name
        if target.exists():                    # same names (other languages/copies) get a suffix
            seen[name] = seen.get(name, 1) + 1
            target = dest / f"{Path(name).stem}__{seen[name]}{Path(name).suffix}"
        shutil.copy2(src, target)
        print(f"{key} -> {target.name} ({size})")


if __name__ == "__main__":
    main(*sys.argv[1:4])
