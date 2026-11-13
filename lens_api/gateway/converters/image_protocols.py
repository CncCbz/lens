from __future__ import annotations

from copy import deepcopy
from typing import Any


def image_request(body: dict[str, Any], *, to_openrouter: bool) -> dict[str, Any]:
    result = deepcopy(body)
    if to_openrouter:
        if result.get("image") is not None or result.get("mask") is not None:
            raise ValueError("Image edits require an OpenAI Image upstream")
        prompt = result.pop("prompt", None)
        if not isinstance(prompt, str):
            raise ValueError("Image generation requires a text prompt")
        if result.get("n", 1) != 1:
            raise ValueError(
                "OpenRouter image conversion supports one image request at a time"
            )
        result.pop("n", None)
        for key in (
            "size",
            "quality",
            "background",
            "output_format",
            "output_compression",
            "style",
            "moderation",
        ):
            if key in result:
                raise ValueError(
                    f"OpenAI Image parameter {key} requires a native image upstream"
                )
        result.pop("response_format", None)
        result["messages"] = [{"role": "user", "content": prompt}]
        result["modalities"] = ["image", "text"]
        result["stream"] = False
    else:
        messages = result.pop("messages", None)
        if not isinstance(messages, list):
            raise ValueError("OpenRouter image request requires messages")
        prompts: list[str] = []
        for message in messages:
            if not isinstance(message, dict) or message.get("role") != "user":
                raise ValueError("OpenAI image conversion requires user text input")
            content = message.get("content")
            if isinstance(content, str):
                prompts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict) or part.get("type") != "text":
                        raise ValueError(
                            "Image input conversion requires an image edit endpoint"
                        )
                    prompts.append(str(part.get("text") or ""))
            else:
                raise ValueError("Image generation requires text content")
        if "image_config" in result:
            raise ValueError("OpenRouter image_config requires an OpenRouter upstream")
        result["prompt"] = "\n".join(prompts)
        result["response_format"] = "b64_json"
        result.pop("modalities", None)
        result.pop("stream", None)
    return result


def _image_usage(value: Any, *, to_openrouter: bool) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    usage = deepcopy(value)
    for image_key, chat_key in (
        ("input_tokens", "prompt_tokens"),
        ("output_tokens", "completion_tokens"),
        ("input_tokens_details", "prompt_tokens_details"),
        ("output_tokens_details", "completion_tokens_details"),
    ):
        source, target = (
            (image_key, chat_key) if to_openrouter else (chat_key, image_key)
        )
        if source in usage:
            usage.setdefault(target, usage.pop(source))
    return usage


def image_response(
    body: dict[str, Any], *, to_openrouter: bool, original_model: str = ""
) -> dict[str, Any]:
    if to_openrouter:
        images: list[dict[str, Any]] = []
        for item in body.get("data") or []:
            if not isinstance(item, dict):
                raise ValueError("Invalid image response item")
            if isinstance(item.get("b64_json"), str):
                mime = body.get("output_format") or "png"
                mime = "jpeg" if mime in {"jpg", "jpeg"} else mime
                url = f"data:image/{mime};base64,{item['b64_json']}"
            elif isinstance(item.get("url"), str):
                url = item["url"]
            else:
                raise ValueError("Image response contains no image")
            images.append({"type": "image_url", "image_url": {"url": url}})
        if not images:
            raise ValueError("Image response contains no images")
        usage = body.get("usage") or {}
        return {
            "id": body.get("id") or "image-generation",
            "object": "chat.completion",
            "created": body.get("created", 0),
            "model": original_model or body.get("model", ""),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "", "images": images},
                    "finish_reason": "stop",
                }
            ],
            "usage": _image_usage(usage, to_openrouter=True),
        }
    choices = body.get("choices")
    if not isinstance(choices, list):
        raise ValueError("Invalid OpenRouter image response")
    data: list[dict[str, str]] = []
    for choice in choices:
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
            continue
        for image in choice["message"].get("images") or []:
            if not isinstance(image, dict):
                continue
            image_url = image.get("image_url")
            url = image_url.get("url") if isinstance(image_url, dict) else image_url
            if not isinstance(url, str):
                raise ValueError("Invalid OpenRouter image URL")
            if url.startswith("data:") and ";base64," in url:
                data.append({"b64_json": url.split(";base64,", 1)[1]})
            else:
                data.append({"url": url})
    if not data:
        raise ValueError("OpenRouter image response contains no images")
    return {
        "created": body.get("created", 0),
        "data": data,
        "usage": _image_usage(body.get("usage"), to_openrouter=False),
    }
