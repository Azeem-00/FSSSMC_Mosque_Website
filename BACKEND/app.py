import os
import sqlite3
import uuid
from functools import wraps
from pathlib import Path
from urllib.parse import quote_plus

import requests
from dotenv import load_dotenv
from flask import Flask, abort, jsonify, redirect, render_template, render_template_string, request, send_from_directory, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(__file__).resolve().parent / "fsssmc.db"
APP_ROOT = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")
load_dotenv(APP_ROOT / ".env")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "fsssmc-admin-secret-key-2026")


@app.route("/IMAGE/<path:filename>")
def serve_image(filename):
    return send_from_directory(str(BASE_DIR / "IMAGE"), filename)


@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin")
    response.headers["Access-Control-Allow-Origin"] = origin or "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Credentials"] = "true"
    return response


def get_db_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def table_has_column(table_name, column_name):
    connection = get_db_connection()
    columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()]
    connection.close()
    return column_name in columns


def has_real_paystack_keys():
    public_key = (os.getenv("PAYSTACK_PUBLIC_KEY") or "").strip()
    secret_key = (os.getenv("PAYSTACK_SECRET_KEY") or "").strip()
    placeholder_markers = (
        "your_public_key_here",
        "your_secret_key_here",
        "pk_test_",
        "sk_test_",
    )
    if not public_key or not secret_key:
        return False
    if "your_public_key_here" in public_key.lower() or "your_secret_key_here" in secret_key.lower():
        return False
    if not public_key.startswith("pk_") or not secret_key.startswith("sk_"):
        return False
    return True


def ensure_schema():
    connection = get_db_connection()

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS Mosque_Members (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            First_Name TEXT NOT NULL,
            Last_Name TEXT NOT NULL,
            Title TEXT NOT NULL,
            Phone_Number INTEGER UNIQUE,
            Address TEXT DEFAULT 'Lagos',
            Phone TEXT,
            Email TEXT,
            Member_Type TEXT,
            Is_Active INTEGER DEFAULT 1
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
            Payment_Provider TEXT DEFAULT 'Paystack'
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS Admin_Users (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            Username TEXT UNIQUE NOT NULL,
            Password_Hash TEXT NOT NULL,
            Full_Name TEXT NOT NULL,
            Created_At TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS User_Accounts (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            Full_Name TEXT NOT NULL,
            Email TEXT UNIQUE NOT NULL,
            Username TEXT UNIQUE NOT NULL,
            Password_Hash TEXT NOT NULL,
            Title TEXT,
            Phone TEXT,
            Address TEXT,
            Member_Type TEXT,
            Role TEXT DEFAULT 'member',
            Is_Admin INTEGER DEFAULT 0,
            Created_At TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    if not connection.execute("SELECT 1 FROM Admin_Users LIMIT 1").fetchone():
        admin_username = os.getenv("ADMIN_USERNAME", "admin")
        admin_password = os.getenv("ADMIN_PASSWORD", "Admin@123")
        connection.execute(
            "INSERT INTO Admin_Users (Username, Password_Hash, Full_Name) VALUES (?, ?, ?)",
            (admin_username, generate_password_hash(admin_password), "System Administrator"),
        )

    user_columns = {row[1] for row in connection.execute("PRAGMA table_info(User_Accounts)").fetchall()}
    missing_user_columns = [
        ("Title", "TEXT"),
        ("Phone", "TEXT"),
        ("Address", "TEXT"),
        ("Member_Type", "TEXT"),
        ("Role", "TEXT DEFAULT 'member'"),
        ("Is_Admin", "INTEGER DEFAULT 0"),
    ]
    for column_name, column_type in missing_user_columns:
        if column_name not in user_columns:
            connection.execute(f"ALTER TABLE User_Accounts ADD COLUMN {column_name} {column_type}")

    member_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(Mosque_Members)").fetchall()
    }
    missing_member_columns = [
        ("Phone", "TEXT"),
        ("Email", "TEXT"),
        ("Member_Type", "TEXT"),
        ("Is_Active", "INTEGER DEFAULT 1"),
    ]
    for column_name, column_type in missing_member_columns:
        if column_name not in member_columns:
            connection.execute(
                f"ALTER TABLE Mosque_Members ADD COLUMN {column_name} {column_type}"
            )

    connection.execute(
        "UPDATE Mosque_Members SET Is_Active = 1 WHERE Is_Active IS NULL"
    )

    donation_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(Donations)").fetchall()
    }
    missing_donation_columns = [
        ("Title", "TEXT"),
        ("Email", "TEXT"),
        ("Phone", "TEXT"),
        ("Reference", "TEXT"),
        ("Status", "TEXT DEFAULT 'pending'"),
        ("Payment_Provider", "TEXT DEFAULT 'Paystack'"),
    ]
    for column_name, column_type in missing_donation_columns:
        if column_name not in donation_columns:
            connection.execute(
                f"ALTER TABLE Donations ADD COLUMN {column_name} {column_type}"
            )

    if not table_has_column("Donations", "Created_At"):
        connection.execute("ALTER TABLE Donations ADD COLUMN Created_At TEXT DEFAULT CURRENT_TIMESTAMP")

    connection.commit()
    connection.close()


@app.before_request
def init_db_on_request():
    ensure_schema()


def admin_login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect("/account/login?role=admin")
        return view(*args, **kwargs)
    return wrapped_view


def user_login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("user_logged_in"):
            return redirect("/account/login")
        return view(*args, **kwargs)
    return wrapped_view


@app.route("/")
@app.route("/home")
@app.route("/home.html")
def index_page():
    user_logged_in = bool(session.get("user_logged_in"))
    username = (session.get("user_username") or "User").strip() or "User"

    if user_logged_in:
        auth_links = """
        <div class="profile-menu" aria-label="Profile menu">
          <button type="button" class="profile-trigger" aria-expanded="false" aria-controls="profile-menu-items">
            <span class="profile-icon">👤</span>
            <span class="profile-name">{username}</span>
            <span class="caret">▾</span>
          </button>
          <div id="profile-menu-items" class="profile-menu-items" role="menu">
            <a href="/account/profile" role="menuitem" data-icon="👤">Profile</a>
            <a href="/account/edit-profile" role="menuitem" data-icon="✏️">Edit Profile</a>
            <a href="/account/logout" class="logout-link" role="menuitem" data-icon="⎋">Sign Out</a>
          </div>
        </div>
        <style>
          .profile-menu {
            position: relative;
            display: inline-flex;
            align-items: center;
            margin-top: 28px;
            z-index: 25;
          }
          .profile-trigger {
            display: inline-flex;
            align-items: center;
            gap: 10px;
            padding: 14px 22px;
            border-radius: 999px;
            background: linear-gradient(135deg, rgba(255,255,255,0.18), rgba(255,255,255,0.08));
            border: 1px solid rgba(255, 255, 255, 0.26);
            color: #ffffff;
            font-weight: 700;
            box-shadow: 0 18px 32px rgba(39, 145, 93, 0.24);
            cursor: pointer;
            backdrop-filter: blur(6px);
            transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
          }
          .profile-trigger:hover {
            transform: translateY(-1px);
            box-shadow: 0 22px 34px rgba(39, 145, 93, 0.3);
            border-color: rgba(255, 255, 255, 0.4);
          }
          .profile-icon {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 28px;
            height: 28px;
            border-radius: 50%;
            background: rgba(255, 255, 255, 0.14);
            color: #ffffff;
            font-size: 0.9rem;
            box-shadow: inset 0 0 0 1px rgba(255,255,255,0.12);
          }
          .profile-name { white-space: nowrap; }
          .caret { font-size: 0.8rem; }
          .profile-menu-items {
            position: absolute;
            left: 0;
            top: calc(100% + 12px);
            min-width: 210px;
            background: rgba(255, 255, 255, 0.97);
            border: 1px solid rgba(15, 61, 46, 0.12);
            border-radius: 16px;
            box-shadow: 0 18px 30px rgba(13, 44, 35, 0.18);
            opacity: 0;
            pointer-events: none;
            transform: translateY(6px);
            transition: all 0.2s ease;
            z-index: 30;
            overflow: hidden;
            display: block;
            padding: 8px;
          }
          .profile-menu:hover .profile-menu-items, .profile-menu:focus-within .profile-menu-items, .profile-menu.open .profile-menu-items {
            opacity: 1;
            pointer-events: auto;
            transform: translateY(0);
          }
          .profile-menu-items a {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 12px 14px;
            color: #0f5f35;
            text-decoration: none;
            font-weight: 700;
            border-radius: 10px;
            background: rgba(255,255,255,0.8);
            transition: transform 0.18s ease, background 0.18s ease, box-shadow 0.18s ease;
          }
          .profile-menu-items a::before {
            content: attr(data-icon);
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 28px;
            height: 28px;
            border-radius: 9px;
            background: linear-gradient(135deg, #edf9f0, #dff3e7);
            color: #0f5f35;
            font-size: 0.9rem;
            box-shadow: inset 0 0 0 1px rgba(15, 95, 53, 0.08);
          }
          .profile-menu-items a:last-child { border-bottom: none; }
          .profile-menu-items a:hover {
            background: #f3faf5;
            transform: translateX(2px);
            box-shadow: 0 8px 18px rgba(15, 95, 53, 0.08);
          }
          .profile-menu-items .logout-link { color: #ad3b3b; }
          .profile-menu-items .logout-link::before {
            background: linear-gradient(135deg, #fff1f0, #fce4e1);
            color: #ad3b3b;
          }
        </style>
        <script>
          document.addEventListener('DOMContentLoaded', function() {
            const menu = document.querySelector('.profile-menu');
            if (!menu) return;
            const trigger = menu.querySelector('.profile-trigger');
            const items = menu.querySelector('.profile-menu-items');
            if (!trigger || !items) return;

            function setMenuState(open) {
              menu.classList.toggle('open', open);
              trigger.setAttribute('aria-expanded', String(open));
            }

            trigger.addEventListener('click', function(e) {
              e.preventDefault();
              e.stopPropagation();
              const isOpen = menu.classList.contains('open');
              setMenuState(!isOpen);
            });

            document.addEventListener('click', function(event) {
              if (!menu.contains(event.target)) {
                setMenuState(false);
              }
            });
          });
        </script>
        """.replace("{username}", username)
    else:
        auth_links = """
        <div class="cta-row">
          <a class="btn btn-primary" href="/account/register">Create Account</a>
          <a class="btn btn-secondary" href="/account/login">Login</a>
        </div>
        """

    home_html = (BASE_DIR / "home.html").read_text(encoding="utf-8")
    return render_template_string(home_html, account_cta_html=auth_links)


@app.route("/account/register", methods=["GET", "POST"])
def user_register():
    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        email = (request.form.get("email") or "").strip()
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        confirm_password = request.form.get("confirm_password")
        title = (request.form.get("title") or "").strip()
        phone = normalize_phone(request.form.get("phone") or "")
        address = (request.form.get("address") or "").strip() or "Lagos"
        member_type = (request.form.get("member_type") or "").strip()

        if not full_name or not email or not username or not password or not title or not member_type:
            return redirect(
                "/account/register?error=Please+fill+in+all+required+fields"
                f"&full_name={quote_plus(full_name)}&email={quote_plus(email)}&username={quote_plus(username)}"
                f"&title={quote_plus(title)}&phone={quote_plus(request.form.get('phone') or '')}&address={quote_plus(address)}&member_type={quote_plus(member_type)}"
            )

        if confirm_password is not None and confirm_password != "" and password != confirm_password:
            return redirect(
                "/account/register?error=Passwords+do+not+match"
                f"&full_name={quote_plus(full_name)}&email={quote_plus(email)}&username={quote_plus(username)}"
                f"&title={quote_plus(title)}&phone={quote_plus(request.form.get('phone') or '')}&address={quote_plus(address)}&member_type={quote_plus(member_type)}"
            )

        if len(password) < 6:
            return redirect(
                "/account/register?error=Password+must+be+at+least+6+characters"
                f"&full_name={quote_plus(full_name)}&email={quote_plus(email)}&username={quote_plus(username)}"
                f"&title={quote_plus(title)}&phone={quote_plus(request.form.get('phone') or '')}&address={quote_plus(address)}&member_type={quote_plus(member_type)}"
            )

        phone_text = phone or (request.form.get("phone") or "").strip()
        connection = get_db_connection()
        existing = connection.execute(
            """
            SELECT ID FROM User_Accounts
            WHERE LOWER(Username) = LOWER(?)
               OR LOWER(Email) = LOWER(?)
               OR (? != '' AND LOWER(Phone) = LOWER(?))
            """,
            (username, email, phone_text, phone_text),
        ).fetchone()

        if existing:
            connection.close()
            return redirect("/account/login?error=Account+already+exists")

        password_hash = generate_password_hash(password)
        first_name, last_name = split_full_name(full_name)
        phone_number = None
        if phone and phone.isdigit():
            try:
                phone_number = int(phone)
            except ValueError:
                phone_number = None

        connection.execute(
            """
            INSERT INTO User_Accounts (Full_Name, Email, Username, Password_Hash, Title, Phone, Address, Member_Type, Role, Is_Admin)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'member', 0)
            """,
            (full_name, email, username, password_hash, title, phone_text, address, member_type),
        )
        connection.execute(
            """
            INSERT INTO Mosque_Members (First_Name, Last_Name, Title, Phone_Number, Address, Phone, Email, Member_Type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (first_name, last_name, title, phone_number, address, phone_text, email, member_type),
        )
        connection.commit()
        connection.close()

        return redirect("/account/login?registered=1")

    return send_from_directory(str(BASE_DIR), "account-register.html")


@app.route("/account/login", methods=["GET", "POST"])
def user_login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        requested_role = (request.form.get("role") or "").strip().lower()

        if not username or not password:
            return redirect(f"/account/login?error=Please+enter+your+username+and+password&username={quote_plus(username)}")

        if requested_role == "admin":
            connection = get_db_connection()
            admin = connection.execute(
                "SELECT * FROM Admin_Users WHERE LOWER(Username) = LOWER(?)",
                (username,),
            ).fetchone()
            connection.close()

            if admin and check_password_hash(admin["Password_Hash"], password):
                session.clear()
                session["admin_logged_in"] = True
                session["admin_id"] = admin["ID"]
                session["admin_username"] = admin["Username"]
                session["admin_full_name"] = admin["Full_Name"]
                return redirect("/admin?welcome=1")

            return redirect(f"/account/login?error=Invalid+username+or+password&username={quote_plus(username)}")

        connection = get_db_connection()
        user = connection.execute(
            "SELECT * FROM User_Accounts WHERE LOWER(Username) = LOWER(?) OR LOWER(Email) = LOWER(?)",
            (username, username),
        ).fetchone()
        connection.close()

        if user and check_password_hash(user["Password_Hash"], password):
            session.clear()
            session["user_logged_in"] = True
            session["user_id"] = user["ID"]
            session["user_username"] = user["Username"]
            session["user_full_name"] = user["Full_Name"]
            session["user_email"] = user["Email"]
            return redirect("/")

        connection = get_db_connection()
        admin = connection.execute(
            "SELECT * FROM Admin_Users WHERE LOWER(Username) = LOWER(?)",
            (username,),
        ).fetchone()
        connection.close()

        if admin and check_password_hash(admin["Password_Hash"], password):
            session.clear()
            session["admin_logged_in"] = True
            session["admin_id"] = admin["ID"]
            session["admin_username"] = admin["Username"]
            session["admin_full_name"] = admin["Full_Name"]
            return redirect("/admin?welcome=1")

        if not user and not admin:
            return redirect("/account/register?error=Account+does+not+exist")

        return redirect("/account/login?error=Invalid+username+or+password")

    return send_from_directory(str(BASE_DIR), "account-login.html")


@app.route("/account/edit-profile", methods=["GET", "POST"])
@user_login_required
def edit_profile():
    user_id = session.get("user_id")
    connection = get_db_connection()
    user = connection.execute(
        "SELECT * FROM User_Accounts WHERE ID = ?",
        (user_id,),
    ).fetchone()
    connection.close()

    if not user:
        return redirect("/account/login")

    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        email = (request.form.get("email") or "").strip()
        username = (request.form.get("username") or "").strip()
        title = (request.form.get("title") or "").strip()
        phone = normalize_phone(request.form.get("phone") or "")
        address = (request.form.get("address") or "").strip() or "Lagos"
        member_type = (request.form.get("member_type") or "").strip()
        new_password = request.form.get("new_password") or ""
        confirm_password = request.form.get("confirm_password") or ""

        if not full_name or not email or not username or not title or not member_type:
            return redirect("/account/edit-profile?error=Please+fill+in+all+required+fields")

        if new_password and len(new_password) < 6:
            return redirect("/account/edit-profile?error=Password+must+be+at+least+6+characters")

        if new_password and new_password != confirm_password:
            return redirect("/account/edit-profile?error=Passwords+do+not+match")

        connection = get_db_connection()
        duplicate_username = connection.execute(
            "SELECT ID FROM User_Accounts WHERE ID != ? AND LOWER(Username) = LOWER(?)",
            (user_id, username),
        ).fetchone()
        duplicate_email = connection.execute(
            "SELECT ID FROM User_Accounts WHERE ID != ? AND LOWER(Email) = LOWER(?)",
            (user_id, email),
        ).fetchone()
        duplicate_phone = None
        if phone:
            duplicate_phone = connection.execute(
                "SELECT ID FROM User_Accounts WHERE ID != ? AND (? != '' AND LOWER(Phone) = LOWER(?))",
                (user_id, phone, phone),
            ).fetchone()

        if duplicate_username or duplicate_email or duplicate_phone:
            connection.close()
            return redirect("/account/edit-profile?error=Account+with+that+username+or+email+already+exists")

        password_hash = user["Password_Hash"]
        if new_password:
            password_hash = generate_password_hash(new_password)

        first_name, last_name = split_full_name(full_name)
        phone_number = None
        if phone and phone.isdigit():
            try:
                phone_number = int(phone)
            except ValueError:
                phone_number = None

        connection.execute(
            """
            UPDATE User_Accounts
            SET Full_Name = ?, Email = ?, Username = ?, Password_Hash = ?, Title = ?, Phone = ?, Address = ?, Member_Type = ?
            WHERE ID = ?
            """,
            (full_name, email, username, password_hash, title, phone, address, member_type, user_id),
        )
        connection.execute(
            """
            UPDATE Mosque_Members
            SET First_Name = ?, Last_Name = ?, Title = ?, Phone_Number = ?, Address = ?, Phone = ?, Email = ?, Member_Type = ?
            WHERE LOWER(Email) = LOWER(?) OR (Phone IS NOT NULL AND Phone = ?)
            """,
            (first_name, last_name, title, phone_number, address, phone, email, member_type, user["Email"], user["Phone"]),
        )
        connection.commit()
        connection.close()

        session["user_full_name"] = full_name
        session["user_username"] = username
        session["user_email"] = email
        return redirect("/account/profile?updated=1")

    error_message = request.args.get("error")
    return render_template_string(
        """
        <!doctype html>
        <html lang="en">
          <head>
            <meta charset="UTF-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1.0" />
            <title>Edit Profile</title>
            <style>
              :root {
                --bg: #edf5f0; --panel: #ffffff; --soft: #f5faf7; --green: #1d8f4d; --green-dark: #0f5f35; --green-soft: #ecf9f1; --text: #1e2d2b; --muted: #5d6d68; --line: #dfeae4; --danger: #ad3b3b; --shadow: 0 24px 52px rgba(15,44,31,0.12); --shadow-soft: 0 16px 28px rgba(15,44,31,0.08);
              }
              * { box-sizing: border-box; }
              body {
                margin: 0; font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
                background:
                  radial-gradient(circle at top left, rgba(29,143,77,0.12), transparent 30%),
                  linear-gradient(135deg, #edf7f0, #f8fbf8 45%, #eef4ef);
                color: var(--text);
              }
              .page-shell { max-width: 1100px; margin: 0 auto; padding: 30px 20px 60px; }
              .breadcrumb {
                display: flex; align-items: center; gap: 10px; font-size: 0.92rem; color: var(--muted);
                margin-bottom: 18px; font-weight: 600;
              }
              .breadcrumb a { color: var(--green-dark); text-decoration: none; font-weight: 700; }
              .topbar {
                background: linear-gradient(135deg, #1a8b53, var(--green-dark));
                color: white; border-radius: 22px 22px 0 0; padding: 22px 28px;
                box-shadow: 0 20px 40px rgba(15,95,53,0.18);
              }
              .topbar-inner {
                display: flex; justify-content: space-between; align-items: center; gap: 14px;
              }
              .title-wrap { display: flex; align-items: center; gap: 14px; }
              .title-icon {
                width: 46px; height: 46px; display: inline-flex; align-items: center; justify-content: center;
                border-radius: 14px; background: rgba(255,255,255,0.15); border: 1px solid rgba(255,255,255,0.18);
                font-size: 1.4rem;
              }
              h1 { margin: 0; font-size: clamp(1.8rem, 2vw, 2.4rem); }
              .card {
                background: linear-gradient(180deg, #ffffff 0%, #f8fbf9 100%);
                border: 1px solid rgba(14, 86, 56, 0.08);
                border-radius: 0 0 28px 28px;
                box-shadow: 0 28px 60px rgba(15,44,31,0.12);
                padding: 28px 26px 30px;
              }
              .error {
                background: #fff1f1; color: var(--danger); border: 1px solid #f0d2d2; border-radius: 14px;
                padding: 12px 14px; margin-bottom: 18px; font-weight: 700;
              }
              form {
                display: grid; grid-template-columns: 1fr; gap: 18px;
              }
              .full {
                width: 100%;
              }
              label {
                display: block; margin-bottom: 9px; font-size: 0.75rem; letter-spacing: 0.12em; text-transform: uppercase; font-weight: 800; color: var(--green-dark);
              }
              input, select {
                width: 100%; min-height: 58px; padding: 15px 18px; border: 1px solid rgba(15, 95, 53, 0.13);
                border-radius: 18px; font-size: 1rem; background: linear-gradient(180deg, #ffffff, #f7faf8);
                color: var(--text); transition: all 0.2s ease; box-shadow: inset 0 1px 2px rgba(15,95,53,0.02);
              }
              select {
                -webkit-appearance: none; appearance: none;
                background-image:
                  linear-gradient(45deg, transparent 50%, #0f5f35 50%),
                  linear-gradient(135deg, #0f5f35 50%, transparent 50%);
                background-position:
                  calc(100% - 20px) calc(50% - 3px),
                  calc(100% - 14px) calc(50% - 3px);
                background-size: 6px 6px, 6px 6px;
                background-repeat: no-repeat;
                padding-right: 44px;
              }
              input::placeholder { color: #7c8d88; }
              input:focus, select:focus {
                outline: none; border-color: var(--green);
                box-shadow: 0 0 0 4px rgba(29,143,77,0.12), inset 0 1px 2px rgba(15,95,53,0.04);
              }
              .password-wrap {
                position: relative;
                display: flex;
                align-items: center;
                min-height: 58px;
                border: 1px solid rgba(15, 95, 53, 0.14);
                border-radius: 18px;
                background: linear-gradient(180deg, #ffffff, #f7faf8);
                box-shadow: inset 0 1px 2px rgba(15,95,53,0.03);
                transition: all 0.2s ease;
              }
              .password-wrap:focus-within {
                border-color: var(--green);
                box-shadow: 0 0 0 4px rgba(29,143,77,0.12), inset 0 1px 2px rgba(15,95,53,0.04);
              }
              .password-wrap input {
                width: 100%;
                min-height: 58px;
                border: none;
                background: transparent;
                padding: 15px 94px 15px 18px;
                border-radius: 18px;
                box-shadow: none;
              }
              .password-wrap input:focus {
                outline: none;
                box-shadow: none;
              }
              .toggle-password {
                position: absolute;
                right: 10px;
                top: 50%;
                transform: translateY(-50%);
                border: none;
                background: transparent;
                box-shadow: none;
                color: var(--green-dark);
                border-radius: 0;
                padding: 0;
                margin: 0;
                font-size: 0.74rem;
                font-weight: 700;
                letter-spacing: 0.08em;
                text-transform: uppercase;
                cursor: pointer;
                width: auto;
                min-width: 0;
                line-height: 1;
                z-index: 2;
                transition: none;
              }
              .toggle-password:hover,
              .toggle-password:focus {
                background: transparent;
                box-shadow: none;
                outline: none;
              }
              .actions {
                display: flex; justify-content: flex-start; gap: 12px; margin-top: 10px;
              }
              button, .secondary {
                display: inline-flex; align-items: center; justify-content: center; min-height: 50px; padding: 12px 20px; border-radius: 14px; text-decoration: none; font-weight: 800; border: none; cursor: pointer;
              }
              button {
                background: linear-gradient(135deg, var(--green), var(--green-dark)); color: white; box-shadow: 0 18px 28px rgba(15,95,53,0.18);
              }
              .secondary {
                background: var(--green-soft); color: var(--green-dark); border: 1px solid rgba(15,95,53,0.1);
              }
              @media (max-width: 700px) { .topbar-inner { flex-direction: column; align-items: flex-start; } }
            </style>
          </head>
          <body>
            <div class="page-shell">
              <nav class="breadcrumb" aria-label="Breadcrumb">
                <a href="/">Home</a>
                <span>›</span>
                <a href="/account/profile">Profile</a>
                <span>›</span>
                <span>Edit Profile</span>
              </nav>

              <div class="topbar">
                <div class="topbar-inner">
                  <h1>Edit Profile</h1>
                  <a class="secondary" href="/account/profile">Back to Profile</a>
                </div>
              </div>

              <div class="card">
                {% if error_message %}
                <div class="error">{{ error_message }}</div>
                {% endif %}

                <form method="POST" action="/account/edit-profile">
                  <div class="full">
                    <label for="full_name">Full Name</label>
                    <input type="text" id="full_name" name="full_name" value="{{ user['Full_Name'] }}" required />
                  </div>

                  <div>
                    <label for="title">Title</label>
                    <select id="title" name="title" required>
                      <option value="" {% if not user['Title'] %}selected{% endif %}>Select title</option>
                      <option value="Alhaji" {% if user['Title'] == 'Alhaji' %}selected{% endif %}>Alhaji</option>
                      <option value="Alhaja" {% if user['Title'] == 'Alhaja' %}selected{% endif %}>Alhaja</option>
                      <option value="Mr" {% if user['Title'] == 'Mr' %}selected{% endif %}>Mr</option>
                      <option value="Mrs" {% if user['Title'] == 'Mrs' %}selected{% endif %}>Mrs</option>
                      <option value="Miss" {% if user['Title'] == 'Miss' %}selected{% endif %}>Miss</option>
                      <option value="Brother" {% if user['Title'] == 'Brother' %}selected{% endif %}>Brother</option>
                      <option value="Sister" {% if user['Title'] == 'Sister' %}selected{% endif %}>Sister</option>
                    </select>
                  </div>

                  <div>
                    <label for="member_type">Membership Type</label>
                    <select id="member_type" name="member_type" required>
                      <option value="" {% if not user['Member_Type'] %}selected{% endif %}>Select type</option>
                      <option value="Regular Member" {% if user['Member_Type'] == 'Regular Member' %}selected{% endif %}>Regular Member</option>
                      <option value="Volunteer" {% if user['Member_Type'] == 'Volunteer' %}selected{% endif %}>Volunteer</option>
                      <option value="Youth Member" {% if user['Member_Type'] == 'Youth Member' %}selected{% endif %}>Youth Member</option>
                      <option value="Supporter" {% if user['Member_Type'] == 'Supporter' %}selected{% endif %}>Supporter</option>
                    </select>
                  </div>

                  <div>
                    <label for="phone">Phone</label>
                    <input type="tel" id="phone" name="phone" value="{{ user['Phone'] or '' }}" />
                  </div>

                  <div>
                    <label for="email">Email</label>
                    <input type="email" id="email" name="email" value="{{ user['Email'] }}" required />
                  </div>

                  <div class="full">
                    <label for="address">Address</label>
                    <input type="text" id="address" name="address" value="{{ user['Address'] or '' }}" />
                  </div>

                  <div class="full">
                    <label for="username">Username</label>
                    <input type="text" id="username" name="username" value="{{ user['Username'] }}" required />
                  </div>

                  <div class="full">
                    <label for="new_password">New Password</label>
                    <div class="password-wrap">
                      <input type="password" id="new_password" name="new_password" placeholder="Leave blank to keep current password" />
                      <button type="button" class="toggle-password" data-target="new_password" aria-label="Show password">Hide</button>
                    </div>
                  </div>

                  <div class="full">
                    <label for="confirm_password">Repeat Password</label>
                    <div class="password-wrap">
                      <input type="password" id="confirm_password" name="confirm_password" placeholder="Repeat new password" />
                      <button type="button" class="toggle-password" data-target="confirm_password" aria-label="Show password">Hide</button>
                    </div>
                  </div>

                  <div class="actions">
                    <button type="submit">Save Changes</button>
                    <a class="secondary" href="/account/profile">Cancel</a>
                  </div>
                </form>
              </div>
            </div>

            <script>
              document.querySelectorAll('.toggle-password').forEach((button) => {
                button.addEventListener('click', () => {
                  const targetId = button.dataset.target;
                  const input = document.getElementById(targetId);
                  if (!input) return;

                  const isPassword = input.type === 'password';
                  input.type = isPassword ? 'text' : 'password';
                  button.textContent = isPassword ? 'Show' : 'Hide';
                  button.setAttribute('aria-label', isPassword ? 'Hide password' : 'Show password');
                });
              });
            </script>
          </body>
        </html>
        """,
        user=user,
        error_message=error_message,
    )


@app.route("/account/profile")
@user_login_required
def user_profile():
    full_name = session.get("user_full_name") or "User"
    username = session.get("user_username") or "user"
    email = session.get("user_email") or "user@example.com"
    welcome_message = request.args.get("welcome") == "1"
    updated_message = request.args.get("updated") == "1"
    return render_template_string(
        """
        <!doctype html>
        <html lang="en">
          <head>
            <meta charset="UTF-8" />
            <meta name="viewport" content="width=device-width, initial-scale=1.0" />
            <title>My Profile</title>
            <style>
              :root {
                --bg: #edf5f0; --panel: #ffffff; --soft: #f5faf7; --green: #1d8f4d; --green-dark: #0f5f35; --green-soft: #ebf9f0; --text: #1e2d2b; --muted: #5d6d68; --line: #dfeae4; --shadow: 0 24px 52px rgba(15,44,31,0.12); --shadow-soft: 0 18px 30px rgba(15,44,31,0.08)
              }
              * { box-sizing: border-box; }
              body {
                margin: 0; font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
                background:
                  radial-gradient(circle at top left, rgba(29,143,77,0.12), transparent 25%),
                  linear-gradient(135deg, #eef7f1, #f8fbf8 45%, #edf4ef);
                color: var(--text);
              }
              .page-shell { max-width: 1100px; margin: 0 auto; padding: 30px 20px 60px; }
              .breadcrumb {
                display: flex; align-items: center; gap: 10px; font-size: 0.92rem; color: var(--muted);
                margin-bottom: 18px; font-weight: 600;
              }
              .breadcrumb a { color: var(--green-dark); text-decoration: none; font-weight: 700; }
              .topbar {
                background: linear-gradient(135deg, #1a8b53, var(--green-dark));
                color: white; border-radius: 22px 22px 0 0; padding: 22px 28px;
                box-shadow: 0 20px 40px rgba(15,95,53,0.18);
              }
              .topbar-inner {
                display: flex; justify-content: space-between; align-items: center; gap: 14px;
              }
              .title-wrap {
                display: flex; align-items: center; gap: 14px;
              }
              .title-icon {
                width: 48px; height: 48px; display: inline-flex; align-items: center; justify-content: center;
                border-radius: 15px; background: rgba(255,255,255,0.14); border: 1px solid rgba(255,255,255,0.18);
                font-size: 1.5rem;
              }
              h1 { margin: 0; font-size: clamp(1.8rem, 2vw, 2.5rem); }
              .logout {
                display: inline-flex; align-items: center; justify-content: center; padding: 10px 18px; border-radius: 999px;
                background: rgba(255,255,255,0.12); border: 1px solid rgba(255,255,255,0.24); color: white; text-decoration: none; font-weight: 700;
              }
              .profile-card {
                background: linear-gradient(180deg, rgba(255,255,255,0.98), rgba(245,250,247,0.96));
                border: 1px solid rgba(14, 86, 56, 0.08); border-radius: 0 0 22px 22px; box-shadow: var(--shadow); padding: 30px 24px;
              }
              .alert {
                background: #edf9f1; color: #0f5f35; border: 1px solid #cfe8d5; border-radius: 12px; padding: 12px 14px; margin-bottom: 18px; font-weight: 700;
              }
              .profile-header {
                display: flex; align-items: center; gap: 18px; padding: 8px 0 22px; margin-bottom: 24px; border-bottom: 1px solid rgba(15,95,53,0.09);
              }
              .avatar {
                width: 86px; height: 86px; border-radius: 24px; display: inline-flex; align-items: center; justify-content: center;
                background: linear-gradient(135deg, #eaf9f0, #d9f0e3); border: 1px solid rgba(15,95,53,0.12); font-size: 2.2rem;
                box-shadow: var(--shadow-soft);
              }
              .profile-name { font-size: clamp(1.5rem, 2vw, 2.1rem); font-weight: 800; }
              .profile-subtitle { color: var(--muted); font-weight: 600; }
              .info-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px 26px; margin-top: 14px; }
              .info-box {
                background: linear-gradient(135deg, #f9fbfa, #f4faf5); border: 1px solid rgba(15,95,53,0.1); border-radius: 18px; padding: 20px 18px; box-shadow: inset 0 1px 0 rgba(255,255,255,0.8); transition: transform 0.2s ease, box-shadow 0.2s ease;
              }
              .info-box:hover {
                transform: translateY(-2px); box-shadow: 0 12px 22px rgba(15,95,53,0.08);
              }
              .label { display: block; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; font-size: 0.72rem; margin-bottom: 8px; }
              .value { font-size: 1.08rem; font-weight: 700; }
              .actions { margin-top: 28px; display: flex; gap: 12px; flex-wrap: wrap; }
              .link-btn, .secondary {
                display: inline-flex; align-items: center; justify-content: center; min-height: 48px; padding: 12px 18px; border-radius: 12px; text-decoration: none; font-weight: 700;
              }
              .link-btn {
                background: linear-gradient(135deg, #1e9b59, var(--green-dark)); color: white; box-shadow: 0 15px 24px rgba(15,95,53,0.14);
              }
              .secondary { background: var(--green-soft); color: var(--green-dark); border: 1px solid rgba(15,95,53,0.1); }
              @media (max-width: 700px) { .info-grid { grid-template-columns: 1fr; } .topbar-inner { flex-direction: column; align-items: flex-start; } }
            </style>
          </head>
          <body>
            <div class="page-shell">
              <nav class="breadcrumb" aria-label="Breadcrumb">
                <a href="/">Home</a>
                <span>›</span>
                <span>Profile</span>
              </nav>

              <div class="topbar">
                <div class="topbar-inner">
                  <h1>My Profile</h1>
                  <a class="logout" href="/account/logout">Logout</a>
                </div>
              </div>

              <div class="profile-card">
                {% if welcome_message %}
                <div class="alert">Login successful. Welcome back, {{ full_name }}.</div>
                {% endif %}
                {% if updated_message %}
                <div class="alert">Your profile has been updated successfully.</div>
                {% endif %}

                <div class="profile-header">
                  <div class="avatar">👤</div>
                  <div>
                    <div class="profile-name">{{ full_name }}</div>
                    <div class="profile-subtitle">Member account</div>
                  </div>
                </div>

                <div class="info-grid">
                  <div class="info-box">
                    <div class="label">Full Name</div>
                    <div class="value">{{ full_name }}</div>
                  </div>

                  <div class="info-box">
                    <div class="label">Username</div>
                    <div class="value">{{ username }}</div>
                  </div>

                  <div class="info-box">
                    <div class="label">Email</div>
                    <div class="value">{{ email }}</div>
                  </div>

                  <div class="info-box">
                    <div class="label">Member Access</div>
                    <div class="value">Active</div>
                  </div>
                </div>

                <div class="actions">
                  <a class="link-btn" href="/account/edit-profile">Edit Profile</a>
                  <a class="secondary" href="/">Back to Home</a>
                </div>
              </div>
            </div>
          </body>
        </html>
        """,
        full_name=full_name,
        username=username,
        email=email,
        welcome_message=welcome_message,
        updated_message=updated_message,
    )


@app.route("/account/logout")
def user_logout():
    session.pop("user_logged_in", None)
    session.pop("user_id", None)
    session.pop("user_username", None)
    session.pop("user_full_name", None)
    session.pop("user_email", None)
    return redirect("/account/login")


@app.route("/account")
def account_home():
    if session.get("admin_logged_in"):
        return redirect("/admin")
    if session.get("user_logged_in"):
        return redirect("/account/profile")
    return redirect("/account/login")


@app.route("/admin/register", methods=["GET", "POST"])
def admin_register():
    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        if not full_name or not username or not password:
            return redirect("/admin/register?error=Please+fill+in+all+fields")

        if len(password) < 6:
            return redirect("/admin/register?error=Password+must+be+at+least+6+characters")

        connection = get_db_connection()
        existing = connection.execute(
            "SELECT ID FROM Admin_Users WHERE LOWER(Username) = LOWER(?)",
            (username,),
        ).fetchone()

        if existing:
            connection.close()
            return redirect("/admin/register?error=That+username+already+exists")

        password_hash = generate_password_hash(password)
        connection.execute(
            "INSERT INTO Admin_Users (Username, Password_Hash, Full_Name) VALUES (?, ?, ?)",
            (username, password_hash, full_name),
        )
        connection.commit()
        connection.close()

        return redirect("/admin/login?registered=1")

    return send_from_directory(str(BASE_DIR), "admin-register.html")


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "GET":
        return redirect("/account/login?role=admin")

    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    connection = get_db_connection()
    admin = connection.execute(
        "SELECT * FROM Admin_Users WHERE LOWER(Username) = LOWER(?)",
        (username,),
    ).fetchone()
    connection.close()

    if admin and check_password_hash(admin["Password_Hash"], password):
        session.clear()
        session["admin_logged_in"] = True
        session["admin_id"] = admin["ID"]
        session["admin_username"] = admin["Username"]
        session["admin_full_name"] = admin["Full_Name"]
        return redirect("/admin")

    return redirect("/account/login?role=admin&error=Invalid+admin+username+or+password")


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/account/login?role=admin")


@app.route("/admin")
@admin_login_required
def admin_dashboard():
    return send_from_directory(str(BASE_DIR), "admin-dashboard.html")


@app.route("/private-admin")
def private_admin_entry():
    return redirect("/fsssmc-admin-login")


@app.route("/involve")
@app.route("/involve.html")
def serve_involve_page():
    involve_html = (BASE_DIR / "involve.html").read_text(encoding="utf-8")
    return render_template_string(involve_html, user_logged_in=bool(session.get("user_logged_in")))


@app.route("/<path:page_name>")
def serve_site_file(page_name):
    if page_name.startswith("api/") or page_name.startswith("admin"):
        abort(404)

    file_path = (BASE_DIR / page_name).resolve()
    if not file_path.exists() or not file_path.is_file():
        abort(404)

    return send_from_directory(str(BASE_DIR), page_name)


def split_full_name(full_name):
    if not full_name:
        return "", ""
    parts = full_name.strip().split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def normalize_phone(value):
    if value is None:
        return ""
    return "".join(ch for ch in str(value).strip() if ch.isdigit())


@app.route("/api/members", methods=["POST", "OPTIONS"])
def create_member():
    if request.method == "OPTIONS":
        return "", 200

    incoming = request.get_json(silent=True) or request.form.to_dict()

    title = (incoming.get("title") or "").strip()
    full_name = (incoming.get("full_name") or incoming.get("fullName") or "").strip()
    phone = (incoming.get("phone") or "").strip()
    email = (incoming.get("email") or "").strip()
    address = (incoming.get("address") or "").strip() or "Lagos"
    member_type = (incoming.get("member_type") or incoming.get("membershipType") or "").strip()

    if not title or not full_name:
        return jsonify({"status": "error", "message": "Title and full name are required."}), 400

    first_name, last_name = split_full_name(full_name)
    phone_number = None
    if phone:
        raw_phone = phone.replace(" ", "").replace("-", "")
        try:
            phone_number = int(raw_phone)
        except ValueError:
            phone_number = None

    normalized_phone = normalize_phone(phone)
    normalized_email = (email or "").strip().lower()

    connection = get_db_connection()
    existing_member = connection.execute(
        """
        SELECT ID FROM Mosque_Members
        WHERE (
            LOWER(COALESCE(Email, '')) = ?
            OR Phone = ?
            OR Phone_Number = ?
        )
        LIMIT 1
        """,
        (normalized_email, normalized_phone, phone_number),
    ).fetchone()

    if existing_member:
        connection.close()
        return jsonify({
            "status": "error",
            "message": "This phone number or email is already registered as a mosque member.",
        }), 409

    connection.execute(
        """
        INSERT INTO Mosque_Members (First_Name, Last_Name, Title, Phone_Number, Address, Phone, Email, Member_Type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (first_name, last_name, title, phone_number, address, phone, email, member_type),
    )
    connection.commit()
    connection.close()

    return jsonify({
        "status": "success",
        "message": "Mosque member information saved successfully.",
    }), 201


@app.route("/api/donations", methods=["POST", "OPTIONS"])
def create_donation():
    if request.method == "OPTIONS":
        return "", 200

    incoming = request.get_json(silent=True) or request.form.to_dict()

    title = (incoming.get("title") or "").strip()
    full_name = (incoming.get("full_name") or incoming.get("fullName") or "").strip()
    email = (incoming.get("email") or "").strip()
    phone = (incoming.get("phone") or "").strip()
    amount_raw = incoming.get("amount")

    try:
        amount = float(amount_raw)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Please enter a valid donation amount."}), 400

    if not full_name:
        return jsonify({"status": "error", "message": "Full name is required."}), 400
    if amount < 1000:
        return jsonify({"status": "error", "message": "Minimum donation amount is ₦1,000."}), 400

    reference = f"FSSSMC-{uuid.uuid4().hex[:12].upper()}"

    connection = get_db_connection()
    if table_has_column("Donations", "Created_At"):
        connection.execute(
            """
            INSERT INTO Donations (Title, Full_Name, Email, Phone, Amount_Donated, Reference, Status, Payment_Provider, Created_At)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', 'Paystack', datetime('now'))
            """,
            (title, full_name, email, phone, amount, reference),
        )
    else:
        connection.execute(
            """
            INSERT INTO Donations (Title, Full_Name, Email, Phone, Amount_Donated, Reference, Status, Payment_Provider)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', 'Paystack')
            """,
            (title, full_name, email, phone, amount, reference),
        )
    connection.commit()
    connection.close()

    secret_key = (os.getenv("PAYSTACK_SECRET_KEY") or "").strip()
    public_key = (os.getenv("PAYSTACK_PUBLIC_KEY") or "").strip()

    if not has_real_paystack_keys():
        return jsonify({
            "status": "success",
            "saved": True,
            "message": "Donation saved locally. Add real Paystack keys in the .env file to enable live payment checkout.",
            "reference": reference,
        })

    payload = {
        "email": email or "support@fsssmcmosque.com",
        "amount": int(round(amount * 100)),
        "currency": "NGN",
        "reference": reference,
        "callback_url": str(request.url_root) + "api/paystack/callback",
    }

    headers = {
        "Authorization": f"Bearer {secret_key}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            "https://api.paystack.co/transaction/initialize",
            headers=headers,
            json=payload,
            timeout=20,
        )
        response_json = response.json()
        if response.status_code >= 400 or not response_json.get("status"):
            return jsonify({
                "status": "error",
                "message": response_json.get("message", "Paystack could not initialize the payment."),
            }), 400

        authorization_url = response_json["data"]["authorization_url"]
        return jsonify({
            "status": "success",
            "message": "Payment initialized successfully.",
            "redirect_url": authorization_url,
            "reference": reference,
        })
    except requests.RequestException:
        return jsonify({
            "status": "error",
            "message": "Could not connect to Paystack right now. Please try again in a moment.",
        }), 500


@app.route("/api/paystack/config")
def paystack_config():
    return jsonify({"public_key": os.getenv("PAYSTACK_PUBLIC_KEY", "")})


@app.route("/api/paystack/callback")
def paystack_callback():
    reference = request.args.get("reference")
    if not reference:
        return redirect("/involve.html?payment=failed")

    secret_key = os.getenv("PAYSTACK_SECRET_KEY")
    if not secret_key:
        return redirect("/involve.html?payment=failed")

    try:
        response = requests.get(
            f"https://api.paystack.co/transaction/verify/{reference}",
            headers={"Authorization": f"Bearer {secret_key}"},
            timeout=20,
        )
        data = response.json()
        if data.get("status") and data.get("data", {}).get("status") == "success":
            connection = get_db_connection()
            connection.execute(
                "UPDATE Donations SET Status = 'paid' WHERE Reference = ?",
                (reference,),
            )
            connection.commit()
            connection.close()
            return redirect(f"/involve.html?payment=success&reference={reference}")
    except requests.RequestException:
        pass

    return redirect("/involve.html?payment=failed")


@app.route("/api/donations/report")
def donation_report():
    connection = get_db_connection()
    columns = [row[1] for row in connection.execute("PRAGMA table_info(Donations)").fetchall()]
    select_sql = "SELECT ID, Title, Full_Name, Email, Phone, Amount_Donated, Reference, Status"
    if "Created_At" in columns:
        select_sql += ", Created_At"
    select_sql += " FROM Donations ORDER BY ID DESC"
    donations = connection.execute(select_sql).fetchall()
    connection.close()
    return jsonify({"status": "success", "data": [dict(row) for row in donations]})


@app.route("/api/members/report")
def members_report():
    connection = get_db_connection()
    members = connection.execute(
        "SELECT ID, Title, First_Name, Last_Name, Phone_Number, Phone, Email, Address, Member_Type, Is_Active FROM Mosque_Members WHERE COALESCE(Is_Active, 1) = 1 ORDER BY ID DESC"
    ).fetchall()
    connection.close()
    return jsonify({"status": "success", "data": [dict(row) for row in members]})


@app.route("/api/admin/members/current")
@admin_login_required
def admin_current_members():
    connection = get_db_connection()
    members = connection.execute(
        "SELECT ID, Title, First_Name, Last_Name, Phone_Number, Phone, Email, Address, Member_Type, Is_Active FROM Mosque_Members WHERE COALESCE(Is_Active, 1) = 1 ORDER BY ID DESC"
    ).fetchall()
    connection.close()
    return jsonify({"status": "success", "data": [dict(row) for row in members]})


@app.route("/api/admin/members/removed")
@admin_login_required
def admin_removed_members():
    connection = get_db_connection()
    members = connection.execute(
        "SELECT ID, Title, First_Name, Last_Name, Phone_Number, Phone, Email, Address, Member_Type, Is_Active FROM Mosque_Members WHERE COALESCE(Is_Active, 1) = 0 ORDER BY ID DESC"
    ).fetchall()
    connection.close()
    return jsonify({"status": "success", "data": [dict(row) for row in members]})


@app.route("/api/admin/members/<int:member_id>/remove", methods=["POST"]) 
@admin_login_required
def remove_member(member_id):
    connection = get_db_connection()
    member = connection.execute(
        "SELECT ID, Is_Active FROM Mosque_Members WHERE ID = ?",
        (member_id,),
    ).fetchone()
    if not member:
        connection.close()
        return jsonify({"status": "error", "message": "Member not found."}), 404

    connection.execute(
        "UPDATE Mosque_Members SET Is_Active = 0 WHERE ID = ?",
        (member_id,),
    )
    connection.commit()
    connection.close()
    return jsonify({"status": "success", "message": "Member removed successfully.", "active": False})


@app.route("/api/admin/members/<int:member_id>/restore", methods=["POST"]) 
@admin_login_required
def restore_member(member_id):
    connection = get_db_connection()
    member = connection.execute(
        "SELECT ID, Is_Active FROM Mosque_Members WHERE ID = ?",
        (member_id,),
    ).fetchone()
    if not member:
        connection.close()
        return jsonify({"status": "error", "message": "Member not found."}), 404

    connection.execute(
        "UPDATE Mosque_Members SET Is_Active = 1 WHERE ID = ?",
        (member_id,),
    )
    connection.commit()
    connection.close()
    return jsonify({"status": "success", "message": "Member restored successfully.", "active": True})


@app.route("/api/admin/users", methods=["POST"]) 
@admin_login_required
def create_admin_account():
    incoming = request.get_json(silent=True) or request.form.to_dict()
    username = (incoming.get("username") or "").strip()
    password = (incoming.get("password") or "").strip()
    full_name = (incoming.get("full_name") or incoming.get("fullName") or "").strip()

    if not username or not password or not full_name:
        return jsonify({"status": "error", "message": "Username, password, and full name are required."}), 400

    if len(password) < 6:
        return jsonify({"status": "error", "message": "Password must be at least 6 characters long."}), 400

    connection = get_db_connection()
    existing = connection.execute(
        "SELECT ID FROM Admin_Users WHERE LOWER(Username) = LOWER(?)",
        (username,),
    ).fetchone()
    if existing:
        connection.close()
        return jsonify({"status": "error", "message": "That admin username already exists."}), 409

    connection.execute(
        "INSERT INTO Admin_Users (Username, Password_Hash, Full_Name) VALUES (?, ?, ?)",
        (username, generate_password_hash(password), full_name),
    )
    connection.commit()
    connection.close()

    return jsonify({
        "status": "success",
        "message": "Admin account created successfully.",
        "admin": {"username": username, "full_name": full_name},
    })


if __name__ == "__main__":
    ensure_schema()
    app.run(debug=True, host="0.0.0.0", port=5000)
