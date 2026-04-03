from app.db.init_db import seed_default_user


if __name__ == "__main__":
    seed_default_user()
    print("Seeded default user: default-user")
