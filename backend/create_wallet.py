"""Generate a disposable wallet for local Base Sepolia testing."""

from eth_account import Account


account = Account.create()

print("TESTNET WALLET - do not use with real funds")
print("Address:", account.address)
print("Private key (shown only once):", account.key.hex())
print("Save both values in the ignored .env file and never commit the key.")
