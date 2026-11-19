# Container image scan

Checklist 2: "Container image scanned; dependencies pinned." The scan job is in
`.gitlab-ci.yml` and has never run, because where the repository lives and which CI runs it
is open (decision D6). This is the same scan run by hand on 16 November 2026, with the scanner
version the CI file names.

| | |
|---|---|
| Image | `skylink-care`, built from this repository's `Dockerfile` at M4 slice E (`docker build -t skylink-care:m4 .`), image id `sha256:687ccb9c3ce6`, 251 MB, base `python:3.12-slim-bookworm` (Debian 12.15) |
| Scanner | Trivy 0.57.1 (`aquasec/trivy:0.57.1`), on the image saved to a file: the scanner was given no access to the Docker daemon |
| Dependencies | Installed from `uv.lock` with `uv sync --locked --no-dev`; the lock file then pinned 77 packages with hashes, and the project itself. The OpenAI SDK (`openai` 3.52.0) has been added since, making 78; the image has not been scanned again |

## What the CI job asks: HIGH and CRITICAL vulnerabilities that have a fix

```
trivy image --input image.tar --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed
```

**None.** Operating-system packages: 0 HIGH, 0 CRITICAL. Python packages: none reported.

## What that leaves out: vulnerabilities with no fix yet

```
trivy image --input image.tar --scanners vuln --severity HIGH,CRITICAL
```

101 in the base image's operating-system packages (99 HIGH, 2 CRITICAL), none of which
Debian 12 has a fixed package for: the two CRITICAL are `zlib1g` CVE-2023-45853 (Debian: will
not fix) and `libsqlite3-0` CVE-2025-7458. None is in a Python package. Of the platform's own
processes none uses SQLite; the stand-in gateway does, and it is not part of a deployment
against SkyLink's systems. They are what a general-purpose base image carries. A
smaller base for the production image (distroless, or a slim image rebuilt on each release)
would remove most of them; that belongs to the deployment work.

## A fault in the CI job, found by running it

Trivy also looks for secrets by default. It reports 11 "CRITICAL" findings of the kind
"Google service-account", all in the Anthropic SDK: docstrings and type definitions that
show the shape of a credential as an example
(`anthropic/resources/beta/agents/agents.py`, `anthropic/types/beta/…`). They are not
secrets. With `--exit-code 1` they would have failed the build on its first run for a reason
that has nothing to do with this code. The job now scans for vulnerabilities only
(`--scanners vuln`). Secrets in this repository's own files are looked for by hand, in the diff, before each commit;
there is no pre-commit hook, and the CI secret scan (gitleaks) has never run.
