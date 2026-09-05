const state = {
  apiKey: sessionStorage.getItem("stablepay_api_key") || "",
  vaultToken: sessionStorage.getItem("stablepay_vault_token") || "",
  merchant: null,
  limit: 10,
  offset: 0,
  status: "",
};

const elements = {
  loginView: document.querySelector("#login-view"),
  dashboardView: document.querySelector("#dashboard-view"),
  loginForm: document.querySelector("#login-form"),
  apiKey: document.querySelector("#api-key"),
  loginError: document.querySelector("#login-error"),
  logoutButton: document.querySelector("#logout-button"),
  refreshButton: document.querySelector("#refresh-button"),
  merchantGreeting: document.querySelector("#merchant-greeting"),
  merchantName: document.querySelector("#merchant-name"),
  merchantWallet: document.querySelector("#merchant-wallet"),
  merchantWebhook: document.querySelector("#merchant-webhook"),
  vaultConnectView: document.querySelector("#vault-connect-view"),
  vaultConnectForm: document.querySelector("#vault-connect-form"),
  vaultToken: document.querySelector("#vault-token"),
  vaultConnectError: document.querySelector("#vault-connect-error"),
  vaultDemoView: document.querySelector("#vault-demo-view"),
  vaultDisconnectButton: document.querySelector("#vault-disconnect-button"),
  demoVaultBalance: document.querySelector("#demo-vault-balance"),
  demoVaultWallet: document.querySelector("#demo-vault-wallet"),
  demoMerchantBalance: document.querySelector("#demo-merchant-balance"),
  demoMerchantName: document.querySelector("#demo-merchant-name"),
  demoDepositCount: document.querySelector("#demo-deposit-count"),
  demoActivityEmpty: document.querySelector("#demo-activity-empty"),
  demoActivityList: document.querySelector("#demo-activity-list"),
  micropaymentForm: document.querySelector("#micropayment-form"),
  micropaymentAmount: document.querySelector("#micropayment-amount"),
  micropaymentReference: document.querySelector("#micropayment-reference"),
  micropaymentError: document.querySelector("#micropayment-error"),
  micropaymentResult: document.querySelector("#micropayment-result"),
  micropaymentResultDetail: document.querySelector("#micropayment-result-detail"),
  availableBalance: document.querySelector("#available-balance"),
  reservedBalance: document.querySelector("#reserved-balance"),
  settledBalance: document.querySelector("#settled-balance"),
  settlementForm: document.querySelector("#settlement-form"),
  settlementAmount: document.querySelector("#settlement-amount"),
  settlementError: document.querySelector("#settlement-error"),
  settlementsEmpty: document.querySelector("#settlements-empty"),
  settlementsTableWrap: document.querySelector("#settlements-table-wrap"),
  settlementsBody: document.querySelector("#settlements-body"),
  totalCount: document.querySelector("#total-count"),
  pendingCount: document.querySelector("#pending-count"),
  confirmingCount: document.querySelector("#confirming-count"),
  confirmedCount: document.querySelector("#confirmed-count"),
  createForm: document.querySelector("#create-payment-form"),
  paymentAmount: document.querySelector("#payment-amount"),
  createError: document.querySelector("#create-error"),
  createdPayment: document.querySelector("#created-payment"),
  createdPaymentLink: document.querySelector("#created-payment-link"),
  statusFilter: document.querySelector("#status-filter"),
  paymentsLoading: document.querySelector("#payments-loading"),
  paymentsEmpty: document.querySelector("#payments-empty"),
  tableWrap: document.querySelector("#payments-table-wrap"),
  paymentsBody: document.querySelector("#payments-body"),
  pagination: document.querySelector("#pagination"),
  pageLabel: document.querySelector("#page-label"),
  previousPage: document.querySelector("#previous-page"),
  nextPage: document.querySelector("#next-page"),
  toast: document.querySelector("#toast"),
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

async function apiRequest(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Authorization", `Bearer ${state.apiKey}`);
  const response = await fetch(path, { ...options, headers });
  let payload = null;
  if (response.status !== 204) {
    payload = await response.json().catch(() => null);
  }
  if (response.status === 401) {
    showLogin();
    throw new Error("Your API key is invalid, expired, or revoked.");
  }
  if (!response.ok) {
    throw new Error(readableError(payload, "StablePay could not complete the request."));
  }
  return payload;
}

async function vaultRequest(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Authorization", `Bearer ${state.vaultToken}`);
  const response = await fetch(path, { ...options, headers });
  let payload = null;
  if (response.status !== 204) {
    payload = await response.json().catch(() => null);
  }
  if (response.status === 401) {
    disconnectVault();
    throw new Error("The customer vault token is invalid.");
  }
  if (!response.ok) {
    throw new Error(readableError(payload, "StablePay could not complete the vault request."));
  }
  return payload;
}

function showLogin(message = "") {
  state.apiKey = "";
  state.merchant = null;
  sessionStorage.removeItem("stablepay_api_key");
  disconnectVault();
  setHidden(elements.loginView, false);
  setHidden(elements.dashboardView, true);
  setHidden(elements.logoutButton, true);
  elements.apiKey.value = "";
  elements.loginError.textContent = message;
  setHidden(elements.loginError, !message);
}

function showDashboard() {
  setHidden(elements.loginView, true);
  setHidden(elements.dashboardView, false);
  setHidden(elements.logoutButton, false);
}

function formatAmount(amount) {
  return Number(amount).toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: 6,
  });
}

function formatDate(value) {
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function checkoutUrl(paymentId) {
  return `${window.location.origin}/checkout/${paymentId}`;
}

function showToast(message) {
  elements.toast.textContent = message;
  setHidden(elements.toast, false);
  window.clearTimeout(showToast.timeout);
  showToast.timeout = window.setTimeout(() => setHidden(elements.toast, true), 2200);
}

async function copyText(value) {
  await navigator.clipboard.writeText(value);
  showToast("Checkout link copied");
}

function renderMerchant(merchant) {
  state.merchant = merchant;
  elements.merchantGreeting.textContent = `${merchant.name} demo`;
  elements.merchantName.textContent = merchant.name;
  elements.merchantWallet.textContent = merchant.wallet_address;
  elements.merchantWallet.title = merchant.wallet_address;
  elements.merchantWebhook.textContent = merchant.webhook_url;
  elements.merchantWebhook.title = merchant.webhook_url;
  elements.demoMerchantName.textContent = merchant.name;
}

function shortValue(value, start = 8, end = 6) {
  if (!value || value.length <= start + end + 3) return value || "";
  return `${value.slice(0, start)}...${value.slice(-end)}`;
}

function disconnectVault() {
  state.vaultToken = "";
  sessionStorage.removeItem("stablepay_vault_token");
  elements.vaultToken.value = "";
  setHidden(elements.vaultConnectView, false);
  setHidden(elements.vaultDemoView, true);
  setHidden(elements.micropaymentResult, true);
}

function describeActivity(entry) {
  const labels = {
    deposit: "Vault funded",
    micropayment: "Micropayment",
    settlement: "Settlement movement",
  };
  return labels[entry.transaction_type] || entry.transaction_type.replaceAll("_", " ");
}

function makeLedgerItem(entry) {
  const item = document.createElement("div");
  item.className = "ledger-item";

  const copy = document.createElement("div");
  const heading = document.createElement("strong");
  heading.textContent = `${entry.role}: ${describeActivity(entry)}`;
  const reference = document.createElement("span");
  reference.textContent = `${entry.reference} - ${formatDate(entry.created_at)}`;
  copy.append(heading, reference);

  const amount = document.createElement("b");
  const numericAmount = Number(entry.amount);
  amount.className = numericAmount >= 0 ? "ledger-credit" : "ledger-debit";
  amount.textContent = `${numericAmount > 0 ? "+" : ""}${formatAmount(entry.amount)} USDC`;
  item.append(copy, amount);
  return item;
}

function renderDemoActivity(vaultActivity, merchantActivity) {
  const entries = [
    ...vaultActivity.map((entry) => ({ ...entry, role: "Customer" })),
    ...merchantActivity.map((entry) => ({ ...entry, role: "Merchant" })),
  ]
    .sort((left, right) => new Date(right.created_at) - new Date(left.created_at))
    .slice(0, 10);

  elements.demoActivityList.replaceChildren(...entries.map(makeLedgerItem));
  setHidden(elements.demoActivityEmpty, entries.length !== 0);
  setHidden(elements.demoActivityList, entries.length === 0);
}

function renderVaultData(vault, deposits, vaultActivity, merchantActivity) {
  elements.demoVaultBalance.textContent = `${formatAmount(vault.balance)} ${vault.currency}`;
  elements.demoVaultWallet.textContent = shortValue(vault.wallet_address);
  elements.demoVaultWallet.title = vault.wallet_address;
  const confirmedDeposits = deposits.filter((deposit) => deposit.status === "confirmed").length;
  elements.demoDepositCount.textContent = `${confirmedDeposits} confirmed ${confirmedDeposits === 1 ? "deposit" : "deposits"}`;
  renderDemoActivity(vaultActivity, merchantActivity);
  setHidden(elements.vaultConnectView, true);
  setHidden(elements.vaultDemoView, false);
}

async function loadVaultData() {
  if (!state.vaultToken) return;
  const [vault, deposits, vaultActivity, merchantActivity] = await Promise.all([
    vaultRequest("/vaults/me"),
    vaultRequest("/vaults/deposits?limit=20"),
    vaultRequest("/vaults/activity?limit=20"),
    apiRequest("/merchants/me/activity?limit=20"),
  ]);
  renderVaultData(vault, deposits, vaultActivity, merchantActivity);
}

function makePaymentRow(payment) {
  const row = document.createElement("tr");

  const idCell = document.createElement("td");
  const idCode = document.createElement("code");
  idCode.textContent = `${payment.id.slice(0, 12)}…`;
  idCode.title = payment.id;
  idCell.append(idCode);

  const amountCell = document.createElement("td");
  amountCell.textContent = `${formatAmount(payment.amount)} ${payment.currency}`;

  const statusCell = document.createElement("td");
  const status = document.createElement("span");
  status.className = `status-pill ${payment.status}`;
  status.textContent = payment.status;
  statusCell.append(status);

  const dateCell = document.createElement("td");
  dateCell.textContent = formatDate(payment.created_at);

  const actionCell = document.createElement("td");
  actionCell.className = "table-actions";
  const openLink = document.createElement("a");
  openLink.href = checkoutUrl(payment.id);
  openLink.target = "_blank";
  openLink.rel = "noopener";
  openLink.textContent = "Open ↗";
  const copyButton = document.createElement("button");
  copyButton.type = "button";
  copyButton.textContent = "Copy";
  copyButton.addEventListener("click", () => copyText(checkoutUrl(payment.id)));
  actionCell.append(copyButton, openLink);

  row.append(idCell, amountCell, statusCell, dateCell, actionCell);
  return row;
}

function renderPayments(result) {
  const counts = result.status_counts;
  elements.totalCount.textContent = String(
    counts.pending + counts.confirming + counts.confirmed + counts.expired,
  );
  elements.pendingCount.textContent = String(counts.pending);
  elements.confirmingCount.textContent = String(counts.confirming);
  elements.confirmedCount.textContent = String(counts.confirmed);

  elements.paymentsBody.replaceChildren(...result.items.map(makePaymentRow));
  setHidden(elements.paymentsLoading, true);
  setHidden(elements.paymentsEmpty, result.items.length !== 0);
  setHidden(elements.tableWrap, result.items.length === 0);

  const start = result.total === 0 ? 0 : result.offset + 1;
  const end = Math.min(result.offset + result.items.length, result.total);
  elements.pageLabel.textContent = `${start}–${end} of ${result.total}`;
  elements.previousPage.disabled = result.offset === 0;
  elements.nextPage.disabled = result.offset + result.items.length >= result.total;
  setHidden(elements.pagination, result.total <= state.limit);
}

function settlementExplorerUrl(transactionHash) {
  return `https://sepolia.basescan.org/tx/${transactionHash}`;
}

function makeSettlementRow(settlement) {
  const row = document.createElement("tr");
  const idCell = document.createElement("td");
  const idCode = document.createElement("code");
  idCode.textContent = `${settlement.id.slice(0, 12)}…`;
  idCode.title = settlement.id;
  idCell.append(idCode);

  const amountCell = document.createElement("td");
  amountCell.textContent = `${formatAmount(settlement.amount)} ${settlement.currency}`;
  const statusCell = document.createElement("td");
  const status = document.createElement("span");
  status.className = `status-pill ${settlement.status}`;
  status.textContent = settlement.status.replace("_", " ");
  statusCell.append(status);
  const destinationCell = document.createElement("td");
  const destination = document.createElement("code");
  destination.textContent = `${settlement.destination_address.slice(0, 10)}…`;
  destination.title = settlement.destination_address;
  destinationCell.append(destination);

  const actionCell = document.createElement("td");
  actionCell.className = "table-actions";
  if (settlement.transaction_hash) {
    const explorer = document.createElement("a");
    explorer.href = settlementExplorerUrl(settlement.transaction_hash);
    explorer.target = "_blank";
    explorer.rel = "noopener";
    explorer.textContent = "Explorer ↗";
    actionCell.append(explorer);
  } else if (settlement.status === "pending") {
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.textContent = "Cancel";
    cancel.addEventListener("click", () => cancelSettlement(settlement.id));
    actionCell.append(cancel);
  }

  row.append(idCell, amountCell, statusCell, destinationCell, actionCell);
  return row;
}

function renderSettlementData(balance, settlements) {
  elements.availableBalance.textContent = `${formatAmount(balance.available_balance)} USDC`;
  elements.reservedBalance.textContent = `${formatAmount(balance.reserved_balance)} USDC`;
  elements.settledBalance.textContent = `${formatAmount(balance.settled_balance)} USDC`;
  elements.demoMerchantBalance.textContent = `${formatAmount(balance.available_balance)} USDC`;
  elements.settlementsBody.replaceChildren(...settlements.map(makeSettlementRow));
  setHidden(elements.settlementsEmpty, settlements.length !== 0);
  setHidden(elements.settlementsTableWrap, settlements.length === 0);
}

async function loadSettlementData() {
  const [balance, settlements] = await Promise.all([
    apiRequest("/merchants/me/balance"),
    apiRequest("/merchants/me/settlements?limit=10"),
  ]);
  renderSettlementData(balance, settlements);
}

async function cancelSettlement(settlementId) {
  try {
    await apiRequest(`/merchants/me/settlements/${settlementId}/cancel`, {
      method: "POST",
    });
    await loadSettlementData();
    showToast("Settlement cancelled; balance restored");
  } catch (error) {
    showToast(error.message);
  }
}

async function loadPayments() {
  setHidden(elements.paymentsLoading, false);
  const parameters = new URLSearchParams({
    limit: String(state.limit),
    offset: String(state.offset),
  });
  if (state.status) parameters.set("status", state.status);
  const result = await apiRequest(`/payments?${parameters}`);
  renderPayments(result);
}

async function loadVaultIfConnected() {
  if (!state.vaultToken) return;
  try {
    await loadVaultData();
  } catch (error) {
    showToast(error.message);
  }
}

async function loadDashboard() {
  const merchant = await apiRequest("/merchants/me");
  renderMerchant(merchant);
  showDashboard();
  await Promise.all([
    loadPayments(),
    loadSettlementData(),
    loadVaultIfConnected(),
  ]);
}

elements.vaultConnectForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = elements.vaultConnectForm.querySelector("button[type='submit']");
  state.vaultToken = elements.vaultToken.value.trim();
  setHidden(elements.vaultConnectError, true);
  button.disabled = true;
  button.textContent = "Connecting...";
  try {
    await loadVaultData();
    sessionStorage.setItem("stablepay_vault_token", state.vaultToken);
    showToast("Customer vault connected");
  } catch (error) {
    elements.vaultConnectError.textContent = error.message;
    setHidden(elements.vaultConnectError, false);
  } finally {
    button.disabled = false;
    button.textContent = "Connect customer vault";
  }
});

elements.vaultDisconnectButton.addEventListener("click", () => {
  disconnectVault();
  showToast("Customer vault disconnected");
});

elements.micropaymentForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = elements.micropaymentForm.querySelector("button[type='submit']");
  setHidden(elements.micropaymentError, true);
  setHidden(elements.micropaymentResult, true);
  button.disabled = true;
  button.textContent = "Calling API and paying...";
  try {
    if (!state.merchant) throw new Error("Merchant account is not loaded.");
    const payment = await vaultRequest("/vaults/micropayments", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": `dashboard-demo-${crypto.randomUUID()}`,
      },
      body: JSON.stringify({
        merchant_id: state.merchant.id,
        amount: elements.micropaymentAmount.value.trim(),
        reference: elements.micropaymentReference.value.trim(),
      }),
    });
    elements.micropaymentResultDetail.textContent =
      `${formatAmount(payment.amount)} ${payment.currency} moved internally. Receipt ${shortValue(payment.id, 12, 4)}.`;
    setHidden(elements.micropaymentResult, false);
    await Promise.all([loadVaultData(), loadSettlementData()]);
    showToast("Micropayment confirmed instantly");
  } catch (error) {
    elements.micropaymentError.textContent = error.message;
    setHidden(elements.micropaymentError, false);
  } finally {
    button.disabled = false;
    button.textContent = "Call API and pay";
  }
});

elements.loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submitButton = elements.loginForm.querySelector("button[type='submit']");
  state.apiKey = elements.apiKey.value.trim();
  elements.loginError.textContent = "";
  setHidden(elements.loginError, true);
  submitButton.disabled = true;
  submitButton.textContent = "Checking key…";
  try {
    sessionStorage.setItem("stablepay_api_key", state.apiKey);
    await loadDashboard();
  } catch (error) {
    showLogin(error.message);
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "Continue";
  }
});

elements.settlementForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = elements.settlementForm.querySelector("button[type='submit']");
  const amount = elements.settlementAmount.value.trim();
  setHidden(elements.settlementError, true);
  button.disabled = true;
  button.textContent = "Reserving…";
  try {
    const body = amount ? { amount } : {};
    await apiRequest("/merchants/me/settlements", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": crypto.randomUUID(),
      },
      body: JSON.stringify(body),
    });
    elements.settlementAmount.value = "";
    await loadSettlementData();
    showToast("Settlement requested");
  } catch (error) {
    elements.settlementError.textContent = error.message;
    setHidden(elements.settlementError, false);
  } finally {
    button.disabled = false;
    button.textContent = "Request settlement";
  }
});

elements.logoutButton.addEventListener("click", () => showLogin());
elements.refreshButton.addEventListener("click", async () => {
  elements.refreshButton.disabled = true;
  try {
    await loadDashboard();
    showToast("Dashboard refreshed");
  } catch (error) {
    showToast(error.message);
  } finally {
    elements.refreshButton.disabled = false;
  }
});

elements.createForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submitButton = elements.createForm.querySelector("button[type='submit']");
  setHidden(elements.createError, true);
  setHidden(elements.createdPayment, true);
  submitButton.disabled = true;
  submitButton.textContent = "Creating…";
  try {
    const payment = await apiRequest("/payments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ amount: elements.paymentAmount.value.trim() }),
    });
    const url = checkoutUrl(payment.id);
    elements.createdPaymentLink.href = url;
    setHidden(elements.createdPayment, false);
    elements.paymentAmount.value = "";
    state.offset = 0;
    await loadPayments();
  } catch (error) {
    elements.createError.textContent = error.message;
    setHidden(elements.createError, false);
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "Create payment request";
  }
});

elements.statusFilter.addEventListener("change", async () => {
  state.status = elements.statusFilter.value;
  state.offset = 0;
  await loadPayments().catch((error) => showToast(error.message));
});

elements.previousPage.addEventListener("click", async () => {
  state.offset = Math.max(0, state.offset - state.limit);
  await loadPayments().catch((error) => showToast(error.message));
});

elements.nextPage.addEventListener("click", async () => {
  state.offset += state.limit;
  await loadPayments().catch((error) => showToast(error.message));
});

if (state.apiKey) {
  loadDashboard().catch((error) => showLogin(error.message));
} else {
  showLogin();
}
