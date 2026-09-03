from eth_account import Account

Account.enable_unaudited_hdwallet_features()

account, _ = Account.create_with_mnemonic()

print("Address:", account.address)
