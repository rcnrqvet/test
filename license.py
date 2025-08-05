import psycopg2
from datetime import datetime
import uuid

def get_machine_id():
    return str(uuid.getnode())  # unique ID per device

def verify_license(input_key):
    machine_id = get_machine_id()
    conn = psycopg2.connect(
        dbname="pure_scripts",
        user="postgres",             # 🔁 CHANGE THIS IF DIFFERENT
        password="your_password",    # 🔁 CHANGE THIS IF DIFFERENT
        host="localhost",
        port="5432"
    )
    cur = conn.cursor()

    # Check if the key exists and is not activated
    cur.execute("SELECT activated FROM license_keys WHERE key=%s", (input_key,))
    result = cur.fetchone()

    if not result:
        return False, "Invalid license key"

    if result[0] is True:
        return False, "License key already used"

    # If not activated, mark it as used
    cur.execute("""
        UPDATE license_keys
        SET activated=TRUE,
            activated_on=%s,
            machine_id=%s
        WHERE key=%s
    """, (datetime.utcnow(), machine_id, input_key))

    conn.commit()
    return True, "License activated"
