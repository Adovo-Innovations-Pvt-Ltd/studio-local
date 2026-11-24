"""Offline protocol, accounting and upload-boundary tests with real SDK parsing."""
import asyncio
import base64
from email.parser import BytesParser
from email.policy import default as email_policy
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PLUGIN = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("studio", PLUGIN / "server/mcp_server.py")
studio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(studio)
import storage
FAKE_KEY = "sk-offline-test-only"
IMAGE_OPTIONS = {"model": "gpt-image-1", "quality": "high", "complexity": "default"}
TEXT_OPTIONS = {"model": "gpt-4.1", "reasoning_effort": "default"}


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.originals = {name: getattr(studio, name) for name in ("ROOT", "SETTINGS", "INPUTS", "OUTPUTS")}
        studio.ROOT = self.root / "state"
        studio.ROOT.mkdir()
        studio.SETTINGS = studio.ROOT / "settings.json"
        studio.INPUTS = self.root / "inputs"
        studio.OUTPUTS = self.root / "outputs"
        studio.INPUTS.mkdir()
        studio.OUTPUTS.mkdir()
        self.defaults = json.loads((PLUGIN / "tests/default-settings.json").read_text())
        self.defaults["api_key"] = FAKE_KEY
        studio.save_settings(self.defaults)
        self.requests = []
        client_class = httpx.Client

        responder = self.respond
        class TestClient(client_class):
            def __init__(self, **kwargs):
                super().__init__(**kwargs, transport=httpx.MockTransport(responder))
        self.patcher = patch.object(studio.httpx, "Client", TestClient)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        for name, value in self.originals.items():
            setattr(studio, name, value)
        self.temp.cleanup()

    def respond(self, request):
        self.requests.append(request)
        self.assertEqual(request.url.host, "api.openai.com")
        path = request.url.path
        if path == "/v1/models":
            return httpx.Response(200, json={"object": "list", "data": [
                {"id": name, "created": 1, "object": "model", "owned_by": "system"}
                for name in ["gpt-image-1", "gpt-4o-mini-tts", "gpt-4o-transcribe", "sora-2",
                             "gpt-4.1", "gpt-realtime", "gpt-audio", "text-embedding-3-small",
                             "omni-moderation-latest", "davinci-002"]]})
        if path.startswith("/v1/images/"):
            return httpx.Response(200, json={"created": 1, "data": [{"b64_json": base64.b64encode(b"offline-image").decode()}],
                "usage": {"input_tokens": 10, "output_tokens": 100, "total_tokens": 110,
                          "input_tokens_details": {"text_tokens": 10, "image_tokens": 0}}})
        if path == "/v1/audio/speech":
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(24000)
                audio.writeframes(b"\0" * 48000)
            return httpx.Response(200, content=buffer.getvalue(), headers={"content-type": "audio/wav"})
        if path == "/v1/audio/transcriptions":
            return httpx.Response(200, json={"text": "offline transcript", "usage": {"type": "tokens", "input_tokens": 20,
                "output_tokens": 5, "total_tokens": 25, "input_token_details": {"text_tokens": 0, "audio_tokens": 20}}})
        payload = json.loads(request.content)
        if payload["input"] == "trigger-error":
            return httpx.Response(429, json={"error": {"message": "do not echo " + FAKE_KEY, "type": "rate_limit_error"}})
        return httpx.Response(200, json={"id": "resp_offline", "object": "response", "created_at": 1,
            "model": payload["model"], "status": "completed", "output": [{"id": "msg_offline", "type": "message",
                "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "offline text", "annotations": []}]}],
            "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
                      "input_tokens_details": {"cached_tokens": 40}, "output_tokens_details": {"reasoning_tokens": 0}}})

    def test_state_and_key_never_echo_secret(self):
        self.assertNotIn(FAKE_KEY, json.dumps(studio.get_studio_state()))
        storage.save_key(self.root, "sk-new-offline-test")
        result = studio.get_studio_key_status()
        self.assertNotIn("sk-new-offline-test", json.dumps(result))
        self.assertEqual(stat.S_IMODE(studio.SETTINGS.stat().st_mode), 0o600)
        self.assertTrue(result["api_key_configured"])
        storage.save_key(self.root, "")
        self.assertFalse(studio.get_studio_key_status()["api_key_configured"])
        self.assertEqual(self.requests, [])

    def test_no_assumed_balance(self):
        state = studio.get_studio_state()
        self.assertIsNone(state["billing"]["balance_usd"])
        self.assertIsNone(state["credit_budget_usd"])
        self.assertIsNone(state["estimated_remaining_usd"])
        self.assertEqual(state["billing"]["status"], "unavailable_via_project_key")

    def test_model_discovery_is_approved_read_only_and_not_billable(self):
        with self.assertRaises(studio.ToolError):
            studio.refresh_studio_models()
        self.assertEqual(self.requests, [])
        result = studio.refresh_studio_models(approved_for_openai=True)
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(str(request.url), "https://api.openai.com/v1/models")
        self.assertEqual(request.content, b"")
        self.assertEqual(request.headers["Authorization"], "Bearer " + FAKE_KEY)
        self.assertEqual(result["catalog"]["groups"]["images"], ["gpt-image-1"])
        self.assertEqual(result["catalog"]["groups"]["video"], ["sora-2"])
        self.assertEqual(result["catalog"]["groups"]["responses"], ["gpt-4.1"])
        self.assertEqual(result["estimated_spend_usd"], 0)
        self.assertEqual(result["history"], [])
        self.assertNotIn(FAKE_KEY, json.dumps(result))

    def test_metadata_client_cannot_call_generation_or_account_endpoints(self):
        with studio.api_client(studio.load_settings(), metadata_only=True) as client:
            for method, url in [("POST", "https://api.openai.com/v1/responses"),
                                ("GET", "https://api.openai.com/v1/organization/costs"),
                                ("GET", "https://api.openai.com/v1/models?private=value")]:
                with self.assertRaises(studio.ToolError):
                    client._client.request(method, url)
        self.assertEqual(self.requests, [])

    def test_key_change_invalidates_metadata(self):
        studio.refresh_studio_models(approved_for_openai=True)
        studio.write_cache("observed_limits.json", {"old-model": {"headers": {}}})
        storage.save_key(self.root, "sk-replacement-offline")
        result = studio.get_studio_key_status()
        self.assertEqual(result, {"api_key_configured": True})
        state = studio.get_studio_state()
        self.assertIsNone(state["catalog"]["fetched_at"])
        self.assertEqual(state["observed_rate_limits"], {})

    def test_observed_headers_do_not_store_private_headers(self):
        studio.observe_limits("gpt-4.1", httpx.Response(200, headers={
            "x-ratelimit-limit-requests": "60", "x-ratelimit-limit-tokens": "30000",
            "authorization": FAKE_KEY, "openai-organization": "private-org"}))
        observed = studio.get_studio_state()["observed_rate_limits"]
        self.assertEqual(observed["gpt-4.1"]["headers"]["x-ratelimit-limit-tokens"], "30000")
        self.assertNotIn(FAKE_KEY, json.dumps(observed))
        self.assertNotIn("private-org", json.dumps(observed))

    def test_per_request_options_reach_sdk_and_do_not_change_defaults(self):
        before = studio.load_settings()
        studio.generate_image("one", model="gpt-image-2.5-sunburst", quality="max", complexity="high",
                              output_format="webp", background="transparent", approved_for_openai=True)
        studio.generate_speech("two", model="tts-1", voice="coral", speed=1.25,
                               output_format="mp3", approved_for_openai=True)
        studio.generate_bulk_text(["three"], model="gpt-6.2-sol", reasoning_effort="low", max_output_tokens=512,
                                  verbosity="high", approved_for_openai=True)
        image, speech, text = [json.loads(request.content) for request in self.requests]
        self.assertEqual(image["model"], "gpt-image-2.5-sunburst")
        self.assertEqual(image["quality"], "max")
        self.assertEqual(image["complexity"], "high")
        self.assertEqual(image["output_format"], "webp")
        self.assertEqual(image["background"], "transparent")
        self.assertEqual(speech["response_format"], "mp3")
        self.assertEqual(speech["speed"], 1.25)
        self.assertEqual(speech["model"], "tts-1")
        self.assertEqual(speech["voice"], "coral")
        self.assertEqual(text["model"], "gpt-6.2-sol")
        self.assertEqual(text["max_output_tokens"], 512)
        self.assertEqual(text["reasoning"], {"effort": "low"})
        self.assertEqual(text["text"], {"verbosity": "high"})
        for field in studio.EDITABLE:
            self.assertEqual(studio.load_settings()[field], before[field])

    def test_approval_blocks_every_network_route(self):
        for fn, args in [(studio.generate_image, {"prompt": "test", **IMAGE_OPTIONS}),
                         (studio.edit_image, {"prompt": "test", "image_paths": ["no.png"], **IMAGE_OPTIONS}),
                         (studio.generate_speech, {"text": "test", "model": "tts-1", "voice": "alloy"}),
                         (studio.transcribe_audio, {"audio_path": "no.wav", "model": "whisper-1"}),
                         (studio.generate_bulk_text, {"prompts": ["test"], **TEXT_OPTIONS})]:
            with self.assertRaisesRegex(studio.ToolError, "No data sent"):
                fn(**args)
        self.assertEqual(self.requests, [])
        self.assertEqual(studio.load_settings()["history"], [])

    def test_origin_guard_rejects_other_hosts_and_protocols(self):
        with studio.api_client(studio.load_settings()) as client:
            for url in ('https://example.com/v1', 'http://api.openai.com/v1', 'https://api.openai.com:8443/v1'):
                with self.assertRaises(studio.ToolError):
                    client._client.get(url)
        self.assertEqual(self.requests, [])

    def test_accounting_is_atomic(self):
        result = studio.generate_bulk_text(["one", "two"], **TEXT_OPTIONS, approved_for_openai=True)
        self.assertEqual(result["status"], "completed")
        self.assertAlmostEqual(studio.load_settings()["estimated_spend_usd"], .0006)
        self.assertEqual(Path(result["completed"][0]["files"][0]).read_text(), "offline text")
        self.assertFalse(json.loads(self.requests[0].content)["store"])
        with ThreadPoolExecutor(max_workers=4) as workers:
            list(workers.map(lambda i: studio.record({"id": str(i), "estimated_cost_usd": .01}), range(25)))
        self.assertAlmostEqual(studio.load_settings()["estimated_spend_usd"], .2506)
        self.assertEqual(len(studio.load_settings()["history"]), 27)

    def test_all_asset_routes_save_without_leaking_input_paths(self):
        (studio.INPUTS / "private.png").write_bytes(b"approved-image-only")
        (studio.INPUTS / "private.wav").write_bytes(b"approved-audio-only")
        jobs = [studio.generate_image("test image", **IMAGE_OPTIONS, approved_for_openai=True),
                studio.edit_image("test edit", ["private.png"], **IMAGE_OPTIONS, approved_for_openai=True),
                studio.generate_speech("hello", model="gpt-4o-mini-tts", voice="alloy", approved_for_openai=True),
                studio.transcribe_audio("private.wav", model="gpt-4o-transcribe", approved_for_openai=True)]
        for job in jobs:
            self.assertEqual(job["status"], "completed")
            self.assertIsNotNone(job["estimated_cost_usd"])
            self.assertTrue(Path(job["files"][0]).is_file())
            self.assertEqual(stat.S_IMODE(Path(job["files"][0]).stat().st_mode), 0o600)
        serialized = json.dumps(studio.get_studio_state())
        self.assertNotIn("test image", serialized)
        self.assertNotIn("private.png", serialized)
        for request in self.requests:
            self.assertNotIn(str(self.root).encode(), request.content)
            self.assertNotIn(b"private.png", request.content)

    def test_path_and_settings_boundaries(self):
        outside = self.root / "private.txt"
        outside.write_text("not approved")
        (studio.INPUTS / "escape.txt").symlink_to(outside)
        for name in (str(outside), "../private.txt", "escape.txt", "https://example.com/data.txt"):
            with self.assertRaises(studio.ToolError):
                studio.read_input(name, {".txt"}, 100)
        for change in ({"api_key": "secret"}, {"estimated_spend_usd": "0"}, {"text_model": "https://evil.test"}):
            with self.assertRaises(studio.ToolError):
                studio.validate_options(change)
        self.assertEqual(self.requests, [])

    def test_partial_bulk_no_retry_and_safe_error(self):
        result = studio.generate_bulk_text(["one", "trigger-error", "never-sent"], **TEXT_OPTIONS, approved_for_openai=True)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["stopped_at"], 1)
        self.assertEqual(len(result["completed"]), 1)
        self.assertEqual(len(self.requests), 2)
        self.assertNotIn(FAKE_KEY, json.dumps(result))
        self.assertEqual(studio.load_settings()["unpriced_generations"], 1)

    def test_unknown_cost_is_not_reported_as_free(self):
        result = studio.generate_bulk_text(["one"], model="custom-model", reasoning_effort="default", approved_for_openai=True)
        self.assertIsNone(result["completed"][0]["estimated_cost_usd"])
        self.assertEqual(studio.get_studio_state()["unpriced_generations"], 1)

    def test_local_save_failure_preserves_spend(self):
        with patch.object(studio, "save_output", side_effect=OSError("disk full")):
            with self.assertRaises(studio.ToolError):
                studio.generate_image("test", **IMAGE_OPTIONS, approved_for_openai=True)
        state = studio.load_settings()
        self.assertGreater(state["estimated_spend_usd"], 0)
        self.assertEqual(state["history"][0]["status"], "failed")

    def test_corrupt_settings_are_not_reset(self):
        studio.SETTINGS.write_text("invalid JSON")
        with self.assertRaises(studio.ToolError):
            studio.get_studio_state()
        self.assertEqual(studio.SETTINGS.read_text(), "invalid JSON")

    def test_key_setup_returns_instructions_without_secret_ui(self):
        result = studio.open_studio_settings()
        self.assertTrue(result["api_key_configured"])
        self.assertIn('setup-key', result['setup_command'])
        self.assertNotIn(FAKE_KEY, json.dumps(result))
        self.assertEqual(studio.get_studio_key_status(), {"api_key_configured": True})
        self.assertNotIn("settings", studio.get_studio_state())
        self.assertEqual(self.requests, [])

    def test_default_effort_omits_api_options_and_ignores_saved_choices(self):
        state = studio.load_settings()
        state.update(image_model="stale-image", image_quality="xhigh", text_model="stale-text",
                     text_reasoning_effort="max", tts_model="stale-speech", transcribe_model="stale-transcribe")
        studio.save_settings(state)
        studio.generate_image("image", model="gpt-image-2", quality="high", complexity="default", approved_for_openai=True)
        studio.generate_bulk_text(["text"], **TEXT_OPTIONS, approved_for_openai=True)
        image, text = [json.loads(request.content) for request in self.requests]
        self.assertNotIn("complexity", image)
        self.assertNotIn("reasoning", text)
        self.assertEqual(image["model"], "gpt-image-2")
        self.assertEqual(text["model"], "gpt-4.1")
        self.assertEqual(studio.load_settings()["image_model"], "stale-image")

    def test_invalid_request_options_block_before_api(self):
        for options in [dict(model="sk-secret", quality="high", complexity="default"),
                        dict(model="gpt-image-2", quality="xhigh", complexity="default"),
                        dict(model="gpt-image-2-2026-04-21", quality="max", complexity="default"),
                        dict(model="gpt-image-2", quality="high", complexity="extreme"),
                        dict(model="gpt-image-2", quality="high", complexity="default", background="transparent", output_format="jpeg")]:
            with self.assertRaises(studio.ToolError):
                studio.generate_image("test", **options, approved_for_openai=True)
        for effort in ["", " ", "extreme", "none"]:
            with self.assertRaises(studio.ToolError):
                studio.generate_bulk_text(["test"], model="gpt-6.2-sol", reasoning_effort=effort, approved_for_openai=True)
        with self.assertRaises(TypeError):
            studio.generate_image("test", approved_for_openai=True)
        self.assertEqual(self.requests, [])

    def test_multiple_references_and_mask_use_one_ordered_multipart_request(self):
        for name, data in [("private-a.png", b"first-approved-image"), ("private-b.webp", b"second-approved-image"),
                           ("private-mask.png", b"approved-mask")]:
            (studio.INPUTS/name).write_bytes(data)
        job = studio.edit_image("combine these", ["private-a.png", "private-b.webp"], model="gpt-image-2.5-sunburst",
                                 quality="high", complexity="low", mask_path="private-mask.png", approved_for_openai=True)
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual(request.url.path, "/v1/images/edits")
        message = BytesParser(policy=email_policy).parsebytes(
            ("Content-Type: " + request.headers["content-type"] + "\r\n\r\n").encode() + request.content)
        parts = list(message.iter_parts())
        images = [p for p in parts if p.get_param("name", header="content-disposition") == "image[]"]
        self.assertEqual([p.get_payload(decode=True) for p in images], [b"first-approved-image", b"second-approved-image"])
        self.assertEqual([p.get_filename() for p in images], ["reference-1.png", "reference-2.webp"])
        masks = [p for p in parts if p.get_param("name", header="content-disposition") == "mask"]
        self.assertEqual(masks[0].get_payload(decode=True), b"approved-mask")
        self.assertEqual(job["request_options"]["reference_count"], 2)
        self.assertNotIn(b"private-", request.content)
        self.assertNotIn(str(self.root).encode(), request.content)

    def test_reference_count_limits_and_validation_before_upload(self):
        (studio.INPUTS/"one.png").write_bytes(b"approved")
        for names in [[], ["one.png"]*17, ["one.png", "missing.png"], ["one.png", "../settings.json"]]:
            with self.assertRaises(studio.ToolError):
                studio.edit_image("test", names, **IMAGE_OPTIONS, approved_for_openai=True)
        with self.assertRaises(studio.ToolError):
            studio.edit_image("test", ["one.png"], **IMAGE_OPTIONS, mask_path="one.webp", approved_for_openai=True)
        self.assertEqual(self.requests, [])
        job = studio.edit_image("test", ["one.png"]*16, **IMAGE_OPTIONS, approved_for_openai=True)
        self.assertEqual(job["request_options"]["reference_count"], 16)
        self.assertEqual(len(self.requests), 1)

    def test_reference_bundle_size_is_bounded_before_upload(self):
        class LargeReference:
            def __len__(self):
                return 25 * 1024 * 1024
        with patch.object(studio, "read_input", return_value=(LargeReference(), ".png")):
            with self.assertRaisesRegex(studio.ToolError, "100 MiB"):
                studio.edit_image("test", ["one.png"]*5, **IMAGE_OPTIONS, approved_for_openai=True)
        self.assertEqual(self.requests, [])

    def test_transcription_uses_request_model_and_language(self):
        (studio.INPUTS/"approved.wav").write_bytes(b"approved-audio")
        job = studio.transcribe_audio("approved.wav", model="gpt-transcribe", language="en", approved_for_openai=True)
        self.assertEqual(job["model"], "gpt-transcribe")
        self.assertIn(b"gpt-transcribe", self.requests[0].content)
        self.assertIn(b"\r\nen\r\n", self.requests[0].content)


class ProtocolTests(unittest.TestCase):
    def test_stdio_discovery_resource_and_tools(self):
        async def check():
            params = StdioServerParameters(command=sys.executable, args=["-I", str(PLUGIN / "server/entry.py")],
                                           env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1", "OPENAI_STUDIO_DATA_DIR": str(data)})
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                    self.assertEqual(len(tools), 8)
                    self.assertNotIn("update_studio_settings", tools)
                    self.assertNotIn("get_studio_asset", tools)
                    self.assertNotIn('set_studio_api_key', tools)
                    self.assertFalse(tools['open_studio_settings'].meta)
                    resources = await session.list_resources()
                    self.assertEqual(resources.resources, [])
                    result = await session.call_tool("open_studio_settings", {})
                    self.assertFalse(result.isError)
                    self.assertIn('setup_command', result.structuredContent)
                    self.assertFalse(result.structuredContent['api_key_configured'])
                    for name, required in [("generate_image", {"model", "quality", "complexity"}),
                                           ("edit_image", {"model", "quality", "complexity", "image_paths"}),
                                           ("generate_bulk_text", {"model", "reasoning_effort"}),
                                           ("generate_speech", {"model", "voice"}),
                                           ("transcribe_audio", {"model"})]:
                        self.assertTrue(required <= set(tools[name].inputSchema["required"]))
                    blocked = await session.call_tool("generate_image", {"prompt": "offline"})
                    self.assertTrue(blocked.isError)
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory).resolve()/'studio-data'
            asyncio.run(check())


if __name__ == "__main__":
    unittest.main(verbosity=2)
