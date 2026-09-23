# Running these in Docker

## Layout expected on the host

```
your-project/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .dockerignore
├── pdfcon.py
├── gutenberg_scraper.py
├── Backfill_gutenberg_metadata.py
├── input_folder/              # PDFs for pdfcon.py go here
├── Saved_list.txt             # touch these three if they don't exist yet
├── Saved_gutenberg_list.txt
└── pg_catalog_cache.csv
../Ashurbanipal/               # separate clone, sibling to your-project/
```

## One-time setup

```bash
touch Saved_list.txt Saved_gutenberg_list.txt pg_catalog_cache.csv
docker compose build
```

Push authentication uses SSH via the host's agent socket (forwarded by
docker-compose.yml) — no private keys and no credential files ever enter
the container, and `~/.git-credentials` is not mounted at all. The image
runs as a non-root user (UID 1000, matching your host UID).

Make sure the Ashurbanipal clone's remote is the SSH form (not HTTPS,
which would re-require the credential store):

```bash
git -C ../Ashurbanipal remote set-url origin git@github.com:you/Ashurbanipal.git
```

Verify the host can already push over SSH before running the container:

```bash
ssh -T git@github.com
```

The compose file needs `SSH_AUTH_SOCK` set (an agent with your key
loaded). On a normal desktop session it's already exported; if not:

```bash
eval "$(ssh-agent -s)" && ssh-add
```

`~/.gitconfig` and `~/.ssh/known_hosts` are mounted read-only so commits
are authored correctly and GitHub's host key is verified. Because the
container runs as the same UID as you, git won't complain about "dubious
ownership" and no `safe.directory` fix is needed.

## Running each script

All three are interactive (`console.input()` prompts), so always use
`run`, not `up`, and keep stdin attached:

```bash
docker compose run --rm converter python pdfcon.py
docker compose run --rm converter python gutenberg_scraper.py
docker compose run --rm converter python Backfill_gutenberg_metadata.py
```

## Notes

- The image only bakes in the three `.py` files and their pip deps. PDFs,
  the Ashurbanipal clone, and the saved-list/catalog-cache state all live
  on the host via bind mounts, so nothing is lost on rebuild.
- `pdfcon.py`'s `ensure_data_repo_exists()` checks `ASHURBANIPAL_REPO` (set
  in the Dockerfile/compose file to `/data/Ashurbanipal`) — as long as the
  volume mount above points at your real clone, this just works.
- If you rename any of the three scripts, update both the `COPY` line in
  the Dockerfile and the filenames in the `run` commands above.
- Rebuild (`docker compose build`) only when `requirements.txt` or the
  `.py` files change the mounted state doesn't require a rebuild.
- Your repo already has its own `requirements.txt` and `README.md` —
  merge the generated `requirements.txt` into yours (don't just overwrite
  it) and keep this file as `README-docker.md` so it doesn't collide with
  your existing `README.md`.
- `fuckywucky.html` gets rewritten by `pdfcon.py` on every import
  (`console.save_html(...)` runs at module load, not inside `__main__`) —
  it'll reappear inside the container's `/app` each run; it's already
  excluded from the build context in `.dockerignore` so it won't get
  baked into the image.
