# Security boundaries

- Enter API keys only through the user's own hidden Terminal prompt. No MCP
  setter, secret elicitation, UI bridge, environment key or `.env` loader exists.
- Key files are plaintext with owner-only permissions, not encrypted storage.
  Other software running as the same user may have access. File permissions do
  not provide isolation from a compromised user account.
- Generation requires consent covering the specific prompt/attachments and the
  OpenAI API destination. Model discovery is a separate read-only authorization.
- Package and selected SDK source hashes are checked at launch. Setup installs
  only bundled wheels with exact pins/hashes, no network or source build scripts.
  Local hash manifests are change detection, not publisher signatures.
- Production sandbox writes exclude package code and the runtime. It permits
  TCP 443 because macOS Seatbelt cannot filter arbitrary remote hostnames. The
  application request hook limits destinations to api.openai.com with verified
  HTTPS, no redirects/proxies. Its hostname boundary is application-level.
- The included macOS native dependencies have public PyPI provenance and pinned
  hashes. Their complete source/native implementations have not been audited.
  Review and sandboxing do not guarantee vulnerability-free dependencies.
- The public-wheel downloader is a maintainer command, never invoked by setup
  or generation. It has a clean environment, fixed PyPI hosts, TLS verification,
  no redirects and a restricted filesystem sandbox.

Do not place private credentials or exploit payloads in a public issue. Before
public publication, the publisher should provide a private reporting contact
and the required support/privacy/terms website URLs.
