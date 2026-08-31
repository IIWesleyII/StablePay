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

## Creating future migrations

After changing a SQLAlchemy model, generate a migration and review the generated
file before applying it:

```powershell
.\venv\Scripts\python.exe -m alembic -c backend\alembic.ini revision --autogenerate -m "describe the schema change"
.\venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
```
