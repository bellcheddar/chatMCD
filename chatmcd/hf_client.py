"""Client for the chatMCD Hugging Face ZeroGPU Space.

Holds the HF token, streams tokens back to the Flask layer, and turns the two
things that actually go wrong on ZeroGPU into states the UI can render:

  * a cold start (the Space was asleep and is building/booting)  -> "warming"
  * the GPU quota queue                                          -> "queued"

Both surface as a `status` event on the stream rather than an exception, so the
front end shows "warming up" instead of an error.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Iterator

log = logging.getLogger(__name__)

# Substrings that mean "the Space is not ready yet", not "the request was wrong".
_WARMING = ("is building", "is sleeping", "starting", "no application file",
            "runtime error", "connection", "not ready", "502", "503", "504")
_QUEUED = ("quota", "gpu task aborted", "too many", "queue")


@dataclass
class ChatChunk:
    """One event on the stream."""
    kind: str          # "token" | "status" | "done" | "error"
    text: str = ""
    detail: str = ""


class SpaceClient:
    def __init__(self, space_id: str, token: str | None, timeout: float = 180.0):
        self.space_id = space_id
        self.token = token
        self.timeout = timeout
        self._client = None
        self._lock = threading.Lock()
        self._warm_at = 0.0

    # -- connection ---------------------------------------------------------

    def _connect(self):
        """Create the gradio_client lazily; it does a network round trip.

        The token kwarg was renamed from `hf_token` to `token` in gradio_client
        2.x. Both spellings are attempted so the app runs against either, rather
        than pinning a version and discovering the mismatch in production — which
        is exactly how this surfaced.
        """
        from gradio_client import Client

        for kw in ("token", "hf_token"):
            try:
                return Client(self.space_id, verbose=False, **{kw: self.token})
            except TypeError as e:
                if kw not in str(e):
                    raise
        # Neither kwarg is accepted: connect anonymously rather than not at all.
        return Client(self.space_id, verbose=False)

    def client(self, force: bool = False):
        with self._lock:
            if self._client is None or force:
                self._client = self._connect()
            return self._client

    @property
    def warm(self) -> bool:
        return time.time() - self._warm_at < 900

    def ping(self) -> bool:
        """Keep-warm probe. Cheap: just re-establishes the config handshake."""
        try:
            self.client(force=True)
            self._warm_at = time.time()
            return True
        except Exception as e:  # a sleeping Space is the normal case here
            log.info("keep-warm ping failed: %s", e)
            return False

    # -- generation ---------------------------------------------------------

    @staticmethod
    def _classify(err: Exception) -> ChatChunk:
        msg = str(err).lower()
        if any(s in msg for s in _QUEUED):
            return ChatChunk("status", "queued",
                             "The GPU is busy. Holding your place in the queue…")
        if any(s in msg for s in _WARMING):
            return ChatChunk("status", "warming",
                             "Waking the model up. This takes about 30 seconds on a "
                             "cold start.")
        return ChatChunk("error", "error", str(err)[:300])

    def stream(self, message: str, history: list[dict], *, temperature: float,
               top_p: float, repetition_penalty: float,
               max_new_tokens: int) -> Iterator[ChatChunk]:
        """Yield ChatChunks. The Space's predict fn is a generator that yields
        the answer so far, so we diff successive payloads into deltas."""
        try:
            client = self.client()
        except Exception as e:
            yield self._classify(e)
            # One retry after a cold start: the first call is what wakes the Space.
            time.sleep(3)
            try:
                client = self.client(force=True)
            except Exception as e2:
                yield self._classify(e2)
                return

        payload = (message, history, temperature, top_p, repetition_penalty,
                   max_new_tokens)
        try:
            job = client.submit(*payload, api_name="/chat")
        except Exception as e:
            yield self._classify(e)
            return

        sent = ""
        deadline = time.time() + self.timeout
        try:
            for partial in job:
                if time.time() > deadline:
                    yield ChatChunk("error", "error", "Timed out waiting for the model.")
                    return
                text = partial if isinstance(partial, str) else str(partial)
                if text.startswith(sent):
                    delta, sent = text[len(sent):], text
                else:
                    # The Space rewrote its output (a retry inside the fn).
                    delta, sent = text, text
                if delta:
                    yield ChatChunk("token", delta)
        except Exception as e:
            yield self._classify(e)
            return

        # A job that finishes having produced nothing is a FAILURE, and it does
        # not raise. ZeroGPU signals an exhausted quota as `event: error` with a
        # null payload; gradio_client turns that into a generator that stops
        # immediately with Status.FINISHED and no exception. Reported as success
        # it becomes {"answer": ""} with no error in any log on either side —
        # which is exactly how it presented, for an hour, while the Space itself
        # was answering the same question correctly from a different IP.
        if not sent.strip():
            log.warning("empty completion from %s (status=%s); treating as quota",
                        self.space_id, self._status_code(job))
            yield ChatChunk(
                "status", "queued",
                "The GPU quota for this server is used up. Waiting for it to reset."
                if not self.token else
                "The GPU is busy. Holding your place in the queue…")
            # The visitor gets a visitor's message. The operator's version --
            # which names the token as the fix -- goes to the log above, because
            # a public page about Marc should not be printing this server's
            # configuration advice to whoever happens to be reading.
            yield ChatChunk("error", "error",
                            "chatMCD has run out of GPU time for the moment. "
                            "Please try again shortly.")
            return

        self._warm_at = time.time()
        yield ChatChunk("done", "")

    @staticmethod
    def _status_code(job) -> str:
        try:
            return str(job.status().code)
        except Exception:
            return "unknown"


class MockClient:
    """Stand-in for the Space, so the front end can be built and screenshotted
    before the model exists and without spending ZeroGPU quota.

    Enabled with MOCK_SPACE=1. Answers come from the training corpus, so what is
    on screen during development is the sort of thing the real model will say.
    """

    ANSWERS = {
        "résumé": "Marc C. Deller, D.Phil., is a structural biologist and drug-discovery "
                  "leader with 25+ years across Oxford, Yale, Pfizer, Scripps (JCSG), "
                  "Stanford and Incyte. He is now Co-Founder and CSO of **Elora "
                  "Therapeutics** and a scientific advisor to DeepCovalent.\n\n"
                  "The tally:\n\n"
                  "- 400+ protein structures\n- 60+ publications\n- 7+ patents\n"
                  "- 6+ FDA INDs\n- two wet labs built from the ground up",
        "fun": "He named his structure-prediction tool **BoltzMaker** after a beer, "
               "and his favourite line about the job is that *if science worked on the "
               "first attempt, it would be called Search.*",
        "publication": "60+ publications and 400+ PDB depositions. His most-cited is the "
                       "2013 *Science* structure of a soluble cleaved HIV-1 envelope "
                       "trimer (~979 citations). Most recent: the povorcitinib JAK1 "
                       "discovery paper in *J. Med. Chem.* 2026, with his co-crystal "
                       "structures deposited as `10PI` and `10PJ` at 1.54 and 1.59 Å.",
        "leadership": "Marc leads by what he calls the **6 C's**: Curiosity, Courage, "
                      "Creativity, Communication, Compassion and Collaboration. He has "
                      "mentored 50+ scientists, built two automated structural biology "
                      "facilities, and managed budgets north of $8M.",
        "elora": "Elora is the pre-seed biotech Marc co-founded in 2025, where he serves "
                 "as CSO. It is engineering PET-degrading enzymes into a first-in-class "
                 "systemic therapy to reduce the microplastic burden in human blood and "
                 "tissue, with an initial focus on cardiometabolic disease.",
    }
    DEFAULT = ("That is a good question. In this development build chatMCD is running "
               "against a mock, so it can only answer the five preset prompts. Ask the "
               "real one at [chatmcd.mdeller.com](https://chatmcd.mdeller.com), or email "
               "marc@marcdeller.com.")

    def __init__(self, *_, **__):
        self._warm_at = time.time()

    @property
    def warm(self) -> bool:
        return True

    def ping(self) -> bool:
        return True

    def stream(self, message: str, history: list[dict], **_) -> Iterator[ChatChunk]:
        low = message.lower()
        text = next((v for k, v in self.ANSWERS.items() if k in low), self.DEFAULT)
        if len(history) == 0 and "résumé" not in low:
            yield ChatChunk("status", "warming",
                            "Waking the model up. This takes about 30 seconds on a "
                            "cold start.")
            time.sleep(0.9)
        for word in text.split(" "):
            time.sleep(0.022)
            yield ChatChunk("token", word + " ")
        yield ChatChunk("done", "")


class KeepWarm(threading.Thread):
    """Background thread that pings the Space so it never goes cold on a visitor."""

    daemon = True

    def __init__(self, client: SpaceClient, interval: int):
        super().__init__(name="chatmcd-keepwarm")
        self.client = client
        self.interval = interval
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.wait(self.interval):
            self.client.ping()

    def stop(self) -> None:
        self._stop.set()
