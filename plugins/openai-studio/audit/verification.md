# Portable release verification — 24 November 2026

Studio Local 1.1.1 is a separate portable Apple Silicon/macOS package. The
original local installation, saved key, history and generated files are untouched.
No key, state, input/output or account catalog was read to construct this release.

The base source was copied from six explicitly reviewed public files; version
1.0.0 revision chat-choices-and-multiple-references-2026-11-24. Upstream local
skill files remain unchanged. Portable workflow instructions were revised in
the separate package. Source/dependency hashes are in release-manifest.json.

Reviewed entry points: bin/studio, scripts/setup.py, scripts/setup_key.py,
server/launch.py, server/entry.py and server/mcp_server.py. The local storage
helper is the only new production import. It uses standard-library file locking,
JSON and atomic owner-only writes. There are no shell evaluations of prompts,
arbitrary command tools, URL asset downloads, telemetry or runtime code fetching.

Production SDK destination/approval guards and generation accounting remain as
reviewed. The package's only credential entry is the user's own getpass Terminal
prompt; no secret tool input, UI or MCP elicitation is exposed. Key storage is
plaintext with 0600 permissions, outside the package, not Keychain encryption.

Service, portable and release-tool tests passed offline with fake keys and mocked
API transport. They cover required per-request choices, one-request multipart arrays,
1/16 reference boundaries, invalid files/counts/size, key redaction, metadata-only
authorization, no assumed balance, safe partial failures, accounting, private
state preservation, symlink rejection, prompt refusal for pipes/unmasked fallback,
and stdio discovery (8 tools, no UI resources). Path regression checks cover dot
segments resolving to home/root, case aliases and data paths overlapping the plugin. Release
tests also check repository documents for credential-like text and personal
paths, and reject symlink/outside-root exports. These scans supplement the
allowlist; they do not prove all confidential data is absent.

Public PyPI wheel acquisition used a separate reviewed sandbox with a clean
environment, fixed pypi.org/files.pythonhosted.org HTTPS destinations, verified
CA certificates, no redirects and no access to operational user data. Thirty-four
Apple Silicon wheels are pinned with SHA-256 hashes; provenance is recorded in
audit/dependencies.json. No source builds or dependency upgrades were performed.
Intel support was excluded because cryptography 50.0.2 has no Intel macOS wheel.

The icon is a locally typeset lowercase sl mark reviewed with antislop 3.2.24
during mode. SVG outlines are rendered offline at 1024/256/64px. Light, dark
and monochrome variants were inspected at 16/24/32/64/128px. Measured contrast
ratios range from 6.54:1 to 17.23:1. See DESIGN.md and audit/logo-gate.md for
decision reasons and the complete delivery gate. No asset or prompt was uploaded.

Further release checks cover clean offline installation, launcher hashes,
export allowlists, ZIP integrity, SHA-256 checksums and private-file canaries.
Each verifier run uses and removes a fresh temporary directory. Results and the
actual executed test count are recorded in dist/verification.json after export.
Pattern scans supplement allowlisting; neither proves absence of vulnerabilities
or all possible secrets. No live API generation or real key entry is used for
release testing. Native Intel execution and human Terminal entry are untested.
