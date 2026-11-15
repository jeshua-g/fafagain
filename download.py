#!/usr/bin/env python3
"""
FAF Downloader -- resumable bulk downloader for fuckingfast.co shortlinks.

  ./download.py                 download everything listed in links.txt
  ./download.py --check         report status + total size, change nothing
  ./download.py --sizes         ask every link how big it is, then exit
  ./download.py --jobs 4        run 4 transfers at once (default 3)
  ./download.py --only 'part0*' only files whose name matches the glob
  ./download.py --limit 2       only the first 2 entries
  ./download.py --retry         re-verify every file against the server

Links file, one entry per line:
  https://fuckingfast.co/<id>#<filename>        (raw, exactly as pasted)
  https://fuckingfast.co/<id><TAB><filename>    (already split)

The "#..." fragment is never sent to the server -- it exists only to name the
file locally, so it is stripped before the request and used as the output name.

Per file:
  1. POST /f/<id>/go          -> Hx-Redirect: https://dl.fuckingfast.co/dl/<token>
  2. GET  <token> Range: 0-0  -> Content-Range gives the exact expected size
  3. GET  <token> -C -        -> resumable transfer into "<name>.part"
  4. rename .part -> final name once the byte count matches

Restart it whenever you like: finished files are skipped, partial ones resume.
"""

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
LINKS_FILE = os.path.join(HERE, "links.txt")
SIZES_CACHE = os.path.join(HERE, ".sizes.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

MB, GB = 1024 ** 2, 1024 ** 3

_lock = threading.Lock()
_log = lambda *a: print(*a, flush=True)


def human(n):
    if n is None:
        return "?"
    for unit, size in (("GiB", GB), ("MiB", MB), ("KiB", 1024)):
        if n >= size:
            return "%.2f %s" % (n / size, unit)
    return "%d B" % n


def curl(args, timeout=60):
    """Run curl; return (returncode, stdout, stderr)."""
    cmd = ["curl", "-sS", "--http1.1", "-A", UA] + args
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "", "timed out after %ss" % timeout
    return p.returncode, p.stdout, p.stderr


# ---------------------------------------------------------------- links file

def parse_line(line):
    """-> (url, filename|None). Accepts raw '#name', TAB-split, or bare url."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if "\t" in line:
        url, name = line.split("\t", 1)
        return url.strip(), name.strip() or None
    if "#" in line:
        url, frag = line.split("#", 1)
        return url.strip(), frag.strip() or None
    return line, None


def load_links(path, only=None, limit=None):
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            item = parse_line(line)
            if not item:
                continue
            url, name = item
            if only and not fnmatch.fnmatch(name or url, only):
                continue
            out.append((url, name))
            if limit and len(out) >= limit:
                break
    return out


# ------------------------------------------------------------------ caching

def load_sizes():
    try:
        with open(SIZES_CACHE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_sizes(d):
    tmp = SIZES_CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, indent=1, sort_keys=True)
    os.replace(tmp, SIZES_CACHE)


# ------------------------------------------------------------------ step 1

def mint(url, tries=5):
    """POST /f/<id>/go and return the direct dl.fuckingfast.co URL."""
    pid = urlsplit(url).path.strip("/")
    if not re.fullmatch(r"[A-Za-z0-9]+", pid or ""):
        raise ValueError("not a fuckingfast shortlink: %s" % url)
    go = "https://fuckingfast.co/f/%s/go" % pid
    last = "no response"
    for attempt in range(tries):
        rc, out, err = curl(["-X", "POST", "-D", "-", "-o", "/dev/null",
                             "-H", "HX-Request: true",
                             "-H", "Referer: %s" % url,
                             "--max-time", "30", go])
        m = re.search(r"(?im)^hx-redirect:\s*(\S+)\s*$", out)
        if m:
            return m.group(1).strip()
        first = out.splitlines()[0] if out.splitlines() else err.strip()
        last = first or "rc=%d" % rc
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("no download link after %d tries (%s)" % (tries, last))


# ------------------------------------------------------------------ step 2

def probe_size(dl, tries=3):
    """Ask for one byte; Content-Range carries the total object size."""
    for _ in range(tries):
        rc, out, _ = curl(["-r", "0-0", "-D", "-", "-o", "/dev/null",
                           "--max-time", "30", dl])
        m = re.search(r"(?im)^content-range:\s*bytes\s+\d+-\d+/(\d+)\s*$", out)
        if m:
            return int(m.group(1))
        time.sleep(1.5)
    return None


# ------------------------------------------------------------------ step 3

def transfer(dl, part, expected, log_name):
    """Resumable download into `part`. Returns True when complete."""
    for attempt in range(6):
        if expected and os.path.exists(part) and os.path.getsize(part) >= expected:
            break
        rc, _, err = curl(["-L", "--fail", "-C", "-", "--retry", "4",
                           "--retry-delay", "3", "--retry-all-errors",
                           "--max-time", "0", "-o", part, dl],
                          timeout=7200)
        if rc == 0:
            break
        # 33/36 = resume rejected, 22 = HTTP error, 416-ish: start over
        if rc in (33, 36) or "range" in (err or "").lower():
            if os.path.exists(part):
                os.remove(part)
        if rc == 22 and os.path.exists(part):
            # server refused the token -- caller should re-mint
            return "remint"
        _log("  ! %s: curl rc=%d %s (retry %d)" % (log_name, rc, err.strip()[:90], attempt + 1))
        time.sleep(3 * (attempt + 1))
    else:
        return False

    if not expected:
        return os.path.exists(part)
    return os.path.exists(part) and os.path.getsize(part) == expected


# ------------------------------------------------------------------ worker

def process(entry, sizes, results, idx, total):
    url, name = entry
    label = name or url.rsplit("/", 1)[-1]
    dest = os.path.join(HERE, label) if name else None
    part = dest + ".part" if dest else None

    def note(state, detail=""):
        with _lock:
            results[label] = (state, detail)
        _log("[%2d/%d] %-58s %s%s" % (idx, total, label[:58], state,
                                      ("  " + detail) if detail else ""))

    if not name:
        note("SKIP", "no filename in link -- add '#name' or a TAB+name")
        return False

    # already on disk at the right size? (uses the cached size, no request)
    cached = sizes.get(label)
    if cached and os.path.exists(dest) and os.path.getsize(dest) == cached:
        note("DONE", human(cached))
        return True
    if os.path.exists(dest) and cached is None:
        # size unknown but present from an earlier run -- trust it once probed
        pass

    try:
        dl = mint(url)
        expected = probe_size(dl)
    except Exception as e:
        note("FAIL", str(e)[:110])
        return False

    if expected is None:
        note("FAIL", "could not read object size")
        return False

    with _lock:
        sizes[label] = expected
        save_sizes(sizes)

    have = os.path.getsize(dest) if os.path.exists(dest) else \
        (os.path.getsize(part) if os.path.exists(part) else 0)

    if have == expected:
        if not os.path.exists(dest):
            os.replace(part, dest)
        note("DONE", human(expected))
        return True

    note("GET ", "%s of %s%s" % (human(have), human(expected),
                                 " (resuming)" if have else ""))

    ok = transfer(dl, part, expected, label[:40])
    if ok == "remint":
        # token went stale mid-transfer: mint a fresh one and resume
        try:
            dl = mint(url)
            ok = transfer(dl, part, expected, label[:40])
        except Exception as e:
            ok = False
            _log("  ! re-mint failed: %s" % e)

    if ok is True:
        os.replace(part, dest)
        note("DONE", human(expected))
        return True

    note("FAIL", "incomplete: %s of %s" % (human(os.path.getsize(part)
                                                 if os.path.exists(part) else 0),
                                           human(expected)))
    return False


def preflight(entries, sizes):
    """Mint + probe every link so the total size is known before we commit."""
    def one(e):
        url, name = e
        if not name or sizes.get(name):
            return
        try:
            sz = probe_size(mint(url))
        except Exception as ex:
            _log("  ? %-56s %s" % (name[:56], str(ex)[:70]))
            return
        if sz:
            with _lock:
                sizes[name] = sz
                save_sizes(sizes)
            _log("  %-56s %s" % (name[:56], human(sz)))

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(one, entries))

    known = [sizes[n] for _, n in entries if n and sizes.get(n)]
    free = shutil_free(HERE)
    _log("")
    _log("%d/%d files sized, total %s, free on disk %s"
         % (len(known), len(entries), human(sum(known)), human(free)))
    if free is not None and sum(known) > free:
        _log("WARNING: not enough free space for everything.")
        return 1
    return 0


# ------------------------------------------------------------------- status

def check(entries, sizes):
    done_bytes = need_bytes = 0
    missing = incomplete = complete = 0
    unknown = 0
    for url, name in entries:
        if not name:
            continue
        path = os.path.join(HERE, name)
        have = os.path.getsize(path) if os.path.exists(path) else 0
        psize = os.path.getsize(path + ".part") if os.path.exists(path + ".part") else 0
        exp = sizes.get(name)
        if exp is None:
            unknown += 1
        need_bytes += exp or 0
        if exp and have == exp:
            complete += 1
            done_bytes += have
        elif have or psize:
            incomplete += 1
            done_bytes += have + psize
        else:
            missing += 1
        state = "done" if (exp and have == exp) else \
                "partial %s/%s" % (human(have + psize), human(exp)) if (have or psize) else \
                ("queued (expect %s)" % human(exp)) if exp else "queued (size unknown)"
        _log("  %-58s %s" % (name[:58], state))
    _log("")
    _log("%d files: %d complete, %d partial, %d not started%s" %
         (len(entries), complete, incomplete, missing,
          ", %d size unknown (run once to learn them)" % unknown if unknown else ""))
    _log("on disk now: %s   expected total: %s" % (human(done_bytes), human(need_bytes) or "?"))
    return 0


# ------------------------------------------------------------- downloads

def run_downloads(entries, sizes, opts):
    todo = []
    for e in entries:
        if e[1] and sizes.get(e[1]):
            p = os.path.join(HERE, e[1])
            if os.path.exists(p) and os.path.getsize(p) == sizes[e[1]]:
                continue
        todo.append(e)

    _log("%d links, %d to fetch, %d already complete  (jobs=%d)"
         % (len(entries), len(todo), len(entries) - len(todo), opts.jobs))
    if not todo:
        _log("nothing to do")
        return 0
    _log("")

    results = {}
    stop = threading.Event()

    def watch():
        """While running, print an overall byte-level progress line."""
        while not stop.is_set():
            time.sleep(10)
            if stop.is_set():
                break
            tot = sum(sizes.get(n, 0) for _, n in entries)
            have = 0
            for _, n in entries:
                if not n:
                    continue
                for p in (os.path.join(HERE, n), os.path.join(HERE, n) + ".part"):
                    if os.path.exists(p):
                        have += os.path.getsize(p)
                        break
            fin = sum(1 for v in results.values() if v[0] in ("DONE", "FAIL", "SKIP"))
            pct = (100.0 * have / tot) if tot else 0
            _log("---- %d/%d files · %s of %s (%.1f%%) ----"
                 % (fin, len(entries), human(have), human(tot), pct))

    threading.Thread(target=watch, daemon=True).start()

    fails = 0
    with ThreadPoolExecutor(max_workers=max(1, opts.jobs)) as pool:
        futs = {pool.submit(process, e, sizes, results, i, len(todo)): e
                for i, e in enumerate(todo, 1)}
        for f in as_completed(futs):
            if not f.result():
                fails += 1

    stop.set()
    _log("")
    done = sum(1 for v in results.values() if v[0] == "DONE")
    _log("finished: %d done, %d failed" % (done, fails))
    if fails:
        _log("re-run the same command to retry -- completed files are skipped.")
    return 1 if fails else 0


# ------------------------------------------------------- interactive menu

def disk_state(entries, sizes):
    """-> (bytes on disk, expected total, complete, partial, missing)"""
    have = total = 0
    complete = partial = missing = 0
    for _, name in entries:
        if not name:
            continue
        final = os.path.join(HERE, name)
        part = final + ".part"
        f = os.path.getsize(final) if os.path.exists(final) else 0
        p = os.path.getsize(part) if os.path.exists(part) else 0
        exp = sizes.get(name)
        total += exp or 0
        have += f + p
        if exp and f == exp:
            complete += 1
        elif f or p:
            partial += 1
        else:
            missing += 1
    return have, total, complete, partial, missing


def banner(entries, sizes, pattern, label):
    have, total, complete, partial, missing = disk_state(entries, sizes)
    pct = (100.0 * have / total) if total else 0.0
    free = shutil_free(HERE)

    # what a fresh run would still have to pull
    remaining = 0
    remaining_bytes = 0
    for _, name in entries:
        if not name:
            continue
        exp = sizes.get(name)
        final = os.path.join(HERE, name)
        done = exp and os.path.exists(final) and os.path.getsize(final) == exp
        if not done:
            remaining += 1
            remaining_bytes += exp or 0

    rule = "=" * 60
    _log("")
    _log(rule)
    _log("  FAF Downloader")
    _log("  %s%s" % (label, "" if not pattern else "   filter: %s" % pattern))
    _log(rule)
    _log("  %d files · %s total · %s already on disk (%.0f%%)"
         % (len(entries), human(total), human(have), pct))
    _log("  %d finished · %d part-way · %d untouched"
         % (complete, partial, missing))
    if free is not None:
        _log("  %s free on disk" % human(free))
    _log("-" * 60)
    if remaining:
        _log("   Enter   start / continue   %d file%s, %s to go"
             % (remaining, "s" if remaining != 1 else "", human(remaining_bytes)))
    else:
        _log("   Enter   everything already downloaded")
    _log("   s       file-by-file status")
    _log("   c       choose which files (by name pattern)")
    _log("   q       quit")
    _log(rule)


def as_glob(pat):
    """A bare word matches anywhere in the name; wildcards are used as given.

    People naturally type 'part0' or 'english' and expect a hit, but fnmatch
    anchors at the start, so 'part0*' would miss '..._.part01.rar'.
    """
    if any(c in pat for c in "*?["):
        return pat
    return "*%s*" % pat


def interactive(entries, sizes, opts):
    """Guided mode: a bare run shows a menu instead of pulling 112 GiB."""
    pattern = opts.only
    label = os.path.basename(opts.links)
    while True:
        sel = load_links(opts.links, as_glob(pattern), opts.limit) if pattern else entries
        if not sel:
            _log("nothing matches %r -- clearing the filter" % pattern)
            pattern = None
            continue

        banner(sel, sizes, pattern, label)
        try:
            choice = input("\n> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            _log("")
            return 0

        if choice == "":
            return run_downloads(sel, sizes, opts)
        if choice == "s":
            check(sel, sizes)
            continue
        if choice == "c":
            try:
                ans = input("  which files? (part0, english, fg-selective-german* "
                            "-- Enter = all): ").strip()
            except (EOFError, KeyboardInterrupt):
                _log("")
                return 0
            pattern = ans or None
            continue
        if choice in ("q", "quit", "exit"):
            return 0

        _log("  type Enter, s, c or q")


# --------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Resumable fuckingfast.co downloader")
    ap.add_argument("--links", default=LINKS_FILE, help="links file (default links.txt)")
    ap.add_argument("--jobs", type=int, default=3, help="parallel transfers (default 3)")
    ap.add_argument("--only", help="glob filter on the filename, e.g. 'part0*'")
    ap.add_argument("--limit", type=int, help="only the first N entries")
    ap.add_argument("--check", action="store_true", help="report status, download nothing")
    ap.add_argument("--sizes", action="store_true",
                    help="query every link for its size, then exit")
    ap.add_argument("--retry", action="store_true",
                    help="ignore cached sizes and re-verify every file")
    ap.add_argument("-y", "--yes", action="store_true",
                    help="start downloading immediately, skip the menu")
    opts = ap.parse_args()

    if not os.path.exists(opts.links):
        _log("links file not found: %s" % opts.links)
        return 2

    entries = load_links(opts.links, opts.only, opts.limit)
    if not entries:
        _log("no links in %s" % opts.links)
        return 2

    sizes = {} if opts.retry else load_sizes()

    # free space sanity check against what we already know we need
    known = sum(v for k, v in sizes.items()
                if any(k == n for _, n in entries))
    free = shutil_free(HERE)
    if known and free is not None and known > free:
        _log("NOT ENOUGH SPACE: need ~%s, only %s free" % (human(known), human(free)))
        return 1

    if opts.sizes:
        return preflight(entries, sizes)

    if opts.check:
        return check(entries, sizes)

    # show the menu unless -y was passed; if stdin is not interactive
    # input() hits EOF and the menu exits without downloading anything,
    # so a bare/odd invocation can never start a 112 GiB transfer cold
    if not opts.yes:
        return interactive(entries, sizes, opts)

    return run_downloads(entries, sizes, opts)


def shutil_free(path):
    try:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize
    except OSError:
        return None


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        _log("\ninterrupted -- partial files kept, re-run to resume")
        sys.exit(130)
    except BrokenPipeError:
        # someone piped the output into head/grep and closed early
        try:
            sys.stdout.close()
        except Exception:
            pass
        sys.exit(0)
