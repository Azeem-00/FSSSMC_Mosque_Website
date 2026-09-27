from werkzeug.security import generate_password_hash

from app import app, get_db_connection, ensure_schema


def test_account_registration_stores_member_details_and_duplicate_is_rejected():
    ensure_schema()
    connection = get_db_connection()
    connection.execute("DELETE FROM User_Accounts")
    connection.execute("DELETE FROM Admin_Users")
    connection.execute("DELETE FROM sqlite_sequence WHERE name = 'User_Accounts'")
    connection.execute("DELETE FROM sqlite_sequence WHERE name = 'Admin_Users'")
    connection.commit()

    client = app.test_client()
    response = client.post(
        "/account/register",
        data={
            "full_name": "Aisha Bello",
            "email": "aisha@example.com",
            "username": "aishabello",
            "password": "secret123",
            "title": "Miss",
            "phone": "08012345678",
            "address": "Lekki, Lagos",
            "member_type": "Regular Member",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    row = connection.execute(
        "SELECT Full_Name, Email, Username, Phone, Address, Member_Type, Role, Is_Admin FROM User_Accounts WHERE LOWER(Username) = LOWER(?)",
        ("aishabello",),
    ).fetchone()
    assert row is not None
    assert row["Phone"] == "08012345678"
    assert row["Address"] == "Lekki, Lagos"
    assert row["Member_Type"] == "Regular Member"
    assert row["Role"] == "member"

    duplicate_response = client.post(
        "/account/register",
        data={
            "full_name": "Aisha Bello",
            "email": "aisha@example.com",
            "username": "aishabello",
            "password": "secret123",
            "title": "Miss",
            "phone": "08012345678",
            "address": "Lekki, Lagos",
            "member_type": "Regular Member",
        },
        follow_redirects=False,
    )

    assert duplicate_response.status_code == 302
    assert b"already+exists" in duplicate_response.headers["Location"].encode()

    connection.close()


def test_admin_can_remove_members_and_create_new_admin_accounts():
    ensure_schema()
    connection = get_db_connection()
    connection.execute("DELETE FROM Mosque_Members")
    connection.execute("DELETE FROM Admin_Users")
    connection.execute("DELETE FROM sqlite_sequence WHERE name = 'Mosque_Members'")
    connection.execute("DELETE FROM sqlite_sequence WHERE name = 'Admin_Users'")
    connection.commit()

    connection.execute(
        "INSERT INTO Mosque_Members (First_Name, Last_Name, Title, Phone_Number, Address, Phone, Email, Member_Type, Is_Active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("Ayo", "Balogun", "Brother", 8012345678, "Surulere", "08012345678", "ayo@example.com", "Regular Member", 1),
    )
    connection.execute(
        "INSERT INTO Admin_Users (Username, Password_Hash, Full_Name) VALUES (?, ?, ?)",
        ("superadmin", generate_password_hash("superpass123"), "Super Admin"),
    )
    connection.commit()
    connection.close()

    client = app.test_client()
    login = client.post(
        "/account/login",
        data={"username": "superadmin", "password": "superpass123"},
        follow_redirects=False,
    )
    assert login.status_code == 302

    remove = client.post("/api/admin/members/1/remove", follow_redirects=False)
    assert remove.status_code == 200
    payload = remove.get_json()
    assert payload["status"] == "success"
    assert payload["active"] is False

    current = client.get("/api/admin/members/current")
    assert current.status_code == 200
    assert current.get_json()["data"] == []

    removed = client.get("/api/admin/members/removed")
    assert removed.status_code == 200
    assert len(removed.get_json()["data"]) == 1

    create_admin = client.post(
        "/api/admin/users",
        json={"username": "assistantadmin", "password": "StrongPass@123", "full_name": "Assistant Admin"},
        follow_redirects=False,
    )
    assert create_admin.status_code == 200
    assert create_admin.get_json()["status"] == "success"

    check_admin = get_db_connection().execute(
        "SELECT Username FROM Admin_Users WHERE LOWER(Username) = LOWER(?)",
        ("assistantadmin",),
    ).fetchone()
    assert check_admin is not None
