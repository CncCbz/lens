import copy
import json
import math
from typing import Any

from ...models import ProtocolKind

_SYSTEM_ONE = {
    ProtocolKind.TYPESAFE_SYSTEM_ONE,
    ProtocolKind.CLOUDFLARE_WORKERS_AI_SYSTEM_ONE,
}


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _number(value: Any, label: str, *, probability: bool = False) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"Invalid {label}")
    if probability and not 0 <= value <= 1:
        raise ValueError(f"Invalid {label}")
    return float(value)


def _questions(value: Any, *, decisions: bool) -> dict[str, Any]:
    if decisions:
        if not isinstance(value, list):
            raise ValueError("Decisions questions must be an array")
        result: dict[str, Any] = {}
        for item in value:
            q = _object(item, "Question")
            name = q.get("name")
            if not isinstance(name, str) or not name or name in result:
                raise ValueError("Question names must be unique nonempty strings")
            allowed = {"type", "name", "instructions", "choices", "levels"}
            if set(q) - allowed:
                raise ValueError("Unsupported Decisions question fields")
            kind = q.get("type")
            mapped = {
                "type": "bool" if kind == "predicate" else kind,
                "instructions": q.get("instructions", ""),
            }
            if kind == "choice":
                choices = q.get("choices")
                if not isinstance(choices, list):
                    raise ValueError("Choice questions require choices")
                criteria = {}
                for option in choices:
                    option = _object(option, "Choice")
                    key = option.get("value")
                    if (
                        set(option) - {"value", "description"}
                        or not isinstance(key, str)
                        or key in criteria
                    ):
                        raise ValueError("Unsupported or duplicate choice")
                    criteria[key] = option.get("description", "")
                mapped["criteria"] = criteria
            elif kind == "score":
                levels = q.get("levels")
                if not isinstance(levels, list) or any(
                    not isinstance(level, dict) or set(level) != {"label"}
                    for level in levels
                ):
                    raise ValueError("Score questions require labeled levels")
                mapped["criteria"] = [level["label"] for level in levels]
            elif kind == "predicate":
                if "choices" in q or "levels" in q:
                    raise ValueError(
                        "Predicate questions cannot contain choices or levels"
                    )
                mapped["criteria"] = {}
            else:
                raise ValueError("Unsupported Decisions question type")
            result[name] = mapped
    else:
        result = copy.deepcopy(_object(value, "Questions"))
        for question in result.values():
            if isinstance(question, dict) and question.get("type") == "noul":
                question["type"] = "bool"
    if not result:
        raise ValueError("Classification requires questions")
    for name, question in result.items():
        q = _object(question, "Question")
        if (
            not isinstance(name, str)
            or not name
            or set(q) - {"type", "instructions", "criteria"}
        ):
            raise ValueError("Unsupported question fields")
        if not isinstance(q.get("instructions"), str):
            raise ValueError("Question instructions must be a string")
        criteria = q.get("criteria")
        if q.get("type") == "choice":
            if (
                not isinstance(criteria, dict)
                or len(criteria) < 2
                or any(
                    not isinstance(k, str) or not isinstance(v, str)
                    for k, v in criteria.items()
                )
            ):
                raise ValueError("Choice questions require at least two named criteria")
        elif q.get("type") == "score":
            if (
                not isinstance(criteria, list)
                or len(criteria) < 2
                or any(not isinstance(v, str) for v in criteria)
            ):
                raise ValueError("Score questions require at least two levels")
        elif q.get("type") == "bool":
            if (
                not isinstance(criteria, dict)
                or set(criteria) - {"true", "false"}
                or any(not isinstance(v, str) for v in criteria.values())
            ):
                raise ValueError("Bool criteria must contain true/false meanings")
        else:
            raise ValueError("Unsupported classifier question type")
    return result


def classifier_context(protocol: ProtocolKind, body: dict[str, Any]) -> dict[str, Any]:
    source = body
    if protocol == ProtocolKind.CLOUDFLARE_WORKERS_AI_SYSTEM_ONE:
        if set(body) - {"model", "input", "stream"}:
            raise ValueError("Unsupported Cloudflare classifier options")
        source = _object(body.get("input"), "Cloudflare input")
    decisions = protocol == ProtocolKind.OPENAI_DECISIONS
    allowed = (
        {"model", "stream", "questions", "input"}
        if decisions
        else {
            "model",
            "stream",
            "questions",
            "state",
            "assets",
            "images",
            "temperature",
            "_lens_classifier",
        }
    )
    if set(source) - allowed or source.get("stream"):
        raise ValueError("Unsupported classifier options or streaming")
    questions = _questions(source.get("questions"), decisions=decisions)
    images: list[str] = []
    if decisions:
        value = source.get("input")
        if isinstance(value, str):
            try:
                state = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "Cross-protocol Decisions input must contain JSON state"
                ) from exc
        elif (
            isinstance(value, list)
            and len(value) == 1
            and isinstance(value[0], dict)
            and value[0].get("role") == "user"
            and set(value[0]) == {"role", "content"}
        ):
            parts = value[0]["content"]
            if (
                not isinstance(parts, list)
                or not parts
                or not isinstance(parts[0], dict)
                or set(parts[0]) != {"type", "text"}
                or parts[0].get("type") != "input_text"
            ):
                raise ValueError("Unsupported Decisions input")
            try:
                state = json.loads(parts[0]["text"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise ValueError("Decisions text must contain JSON state") from exc
            for part in parts[1:]:
                if (
                    not isinstance(part, dict)
                    or set(part) != {"type", "image_url"}
                    or part.get("type") != "input_image"
                ):
                    raise ValueError("Unsupported Decisions content")
                images.append(part["image_url"])
        else:
            raise ValueError(
                "Unsupported Decisions input for cross-protocol classification"
            )
    else:
        if "state" not in source:
            raise ValueError("Classification requires state")
        state = copy.deepcopy(source["state"])
        raw_images = source.get("images", [])
        if not isinstance(raw_images, list):
            raise ValueError("Classifier images must be an array")
        images.extend(raw_images)
        assets = source.get("assets", {})
        if not isinstance(assets, dict):
            raise ValueError("Classifier assets must be an object")
        if assets:
            names = list(assets)
            if (
                names != [f"<image-{i + 1}>" for i in range(len(names))]
                or not isinstance(state, list)
                or state[:-1] != names
            ):
                raise ValueError(
                    "Cannot map custom asset references across classifier protocols"
                )
            state = state[-1]
            for asset in assets.values():
                if (
                    not isinstance(asset, dict)
                    or set(asset) != {"type", "format", "data_base64"}
                    or asset.get("type") != "image"
                    or asset.get("format") not in {"png", "jpeg"}
                ):
                    raise ValueError("Unsupported classifier image asset")
                images.append(
                    f"data:image/{asset['format']};base64,{asset['data_base64']}"
                )
        if (
            isinstance(state, list)
            and len(state) > 1
            and all(
                isinstance(part, dict) and part.get("type") == "image_url"
                for part in state[:-1]
            )
        ):
            for part in state[:-1]:
                if (
                    set(part) != {"type", "image_url"}
                    or not isinstance(part["image_url"], dict)
                    or set(part["image_url"]) != {"url"}
                ):
                    raise ValueError("Unsupported classifier image URL part")
                images.append(part["image_url"]["url"])
            state = state[-1]
    if any(
        not isinstance(image, str)
        or not image.startswith(
            (
                "data:image/png;base64,",
                "data:image/jpeg;base64,",
                "data:image/webp;base64,",
            )
        )
        for image in images
    ):
        raise ValueError(
            "Cross-protocol classifier images require PNG, JPEG, or WebP data URLs"
        )
    temperature = source.get("temperature", 1)
    if _number(temperature, "classifier temperature") <= 0:
        raise ValueError("Classifier temperature must be positive")
    return {
        "state": state,
        "questions": questions,
        "images": images,
        "temperature": temperature,
    }


def convert_classifier_request(
    client_protocol: ProtocolKind,
    channel_protocol: ProtocolKind,
    body: dict[str, Any],
    target_model: str | None = None,
) -> dict[str, Any]:
    if client_protocol == channel_protocol:
        result = copy.deepcopy(body)
        if target_model:
            result["model"] = target_model
        return result
    if client_protocol == ProtocolKind.LLAMA_CPP_CLASSIFY:
        raise ValueError("Native llama.cpp operations require a llama.cpp upstream")
    context = classifier_context(client_protocol, body)
    model = target_model or body.get("model", "")
    questions = context["questions"]
    images = context["images"]
    if channel_protocol == ProtocolKind.LLAMA_CPP_CLASSIFY:
        if images:
            raise ValueError("llama.cpp classification does not support images")
        for q in questions.values():
            limit = 62 if q["type"] == "choice" else 10
            if q["type"] != "bool" and len(q["criteria"]) > limit:
                raise ValueError(f"llama.cpp supports at most {limit} answer labels")
        return {
            "model": model,
            "state": context["state"],
            "questions": questions,
            "temperature": context["temperature"],
            "_lens_classifier": True,
        }
    if context["temperature"] != 1:
        raise ValueError(
            "This classifier upstream cannot represent readout temperature"
        )
    if channel_protocol == ProtocolKind.OPENAI_DECISIONS:
        wire_questions = []
        for name, q in questions.items():
            kind = q["type"]
            item = {
                "name": name,
                "type": "predicate" if kind == "bool" else kind,
                "instructions": q["instructions"],
            }
            if kind == "choice":
                item["choices"] = [
                    {
                        "value": key,
                        **({"description": description} if description else {}),
                    }
                    for key, description in q["criteria"].items()
                ]
            elif kind == "score":
                item["levels"] = [{"label": label} for label in q["criteria"]]
            else:
                meanings = [
                    f"{key.title()} means: {value}"
                    for key, value in q["criteria"].items()
                    if value
                ]
                if meanings:
                    item["instructions"] += "\n\n" + "\n".join(meanings)
            wire_questions.append(item)
        value = json.dumps(context["state"], ensure_ascii=False, separators=(",", ":"))
        if len(images) > 128:
            raise ValueError("OpenAI Decisions accepts at most 128 images")
        if images:
            value = [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": value},
                        *(
                            {"type": "input_image", "image_url": image}
                            for image in images
                        ),
                    ],
                }
            ]
        return {"model": model, "input": value, "questions": wire_questions}
    if channel_protocol not in _SYSTEM_ONE:
        raise ValueError("Unsupported classifier protocol")
    wire = {
        "state": context["state"],
        "questions": {
            name: {**q, "type": "noul" if q["type"] == "bool" else q["type"]}
            for name, q in questions.items()
        },
    }
    if images:
        if channel_protocol == ProtocolKind.CLOUDFLARE_WORKERS_AI_SYSTEM_ONE and str(
            model
        ).startswith("@cf/cloudflare/clef"):
            if len(images) > 4:
                raise ValueError("Cloudflare Clef accepts at most four images")
            wire["images"] = images
        else:
            assets = {}
            for i, image in enumerate(images):
                mime, data = image.split(";base64,", 1)
                if mime not in {"data:image/png", "data:image/jpeg"}:
                    raise ValueError("System One assets accept only PNG and JPEG")
                assets[f"<image-{i + 1}>"] = {
                    "type": "image",
                    "format": mime.rsplit("/", 1)[1],
                    "data_base64": data,
                }
            wire["assets"] = assets
            wire["state"] = [*assets, wire["state"]]
    if channel_protocol == ProtocolKind.CLOUDFLARE_WORKERS_AI_SYSTEM_ONE:
        return {"model": model, "input": wire}
    return {"model": model, **wire}


def classifier_output(protocol: ProtocolKind, body: dict[str, Any]) -> dict[str, Any]:
    if protocol != ProtocolKind.CLOUDFLARE_WORKERS_AI_SYSTEM_ONE:
        return body
    if body.get("success") is False:
        raise ValueError(f"Cloudflare classification failed: {body.get('errors', [])}")
    result = _object(body.get("result"), "Cloudflare result")
    if "answers" not in result:
        if result.get("state") != "Completed":
            raise ValueError("Cloudflare classifier run did not complete")
        result = _object(result.get("result"), "Cloudflare classifier output")
    return result


def convert_classifier_response(
    client_protocol: ProtocolKind,
    channel_protocol: ProtocolKind,
    body: dict[str, Any],
    original_model: str = "",
    *,
    request_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if client_protocol == channel_protocol:
        return copy.deepcopy(body)
    if request_body is None:
        raise ValueError("Classifier response conversion requires original questions")
    context = classifier_context(client_protocol, request_body)
    output = classifier_output(channel_protocol, body)
    raw_answers = output.get("answers")
    if channel_protocol == ProtocolKind.OPENAI_DECISIONS:
        if not isinstance(raw_answers, list):
            raise ValueError("Decisions answers must be an array")
        by_name = {}
        for answer in raw_answers:
            answer = _object(answer, "Classifier answer")
            name = answer.get("name")
            if not isinstance(name, str) or name in by_name:
                raise ValueError("Duplicate or missing classifier answer name")
            by_name[name] = answer
    else:
        by_name = _object(raw_answers, "Classifier answers")
    result = {}
    for name, q in context["questions"].items():
        answer = _object(by_name.get(name), f"Answer for {name}")
        kind = q["type"]
        expected = (
            "predicate"
            if channel_protocol == ProtocolKind.OPENAI_DECISIONS and kind == "bool"
            else "noul" if channel_protocol in _SYSTEM_ONE and kind == "bool" else kind
        )
        if answer.get("type") != expected:
            raise ValueError(f"Wrong or refused answer type for {name}")
        if kind == "bool":
            probability = _number(
                answer.get("noul") if expected == "noul" else answer.get("probability"),
                "bool probability",
                probability=True,
            )
            result[name] = {"type": "bool", "probability": probability}
        elif kind == "score":
            score = _number(answer.get("score"), "score")
            if not 0 <= score <= len(q["criteria"]) - 1:
                raise ValueError("Classifier score is outside the requested levels")
            result[name] = {
                "type": "score",
                "score": score,
                "confidence": _number(
                    answer.get("confidence"), "confidence", probability=True
                ),
            }
        else:
            probabilities = answer.get("probabilities")
            if channel_protocol == ProtocolKind.OPENAI_DECISIONS:
                if not isinstance(probabilities, list):
                    raise ValueError("Decisions choice probabilities must be an array")
                mapped = {}
                for entry in probabilities:
                    entry = _object(entry, "Choice probability")
                    key = entry.get("value")
                    if not isinstance(key, str) or key in mapped:
                        raise ValueError("Duplicate choice probability")
                    mapped[key] = entry.get("probability")
                probabilities = mapped
            probabilities = _object(probabilities, "Choice probabilities")
            if (
                set(probabilities) != set(q["criteria"])
                or answer.get("choice") not in q["criteria"]
            ):
                raise ValueError("Classifier returned unknown or missing choices")
            result[name] = {
                "type": "choice",
                "choice": answer["choice"],
                "probabilities": {
                    key: _number(value, "choice probability", probability=True)
                    for key, value in probabilities.items()
                },
                "confidence": _number(
                    answer.get("confidence"), "confidence", probability=True
                ),
            }
    usage = {"usage": copy.deepcopy(output["usage"])} if "usage" in output else {}
    if client_protocol == ProtocolKind.OPENAI_DECISIONS:
        answers = []
        for name, answer in result.items():
            item = {"name": name, **answer}
            if answer["type"] == "bool":
                item["type"] = "predicate"
            elif answer["type"] == "choice":
                item["probabilities"] = [
                    {"value": key, "probability": value}
                    for key, value in answer["probabilities"].items()
                ]
            answers.append(item)
        return {"model": original_model, "answers": answers, **usage}
    answers = {
        name: (
            {"type": "noul", "noul": answer["probability"]}
            if answer["type"] == "bool"
            else answer
        )
        for name, answer in result.items()
    }
    native = {"model": original_model, "answers": answers, **usage}
    if client_protocol == ProtocolKind.CLOUDFLARE_WORKERS_AI_SYSTEM_ONE:
        return {"success": True, "result": native, "errors": [], "messages": []}
    return native
