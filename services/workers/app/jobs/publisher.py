"""Publishing envelopes onto streams (workers enqueue follow-up tasks; the API has its own)."""

from leadforge_contracts.job_envelope import JobEnvelope
from redis.asyncio import Redis

from app.jobs.envelope import ENVELOPE_FIELD
from app.jobs.retry import STREAM_MAXLEN


async def publish(redis: Redis, stream: str, envelope: JobEnvelope) -> str:
    message_id = await redis.xadd(
        stream, {ENVELOPE_FIELD: envelope.to_wire()}, maxlen=STREAM_MAXLEN, approximate=True
    )
    return message_id.decode() if isinstance(message_id, bytes) else str(message_id)
