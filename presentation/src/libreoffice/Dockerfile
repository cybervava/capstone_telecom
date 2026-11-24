# LibreOffice, headless, for rendering .pptx and recalculating .xlsx during checks.
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      libreoffice-impress libreoffice-calc \
      fonts-liberation2 fonts-crosextra-carlito fonts-crosextra-caladea fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /work
ENTRYPOINT ["soffice"]
