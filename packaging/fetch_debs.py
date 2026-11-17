#!/usr/bin/env python3
"""Download Ubuntu 22.04 (jammy) .deb packages and unpack their shared libraries into one folder.

Used by build_appimage.sh for the few X11/XCB helper libraries Qt's xcb plugin needs that stock distros often lack.
Pulling them from a fixed old release (not from whatever the build machine has) keeps the bundle's glibc needs low.
    fetch_debs.py <dest-dir> pkg [pkg ...]
"""
import io, lzma, os, re, subprocess, sys, tempfile, urllib.request

BASE = "http://archive.ubuntu.com/ubuntu"
SUITES = ("jammy-updates", "jammy")
COMPS = ("main", "universe")


def index() -> dict:
    pk = {}
    for suite in SUITES:                       # updates first: they win
        for comp in COMPS:
            url = f"{BASE}/dists/{suite}/{comp}/binary-amd64/Packages.xz"
            data = lzma.decompress(urllib.request.urlopen(url, timeout=60).read()).decode("utf-8", "replace")
            for block in data.split("\n\n"):
                m = re.search(r"^Package: (\S+)", block, re.M)
                f = re.search(r"^Filename: (\S+)", block, re.M)
                if m and f and m.group(1) not in pk:
                    pk[m.group(1)] = f.group(1)
    return pk


def main() -> int:
    dest, names = sys.argv[1], sys.argv[2:]
    os.makedirs(dest, exist_ok=True)
    pk = index()
    missing = [n for n in names if n not in pk]
    if missing:
        print("not found in jammy:", ", ".join(missing), file=sys.stderr)
        return 1
    for n in names:
        url = f"{BASE}/{pk[n]}"
        with tempfile.TemporaryDirectory() as t:
            deb = os.path.join(t, "p.deb")
            with open(deb, "wb") as fh:
                fh.write(urllib.request.urlopen(url, timeout=120).read())
            out = os.path.join(t, "x")
            os.makedirs(out)
            subprocess.run(["dpkg-deb", "-x", deb, out], check=True)
            for root, _d, files in os.walk(out):
                for f in files:
                    if ".so" in f:
                        src = os.path.join(root, f)
                        dst = os.path.join(dest, f)
                        if os.path.islink(src):
                            if os.path.lexists(dst):
                                os.remove(dst)
                            os.symlink(os.readlink(src), dst)
                        else:
                            with open(src, "rb") as a, open(dst, "wb") as b:
                                b.write(a.read())
        print("fetched", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
