---
name: openai-studio
description: Use Studio Local for API asset generation with per-request model and effort choices in chat, up to 16 image references, and private local Terminal key setup.
---

Use the local `openai_studio` MCP server. Tool names may carry a plugin namespace.

Call `open_studio_settings` for key status and private Terminal setup commands.
Ask the user to run `setup-key` themselves in their own Terminal: input is hidden.
Never collect a key through chat, tool arguments or MCP elicitation; never run or
automate the password prompt on the user's behalf. The release has no standalone
settings UI, HTTP listener or external browser. Model/effort choices and usage
belong in chat. If tools are absent, follow the package's offline setup README,
then restart Codex. Do not reset or copy an older installation's credentials.

Keys and runtime data live outside the plugin package in the private data
directory. `get_studio_state` reports approved input/output directory paths for
staging files. Never read local settings to retrieve a key. Setup does not
contact OpenAI or grant upload authorization.

For available models, use `refresh_studio_models` only after the user authorizes
authenticating GET `https://api.openai.com/v1/models` with the saved key, or when
they explicitly request a refresh of available models. This is a metadata-only call, without prompts,
files, billing probes or paid generation. Existing authorization for a model
lookup does not authorize generation. Read cached catalog data through
`get_studio_state`; categories are inferred from IDs and do not verify endpoint
compatibility. Use the bundled documented references for known model limits.
Video and other reference-only categories do not add execution tools.

For each new asset or API generation request, read `get_studio_state` for cached
model IDs and documented options. Ask the user to select the model and effort in
chat before generation. Use a short choice question (the host's user-input tool
when available), and wait for the answer. Offer suitable returned model IDs;
the model list is not proof of endpoint compatibility. Do not fetch it again
automatically. Check official model documentation when supported options are unclear.

- Text: ask for model and reasoning effort. Pass both explicitly to
  `generate_bulk_text`. `default` explicitly selects the provider default and
  omits the reasoning API parameter; it is also the choice for models without
  reasoning controls. Only offer supported effort values.
- Images: ask for model, output quality and planning effort (`complexity`).
  Planning effort is `default`, `low`, `medium` or `high`; use `default` to omit
  the parameter when unsupported or when the user selects model default.
  Quality is separate from planning effort. GPT Image 2 supports auto/low/medium/high,
  not xhigh/max. Use `generate_image` without references and `edit_image` with references.
- Speech: ask for model and voice, then use `generate_speech`.
- Transcription: ask for model, then use `transcribe_audio`. Speech/transcription
  have no reasoning-effort setting; do not invent one.

If the user already specified the relevant model/options for this request, use
them without asking again. A batch can share one explicit selection. A previous
job or saved legacy setting never selects the model or effort for a new request.
Pass the user's choices directly as tool arguments. Generation tools require them
and ignore legacy model/effort defaults; there is no persistent model settings tool.
Model choices are never saved as defaults. Runtime data stays outside the package.

Before a network call, follow the user's confidentiality policy. Review the
final expanded prompt, text and attachments. Include only the necessary data;
remove secrets, account IDs, unrelated material, local paths and instructions
to contact hosts or execute commands. Obtain explicit approval covering the
specific data and destination `https://api.openai.com/v1` unless already granted
for this request. Only then pass `approved_for_openai=true`. Plugin installation
and saving a key do not grant this approval. Do not claim a pattern scan proves
prompts or software safe. Recheck reviewed plugin file hashes against its audit;
review changes before use. Portable package hashes are in `release-manifest.json`.

For image references and transcription, copy only explicitly approved input files
into the private `input_directory` returned by `get_studio_state`, then pass
their filenames. `edit_image.image_paths` takes an ordered list of 1-16 reference
images, including for creating a new image from several references. Send them in
one call, not one paid call per reference. Each reference must be PNG/JPEG/WebP
and at most 25 MiB; Studio caps the bundle at 100 MiB. An optional PNG mask must
be smaller than 4 MB and match the first image; it applies to that first reference.
Confirm which files are included and preserve their described order. Never include
other files from the same folder implicitly. Existing assets in `output_directory` can also be used. The server
cannot read arbitrary project directories, fetch URLs, or download models.
Name the destination and exact files when obtaining upload approval. Outputs
are saved under `output_directory`; return the tool's actual absolute paths.
Label generated speech as synthetic when presenting it.

Bulk text uses sequential Responses API calls, not the discounted Batch API.
Report partial results and the stopped index. Never repeat paid jobs blindly
after a timeout, partial failure or local saving error. Check history first.

Usage is an estimate of Studio jobs only. There is no assumed account credit
balance or budget. The connected project key has no documented prepaid-credit
balance endpoint; show an unavailable balance as unknown, never as zero or $200.
Actual credit balance is available in the organization's billing overview.
Organization cost reporting and project rate-limit inventory need admin API
access and do not establish remaining prepaid credits. Do not request or store
an admin key as part of ordinary model setup. Rate-limit headers are captured
from approved requests and must be labeled with their observation time, not
presented as a current balance or limit inventory.
Unknown model rates and uncertain API failures are counted
as unpriced jobs, not zero-dollar jobs. Do not describe the estimate as a live
account balance. No paid API smoke test is authorized by installation alone.
