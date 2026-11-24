---
name: setup
description: Set up Studio Local's offline macOS runtime and explain its private Terminal API key prompt. Never collect the key in chat.
---

Use `open_studio_settings` for key status and the exact local setup command.
Tell the user to run `setup-key` themselves in their own Terminal. Its input is
hidden. Never ask them to paste a key into chat, an elicitation form or a tool
argument, and never run or automate the password prompt on their behalf.

If the MCP server cannot start, guide them to run the package's
`python3.11 scripts/setup.py` from Terminal first. This installs bundled,
hash-locked wheels offline. It requires Apple Silicon, macOS 13+ and native Python 3.11.
No runtime download, global Python install or settings reset is needed.

The private data directory is `~/Library/Application Support/Studio Local/`.
It holds state, inputs, outputs and the runtime, outside the plugin package.
The key is in an owner-only local settings file; it is not encrypted by this
plugin. Do not read the file to obtain the key. Key setup does not contact
OpenAI or authorize prompt/file uploads. For available models, ask for approval
to authenticate only GET `https://api.openai.com/v1/models` with the saved key.
