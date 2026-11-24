# Publishing Studio Local

Studio Local is distributed through this Git marketplace repository.

The build process only prepares files. Git publication and an official directory
submission are separate actions. A Git marketplace is not directory approval.

## Git marketplace

1. Review the source, MIT license, included third-party notices and release
   checksums. Confirm the approved public export list before uploading.
2. Push only this
   marketplace repository. Never push the original development/installed plugin
   directories or the private Application Support data directory.
3. Users can add an extracted marketplace checkout using
   `codex plugin marketplace add .`,
   restart the desktop app and install Studio Local from the marketplace.
4. If using a branch other than the host's default, supply `--ref BRANCH`.
5. Attach the plugin ZIP and SHA-256 checksums to a tagged release when publishing
   a downloadable version. Never include the private build workspace.

Keep this first release marked Apple Silicon/macOS 13+/Python 3.11. Do not
advertise Intel support: a pinned dependency lacks an Intel macOS wheel.

## Official directory

The local stdio MCP path currently requires approved-partner eligibility and
an allowlisted OpenAI organization. Confirm that with OpenAI before submission.
Then complete verified publisher identity, product/support/privacy/terms URLs,
review test cases and a walkthrough. The package includes icons;
hosted policy pages, verified portal identity and review credentials
still need the real submission process.

Upload the plugin ZIP through the approved desktop-only `Skills only` path,
resolve findings, submit for review and publish after approval. Do not upload a
real user's API key, history or assets as reviewer data.

References checked 24 November 2026:

- https://developers.openai.com/plugins/build/plugins
- https://developers.openai.com/plugins/deploy/desktop-only
- https://developers.openai.com/plugins/deploy/submission
- https://developers.openai.com/plugins/build/mcp-server#tool-annotations-and-elicitation

## Updates

Review changes and dependencies; bump the release version when publishing a new
package. Update runtime version paths together with the manifests. Run the
offline tests, build from the allowlist, verify ZIP contents/checksums and test a
clean offline install. No self-update or runtime dependency download is used.
