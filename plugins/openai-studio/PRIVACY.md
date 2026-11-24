# Privacy

Studio Local is a local tool, with no publisher-operated backend or telemetry.
Key setup and offline runtime installation make no network requests. The key
is typed into a hidden local Terminal prompt and stored as plaintext in an
owner-only settings file in the user's private data directory. It is not
stored in the plugin archive, Git repository, browser storage or conversation.
The plugin does not use macOS Keychain encryption.

Approved model discovery sends the key for authentication to
`https://api.openai.com/v1/models`. Approved generations send only the requested
prompt/text and named input files to the relevant `https://api.openai.com/v1`
endpoint. OpenAI processes those requests under the user's OpenAI account and
applicable service policies. No data is sent to the plugin publisher.

Local history contains operation/model/options, usage, timestamps and output
paths. Prompts and input paths are excluded from that history. Generated files
remain in the private outputs directory. API errors and cost estimates are not
proof of billing; check the OpenAI account dashboard for authoritative usage.

Release packaging uses an explicit source/dependency allowlist. It excludes
local settings, keys, model/account caches, inputs, outputs, runtime folders,
logs and Git configuration. Test fixtures use recognizable fake keys only.

The host conversation may contain prompts, file references and tool results;
this plugin cannot control the host's retention. Do not place secrets in chat.
To remove the saved key, run `bin/studio remove-key` in Terminal. Removing it
preserves history and outputs. Users can separately delete their private data
directory when they want all local data removed. No deletion is performed
automatically during upgrades or release preparation.
