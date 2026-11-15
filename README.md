# FAF Downloader

Bulk downloader for the 63 fuckingfast.co shortlinks in `links.txt`
(Cyberpunk 2077 FitGirl repack + bonus + all selective voice-overs).
**Total: 112.2 GiB.**

---

## Easiest: double-click the launcher

There's a **FAF Downloader** entry in your app menu (registered at
`~/.local/share/applications/faf-downloader.desktop`). It opens a terminal
straight into the menu.

## Or from a terminal

```bash
faf                     # launcher script on your PATH
```

```bash
cd "/home/jeshua/FAF Downloader"
./download.py           # same menu, by hand
```

Either way you get:

```
============================================================
  FAF Downloader
  links.txt
============================================================
  63 files · 112.22 GiB total · 0 B already on disk (0%)
  0 finished · 0 part-way · 63 untouched
  628.95 GiB free on disk
------------------------------------------------------------
   Enter   start / continue   63 files, 112.22 GiB to go
   s       file-by-file status
   c       choose which files (by name pattern)
   q       quit
============================================================

>
```

Press **Enter** to start, **q** to walk away. Nothing begins until you press
Enter — and the size is on screen first, so there are no surprises.

### Choosing a subset

Press `c`, then type something that appears in the filename:

| You type | You get |
|---|---|
| `Cyberpunk` | the 23 main game parts (43.9 GiB) |
| `english` | the 3 English voice files (5.6 GiB) |
| `fg-selective` | all 37 voice files (63.7 GiB) |
| `german` | the 4 German voice files |
| `part0` | part01–part09 only |
| *(blank)* | everything |

A plain word matches **anywhere** in the name; if you type a wildcard like
`fg-selective-german*` it's used exactly as written.

---

## Scripted / power use

```bash
./download.py --check             # status without downloading
./download.py --sizes             # ask the server how big each file is
./download.py -y                  # go straight to downloading, no menu
./download.py -y --only 'english' # filtered, no menu
./download.py --jobs 4            # 4 parallel transfers (default 3)
./download.py --retry             # ignore cached sizes, re-verify everything
```

Safe to interrupt (Ctrl-C) and re-run: finished files are skipped, partial
ones resume from where they stopped.

---

## Files

| File | What it is |
|---|---|
| `download.py` | the downloader (executable) |
| `links.txt` | the 63 links, `url<TAB>filename`. Also accepts raw `url#filename` |
| `README.md` | this file |
| `paste.txt` | decrypted FitGirl pastebin — verified byte-identical to `links.txt` |
| `.sizes.json` | known file sizes, so re-runs skip the probing step |
| `download.log` | output of the most recent run |

---

## How it works

1. `POST /f/<id>/go` returns `Hx-Redirect: https://dl.fuckingfast.co/dl/<token>`
2. `GET <token>` with `Range: 0-0` returns `Content-Range` — the exact size
3. `GET <token>` with `-C -` resumes into `<name>.part`
4. once the byte count matches, `.part` is renamed to the final name

The `#filename` fragment in each link is **never sent to the server** — it
only names the file locally, so it's stripped before the request.

`fuckingfast.co` itself sits behind Cloudflare, but the `/go` endpoint and the
`dl.fuckingfast.co` file server don't challenge plain `curl`, so no browser
or cookie handling is needed.

---

## After downloading

`fg-selective-*-vo.bin` are optional — keep only the language(s) you want.
Extract the `.rar` parts in order, then run `setup.exe`.
