from enum import Enum


class WebhookDeliveryStatus(str, Enum):
    """The allowed delivery states for a durable webhook event."""

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
