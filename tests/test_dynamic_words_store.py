import json

from dynamic_words_store import DynamicWordsStore


class NotFound(Exception):
    pass


class PreconditionFailed(Exception):
    pass


class FakeBlob:
    def __init__(self, shared_state):
        self.state = shared_state

    @property
    def generation(self):
        return self.state["generation"]

    def download_as_text(self):
        if self.state["payload"] is None:
            raise NotFound("missing")
        return self.state["payload"]

    def upload_from_string(self, payload, content_type=None, if_generation_match=None):
        current_generation = self.state["generation"]
        if if_generation_match is None:
            raise AssertionError("if_generation_match should always be set")
        if current_generation != if_generation_match:
            raise PreconditionFailed("generation mismatch")
        self.state["payload"] = payload
        self.state["generation"] = current_generation + 1


class FakeBucket:
    def __init__(self, shared_state):
        self.shared_state = shared_state

    def blob(self, _object_key):
        return FakeBlob(self.shared_state)


class FakeClient:
    def __init__(self, shared_state):
        self.shared_state = shared_state

    def bucket(self, _bucket_name):
        return FakeBucket(self.shared_state)


def _client_factory(shared_state):
    return lambda: FakeClient(shared_state)


def _payload_count(payload):
    data = json.loads(payload)
    return sum(len(v) for v in data["words"].values())


def test_missing_object_loads_empty():
    state = {"payload": None, "generation": 0}
    store = DynamicWordsStore(
        enabled=True,
        bucket_name="words-bucket",
        object_key="words/custom_words.v1.json",
        client_factory=_client_factory(state),
    )

    store.load_snapshot()

    assert store.total_count == 0
    assert store.last_generation == 0


def test_lookup_and_upsert_roundtrip():
    state = {"payload": None, "generation": 0}
    store = DynamicWordsStore(
        enabled=True,
        bucket_name="words-bucket",
        object_key="words/custom_words.v1.json",
        client_factory=_client_factory(state),
    )

    store.load_snapshot()
    store.upsert(
        {
            "english": "Umbrella",
            "translation": "గొడుగు",
            "romanized": "godugu",
            "emoji": "✏️",
            "language": "telugu",
            "category": "custom",
        }
    )

    hit = store.lookup("umbrella", "telugu")
    assert hit is not None
    assert hit["translation"] == "గొడుగు"


def test_flush_by_count_threshold():
    state = {"payload": None, "generation": 0}
    store = DynamicWordsStore(
        enabled=True,
        bucket_name="words-bucket",
        object_key="words/custom_words.v1.json",
        flush_interval_sec=300,
        flush_max_new_words=2,
        client_factory=_client_factory(state),
    )

    store.load_snapshot()
    store.upsert({"english": "one", "translation": "ఒకటి", "romanized": "okati", "emoji": "✏️", "language": "telugu", "category": "custom"})
    assert store.flush_if_needed(force=False) is False

    store.upsert({"english": "two", "translation": "రెండు", "romanized": "rendu", "emoji": "✏️", "language": "telugu", "category": "custom"})
    assert store.flush_if_needed(force=False) is True
    assert state["payload"] is not None
    assert _payload_count(state["payload"]) == 2


def test_flush_by_age_threshold():
    state = {"payload": None, "generation": 0}
    clock = [1000.0]

    def now():
        return clock[0]

    store = DynamicWordsStore(
        enabled=True,
        bucket_name="words-bucket",
        object_key="words/custom_words.v1.json",
        flush_interval_sec=300,
        flush_max_new_words=50,
        client_factory=_client_factory(state),
        time_fn=now,
    )

    store.load_snapshot()
    store.upsert({"english": "sun", "translation": "సూర్యుడు", "romanized": "suryudu", "emoji": "☀️", "language": "telugu", "category": "custom"})
    assert store.flush_if_needed(force=False) is False

    clock[0] += 301
    assert store.flush_if_needed(force=False) is True
    assert _payload_count(state["payload"]) == 1


def test_conflict_merge_retry_keeps_local_and_remote_changes():
    # Remote already has one word.
    remote_payload = {
        "schema_version": 1,
        "updated_at": "2026-03-03T00:00:00Z",
        "words": {
            "telugu": {
                "moon": {
                    "english": "moon",
                    "translation": "చంద్రుడు",
                    "romanized": "chandrudu",
                    "emoji": "🌙",
                    "language": "telugu",
                    "category": "custom",
                }
            },
            "assamese": {},
        },
    }
    state = {"payload": json.dumps(remote_payload), "generation": 3}

    store = DynamicWordsStore(
        enabled=True,
        bucket_name="words-bucket",
        object_key="words/custom_words.v1.json",
        client_factory=_client_factory(state),
    )

    store.load_snapshot()
    # Simulate another writer modifying remote after we loaded.
    state["generation"] = 4

    store.upsert({"english": "sun", "translation": "సూర్యుడు", "romanized": "suryudu", "emoji": "☀️", "language": "telugu", "category": "custom"})
    assert store.flush_if_needed(force=True) is True

    final_data = json.loads(state["payload"])
    telugu_words = final_data["words"]["telugu"]
    assert "moon" in telugu_words
    assert "sun" in telugu_words
