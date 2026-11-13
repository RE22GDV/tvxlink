"""Helpers for reading PE files (Windows exe) without Ghidra, as used during the reverse engineering.

  python pe_tools.py strings <exe> <out.txt>         ASCII and UTF-16 strings with file offsets
  python pe_tools.py va <exe> <offset_hex>...        file offset → virtual address
  python pe_tools.py read <exe> w:6702c8 d:6c9800    read at a VA: w — UTF-16 string, a — ASCII, d — double
  python pe_tools.py annotate <exe> <disasm.txt> <from_hex> <to_hex>
        objdump disassembly with the strings signed (push $0x…  ; '*?FR')

Disassembly: objdump -d --no-show-raw-insn <exe> > disasm.txt (Apple/LLVM objdump reads PE i386).
"""
import re
import struct
import sys


def load(exe):
    d = open(exe, "rb").read()
    pe = struct.unpack_from("<I", d, 0x3C)[0]
    nsec = struct.unpack_from("<H", d, pe + 6)[0]
    opt = struct.unpack_from("<H", d, pe + 20)[0]
    base = struct.unpack_from("<I", d, pe + 24 + 28)[0]
    o, secs = pe + 24 + opt, []
    for _ in range(nsec):
        vs, va, rs, ra = struct.unpack_from("<IIII", d, o + 8)
        secs.append((va, vs, ra, rs))
        o += 40
    return d, base, secs


def va2off(base, secs, v):
    rva = v - base
    for va, vs, ra, rs in secs:
        if va <= rva < va + max(vs, rs):
            return ra + rva - va
    return None


def off2va(base, secs, f):
    for va, vs, ra, rs in secs:
        if ra <= f < ra + rs:
            return base + va + f - ra
    return None


def string_at(d, f):
    """String at an offset: UTF-16 (MFC) if the second byte is zero, else ASCII (Delphi)."""
    if f is None or f + 2 > len(d):
        return None
    if d[f + 1] == 0:
        e = f
        while e + 1 < len(d) and d[e:e + 2] != b"\0\0" and e - f < 400:
            e += 2
        try:
            t = d[f:e].decode("utf-16le")
        except UnicodeDecodeError:
            return None
        ok = all(32 <= ord(c) < 127 or 0x400 <= ord(c) <= 0x4FF or c in "\r\n\t" for c in t)
        return t if t and ok else None
    e = d.find(b"\0", f)
    t = d[f:e]
    if 1 < len(t) < 200 and all(32 <= c < 127 or c in (9, 10, 13) for c in t):
        return "A:" + t.decode()
    return None


def cmd_strings(exe, out):
    d = open(exe, "rb").read()
    lines = [f"A {m.start():08x} {m.group().decode()}" for m in re.finditer(rb"[\x20-\x7e]{4,}", d)]
    lines += [f"W {m.start():08x} {m.group().decode('utf-16le')}" for m in re.finditer(rb"(?:[\x20-\x7e]\x00){4,}", d)]
    open(out, "w", encoding="utf-8").write("\n".join(sorted(lines, key=lambda s: int(s.split()[1], 16))))


def main():
    cmd, exe, *rest = sys.argv[1:]
    if cmd == "strings":
        cmd_strings(exe, rest[0])
        return
    d, base, secs = load(exe)
    if cmd == "va":
        for x in rest:
            print(x, hex(off2va(base, secs, int(x, 16)) or 0))
    elif cmd == "read":
        for x in rest:
            k, a = x.split(":")
            f = va2off(base, secs, int(a, 16))
            if k == "d":
                print(a, struct.unpack_from("<d", d, f)[0])
            else:
                print(a, repr(string_at(d, f)))
    elif cmd == "annotate":
        dis, a0, a1 = rest[0], int(rest[1], 16), int(rest[2], 16)
        for line in open(dis, encoding="utf-8", errors="replace"):
            m = re.match(r"\s+([0-9a-f]+):\s+(.*)", line)
            if not m or not a0 <= int(m.group(1), 16) < a1:
                continue
            ins = m.group(2).split("#")[0].rstrip()
            notes = [repr(s) for v in re.findall(r"0x([0-9a-f]{6})\b", ins)
                     if (s := string_at(d, va2off(base, secs, int(v, 16))))]
            print(f"{m.group(1)}: {ins}" + ("  ; " + " ".join(notes) if notes else ""))


if __name__ == "__main__":
    main()
