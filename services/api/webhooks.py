"""
Webhook delivery system for LabLink AI.

Handles firing webhooks to subscribed endpoints with:
- HMAC-SHA256 signature verification
- Exponential backoff retry logic
- Automatic deactivation after repeated failures
"""

import asyncio
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

import httpx
from sqlalchemy.orm import Session

from database import (
    SessionLocal, WebhookSubscription, WebhookEvent,
    AuditAction, EntityType, log_audit
)

logger = logging.getLogger(__name__)

# Configuration
MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 1.0
MAX_BACKOFF_SECONDS = 30.0
BACKOFF_MULTIPLIER = 2.0
REQUEST_TIMEOUT_SECONDS = 10.0
MAX_CONSECUTIVE_FAILURES = 10


def compute_signature(payload: str, secret: str, timestamp: str) -> str:
    """
    Compute HMAC-SHA256 signature for webhook payload.

    The signature is computed over: timestamp + "." + payload
    This prevents replay attacks by including the timestamp.
    """
    message = f"{timestamp}.{payload}"
    signature = hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return signature


def build_webhook_payload(
    event_type: WebhookEvent,
    org_id: str,
    data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Build the webhook payload structure.

    Standard payload format for all webhook events.
    """
    return {
        "event": event_type.value,
        "org_id": org_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }


async def deliver_webhook(
    subscription: WebhookSubscription,
    event_type: WebhookEvent,
    payload: Dict[str, Any],
    db: Session,
) -> bool:
    """
    Deliver a webhook to a single subscription with retry logic.

    Args:
        subscription: The webhook subscription
        event_type: Type of event being delivered
        payload: The event payload
        db: Database session for updating subscription status

    Returns:
        True if delivery succeeded, False otherwise
    """
    payload_json = json.dumps(payload, sort_keys=True, default=str)
    timestamp = datetime.now(timezone.utc).isoformat()
    signature = compute_signature(payload_json, subscription.secret, timestamp)

    headers = {
        "Content-Type": "application/json",
        "X-LabLink-Event": event_type.value,
        "X-LabLink-Signature": f"sha256={signature}",
        "X-LabLink-Timestamp": timestamp,
        "X-LabLink-Webhook-ID": str(subscription.id),
        "User-Agent": "LabLink-Webhooks/1.0",
    }

    backoff = INITIAL_BACKOFF_SECONDS
    last_error = None

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        for attempt in range(MAX_RETRIES):
            try:
                response = await client.post(
                    subscription.url,
                    content=payload_json,
                    headers=headers,
                )

                # Success: 2xx status codes
                if 200 <= response.status_code < 300:
                    # Reset failure count on success
                    subscription.failure_count = 0
                    subscription.last_triggered_at = datetime.now(timezone.utc)
                    db.commit()

                    logger.info(
                        f"Webhook delivered: subscription={subscription.id}, "
                        f"event={event_type.value}, status={response.status_code}"
                    )
                    return True

                # Client error (4xx): Don't retry, it won't help
                if 400 <= response.status_code < 500:
                    last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                    logger.warning(
                        f"Webhook client error: subscription={subscription.id}, "
                        f"status={response.status_code}"
                    )
                    break

                # Server error (5xx): Retry with backoff
                last_error = f"HTTP {response.status_code}"
                logger.warning(
                    f"Webhook server error: subscription={subscription.id}, "
                    f"status={response.status_code}, attempt={attempt + 1}"
                )

            except httpx.TimeoutException:
                last_error = "Request timeout"
                logger.warning(
                    f"Webhook timeout: subscription={subscription.id}, "
                    f"attempt={attempt + 1}"
                )

            except httpx.RequestError as e:
                last_error = str(e)
                logger.warning(
                    f"Webhook request error: subscription={subscription.id}, "
                    f"error={e}, attempt={attempt + 1}"
                )

            # Wait before retry (except on last attempt)
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(backoff)
                backoff = min(backoff * BACKOFF_MULTIPLIER, MAX_BACKOFF_SECONDS)

    # All retries failed
    subscription.failure_count += 1
    subscription.last_triggered_at = datetime.now(timezone.utc)

    # Deactivate after too many consecutive failures
    if subscription.failure_count >= MAX_CONSECUTIVE_FAILURES:
        subscription.active = False
        logger.error(
            f"Webhook deactivated after {MAX_CONSECUTIVE_FAILURES} failures: "
            f"subscription={subscription.id}"
        )

    db.commit()

    logger.error(
        f"Webhook delivery failed: subscription={subscription.id}, "
        f"event={event_type.value}, error={last_error}"
    )
    return False


async def fire_webhooks(
    org_id: str,
    event_type: WebhookEvent,
    data: Dict[str, Any],
    db: Optional[Session] = None,
) -> Dict[str, Any]:
    """
    Fire webhooks for an event to all subscribed endpoints.

    Args:
        org_id: Organization ID
        event_type: Type of event
        data: Event-specific data
        db: Database session (optional, will create one if not provided)

    Returns:
        Summary of delivery results
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        # Find active subscriptions for this org and event type
        subscriptions = (
            db.query(WebhookSubscription)
            .filter(
                WebhookSubscription.org_id == org_id,
                WebhookSubscription.active == True,
                WebhookSubscription.events.contains([event_type.value])
            )
            .all()
        )

        if not subscriptions:
            return {
                "event": event_type.value,
                "org_id": org_id,
                "subscriptions_matched": 0,
                "delivered": 0,
                "failed": 0,
            }

        # Build payload
        payload = build_webhook_payload(event_type, org_id, data)

        # Deliver to all subscriptions concurrently
        results = await asyncio.gather(
            *[
                deliver_webhook(sub, event_type, payload, db)
                for sub in subscriptions
            ],
            return_exceptions=True
        )

        # Count successes and failures
        delivered = sum(1 for r in results if r is True)
        failed = len(results) - delivered

        return {
            "event": event_type.value,
            "org_id": org_id,
            "subscriptions_matched": len(subscriptions),
            "delivered": delivered,
            "failed": failed,
        }

    finally:
        if close_db:
            db.close()


def fire_webhooks_sync(
    org_id: str,
    event_type: WebhookEvent,
    data: Dict[str, Any],
    db: Optional[Session] = None,
) -> Dict[str, Any]:
    """
    Synchronous wrapper for fire_webhooks.

    Use this when calling from synchronous code (e.g., background tasks).
    """
    return asyncio.run(fire_webhooks(org_id, event_type, data, db))


async def send_test_webhook(
    subscription: WebhookSubscription,
    db: Session,
) -> Dict[str, Any]:
    """
    Send a test webhook to verify subscription configuration.

    Returns:
        Dict with success status and any error details
    """
    test_payload = build_webhook_payload(
        event_type=WebhookEvent.FILE_INGESTED,  # Use any event type for test
        org_id=subscription.org_id,
        data={
            "test": True,
            "message": "This is a test webhook from LabLink AI",
            "subscription_id": subscription.id,
        }
    )

    payload_json = json.dumps(test_payload, sort_keys=True, default=str)
    timestamp = datetime.now(timezone.utc).isoformat()
    signature = compute_signature(payload_json, subscription.secret, timestamp)

    headers = {
        "Content-Type": "application/json",
        "X-LabLink-Event": "test",
        "X-LabLink-Signature": f"sha256={signature}",
        "X-LabLink-Timestamp": timestamp,
        "X-LabLink-Webhook-ID": str(subscription.id),
        "User-Agent": "LabLink-Webhooks/1.0",
    }

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(
                subscription.url,
                content=payload_json,
                headers=headers,
            )

            return {
                "success": 200 <= response.status_code < 300,
                "status_code": response.status_code,
                "response_body": response.text[:500] if response.text else None,
            }

    except httpx.TimeoutException:
        return {
            "success": False,
            "error": "Request timeout",
        }
    except httpx.RequestError as e:
        return {
            "success": False,
            "error": str(e),
        }


def verify_webhook_signature(
    payload: str,
    signature: str,
    timestamp: str,
    secret: str,
) -> bool:
    """
    Verify a webhook signature.

    This function can be used by webhook consumers to verify
    that the webhook came from LabLink AI.

    Args:
        payload: Raw request body
        signature: Value of X-LabLink-Signature header (without "sha256=" prefix)
        timestamp: Value of X-LabLink-Timestamp header
        secret: The webhook secret

    Returns:
        True if signature is valid
    """
    if signature.startswith("sha256="):
        signature = signature[7:]

    expected = compute_signature(payload, secret, timestamp)
    return hmac.compare_digest(signature, expected)
