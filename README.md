# FAF Downloader

Bulk downloader for the 63 fuckingfast.co shortlinks in `links.txt`
(Cyberpunk 2077 FitGirl repack + bonus + all selective voice-overs).
**Total: 112.2 GiB.**

## Run it

```bash
cd "/home/jeshua/FAF Downloader"
python3 download.py            # download everything
python3 download.py --check    # what's done / partial / still queued
python3 download.py --sizes    # ask the server how big each file is
```

Safe to interrupt and re-run at any time: finished files are skipped,
partial ones resume from where they stopped.

| Option | Effect |
|---|---|
| `--jobs N` | parallel transfers (default 3, running with 4) |
| `--only 'part0*'` | only files matching that glob |
| `--limit 2` | only the first 2 entries |
| `--retry` | ignore cached sizes and re-verify every file |
| `--links FILE` | use a different links file |

## Files

| File | What it is |
|---|---|
| `links.txt` | the 63 links, `url<TAB>filename`. Also accepts raw `url#filename` |
| `download.py` | the downloader |
| `download.log` | live output of the current run |
| `.sizes.json` | known file sizes (so re-runs skip without contacting the server) |
| `paste.txt` | decrypted copy of the FitGirl pastebin you linked — verified byte-identical to `links.txt` |
| `*.rar.part` | in-flight transfers; renamed to the real name on completion |

## How it works

1. `POST /f/<id>/go` returns `Hx-Redirect: https://dl.fuckingfast.co/dl/<token>`
2. `GET <token>` with `Range: 0-0` returns `Content-Range`, giving the exact size
3. `GET <token>` with `-C -` resumes into `<name>.part`
4. once the byte count matches, `.part` is renamed to the final name

The `#filename` fragment in each link is **never sent to the server** — it only
names the file locally, so it is stripped before the request.

`fuckingfast.co` itself sits behind Cloudflare, but the `/go` endpoint and the
`dl.fuckingfast.co` file server do not challenge plain `curl`, so no browser or
cookie handling is needed.

## After downloading

`fg-selective-*-vo.bin` are optional — keep only the language(s) you want.
Extract the `.rar` parts in order, then run `setup.exe`.
# fafagain
