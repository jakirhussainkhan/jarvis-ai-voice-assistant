"""Thread-safe circuit breaker for the remote LLM API.

Voice turns must fail fast. When the network or the API is unhealthy, waiting
for the SDK's timeout on every request makes Jarvis feel broken. This breaker
counts *consecutive* transient failures (5xx responses, timeouts and connection
errors) and, after a threshold, stops calling the API for a cooldown period.

States::

    CLOSED     normal -- requests are allowed
    OPEN       failing -- requests are skipped immediately
    HALF_OPEN  cooldown over -- exactly one probe request is allowed

A success closes the circuit and resets the failure counter. A failed probe
reopens it for another full cooldown.
"""

import threading
import time

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half-open"


class CircuitOpenError(RuntimeError):
    """Raised by callers when the circuit is open and a call was skipped."""


class CircuitBreaker:
    """A small, dependency-free circuit breaker.

    ``clock`` is injectable so tests can advance time without sleeping. All
    state transitions take a lock; the agent may be called from the GUI worker
    thread while a tray action reads the state.
    """

    def __init__(self, failure_threshold=3, recovery_timeout=30.0,
                 clock=time.monotonic):
        self.failure_threshold = max(1, int(failure_threshold))
        self.recovery_timeout = max(0.0, float(recovery_timeout))
        self._clock = clock
        self._lock = threading.Lock()
        self._state = CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._probe_in_flight = False

    # -- read-only views --------------------------------------------------- #
    @property
    def state(self):
        """Current state, transitioning OPEN -> HALF_OPEN after the cooldown."""
        with self._lock:
            self._refresh()
            return self._state

    @property
    def failures(self):
        """Consecutive transient failures since the last success."""
        with self._lock:
            return self._failures

    # -- request gate ------------------------------------------------------ #
    def allow_request(self):
        """Return True if a request may be attempted.

        In HALF_OPEN the first caller becomes the single probe; any concurrent
        caller is rejected until that probe records a result.
        """
        with self._lock:
            self._refresh()
            if self._state == OPEN:
                return False
            if self._state == HALF_OPEN:
                if self._probe_in_flight:
                    return False
                self._probe_in_flight = True
            return True

    # -- outcomes ---------------------------------------------------------- #
    def record_success(self):
        """A valid response: close the circuit and clear the failure streak."""
        with self._lock:
            self._state = CLOSED
            self._failures = 0
            self._opened_at = 0.0
            self._probe_in_flight = False

    def record_failure(self):
        """A transient failure: reopen at the threshold (or on a failed probe)."""
        with self._lock:
            self._failures += 1
            self._probe_in_flight = False
            if self._state == HALF_OPEN or self._failures >= self.failure_threshold:
                self._state = OPEN
                self._opened_at = self._clock()

    def record_reachable(self):
        """The API answered with a non-transient error (e.g. HTTP 400).

        That is not an outage, so a half-open probe is considered successful
        for availability purposes. The failure streak is left untouched in the
        CLOSED state: only a real success resets it.
        """
        with self._lock:
            self._probe_in_flight = False
            if self._state == HALF_OPEN:
                self._state = CLOSED
                self._failures = 0
                self._opened_at = 0.0

    def reset(self):
        """Force the circuit back to CLOSED (tests, or a manual retry)."""
        with self._lock:
            self._state = CLOSED
            self._failures = 0
            self._opened_at = 0.0
            self._probe_in_flight = False

    # -- internals --------------------------------------------------------- #
    def _refresh(self):
        """Move OPEN -> HALF_OPEN once the cooldown has elapsed."""
        if (self._state == OPEN
                and self._clock() - self._opened_at >= self.recovery_timeout):
            self._state = HALF_OPEN
            self._probe_in_flight = False
