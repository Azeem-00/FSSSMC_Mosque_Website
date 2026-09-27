import os
import sys

sys.path.insert(0, r"C:\Users\USER PC\OneDrive\Desktop\FSSSMC\BACKEND")

import app

app.ensure_schema()
conn = app.get_db_connection()
conn.execute("DELETE FROM User_Accounts")
conn.execute("DELETE FROM Admin_Users")
conn.execute(
    "INSERT INTO Admin_Users (Username, Password_Hash, Full_Name) VALUES (?, ?, ?)",
    ("admin", app.generate_password_hash("Admin@123"), "System Administrator"),
)
conn.commit()

client = app.app.test_client()
reg = client.post(
    "/account/register",
    data={
        "full_name": "Aisha Bello",
        "email": "aisha@example.com",
        "username": "aishabello",
        "password": "secret123",
        "confirm_password": "secret123",
        "title": "Miss",
        "phone": "08012345678",
        "address": "Lekki, Lagos",
        "member_type": "Regular Member",
    },
    follow_redirects=False,
)
print("REG", reg.status_code, reg.headers.get("Location"))
row = conn.execute(
    "SELECT Full_Name, Email, Username, Phone, Address, Member_Type, Role, Is_Admin FROM User_Accounts WHERE LOWER(Username)=LOWER(?)",
    ("aishabello",),
).fetchone()
print("ROW", dict(row))

dup = client.post(
    "/account/register",
    data={
        "full_name": "Aisha Bello",
        "email": "aisha@example.com",
        "username": "aishabello",
        "password": "secret123",
        "confirm_password": "secret123",
        "title": "Miss",
        "phone": "08012345678",
        "address": "Lekki, Lagos",
        "member_type": "Regular Member",
    },
    follow_redirects=False,
)
print("DUP", dup.status_code, dup.headers.get("Location"))

mismatch = client.post(
    "/account/register",
    data={
        "full_name": "Jane Doe",
        "email": "jane@example.com",
        "username": "janedoe",
        "password": "secret123",
        "confirm_password": "different",
        "title": "Mrs",
        "phone": "08011111111",
        "address": "Yaba, Lagos",
        "member_type": "Regular Member",
    },
    follow_redirects=False,
)
print("MISMATCH", mismatch.status_code, mismatch.headers.get("Location"))

admin = client.post(
    "/account/login",
    data={"role": "admin", "username": "admin", "password": "Admin@123"},
    follow_redirects=False,
)
print("ADMIN", admin.status_code, admin.headers.get("Location"))

user = client.post(
    "/account/login",
    data={"role": "user", "username": "aishabello", "password": "secret123"},
    follow_redirects=False,
)
print("USER", user.status_code, user.headers.get("Location"))

conn.close()
