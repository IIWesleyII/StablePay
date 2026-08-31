import asyncio

from blockchain.base import BaseSepoliaClient


async def check_connection() -> None:
    async with BaseSepoliaClient.from_settings() as client:
        status = await client.check_connection()

    print("Base Sepolia connection successful")
    print("Chain ID:", status.chain_id)
    print("Latest block:", status.latest_block)
    print("USDC contract:", status.usdc_address)
    print("USDC contract bytecode size:", status.usdc_code_size, "bytes")
    print("Token metadata:", status.usdc_symbol, status.usdc_decimals, "decimals")


if __name__ == "__main__":
    asyncio.run(check_connection())
