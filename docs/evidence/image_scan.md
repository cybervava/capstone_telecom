# Container image scan

Checklist 2: "Container image scanned; dependencies pinned." The scan runs in CI on every push
(job `image` of `.github/workflows/ci.yml`, GitHub Actions): the image is built, saved to a
file and scanned without access to the Docker daemon, and the job fails on a HIGH or CRITICAL
vulnerability that has a fix. Only an image that passes is published, as a release candidate.
The scan was first run by hand on 16 November 2026, before the CI ran anywhere, again by
hand on 19 November with the OpenAI SDK in the image, and on 25 November with the MCP SDK in
it. This is the third run.

| | |
|---|---|
| Image | `skylink-care`, built from this repository's `Dockerfile` (`docker build --pull -t skylink-care:ci .`, as CI builds it), image id `sha256:a38f9240a5e6`, 292 MB (268 MB before the MCP SDK), base `python:3.12-slim-bookworm` (Debian 12.15) |
| Scanner | Trivy 0.57.1 (`aquasec/trivy:0.57.1`), on the image saved to a file |
| Dependencies | Installed from `uv.lock` with `uv sync --locked --no-dev`; the lock file pins 91 packages with hashes, the OpenAI SDK (`openai` 3.52.0) and the MCP SDK (`mcp` 2.3.0, which brought 13 of them) among them, and the project itself |

## What CI asks: HIGH and CRITICAL vulnerabilities that have a fix

```
trivy image --input image.tar --exit-code 1 --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed
```

**None.** Operating-system packages: 0 HIGH, 0 CRITICAL. Python packages: none reported. The
same as on 16 and 19 November, before the MCP SDK was added.

## What that leaves out: vulnerabilities with no fix yet

```
trivy image --input image.tar --scanners vuln --severity HIGH,CRITICAL
```

102 in the base image's operating-system packages (100 HIGH, 2 CRITICAL; 101 on 19 November), none of which
Debian 12 has a fixed package for: the two CRITICAL are `zlib1g` CVE-2023-45853 (Debian: will
not fix) and `libsqlite3-0` CVE-2025-7458. None is in a Python package. Of the platform's own
processes none uses SQLite; the stand-in gateway does, and it is not part of a deployment
against SkyLink's systems. They are what a general-purpose base image carries. A
smaller base for the production image (distroless, or a slim image rebuilt on each release)
would remove most of them; that belongs to the deployment work.

## A fault in the first CI job, found by running it by hand

Trivy also looks for secrets by default. It reports 11 "CRITICAL" findings of the kind
"Google service-account", all in the Anthropic SDK: docstrings and type definitions that
show the shape of a credential as an example
(`anthropic/resources/beta/agents/agents.py`, `anthropic/types/beta/…`). They are not
secrets. With `--exit-code 1` they would have failed the build on its first run for a reason
that has nothing to do with this code. The job scans for vulnerabilities only
(`--scanners vuln`). Secrets in this repository's own files are looked for by gitleaks, over the
whole history, in the job `secret-scan` of the same pipeline.
