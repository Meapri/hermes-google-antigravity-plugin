"""gRPC client for Antigravity FetchQuotaStatus (v1main PredictionService).

Uses raw protobuf encoding — no generated stubs required.
This gives per-model quota with ``quota_type`` distinguishing base vs extended
(Ultra/Pro credit-backed) buckets, unlike the older REST ``retrieveUserQuota``
which only returns base-tier fractions.

Proto schema (reconstructed from agy binary):

    service PredictionService {
      rpc FetchQuotaStatus(FetchQuotaStatusRequest) returns (FetchQuotaStatusResponse);
    }

    message FetchQuotaStatusRequest {}  // empty

    message FetchQuotaStatusResponse {
      repeated BucketInfo buckets = 1;

      enum QuotaType {
        QUOTA_TYPE_UNSPECIFIED = 0;
        // other values TBD — observed at runtime
      }

      message BucketInfo {
        float remaining_fraction = 1;   // fixed32 wire type
        google.protobuf.Timestamp reset_time = 2;
        QuotaType quota_type = 3;       // varint
        string model = 4;              // bytes (string)
      }
    }
"""

from __future__ import annotations

import datetime
import logging
import struct
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

GRPC_HOST = "aicode.googleapis.com:443"
# agy (Antigravity CLI) exposes FetchQuotaStatus on the aicode v1alpha
# PredictionService. The stripped binary also contains v1main symbols, but the
# public endpoint currently returns UNIMPLEMENTED for v1main.
GRPC_METHOD = "/google.gca.aicode.v1alpha.PredictionService/FetchQuotaStatus"

# QuotaType enum values (reconstructed — will be confirmed at runtime)
QUOTA_TYPE_UNSPECIFIED = 0


@dataclass
class QuotaBucket:
    """A single model quota bucket from FetchQuotaStatus."""
    model: str = ""
    remaining_fraction: float = 0.0
    reset_time: str = ""         # ISO 8601
    quota_type: int = 0          # enum value
    quota_type_name: str = ""    # human-readable label
    raw: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Protobuf helpers (no dependency on protobuf library)
# ---------------------------------------------------------------------------

def _decode_varint(data: bytes, pos: int) -> tuple[int, int]:
    val = 0
    shift = 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        val |= (b & 0x7F) << shift
        shift += 7
        if not (b & 0x80):
            break
    return val, pos


def _decode_timestamp(data: bytes) -> str:
    """Decode google.protobuf.Timestamp → ISO 8601 string."""
    pos = 0
    seconds = 0
    while pos < len(data):
        tag, pos = _decode_varint(data, pos)
        fnum = tag >> 3
        wt = tag & 7
        if wt == 0:
            val, pos = _decode_varint(data, pos)
            if fnum == 1:
                seconds = val
        elif wt == 2:
            length, pos = _decode_varint(data, pos)
            pos += length
        else:
            break
    if seconds:
        return datetime.datetime.fromtimestamp(
            seconds, tz=datetime.timezone.utc
        ).isoformat()
    return ""


def _decode_bucket(data: bytes) -> QuotaBucket:
    """Decode a BucketInfo sub-message."""
    pos = 0
    bucket = QuotaBucket()
    raw: Dict[str, Any] = {}

    while pos < len(data):
        tag, pos = _decode_varint(data, pos)
        fnum = tag >> 3
        wt = tag & 7

        if wt == 0:  # varint
            val, pos = _decode_varint(data, pos)
            if fnum == 3:
                bucket.quota_type = val
            raw[f"varint_{fnum}"] = val

        elif wt == 2:  # length-delimited
            length, pos = _decode_varint(data, pos)
            chunk = data[pos:pos + length]
            pos += length
            if fnum == 2:
                bucket.reset_time = _decode_timestamp(chunk)
            elif fnum == 4:
                try:
                    bucket.model = chunk.decode("utf-8")
                except Exception:
                    raw[f"bytes_{fnum}"] = chunk.hex()
            else:
                try:
                    raw[f"string_{fnum}"] = chunk.decode("utf-8")
                except Exception:
                    raw[f"bytes_{fnum}"] = chunk.hex()

        elif wt == 5:  # 32-bit (float)
            val = struct.unpack("<f", data[pos:pos + 4])[0]
            pos += 4
            if fnum == 1:
                bucket.remaining_fraction = round(val, 6)
            else:
                raw[f"float_{fnum}"] = val

        elif wt == 1:  # 64-bit
            pos += 8

        else:
            break

    bucket.raw = raw
    return bucket


def _decode_response(data: bytes) -> List[QuotaBucket]:
    """Decode FetchQuotaStatusResponse top-level."""
    pos = 0
    buckets: List[QuotaBucket] = []

    while pos < len(data):
        tag, pos = _decode_varint(data, pos)
        fnum = tag >> 3
        wt = tag & 7

        if wt == 2:
            length, pos = _decode_varint(data, pos)
            chunk = data[pos:pos + length]
            pos += length
            if fnum == 1:
                buckets.append(_decode_bucket(chunk))
        elif wt == 0:
            _, pos = _decode_varint(data, pos)
        else:
            break

    return buckets


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_quota_status(
    access_token: str,
    *,
    host: str = GRPC_HOST,
    timeout: float = 30.0,
) -> Optional[List[QuotaBucket]]:
    """Call FetchQuotaStatus via gRPC and return parsed buckets.

    Returns None on import/connection errors so callers can fall back to
    the REST ``retrieveUserQuota`` path.
    """
    try:
        import grpc
    except ImportError:
        logger.debug("grpcio not installed — cannot call FetchQuotaStatus")
        return None

    try:
        channel_creds = grpc.ssl_channel_credentials()
        call_creds = grpc.access_token_call_credentials(access_token)
        composite = grpc.composite_channel_credentials(channel_creds, call_creds)

        channel = grpc.secure_channel(host, composite)
        try:
            stub = channel.unary_unary(
                GRPC_METHOD,
                request_serializer=lambda x: x,
                response_deserializer=lambda x: x,
            )
            response_bytes: bytes = stub(b"", timeout=timeout)
            buckets = _decode_response(response_bytes)

            # Label quota_type with human names
            # We'll discover actual enum values dynamically
            observed_types: set[int] = {b.quota_type for b in buckets}
            for b in buckets:
                if b.quota_type == 0:
                    b.quota_type_name = "base"
                else:
                    b.quota_type_name = f"extended ({b.quota_type})"

            return buckets
        finally:
            channel.close()

    except Exception as exc:
        logger.debug("FetchQuotaStatus gRPC call failed: %s", exc)
        return None
