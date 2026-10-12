from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from uuid import uuid4


def assign_credential_base_urls(
    credentials: list[dict],
    base_urls: list[dict],
    protocols: list[dict],
    *,
    new_id: Callable[[], str] | None = None,
) -> tuple[list[dict], dict[str, str]]:
    """Bind each credential to one base URL.

    A credential used by several addresses keeps its id on the address that
    appears most often. Ties use the address ``sort_order``, then its id.
    Protocol configs have no sort column, so that address order is the
    stable stand-in for "the earliest combination". Other addresses get a
    copied credential. Returns the new credential rows and a map of
    protocol id to the credential id that combination should use.
    """

    make_id = new_id or (lambda: str(uuid4()))
    url_rank = {
        str(item["id"]): (int(item.get("sort_order") or 0), str(item["id"]))
        for item in base_urls
        if item.get("id")
    }
    ordered_urls = sorted(url_rank, key=lambda item: url_rank[item])
    protocols_by_credential: dict[str, list[dict]] = defaultdict(list)
    for protocol in protocols:
        credential_id = str(protocol.get("credential_id") or "")
        if credential_id:
            protocols_by_credential[credential_id].append(protocol)

    assigned: list[dict] = []
    protocol_credentials: dict[str, str] = {}
    for credential in credentials:
        credential_id = str(credential["id"])
        used = protocols_by_credential.get(credential_id, [])
        existing = str(credential.get("base_url_id") or "")
        if existing:
            winner = existing
        elif used:
            counts = Counter(str(item.get("base_url_id") or "") for item in used)

            def rank(base_url_id: str) -> tuple[int, int, str, str]:
                protocol_ids = [
                    str(item["id"])
                    for item in used
                    if str(item.get("base_url_id") or "") == base_url_id
                ]
                sort_order, url_id = url_rank.get(base_url_id, (10**9, base_url_id))
                return (
                    -counts[base_url_id],
                    sort_order,
                    url_id,
                    min(protocol_ids) if protocol_ids else "",
                )

            winner = min(counts, key=rank)
        elif ordered_urls:
            winner = ordered_urls[0]
        else:
            raise ValueError(
                f"Credential has no base URL to attach to: {credential_id}"
            )

        assigned.append({**credential, "base_url_id": winner})
        other_urls = sorted(
            {
                str(item.get("base_url_id") or "")
                for item in used
                if str(item.get("base_url_id") or "")
                and str(item.get("base_url_id")) != winner
            },
            key=lambda item: url_rank.get(item, (10**9, item)),
        )
        copies: dict[str, str] = {}
        for base_url_id in other_urls:
            copy_id = make_id()
            copies[base_url_id] = copy_id
            assigned.append({**credential, "id": copy_id, "base_url_id": base_url_id})
        for protocol in used:
            protocol_id = str(protocol["id"])
            base_url_id = str(protocol.get("base_url_id") or "")
            protocol_credentials[protocol_id] = copies.get(base_url_id, credential_id)
    return assigned, protocol_credentials
