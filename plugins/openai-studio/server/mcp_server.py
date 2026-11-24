"""Local stdio tools with private Terminal key setup and approved OpenAI jobs."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import io
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
from typing import Any
import uuid
import wave

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import Settings
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from openai import OpenAI, APIStatusError, APIConnectionError

PACKAGE = Path(__file__).resolve().parent
sys.path.insert(0, str(PACKAGE))
from storage import data_directory

DATA = data_directory()
ROOT = DATA / "state"
SETTINGS = ROOT / "settings.json"
INPUTS = DATA / "inputs"
OUTPUTS = DATA / "outputs"
PLACEHOLDER = "YOUR_OPENAI_API_KEY_HERE"
MODEL_FIELDS = {"image_model", "tts_model", "transcribe_model", "text_model"}
EDITABLE = MODEL_FIELDS | {"image_size", "image_quality", "tts_voice"}
EXTRA_DEFAULTS = {"image_output_format": "png", "image_background": "auto",
                  "tts_format": "wav", "tts_speed": "1", "transcribe_language": "",
                  "text_max_output_tokens": "2048", "text_reasoning_effort": "",
                  "text_verbosity": ""}
EDITABLE |= EXTRA_DEFAULTS.keys()
GROUPS = ("images", "speech", "transcription", "video", "responses", "realtime",
          "audio_other", "embeddings", "moderation", "other")
VOICES = {"alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx",
          "sage", "shimmer", "verse", "marin", "cedar"}
LOCK = threading.RLock()

# FastMCP's default Settings reads .env. Disable that source before construction.
Settings.model_config = {**Settings.model_config, "env_file": None}
mcp = FastMCP("Studio Local", log_level="CRITICAL", instructions=(
    "Open open_studio_settings for private local Terminal key setup instructions. Never request an API key in chat or elicitation, and never execute setup-key on behalf of the user. "
    "Before each new generation, ask the user to select a model and supported reasoning effort or image quality/planning effort in chat, unless supplied for that request. Never reuse saved model defaults. "
    "For image references, edit_image accepts an ordered list of 1-16 approved files. "
    "Keep API keys out of chat. refresh_studio_models authenticates a user-approved, read-only model lookup. Never assume an account balance. Generation tools send only explicitly approved data "
    "to https://api.openai.com/v1; approved_for_openai must reflect user consent."
))
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
GENERATE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)


@contextmanager
def locked():
    with LOCK:
        fd = os.open(ROOT / ".settings.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield


def load_settings() -> dict:
    try:
        if SETTINGS.is_symlink():
            raise ValueError
        with SETTINGS.open(encoding="utf-8") as handle:
            state = json.load(handle)
        if not isinstance(state, dict) or not isinstance(state.get("history"), list):
            raise ValueError
        spend = state["estimated_spend_usd"]
        if isinstance(spend, bool) or not isinstance(spend, (int, float)) or not math.isfinite(spend) or spend < 0:
            raise ValueError
        for key in EDITABLE | {"api_key"}:
            if key in EXTRA_DEFAULTS and key not in state:
                state[key] = EXTRA_DEFAULTS[key]
            if not isinstance(state.get(key), str):
                raise ValueError
        return state
    except (OSError, ValueError, KeyError):
        raise ToolError("Studio settings could not be read. Repair settings.json locally; it has not been reset.") from None


def save_settings(state: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".settings-", dir=ROOT)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, SETTINGS)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def public_state(state: dict) -> dict:
    key = state.get("api_key", "")
    spend = state["estimated_spend_usd"]
    return {
        "configuration_mode": "per_request",
        "input_directory": str(INPUTS),
        "output_directory": str(OUTPUTS),
        "api_key_configured": bool(key and key != PLACEHOLDER),
        "estimated_spend_usd": spend,
        "credit_budget_usd": None,
        "estimated_remaining_usd": None,
        "billing": {"balance_usd": None, "status": "unavailable_via_project_key",
                    "message": "A standard API key has no documented endpoint for remaining prepaid credits. View your organization's billing overview for the actual balance. Studio's local usage estimate is not a balance.",
                    "url": "https://platform.openai.com/settings/organization/billing/overview"},
        "catalog": read_cache("model_catalog.json", {"fetched_at": None, "models": [],
                     "groups": {group: [] for group in GROUPS}}),
        "observed_rate_limits": read_cache("observed_limits.json", {}),
        "model_reference": read_cache("model_reference.json", {"groups": {}, "models": {}}),
        "account_limits_note": "Project rate-limit inventory requires an organization admin key. Observed headers below are from actual requests and may be stale; the model list does not expose account limits.",
        "unpriced_generations": state.get("unpriced_generations", 0),
        "history": state["history"][-30:][::-1],
        "usage_note": "Estimated usage from Studio jobs only, not your OpenAI balance. Excludes other apps, taxes and unknown charges.",
    }


@mcp.tool(annotations=READ)
def get_studio_state() -> dict[str, Any]:
    """Read cached model IDs/options, recent jobs and local usage estimates without network access. Generation choices are per request; never returns the API key."""
    with locked():
        return public_state(load_settings())


def validate_options(settings: dict[str, str]) -> dict[str, str]:
    if set(settings) - EDITABLE:
        raise ToolError("Unsupported request option.")
    cleaned = {}
    for field, value in settings.items():
        if not isinstance(value, str):
            raise ToolError("Request options must be strings.")
        value = value.strip()
        if field in MODEL_FIELDS and (value.startswith("sk-") or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}", value)):
            raise ToolError("Use a model ID, not a URL or path.")
        if field == "image_size" and value != "auto" and not re.fullmatch(r"[1-9][0-9]{1,4}x[1-9][0-9]{1,4}", value):
            raise ToolError("Image size must be WIDTHxHEIGHT or auto; model support varies.")
        if field == "image_quality" and value not in {"auto", "low", "medium", "high", "xhigh", "max", "standard", "hd"}:
            raise ToolError("Select an available image quality.")
        if field == "tts_voice" and value not in VOICES:
            raise ToolError("Select a supported built-in voice.")
        choices = {"image_output_format": {"png", "jpeg", "webp"},
                   "image_background": {"auto", "opaque", "transparent"},
                   "tts_format": {"wav", "mp3", "opus", "aac", "flac", "pcm"},
                   "text_reasoning_effort": {"", "none", "minimal", "low", "medium", "high", "xhigh", "max"},
                   "text_verbosity": {"", "low", "medium", "high"}}
        if field in choices and value not in choices[field]:
            raise ToolError("Select a supported option for " + field + ".")
        if field == "tts_speed":
            try:
                speed = float(value)
            except ValueError:
                raise ToolError("Speech speed must be 0.25-4.") from None
            if not math.isfinite(speed) or not .25 <= speed <= 4:
                raise ToolError("Speech speed must be 0.25-4.")
        if field == "text_max_output_tokens" and (not value.isdigit() or not 16 <= int(value) <= 128000):
            raise ToolError("Studio output-token cap must be 16-128000; model limits may be lower.")
        if field == "transcribe_language" and value and not re.fullmatch(r"[a-z]{2}", value):
            raise ToolError("Use a two-letter ISO language code or leave language blank.")
        cleaned[field] = value
    if cleaned.get("image_background") == "transparent" and cleaned.get("image_output_format") == "jpeg":
        raise ToolError("Transparent image backgrounds need PNG or WebP output.")
    return cleaned


def get_studio_key_status() -> dict[str, bool]:
    """Local key status only; never returns its value."""
    with locked():
        key = load_settings()["api_key"]
        return {"api_key_configured": bool(key and key != PLACEHOLDER)}


@mcp.tool(annotations=READ)
def open_studio_settings() -> dict[str, Any]:
    """Show private Terminal key setup instructions. Do not ask for the key in chat, a tool argument or MCP elicitation. Choose models/effort in chat per generation."""
    import shlex
    command = '/bin/sh ' + shlex.quote(str(PACKAGE.parent/'bin/studio'))
    return {**get_studio_key_status(), 'setup_command': command+' setup-key',
            'remove_key_command': command+' remove-key',
            'message': 'Run setup-key yourself in Terminal for a hidden local prompt. Never paste a key into chat. Saved locally with owner-only file permissions; no network request is made during setup.'}


def require_approval(approved: bool) -> None:
    if approved is not True:
        raise ToolError("No data sent. First obtain user approval to send this exact prompt/text and any named attachments to https://api.openai.com/v1, then set approved_for_openai=true.")


def text_value(value: str, limit: int = 100000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ToolError(f"Provide nonempty text of at most {limit} characters.")
    return value


def read_input(name: str, suffixes: set[str], limit: int) -> tuple[bytes, str]:
    path = Path(name)
    if not path.is_absolute():
        path = INPUTS / path
    try:
        resolved = path.resolve(strict=True)
        if not any(resolved.is_relative_to(folder.resolve()) for folder in (INPUTS, OUTPUTS)):
            raise ValueError
        if resolved.suffix.lower() not in suffixes or not resolved.is_file() or resolved.stat().st_size > limit:
            raise ValueError
        data = resolved.read_bytes()
        if not data or len(data) > limit:
            raise ValueError
        return data, resolved.suffix.lower()
    except (OSError, ValueError):
        raise ToolError("Use an approved file in the private input/output directories reported by get_studio_state, with a supported extension and size.") from None


def api_client(state: dict, *, metadata_only: bool = False, model: str | None = None) -> OpenAI:
    key = state["api_key"]
    if not key or key == PLACEHOLDER:
        raise ToolError("Run Studio Local setup-key in your own Terminal before generating. Never paste the key into chat.")

    def restrict(request: httpx.Request):
        if request.url.scheme != "https" or request.url.host != "api.openai.com" or request.url.port not in (None, 443):
            raise ToolError("Only https://api.openai.com is permitted.")
        if metadata_only and (request.method != "GET" or request.url.path != "/v1/models" or request.url.query):
            raise ToolError("Model discovery permits only GET /v1/models, with no query or generation payload.")

    return OpenAI(api_key=key, base_url="https://api.openai.com/v1", max_retries=0,
                  organization="", project="", webhook_secret="", admin_api_key="",
                  http_client=httpx.Client(trust_env=False, follow_redirects=False,
                                           timeout=30 if metadata_only else 600,
                                           event_hooks={"request": [restrict], "response": [lambda response: observe_limits(model, response)]}))


def read_cache(name: str, default: dict) -> dict:
    path = PACKAGE / name if name == "model_reference.json" else ROOT / name
    try:
        if path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
            return default
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else default
    except (OSError, ValueError):
        return default


def write_cache(name: str, data: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".metadata-", dir=ROOT)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, ROOT / name)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def model_group(model_id: str) -> str:
    name = model_id.lower()
    if name.startswith("ft:"):
        name = name.split(":", 2)[1]
    if "gpt-image" in name or name.startswith("dall-e"):
        return "images"
    if "tts" in name:
        return "speech"
    if "transcrib" in name or name.startswith("whisper"):
        return "transcription"
    if name.startswith("sora") or "video" in name:
        return "video"
    if "realtime" in name or name.startswith("gpt-live"):
        return "realtime"
    if "embedding" in name:
        return "embeddings"
    if "moderation" in name:
        return "moderation"
    if "audio" in name:
        return "audio_other"
    if "search-api" in name:
        return "other"
    if name.startswith(("gpt-3.5", "gpt-4-turbo", "gpt-4-")) or name == "gpt-4":
        return "other"
    if re.match(r"^(gpt-|chatgpt-|o[1-9])", name):
        return "responses"
    return "other"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def refresh_studio_models(approved_for_openai: bool = False) -> dict[str, Any]:
    """Use the saved key only to authenticate GET https://api.openai.com/v1/models. Cache returned IDs for setup; no prompts, files or paid generation requests are sent. Requires user approval for this metadata lookup."""
    if approved_for_openai is not True:
        raise ToolError("Approve authenticating GET https://api.openai.com/v1/models with your saved key first. No request was sent.")
    try:
        with api_client(load_settings(), metadata_only=True) as client:
            page = client.models.list()
        ids = sorted({item.id for item in page.data if isinstance(item.id, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", item.id)})
        if len(ids) > 5000:
            raise ToolError("The returned model list was too large to cache.")
        catalog = {"fetched_at": datetime.now(timezone.utc).isoformat(), "models": ids,
                   "groups": {group: [name for name in ids if model_group(name) == group] for group in GROUPS},
                   "source": "GET https://api.openai.com/v1/models",
                   "note": "IDs are returned for this key. Categories are inferred from model names, not capabilities returned by the API. Listing alone does not verify endpoint compatibility or a successful generation."}
        with locked():
            write_cache("model_catalog.json", catalog)
        return get_studio_state()
    except APIStatusError as exc:
        raise ToolError(f"Model discovery returned HTTP {exc.status_code}. Check the saved key's permissions. Existing cached data was kept; no generation request was sent.") from None
    except APIConnectionError:
        raise ToolError("Could not reach OpenAI for model discovery. Cached data was kept; no generation request was sent.") from None


def observe_limits(model: str | None, response: httpx.Response) -> None:
    if not model:
        return
    names = ("x-ratelimit-limit-requests", "x-ratelimit-limit-tokens", "x-ratelimit-remaining-requests",
             "x-ratelimit-remaining-tokens", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens",
             "x-ratelimit-limit-project-tokens", "x-ratelimit-remaining-project-tokens",
             "x-ratelimit-reset-project-tokens", "retry-after")
    headers = {name: response.headers[name][:80] for name in names if name in response.headers}
    if not headers:
        return
    try:
        with locked():
            observations = read_cache("observed_limits.json", {})
            observations[model] = {"observed_at": datetime.now(timezone.utc).isoformat(), "headers": headers}
            write_cache("observed_limits.json", dict(list(observations.items())[-200:]))
    except OSError:
        pass


def usage_dict(value: Any) -> dict:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    return value if isinstance(value, dict) else {}


def estimate(operation: str, model: str, usage: dict, audio_seconds: float | None = None,
             characters: int = 0) -> tuple[float | None, str]:
    """Published rates in USD per million tokens; unknown models stay explicitly unpriced."""
    if model in {"gpt-4.1", "gpt-4.1-2025-04-14"} and "input_tokens" in usage and "output_tokens" in usage:
        cached = min(usage["input_tokens"], usage.get("input_tokens_details", {}).get("cached_tokens", 0))
        return ((usage["input_tokens"] - cached) * 2 + cached * .5 + usage["output_tokens"] * 8) / 1e6, "API tokens; GPT-4.1 published rates"
    if model == "gpt-image-1" and "output_tokens" in usage and "input_tokens_details" in usage:
        details = usage["input_tokens_details"]
        if "text_tokens" in details and "image_tokens" in details:
            return (details["text_tokens"] * 5 + details["image_tokens"] * 10 + usage["output_tokens"] * 40) / 1e6, "API tokens; uncached image-input rates (may overestimate)"
    if model == "gpt-4o-transcribe" and "output_tokens" in usage:
        details = usage.get("input_token_details", usage.get("input_tokens_details", {}))
        if "audio_tokens" in details and "text_tokens" in details:
            return (details["audio_tokens"] * 6 + details["text_tokens"] * 2.5 + usage["output_tokens"] * 10) / 1e6, "API tokens; transcription published rates"
    if model.startswith("gpt-4o-mini-tts") and audio_seconds is not None:
        # Speech responses omit billed usage: duration and character count are heuristics.
        return (audio_seconds * 50 * 12 + math.ceil(characters / 4) * .6) / 1e6, "Heuristic: 50 audio tokens/sec, 4 characters/text token; not billed usage"
    return None, "No verified rate or sufficient usage data; excluded from estimated spend"


def record(job: dict) -> dict:
    with locked():
        state = load_settings()
        cost = job.get("estimated_cost_usd")
        if cost is not None:
            state["estimated_spend_usd"] = round(state["estimated_spend_usd"] + cost, 10)
        else:
            state["unpriced_generations"] = state.get("unpriced_generations", 0) + 1
        state["history"].append(job)
        state["history"] = state["history"][-500:]
        save_settings(state)
    return job


def save_output(job_id: str, index: int, suffix: str, data: bytes) -> str:
    OUTPUTS.mkdir(mode=0o700, exist_ok=True)
    if OUTPUTS.is_symlink():
        raise ToolError("The output folder must not be a symlink.")
    path = OUTPUTS / f"{job_id}-{index}{suffix}"
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    return str(path)


@contextmanager
def job_context(operation: str, state: dict, model: str):
    client = api_client(state, model=model)
    job = {"id": uuid.uuid4().hex, "created_at": datetime.now(timezone.utc).isoformat(),
           "operation": operation, "model": model, "status": "started", "files": [],
           "estimated_cost_usd": None, "cost_basis": "Unknown: request may not have completed", "usage": {}}
    try:
        with client:
            yield client, job
        job["status"] = "completed"
    except ToolError:
        job["status"] = "failed"
        raise
    except APIStatusError as exc:
        job["status"] = "api_error"
        job["http_status"] = exc.status_code
        raise ToolError(f"OpenAI returned HTTP {exc.status_code}. Check model availability, key and account limits. The request was not retried.") from None
    except APIConnectionError:
        job["status"] = "connection_error"
        raise ToolError("OpenAI connection failed or timed out. Billing may be uncertain; check usage before retrying.") from None
    except Exception:
        job["status"] = "failed"
        raise ToolError("Studio could not complete the job. Check recent history and local files before retrying; no automatic retry occurred.") from None
    finally:
        # Keep usage even when a paid response was received but local saving failed.
        record(job)


def set_cost(job: dict, usage: Any, **kwargs):
    job["usage"] = usage_dict(usage)
    cost, basis = estimate(job["operation"], job["model"], job["usage"], **kwargs)
    job.update(estimated_cost_usd=cost, cost_basis=basis)


def image_job(operation: str, prompt: str, approved: bool, model: str, quality: str,
              complexity: str, image_paths: list[str] | None = None,
              mask_path: str | None = None, n: int = 1, size: str = "auto",
              output_format: str = "png", background: str = "auto") -> dict:
    require_approval(approved)
    text_value(prompt, 32000)
    if not 1 <= n <= 10:
        raise ToolError("Choose 1 to 10 images.")
    options = validate_options({"image_model": model, "image_quality": quality, "image_size": size,
                                "image_output_format": output_format, "image_background": background})
    model, quality = options["image_model"], options["image_quality"]
    if quality in {"standard", "hd"}:
        raise ToolError("Choose auto, low, medium, high, or a quality supported by the selected GPT Image model.")
    if quality in {"xhigh", "max"} and re.fullmatch(r"gpt-image-(?:1(?:\.5|-mini)?|2)(?:-\d{4}-\d{2}-\d{2})?", model):
        raise ToolError("This model supports auto, low, medium or high quality; choose again before generating.")
    if complexity not in {"default", "low", "medium", "high"}:
        raise ToolError("Select image planning effort: default, low, medium or high.")
    if operation == "edit_image" and (not isinstance(image_paths, list) or not 1 <= len(image_paths) <= 16 or
                                      any(not isinstance(path, str) or not path for path in image_paths)):
        raise ToolError("Provide an ordered list of 1-16 approved reference image filenames.")
    files = {}
    references, total_bytes = [], 0
    for index, path in enumerate(image_paths or []):
        data, suffix = read_input(path, {".png", ".jpg", ".jpeg", ".webp"}, 25 * 1024 * 1024)
        total_bytes += len(data)
        if total_bytes > 100 * 1024 * 1024:
            raise ToolError("Studio reference images must total at most 100 MiB.")
        references.append((f"reference-{index + 1}" + suffix, data, mimetypes.guess_type("input" + suffix)[0]))
    if references:
        files["image"] = references
    if mask_path:
        data, suffix = read_input(mask_path, {".png"}, 3_999_999)
        files["mask"] = ("mask.png", data, "image/png")
    state = load_settings()
    with job_context(operation, state, model) as (client, job):
        job["request_options"] = {"quality": quality, "complexity": complexity, "size": options["image_size"],
                                  "output_format": options["image_output_format"], "background": options["image_background"],
                                  "reference_count": len(references)}
        args = dict(model=model, prompt=prompt, size=options["image_size"], quality=quality, n=n,
                    output_format=options["image_output_format"], background=options["image_background"])
        if complexity != "default":
            args["extra_body"] = {"complexity": complexity}
        response = client.images.edit(**args, **files) if references else client.images.generate(**args)
        set_cost(job, getattr(response, "usage", None))
        if not response.data:
            raise ToolError("OpenAI returned no images.")
        for index, item in enumerate(response.data):
            if not item.b64_json:
                raise ToolError("This image model returned a URL. Studio does not download remote URLs; select a model returning base64 image data.")
            output_format = getattr(response, "output_format", None) or options["image_output_format"]
            suffix = {"jpeg": ".jpg", "webp": ".webp"}.get(output_format, ".png")
            job["files"].append(save_output(job["id"], index, suffix, base64.b64decode(item.b64_json, validate=True)))
    return job


@mcp.tool(annotations=GENERATE)
def generate_image(prompt: str, model: str, quality: str, complexity: str, n: int = 1,
                   size: str = "auto", output_format: str = "png", background: str = "auto",
                   approved_for_openai: bool = False) -> dict[str, Any]:
    """Ask for this request's model, quality and planning effort (complexity: default/low/medium/high) before calling. Sends only the approved prompt. No saved model defaults; use edit_image for references."""
    return image_job("generate_image", prompt, approved_for_openai, model, quality, complexity,
                     n=n, size=size, output_format=output_format, background=background)


@mcp.tool(annotations=GENERATE)
def edit_image(prompt: str, image_paths: list[str], model: str, quality: str, complexity: str,
               mask_path: str | None = None, n: int = 1, size: str = "auto",
               output_format: str = "png", background: str = "auto",
               approved_for_openai: bool = False) -> dict[str, Any]:
    """Generate/edit using 1-16 ordered approved references in the private input/output directories reported by get_studio_state. Ask for model, quality and planning effort for this request. A PNG mask applies to the first reference. Sends the approved prompt and all reference bytes together; no paths are uploaded."""
    return image_job("edit_image", prompt, approved_for_openai, model, quality, complexity,
                     image_paths, mask_path, n, size, output_format, background)


@mcp.tool(annotations=GENERATE)
def generate_speech(text: str, model: str, voice: str, instructions: str = "",
                    output_format: str = "wav", speed: float = 1,
                    approved_for_openai: bool = False) -> dict[str, Any]:
    """Ask for this request's speech model and voice. Speech has no reasoning-effort option. Sends approved text and delivery instructions; ignores saved model defaults."""
    require_approval(approved_for_openai)
    text_value(text, 4096)
    if len(instructions) > 4000:
        raise ToolError("Delivery instructions must be at most 4000 characters.")
    options = validate_options({"tts_model": model, "tts_voice": voice, "tts_format": output_format, "tts_speed": str(speed)})
    state = load_settings()
    with job_context("generate_speech", state, options["tts_model"]) as (client, job):
        job["request_options"] = {"voice": options["tts_voice"], "output_format": options["tts_format"], "speed": float(options["tts_speed"])}
        args = dict(model=options["tts_model"], voice=options["tts_voice"], input=text,
                    response_format=options["tts_format"], speed=float(options["tts_speed"]))
        if instructions:
            args["instructions"] = instructions
        response = client.audio.speech.create(**args)
        data = response.content
        duration = None
        try:
            with wave.open(io.BytesIO(data)) as audio:
                # Streaming WAV headers may report a sentinel frame count; use actual bytes.
                duration = len(audio.readframes(audio.getnframes())) / (audio.getframerate() * audio.getsampwidth() * audio.getnchannels())
        except (wave.Error, EOFError):
            pass
        set_cost(job, None, audio_seconds=duration, characters=len(text) + len(instructions))
        job["files"].append(save_output(job["id"], 0, "." + options["tts_format"], data))
    return job


@mcp.tool(annotations=GENERATE)
def transcribe_audio(audio_path: str, model: str, language: str | None = None,
                     approved_for_openai: bool = False) -> dict[str, Any]:
    """Ask for this request's transcription model; this endpoint has no reasoning-effort option. Transcribes an approved local input/output file. Ignores saved model defaults."""
    require_approval(approved_for_openai)
    data, suffix = read_input(audio_path, {".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm", ".flac", ".ogg"}, 25 * 1024 * 1024)
    options = validate_options({"transcribe_model": model, "transcribe_language": language or ""})
    state = load_settings()
    with job_context("transcribe_audio", state, options["transcribe_model"]) as (client, job):
        args = dict(model=options["transcribe_model"], file=("audio" + suffix, data), response_format="json")
        if language:
            args["language"] = language
        response = client.audio.transcriptions.create(**args)
        set_cost(job, getattr(response, "usage", None))
        job["files"].append(save_output(job["id"], 0, ".txt", response.text.encode("utf-8")))
    return job


@mcp.tool(annotations=GENERATE)
def generate_bulk_text(prompts: list[str], model: str, reasoning_effort: str,
                       max_output_tokens: int = 2048, verbosity: str = "",
                       approved_for_openai: bool = False) -> dict[str, Any]:
    """Ask for this request's text model and reasoning_effort (default/none/minimal/low/medium/high/xhigh/max). 'default' omits the API option. No saved model defaults. Runs 1-100 approved prompts sequentially (store=false), stopping on failure; not the discounted Batch API."""
    require_approval(approved_for_openai)
    if not 1 <= len(prompts) <= 100 or sum(len(p) for p in prompts) > 500000:
        raise ToolError("Provide 1-100 prompts totalling at most 500000 characters.")
    for prompt in prompts:
        text_value(prompt)
    if not reasoning_effort or reasoning_effort != reasoning_effort.strip() or (reasoning_effort == "none" and model in {"gpt-6.2-sol", "gpt-6.2-astra"}):
        raise ToolError("Select a supported reasoning effort, or explicitly select default.")
    options = validate_options({"text_model": model, "text_reasoning_effort": "" if reasoning_effort == "default" else reasoning_effort,
                                "text_max_output_tokens": str(max_output_tokens), "text_verbosity": verbosity})
    results = []
    for index, prompt in enumerate(prompts):
        state = load_settings()
        try:
            with job_context("generate_bulk_text", state, options["text_model"]) as (client, job):
                job["request_options"] = {"reasoning_effort": reasoning_effort, "max_output_tokens": int(options["text_max_output_tokens"]), "verbosity": options["text_verbosity"]}
                args = dict(model=options["text_model"], input=prompt, max_output_tokens=int(options["text_max_output_tokens"]), store=False)
                if options["text_reasoning_effort"]:
                    args["reasoning"] = {"effort": options["text_reasoning_effort"]}
                if options["text_verbosity"]:
                    args["text"] = {"verbosity": options["text_verbosity"]}
                response = client.responses.create(**args)
                set_cost(job, getattr(response, "usage", None))
                job["response_status"] = response.status
                job["files"].append(save_output(job["id"], 0, ".txt", response.output_text.encode("utf-8")))
            results.append(job)
            if response.status != "completed":
                return {"status": "partial", "completed": results, "stopped_at": index,
                        "error": "Response was incomplete; remaining prompts were not sent."}
        except ToolError as exc:
            return {"status": "partial" if results else "failed", "completed": results,
                    "stopped_at": index, "error": str(exc)}
    return {"status": "completed", "completed": results}


if __name__ == "__main__":
    os.umask(0o077)
    if sys.argv[1:] == ["--refresh-models"]:
        try:
            result = refresh_studio_models(approved_for_openai=True)
            catalog = result["catalog"]
            print(json.dumps({"fetched_at": catalog["fetched_at"], "model_count": len(catalog["models"]),
                              "groups": {name: len(ids) for name, ids in catalog["groups"].items()},
                              "balance_usd": None, "billing_status": result["billing"]["status"]}))
        except ToolError as exc:
            print(json.dumps({"error": str(exc)}))
            raise SystemExit(1)
    elif sys.argv[1:]:
        raise SystemExit("Unsupported Studio command.")
    else:
        mcp.run(transport="stdio")
