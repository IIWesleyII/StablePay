# StablePay

StablePay is a payment gateway for accepting and tracking USDC payments on
blockchain networks such as Base.

## Local setup

Create `.env` from `.env.example` and replace its placeholder merchant address.
Then install the development dependencies and start PostgreSQL:

```powershell
.\venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
docker compose up -d db
```

Apply every pending database migration:

```powershell
.\venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
```

Start the API using the same command you normally use from your IDE.

## Tests

Run the automated test suite from the project root:

```powershell
.\venv\Scripts\python.exe -m pytest -v
```

## Base Sepolia connection check

After configuring `BASE_SEPOLIA_RPC_URL`, run the read-only network check from
the project root:

```powershell
Set-Location backend\app
..\..\venv\Scripts\python.exe -m blockchain.check_connection
Set-Location ..\..
```

The check verifies the Base Sepolia chain ID, reads the latest block, and
confirms that the configured USDC address contains deployed contract bytecode.

## Manually verifying a testnet payment

First create a payment request:

```powershell
$payment = Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/payments `
  -ContentType "application/json" `
  -Body '{"amount":"0.01"}'

$payment
```

Using a browser wallet on Base Sepolia, send exactly `0.01` testnet USDC to
the `recipient_address` returned above. Copy the resulting transaction hash,
then ask StablePay to verify it:

```powershell
$transactionHash = "0xREPLACE_WITH_THE_REAL_TRANSACTION_HASH"

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/payments/$($payment.id)/verify" `
  -ContentType "application/json" `
  -Body (@{ transaction_hash = $transactionHash } | ConvertTo-Json)
```

StablePay looks up that exact transaction, checks the USDC contract, recipient,
amount, and confirmation count, and then returns either `confirming` or
`confirmed`. A confirming payment can be verified again with the same hash.
There is no last-block time limit. However, submit the transaction hash before
the payment request's `expires_at` time (15 minutes by default).

### Sending the test payment from a script

After creating a pending payment, run this from the project root:

```powershell
.\venv\Scripts\python.exe backend\send_test_payment.py pay_REPLACE_WITH_PAYMENT_ID
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

```powershell
.\venv\Scripts\python.exe -m uvicorn fake_merchant:app `
  --app-dir backend `
  --host 127.0.0.1 `
  --port 9000
```

Then start StablePay and complete a new payment. After confirmation, inspect the
accepted event at:

```text
http://127.0.0.1:9000/webhooks/received
```

The fake merchant stores events only in memory and is intended for local testing
only. Restarting it clears the received-event list.

## Creating future migrations

After changing a SQLAlchemy model, generate a migration and review the generated
file before applying it:

```powershell
.\venv\Scripts\python.exe -m alembic -c backend\alembic.ini revision --autogenerate -m "describe the schema change"
.\venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
```
