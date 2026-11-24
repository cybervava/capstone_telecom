#!/usr/bin/env bash
# Render an Office file (.pptx, .xlsx) to PDF with LibreOffice in a container, for checking how
# it looks and for the PDF copy of the deck. The image is built once:
#
#   docker build -t skylink-presentation-lo:1 presentation/src/libreoffice
#
#   presentation/src/office_to_pdf.sh presentation/G3_Telco_Presentation.pptx [outdir]
#
# Not needed to edit the deck: PowerPoint's own "Save as PDF" gives the same result.
set -euo pipefail
file="$(realpath "$1")"
out="$(realpath "${2:-$(dirname "$file")}")"
dir="$(dirname "$file")"
docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "$dir":/in:ro -v "$out":/out skylink-presentation-lo:1 \
  -env:UserInstallation=file:///tmp/lo-profile \
  --headless --convert-to pdf --outdir /out "/in/$(basename "$file")" > /dev/null 2>&1
[ -f "$out/$(basename "${file%.*}").pdf" ] || { echo "LibreOffice produced no PDF for $file" >&2; exit 1; }
echo "$out/$(basename "${file%.*}").pdf"
