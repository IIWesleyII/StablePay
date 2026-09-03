# StablePay

StablePay is a payment gateway for accepting and tracking USDC payments on
blockchain networks such as Base.

## Local setup

Create `.env` from `.env.example` and replace its placeholder merchant address.
Then install the development dependencies and start PostgreSQL:

```bash
./venv/Scripts/python.exe -m pip install -r backend/requirements-dev.txt
docker compose up -d db
```

Apply every pending database migration:

```bash
./venv/Scripts/python.exe -m alembic -c backend/alembic.ini upgrade head
```

Start the API:

```bash
./venv/Scripts/python.exe -m uvicorn main:app \
  --app-dir backend/app \
  --host 127.0.0.1 \
  --port 8000
```

## Merchant dashboard and checkout

With the API running, open the merchant dashboard:

```text
http://127.0.0.1:8000/dashboard
```

Paste `STABLEPAY_API_KEY` into the login form. The dashboard validates the key,
keeps it only in the browser tab's session storage, and uses it as a Bearer
token for API requests. From the dashboard a merchant can:

- Review payment totals and recent requests.
- Filter payments by lifecycle status.
- Create a new USDC payment request.
- Copy or open the resulting customer checkout link.

Customer checkout links use this format:

```text
http://127.0.0.1:8000/checkout/pay_REPLACE_WITH_PAYMENT_ID
```

The public checkout reveals only the information needed to pay: merchant name,
amount, network, recipient address, expiration, payment status, and any attached
transaction hash. The customer sends the exact testnet USDC transfer and pastes
its transaction hash into checkout for verification. The page polls StablePay
for lifecycle updates until the request is confirmed or expired.

Both pages are testnet-only. The dashboard is a proof-of-concept API-key client,
not a production login system.

## Tests

Run the automated test suite from the project root:

```bash
./venv/Scripts/python.exe -m pytest -v
```

## Base Sepolia connection check

After configuring `BASE_SEPOLIA_RPC_URL`, run the read-only network check from
the project root:

```bash
cd backend/app
../../venv/Scripts/python.exe -m blockchain.check_connection
cd ../..
```

The check verifies the Base Sepolia chain ID, reads the latest block, and
confirms that the configured USDC address contains deployed contract bytecode.

## Manually verifying a testnet payment

First create a payment request:

```bash
set -a
source .env
set +a

curl -X POST http://127.0.0.1:8000/payments \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $STABLEPAY_API_KEY" \
  -d '{"amount":"0.01"}'
```

Using a browser wallet on Base Sepolia, send exactly `0.01` testnet USDC to
the `recipient_address` returned above. Copy the resulting transaction hash,
then ask StablePay to verify it:

```bash
curl -X POST \
  http://127.0.0.1:8000/payments/pay_REPLACE_WITH_PAYMENT_ID/verify \
  -H 'Content-Type: application/json' \
  -d '{"transaction_hash":"0xREPLACE_WITH_TRANSACTION_HASH"}'
```

StablePay looks up that exact transaction, checks the USDC contract, recipient,
amount, and confirmation count, and then returns either `confirming` or
`confirmed`. A confirming payment can be verified again with the same hash.
There is no last-block time limit. However, submit the transaction hash before
the payment request's `expires_at` time (15 minutes by default).

### Sending the test payment from a script

After creating a pending payment, run this from the project root:

```bash
set -a
source .env
set +a

./venv/Scripts/python.exe backend/send_test_payment.py \
  pay_REPLACE_WITH_PAYMENT_ID
```

The script retrieves the requested recipient and amount, prompts for the test
wallet private key without displaying it, shows the transfer details, and asks
for confirmation before broadcasting. It then waits for the receipt and submits
the transaction hash to StablePay automatically. Never place the private key in
the source code or pass it as a command-line argument.

## Webhook delivery

Set a unique webhook secret with at least 32 characters in your local `.env`:

```text
MERCHANT_WEBHOOK_SECRET=replace-with-a-random-local-secret
```

Do not commit the real secret. When it is configured, StablePay's background
worker claims due webhook events, signs them with HMAC-SHA256, and sends them to
`MERCHANT_WEBHOOK_URL`. Without the secret, the worker stays disabled.

Delivery is intentionally at-least-once: a rare crash after the merchant accepts
a webhook but before StablePay records success can cause a duplicate. Merchant
software should therefore store `StablePay-Event-Id` and ignore IDs it has
already processed.

### Local webhook demonstration

Use the same `MERCHANT_WEBHOOK_SECRET` value for StablePay and the local fake
merchant. Start the receiver in a separate terminal:

```bash
./venv/Scripts/python.exe -m uvicorn fake_merchant:app \
  --app-dir backend \
  --host 127.0.0.1 \
  --port 9000
```

Then start StablePay and complete a new payment. After confirmation, inspect the
accepted event at:

```text
http://127.0.0.1:9000/webhooks/received
```

The fake merchant stores events only in memory and is intended for local testing
only. Restarting it clears the received-event list.

## Creating the first merchant

After applying the migrations, create a merchant and its initial API key from
the project root:

```bash
./venv/Scripts/python.exe backend/create_merchant.py \
  --name "Example Merchant" \
  --key-name "Local development"
```

The wallet and webhook URL default to `MERCHANT_WALLET_ADDRESS` and
`MERCHANT_WEBHOOK_URL`. You can override them with `--wallet-address` and
`--webhook-url`. The plaintext API key is displayed only once; StablePay saves
only its hash. Do not commit the key or pass it directly as a command-line
argument.

Test the key against the authenticated merchant endpoint:

```bash
set -a
source .env
set +a

curl http://127.0.0.1:8000/merchants/me \
  -H "Authorization: Bearer $STABLEPAY_API_KEY"
```

Missing, malformed, expired, and revoked keys receive `401 Unauthorized`.
Inactive merchant accounts receive `403 Forbidden`.

Update the merchant settings used by future payments and webhook events:

```bash
curl -X PATCH http://127.0.0.1:8000/merchants/me \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $STABLEPAY_API_KEY" \
  -d '{"name":"Updated Merchant Name"}'
```

You may update `name`, `wallet_address`, and `webhook_url` independently.
Changing the wallet affects only new payment requests; existing payments keep
the recipient address they were created with.

## Managing merchant API keys

Create an additional key before replacing or revoking an existing key:

```bash
curl -X POST http://127.0.0.1:8000/merchants/me/api-keys \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $STABLEPAY_API_KEY" \
  -d '{"name":"Replacement key"}'
```

An optional ISO 8601 `expires_at` value can make a temporary key:

```bash
curl -X POST http://127.0.0.1:8000/merchants/me/api-keys \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $STABLEPAY_API_KEY" \
  -d '{"name":"Temporary key","expires_at":"2030-01-01T00:00:00Z"}'
```

The `api_key` value is returned only by this creation response. Save it
securely because StablePay stores only its hash. List safe metadata for all of
the merchant's keys:

```bash
curl http://127.0.0.1:8000/merchants/me/api-keys \
  -H "Authorization: Bearer $STABLEPAY_API_KEY"
```

After confirming that a replacement works, revoke the old key:

```bash
curl -X DELETE \
  http://127.0.0.1:8000/merchants/me/api-keys/key_REPLACE_WITH_KEY_ID \
  -H "Authorization: Bearer $STABLEPAY_API_KEY"
```

Revocation is immediate and permanent. StablePay keeps the revoked key's safe
metadata for auditing but never returns its secret or hash. StablePay refuses
to revoke a merchant's last active key, preventing accidental account lockout.

## Creating future migrations

After changing a SQLAlchemy model, generate a migration and review the generated
file before applying it:

```bash
./venv/Scripts/python.exe -m alembic -c backend/alembic.ini \
  revision --autogenerate -m "describe the schema change"
./venv/Scripts/python.exe -m alembic -c backend/alembic.ini upgrade head
```
