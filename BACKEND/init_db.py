import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "fsssmc.db"


def init_db():
    connection = sqlite3.connect(DB_PATH)

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS Mosque_Members (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            Title TEXT NOT NULL,
            Full_Name TEXT NOT NULL,
            Phone TEXT,
            Email TEXT,
            Address TEXT,
            Member_Type TEXT,
            Created_At TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS Donations (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            Title TEXT,
            Full_Name TEXT NOT NULL,
            Email TEXT,
            Phone TEXT,
            Amount_Donated REAL NOT NULL,
            Reference TEXT,
            Status TEXT DEFAULT 'pending',
            Payment_Provider TEXT DEFAULT 'Paystack',
            Created_At TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # Add missing columns safely
    member_columns = {row[1] for row in connection.execute("PRAGMA table_info(Mosque_Members)").fetchall()}
    for column_name, column_type in [
        ("Phone", "TEXT"),
        ("Email", "TEXT"),
        ("Address", "TEXT"),
        ("Member_Type", "TEXT"),
    ]:
        if column_name not in member_columns:
            connection.execute(f"ALTER TABLE Mosque_Members ADD COLUMN {column_name} {column_type}")

    donation_columns = {row[1] for row in connection.execute("PRAGMA table_info(Donations)").fetchall()}
    for column_name, column_type in [
        ("Title", "TEXT"),
        ("Email", "TEXT"),
        ("Phone", "TEXT"),
        ("Reference", "TEXT"),
        ("Status", "TEXT DEFAULT 'pending'"),
        ("Payment_Provider", "TEXT DEFAULT 'Paystack'"),
    ]:
        if column_name not in donation_columns:
            connection.execute(f"ALTER TABLE Donations ADD COLUMN {column_name} {column_type}")

    connection.commit()
    connection.close()
    print("Database initialized successfully.")


if __name__ == "__main__":
    init_db()
