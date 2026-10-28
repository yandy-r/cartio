"""Helpers for tests that act as an enrolled collector, signing requests like the real one."""

import json

from cartio_core import signing


def give_key(collector) -> str:
    """Enroll a key for `collector` directly, as if it had enrolled. Returns the private key."""
    private_key = signing.new_private_key()
    collector.public_key = signing.public_key_for(private_key)
    collector.save(update_fields=["public_key"])
    return private_key


def signed_post(client, collector_id, private_key, path, body, **sign_options):
    """POST `body` as JSON, signed as collector `collector_id`."""
    data = json.dumps(body).encode()
    auth = signing.sign(private_key, str(collector_id), "POST", path, data, **sign_options)
    return client.post(path, data=data, content_type="application/json", HTTP_AUTHORIZATION=auth)


CHECKIN = {"hostname": "vm01", "version": "0.1.0", "platform": "Linux", "tls_inspected": False}
