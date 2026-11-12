import asyncio
import asyncpg

async def main():
    try:
        conn = await asyncpg.connect(
            user="postgres.sqwgoblmwdaaziknjlbb",
            password="MwansaMatimba19890319",
            host="aws-0-eu-central-1.pooler.supabase.com",
            port=6543,
            database="postgres",
        )
        print("SUCCESS")
        await conn.close()

    except Exception as e:
        print(type(e).__name__)
        print(str(e))

asyncio.run(main())