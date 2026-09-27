import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv
from werkzeug.security import generate_password_hash

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env")

def main():
    db_path = Path(__file__).resolve().parent / "fsssmc.db"
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found at {db_path}")

    username = (os.getenv("ADMIN_USERNAME") or "admin").strip() or "admin"
    password = os.getenv("ADMIN_PASSWORD") or "Admin@123"
    full_name = "System Administrator"

    connection = sqlite3.connect(db_path)
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS Admin_Users (ID INTEGER PRIMARY KEY AUTOINCREMENT, Username TEXT UNIQUE NOT NULL, Password_Hash TEXT NOT NULL, Full_Name TEXT NOT NULL, Created_At TEXT DEFAULT CURRENT_TIMESTAMP)")

        existing = connection.execute(
            "SELECT Username FROM Admin_Users WHERE LOWER(Username) = LOWER(?)",
            (username,),
        ).fetchone()

        if existing:
            connection.execute(
                "UPDATE Admin_Users SET Password_Hash = ?, Full_Name = ? WHERE LOWER(Username) = LOWER(?)",
                (generate_password_hash(password), full_name, username),
            )
        else:
            connection.execute(
                "INSERT INTO Admin_Users (Username, Password_Hash, Full_Name) VALUES (?, ?, ?)",
                (username, generate_password_hash(password), full_name),
            )

        connection.execute(
            "DELETE FROM Admin_Users WHERE LOWER(Username) != LOWER(?)",
            (username,),
        )

        connection.commit()
        print(f"Admin account is ready: username={username}, password={password}")
        print("Other admin rows were removed.")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
