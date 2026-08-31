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

## Creating future migrations

After changing a SQLAlchemy model, generate a migration and review the generated
file before applying it:

```powershell
.\venv\Scripts\python.exe -m alembic -c backend\alembic.ini revision --autogenerate -m "describe the schema change"
.\venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
```
