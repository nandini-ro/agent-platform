"""Chat attachments: upload, classification, sending, history and providers."""

import base64
import json

import pytest

from app.api.chat import _load_history
from app.db.session import SessionLocal
from app.models.attachment import Attachment
from app.services.attachments import MAX_IMAGE_BYTES, message_blocks
from app.services.llm.base import LLMMessage
from app.services.llm.gemini_provider import GeminiProvider
from app.services.llm.openai_provider import OpenAIProvider
from tests.test_builtin_tools import _pdf

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def _upload(client, name, data, content_type="application/octet-stream"):
    return client.post("/api/attachments", files={"file": (name, data, content_type)})


def _conversation(client, agent_payload):
    agent = client.post("/api/agents", json=agent_payload).json()
    return client.post("/api/conversations", json={"agent_id": agent["id"]}).json()


def _send(client, conversation_id, content="", attachment_ids=()):
    events, status = [], None
    with client.stream(
        "POST",
        f"/api/conversations/{conversation_id}/messages",
        json={"content": content, "attachment_ids": list(attachment_ids)},
    ) as response:
        status = response.status_code
        name = None
        for line in response.iter_lines():
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                events.append((name, json.loads(line.split(":", 1)[1].strip())))
    return status, events


# -- upload and classification ----------------------------------------------


def test_an_image_is_recognised_by_its_bytes_not_its_name(client):
    response = _upload(client, "holiday.txt", PNG, "text/plain")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "image"
    assert body["content_type"] == "image/png"
    assert body["filename"] == "holiday.txt"


def test_text_and_pdf_documents_have_their_text_extracted(client):
    note = _upload(client, "notes.md", b"# Revision\nMitochondria make ATP.").json()
    assert note["kind"] == "document"

    pdf = _upload(client, "paper.pdf", _pdf("Photosynthesis basics")).json()
    assert pdf["kind"] == "document" and pdf["content_type"] == "application/pdf"

    with SessionLocal() as db:
        assert "Mitochondria" in db.get(Attachment, note["id"]).text
        assert "Photosynthesis basics" in db.get(Attachment, pdf["id"]).text
        assert db.get(Attachment, pdf["id"]).data is None  # only the text is kept


@pytest.mark.parametrize(
    "name, data, fragment",
    [
        ("blob.bin", b"\x00\x01\x02binary", "Unsupported file"),
        ("empty.txt", b"", "empty"),
        ("huge.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * MAX_IMAGE_BYTES, "at most 4 MB"),
    ],
)
def test_unusable_uploads_are_refused_with_a_reason(client, name, data, fragment):
    response = _upload(client, name, data)
    assert response.status_code == 422
    assert fragment in response.json()["detail"]


def test_image_bytes_are_served_safely_and_documents_are_not(client):
    image = _upload(client, "a.png", PNG).json()
    served = client.get(f"/api/attachments/{image['id']}/content")
    assert served.status_code == 200
    assert served.content == PNG
    assert served.headers["content-type"] == "image/png"
    assert served.headers["x-content-type-options"] == "nosniff"

    doc = _upload(client, "a.txt", b"hello").json()
    assert client.get(f"/api/attachments/{doc['id']}/content").status_code == 404


# -- sending ------------------------------------------------------------------


def test_attachments_reach_the_model_and_are_recorded(client, agent_payload):
    conversation = _conversation(client, agent_payload)
    doc = _upload(client, "notes.txt", b"The exam is on Friday.").json()
    image = _upload(client, "diagram.png", PNG).json()

    status, events = _send(client, conversation["id"], "Summarise these", [doc["id"], image["id"]])

    assert status == 200
    reply = next(data for name, data in events if name == "done")["content"]
    assert "Summarise these" in reply
    assert "[Attached file: notes.txt]" in reply
    assert "The exam is on Friday." in reply
    assert "[image diagram.png]" in reply

    user = client.get(f"/api/conversations/{conversation['id']}/messages").json()[0]
    assert [a["filename"] for a in user["meta"]["attachments"]] == ["notes.txt", "diagram.png"]


def test_a_message_can_be_only_an_attachment(client, agent_payload):
    conversation = _conversation(client, agent_payload)
    doc = _upload(client, "q.txt", b"What is 2+2?").json()
    status, _ = _send(client, conversation["id"], "", [doc["id"]])
    assert status == 200
    convo = client.get("/api/conversations").json()
    assert next(c for c in convo if c["id"] == conversation["id"])["title"] == "q.txt"


def test_an_empty_message_is_refused(client, agent_payload):
    conversation = _conversation(client, agent_payload)
    status, _ = _send(client, conversation["id"], "   ")
    assert status == 422


def test_an_attachment_is_sent_once(client, agent_payload):
    conversation = _conversation(client, agent_payload)
    doc = _upload(client, "a.txt", b"hello").json()
    assert _send(client, conversation["id"], "first", [doc["id"]])[0] == 200
    assert _send(client, conversation["id"], "again", [doc["id"]])[0] == 422


def test_at_most_three_images_per_message(client, agent_payload):
    conversation = _conversation(client, agent_payload)
    ids = [_upload(client, f"{i}.png", PNG).json()["id"] for i in range(4)]
    assert _send(client, conversation["id"], "look", ids)[0] == 422


def test_deleting_the_conversation_deletes_its_files(client, agent_payload):
    conversation = _conversation(client, agent_payload)
    image = _upload(client, "a.png", PNG).json()
    _send(client, conversation["id"], "hi", [image["id"]])
    assert client.delete(f"/api/conversations/{conversation['id']}").status_code == 204
    with SessionLocal() as db:
        assert db.get(Attachment, image["id"]) is None


# -- history ------------------------------------------------------------------


def test_history_replays_documents_and_only_the_newest_images(client, agent_payload):
    conversation = _conversation(client, agent_payload)
    first = _upload(client, "old.png", PNG).json()
    _send(client, conversation["id"], "first", [first["id"]])
    doc = _upload(client, "notes.txt", b"Remember this.").json()
    second = _upload(client, "new.jpg", JPEG).json()
    _send(client, conversation["id"], "second", [doc["id"], second["id"]])

    with SessionLocal() as db:
        history = _load_history(db, conversation["id"], image_budget=1)

    first_turn, second_turn = history[0], history[2]
    # The older image is past the budget: a placeholder, not the bytes.
    assert isinstance(first_turn.content, str)
    assert "[Image old.png was shared earlier in the conversation]" in first_turn.content
    # The newer turn keeps its image and its document's text.
    assert [b["type"] for b in second_turn.content] == ["text", "image"]
    assert "Remember this." in second_turn.content[0]["text"]
    assert second_turn.content[1]["media_type"] == "image/jpeg"


# -- providers ----------------------------------------------------------------


def _image_turn():
    return LLMMessage(
        "user",
        [
            {"type": "text", "text": "What is in this picture?"},
            {"type": "image", "media_type": "image/png",
             "data": base64.b64encode(PNG).decode(), "name": "a.png"},
        ],
    )


def test_openai_sends_images_as_data_url_content_parts():
    messages = OpenAIProvider(api_key="k")._to_openai_messages("sys", [_image_turn()])
    user = messages[1]
    assert user["content"][0] == {"type": "text", "text": "What is in this picture?"}
    assert user["content"][1]["type"] == "image_url"
    assert user["content"][1]["image_url"]["url"].startswith("data:image/png;base64,iVBOR")


def test_openai_keeps_plain_strings_when_there_is_no_image():
    turn = LLMMessage("user", message_blocks("Read this", []))
    assert OpenAIProvider(api_key="k")._to_openai_messages("", [turn])[0]["content"] == "Read this"


def test_gemini_sends_images_as_inline_bytes():
    contents = GeminiProvider(api_key="k")._to_contents([_image_turn()])
    parts = contents[0].parts
    assert parts[0].text == "What is in this picture?"
    assert parts[1].inline_data.mime_type == "image/png"
    assert parts[1].inline_data.data == PNG
