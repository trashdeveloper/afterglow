# Upload the Afterglow results gallery (2 steps)

`gallery/index.html` is a single self-contained file: inline CSS/JS, zero
external assets, no `fetch()` — it works over `file://` and on any static host.

## Step 1 — preview locally (optional but recommended)

```bash
cd gallery && python3 -m http.server 8137
# open http://localhost:8137/index.html
```

## Step 2 — upload

Upload **just `index.html`** to your hosting (any static host, S3 bucket,
GitHub Pages, Netlify Drop, shared webspace…) as e.g.
`https://YOUR-HOST/afterglow/index.html`. No build step, no sibling files,
no server-side code. Re-upload the file whenever `eval/results.md` changes.

That's it — done. Nothing to commit in this repo for the gallery itself.
