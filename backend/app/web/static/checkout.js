const paymentId = document.body.dataset.paymentId;
let currentPayment = null;

const elements = {
  loading: document.querySelector("#checkout-loading"),
  error: document.querySelector("#checkout-error"),
  errorMessage: document.querySelector("#checkout-error-message"),
  content: document.querySelector("#checkout-content"),
  merchantName: document.querySelector("#checkout-merchant-name"),
  status: document.querySelector("#checkout-status"),
  amount: document.querySelector("#checkout-amount"),
  copyAmount: document.querySelector("#checkout-copy-amount"),
  recipient: document.querySelector("#checkout-recipient"),
  countdown: document.querySelector("#checkout-countdown"),
  paymentId: document.querySelector("#checkout-payment-id"),
  instructions: document.querySelector("#payment-instructions"),
  confirmed: document.querySelector("#confirmed-result"),
  expired: document.querySelector("#expired-result"),
  verifyForm: document.querySelector("#verify-form"),
  transactionHash: document.querySelector("#transaction-hash"),
  verifyButton: document.querySelector("#verify-button"),
  verifyMessage: document.querySelector("#verify-message"),
};

function setHidden(element, hidden) {
  element.classList.toggle("hidden", hidden);
}

function readableError(payload, fallback) {
  if (typeof payload?.detail === "string") return payload.detail;
  if (Array.isArray(payload?.detail) && payload.detail[0]?.msg) {
    return payload.detail[0].msg.replace(/^Value error, /, "");
  }
  return fallback;
}

function formatAmount(amount) {
  return Number(amount).toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 6,
  });
}

function renderPayment(payment) {
  currentPayment = payment;
  elements.merchantName.textContent = payment.merchant_name || currentPayment?.merchant_name || "Merchant";
  elements.status.textContent = payment.status;
  elements.status.className = `status-pill ${payment.status}`;
  elements.amount.textContent = formatAmount(payment.amount);
  elements.copyAmount.textContent = payment.amount;
  elements.recipient.textContent = payment.recipient_address;
  elements.paymentId.textContent = payment.id;

  if (payment.transaction_hash) {
    elements.transactionHash.value = payment.transaction_hash;
  }

  setHidden(elements.instructions, payment.status === "confirmed" || payment.status === "expired");
  setHidden(elements.confirmed, payment.status !== "confirmed");
  setHidden(elements.expired, payment.status !== "expired");
  setHidden(elements.loading, true);
  setHidden(elements.error, true);
  setHidden(elements.content, false);
  updateCountdown();
}

function updateCountdown() {
  if (!currentPayment) return;
  if (currentPayment.status === "confirmed") {
    elements.countdown.textContent = "Confirmed on Base Sepolia";
    return;
  }
  if (currentPayment.status === "expired") {
    elements.countdown.textContent = "This request has expired";
    return;
  }

  const remaining = new Date(currentPayment.expires_at).getTime() - Date.now();
  if (remaining <= 0) {
    elements.countdown.textContent = "Expiration reached — checking status…";
    return;
  }
  const totalSeconds = Math.floor(remaining / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  elements.countdown.textContent = `Expires in ${minutes}:${String(seconds).padStart(2, "0")}`;
}

async function loadPayment() {
  const response = await fetch(`/checkout/${encodeURIComponent(paymentId)}/status`);
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(readableError(payload, "This checkout could not be loaded."));
  }
  renderPayment(payload);
}

function showPageError(message) {
  elements.errorMessage.textContent = message;
  setHidden(elements.loading, true);
  setHidden(elements.content, true);
  setHidden(elements.error, false);
}

function showVerifyMessage(message, isError) {
  elements.verifyMessage.textContent = message;
  elements.verifyMessage.className = `form-message ${isError ? "error-message" : "success-message"}`;
  setHidden(elements.verifyMessage, false);
}

elements.verifyForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const transactionHash = elements.transactionHash.value.trim();
  if (!/^0x[0-9a-fA-F]{64}$/.test(transactionHash)) {
    showVerifyMessage("Enter a complete 0x transaction hash with 64 hexadecimal characters.", true);
    return;
  }

  elements.verifyButton.disabled = true;
  elements.verifyButton.textContent = "Checking Base Sepolia…";
  setHidden(elements.verifyMessage, true);
  try {
    const response = await fetch(`/payments/${encodeURIComponent(paymentId)}/verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ transaction_hash: transactionHash }),
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(readableError(payload, "StablePay could not verify this transaction."));
    }
    renderPayment({
      ...payload.payment,
      merchant_name: currentPayment.merchant_name,
    });
    if (payload.payment.status === "confirming") {
      showVerifyMessage(
        `Transfer found with ${payload.confirmations} of ${payload.required_confirmations} required confirmations. You can verify again shortly.`,
        false,
      );
    }
  } catch (error) {
    showVerifyMessage(error.message, true);
  } finally {
    elements.verifyButton.disabled = false;
    elements.verifyButton.textContent = "Verify payment";
  }
});

document.querySelectorAll("[data-copy-target]").forEach((button) => {
  button.addEventListener("click", async () => {
    const target = document.querySelector(`#${button.dataset.copyTarget}`);
    await navigator.clipboard.writeText(target.textContent.trim());
    const original = button.textContent;
    button.textContent = "Copied";
    window.setTimeout(() => { button.textContent = original; }, 1400);
  });
});

loadPayment().catch((error) => showPageError(error.message));
window.setInterval(updateCountdown, 1000);
window.setInterval(() => {
  if (currentPayment && !["confirmed", "expired"].includes(currentPayment.status)) {
    loadPayment().catch(() => {});
  }
}, 5000);
