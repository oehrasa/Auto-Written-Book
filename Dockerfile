# toolkit: pdfcon.py / gutenberg_scraper.py / backfill_metadata.py
FROM python:3.11-slim

# - git:   pushes converted books to the Ashurbanipal repo clone
# - curl:  gutenberg_scraper.py shells out to curl for Gutendex (Cloudflare
#          tarpits plain urllib there, see that script's docstring)
# - ca-certificates: needed for both git+https and curl+https to trust certs
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code only. Everything that changes at runtime (PDFs, the Ashurbanipal
# clone, Saved_list.txt, Saved_gutenberg_list.txt, pg_catalog_cache.csv)
# is bind-mounted in via docker-compose.yml, not baked into the image.
COPY pdfcon.py gutenberg_scraper.py Backfill_gutenberg_metadata.py ./

# Run as a non-root user. UID 1000 matches the host user so the
# bind-mounted Ashurbanipal clone stays writable and git's "dubious
# ownership" check is satisfied.
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /home/appuser/.ssh \
    && chown -R appuser:appuser /app
USER appuser

ENV ASHURBANIPAL_REPO=/data/Ashurbanipal
ENV PYTHONUNBUFFERED=1

# No default CMD, these are interactive tools, you pick which script to
# run at `docker compose run` time. See README-docker.md.
ENTRYPOINT ["python"]