"""Generate independent pilot secrets once; never print values."""
import base64
import os
import secrets
from pathlib import Path

def main():
    os.umask(0o077)
    directory = Path(__file__).resolve().parent / "private"
    directory.mkdir(mode=0o700, exist_ok=False)
    pw = {role: secrets.token_hex(32) for role in ("chat", "flow", "clock", "redis", "admin")}
    files = {
        "postgres.env": {"POSTGRES_PASSWORD": pw["admin"], **{"PASS_"+role.upper(): pw[role] for role in ("chat","flow","clock")}},
        "redis.env": {"REDISCLI_AUTH": pw["redis"]},
        "chat.env": {"SECRET_KEY_BASE": secrets.token_hex(64), "POSTGRES_PASSWORD": pw["chat"], "REDIS_PASSWORD": pw["redis"]},
        "flow.env": {"LANGFLOW_DATABASE_URL": f"postgresql://flow:{pw['flow']}@postgres:5432/flow", "LANGFLOW_SUPERUSER_PASSWORD": secrets.token_hex(32), "LANGFLOW_SECRET_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode()},
        "clock.env": {"DB_POSTGRESDB_PASSWORD": pw["clock"], "N8N_ENCRYPTION_KEY": secrets.token_hex(32)},
    }
    for name, values in files.items():
        with (directory / name).open("x") as target:
            target.writelines(f"{k}={v}\n" for k,v in values.items())
    print("Created five private environment files.")

if __name__ == "__main__":
    main()
