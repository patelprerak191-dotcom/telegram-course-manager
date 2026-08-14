import os

from dotenv import load_dotenv
from supabase import Client, create_client


load_dotenv()


SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY")


def get_supabase_client() -> Client:
    if not SUPABASE_URL:
        raise RuntimeError(
            "SUPABASE_URL is missing in .env"
        )

    if not SUPABASE_SECRET_KEY:
        raise RuntimeError(
            "SUPABASE_SECRET_KEY is missing in .env"
        )

    return create_client(
        SUPABASE_URL,
        SUPABASE_SECRET_KEY,
    )


supabase: Client = get_supabase_client()