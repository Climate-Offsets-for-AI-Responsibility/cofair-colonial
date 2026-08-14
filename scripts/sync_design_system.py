#!/usr/bin/env python3
"""Vendor the COFAIR design system (and the dashboard's JS/font deps) into
`dashboard/vendor/`.

The dashboard is a zero-build static page published straight from `dashboard/`
by Netlify, so it cannot resolve `@cofair/ui` at runtime the way the React SPAs
do. Instead it consumes the design system's compiled stylesheet and uses the
same `cofair-*` BEM class names, which works because the design system is
CSS-variables + BEM (hub decision D22) and its React components only apply
those classes.

Everything written here is a copy — never hand-edit `dashboard/vendor/`.
Fonts and Chart.js are vendored rather than pulled from a CDN so the page makes
no third-party requests (same privacy concern as hub decision D47).

Usage:
  python3 scripts/sync_design_system.py            # refresh the vendored copies
  python3 scripts/sync_design_system.py --check    # exit 1 if anything is stale
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR_DIR = REPO_ROOT / "dashboard" / "vendor"
FONTS_DIR = VENDOR_DIR / "fonts"

DESIGN_SYSTEM = REPO_ROOT.parent / "cofair-design-system"
NODE_MODULES = REPO_ROOT / "node_modules"

BANNER = (
    "/* Vendored from @cofair/ui — do not edit.\n"
    " * Regenerate: python3 scripts/sync_design_system.py\n"
    " */\n"
)

# (family, weight, fontsource package, file stem) — latin subset only.
FONTS = [
    ("IBM Plex Sans", "400", "ibm-plex-sans", "ibm-plex-sans-latin-400-normal"),
    ("IBM Plex Sans", "500", "ibm-plex-sans", "ibm-plex-sans-latin-500-normal"),
    ("IBM Plex Sans", "600", "ibm-plex-sans", "ibm-plex-sans-latin-600-normal"),
    ("IBM Plex Serif", "600", "ibm-plex-serif", "ibm-plex-serif-latin-600-normal"),
    ("IBM Plex Mono", "400", "ibm-plex-mono", "ibm-plex-mono-latin-400-normal"),
    ("IBM Plex Mono", "500", "ibm-plex-mono", "ibm-plex-mono-latin-500-normal"),
]

# Brand marks, straight from the design system's asset folder.
LOGOS = [
    "cofair-bw-lockup-horizontal.svg",
    "cofair-bw-mark-square.svg",
    "cofair-color-logo.svg",
]

SCRIPTS = [
    (NODE_MODULES / "chart.js/dist/chart.umd.min.js", "chart.umd.min.js"),
    (
        NODE_MODULES
        / "chartjs-adapter-date-fns/dist/chartjs-adapter-date-fns.bundle.min.js",
        "chartjs-adapter-date-fns.bundle.min.js",
    ),
]


class Stale(Exception):
    """A vendored file differs from its source."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def emit(dest: Path, data: bytes, check: bool, changed: list[str]) -> None:
    """Write `data` to `dest`, or record it as stale in --check mode."""
    current = dest.read_bytes() if dest.exists() else None
    if current is not None and digest(current) == digest(data):
        return
    rel = dest.relative_to(REPO_ROOT)
    changed.append(str(rel))
    if check:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    print(f"  wrote {rel}")


def build_fonts_css() -> str:
    """Hand-written @font-face rules.

    The design system's own `fonts.css` @imports bare `@fontsource/*`
    specifiers, which only resolve through a bundler — a static page needs
    explicit `url()`s.
    """
    blocks = [
        "/* IBM Plex, self-hosted (latin subset). Vendored — do not edit.\n"
        " * Regenerate: python3 scripts/sync_design_system.py\n"
        " * Sans = body/UI · Serif = headings · Mono = data.\n"
        " */"
    ]
    for family, weight, _pkg, stem in FONTS:
        blocks.append(
            "@font-face {\n"
            f'  font-family: "{family}";\n'
            "  font-style: normal;\n"
            f"  font-weight: {weight};\n"
            "  font-display: swap;\n"
            f'  src: url("fonts/{stem}.woff2") format("woff2");\n'
            "}"
        )
    return "\n\n".join(blocks) + "\n"


def sync(check: bool) -> int:
    if not DESIGN_SYSTEM.exists():
        print(
            f"error: {DESIGN_SYSTEM} not found — clone cofair-design-system as a sibling",
            file=sys.stderr,
        )
        return 1

    styles = DESIGN_SYSTEM / "dist" / "styles.css"
    if not styles.exists():
        print(
            f"error: {styles} not found — run `npm run build` in cofair-design-system",
            file=sys.stderr,
        )
        return 1

    changed: list[str] = []

    emit(
        VENDOR_DIR / "cofair-ui.css",
        BANNER.encode() + styles.read_bytes(),
        check,
        changed,
    )

    missing: list[Path] = []
    for _family, _weight, pkg, stem in FONTS:
        src = DESIGN_SYSTEM / "node_modules" / "@fontsource" / pkg / "files" / f"{stem}.woff2"
        if not src.exists():
            missing.append(src)
            continue
        emit(FONTS_DIR / f"{stem}.woff2", src.read_bytes(), check, changed)
    if missing:
        print(
            "error: missing font files — run `npm install` in cofair-design-system:\n"
            + "\n".join(f"  {p}" for p in missing),
            file=sys.stderr,
        )
        return 1

    emit(VENDOR_DIR / "fonts.css", build_fonts_css().encode(), check, changed)

    for name in LOGOS:
        src = DESIGN_SYSTEM / "src" / "assets" / name
        if not src.exists():
            print(f"error: {src} not found", file=sys.stderr)
            return 1
        emit(VENDOR_DIR / name, src.read_bytes(), check, changed)

    for src, name in SCRIPTS:
        if not src.exists():
            print(
                f"error: {src} not found — run `npm install` in cofair-colonial",
                file=sys.stderr,
            )
            return 1
        emit(VENDOR_DIR / name, src.read_bytes(), check, changed)

    if check:
        if changed:
            print(
                "Vendored design-system files are stale:\n"
                + "\n".join(f"  - {c}" for c in changed)
                + "\n\nRun: python3 scripts/sync_design_system.py",
                file=sys.stderr,
            )
            return 1
        print("Vendored design-system files are up to date.")
        return 0

    if not changed:
        print("Already up to date.")
    else:
        print(f"Synced {len(changed)} file(s) from @cofair/ui.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check",
        action="store_true",
        help="verify the vendored copies match their sources; exit 1 if stale",
    )
    args = ap.parse_args()
    return sync(args.check)


if __name__ == "__main__":
    sys.exit(main())
