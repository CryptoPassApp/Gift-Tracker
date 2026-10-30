with open("api/dependencies.py", "r", encoding="utf-8") as f:
    text = f.read()

target = """def validate_init_data(init_data: str, bot_token: str) -> dict[str, Any]:
    \"\"\"Валидирует подпись initData и возвращает разобранный payload пользователя.

    Raises:
        HTTPException 401 при несовпадении подписи или битом формате.
    \"\"\"
    try:
        params = parse_qs(init_data, keep_blank_values=True)
        received_hash = params["hash"][0]

        data_check_string = "\\n".join(
            f"{key}={params[key][0]}" for key in sorted(k for k in params if k != "hash")
        )

        secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
        calculated = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(calculated, received_hash):
            raise ValueError("hash mismatch")

        user_payload = json.loads(params["user"][0])
        if not isinstance(user_payload, dict) or "id" not in user_payload:
            raise ValueError("user payload missing")
        return user_payload"""

replacement = """def validate_init_data(
    init_data: str, bot_token: str, max_age_sec: int | None = None
) -> dict[str, Any]:
    \"\"\"Валидирует подпись initData и возвращает разобранный payload пользователя.

    Raises:
        HTTPException 401 при несовпадении подписи или битом формате.
    \"\"\"
    try:
        params = parse_qs(init_data, keep_blank_values=True)
        received_hash = params["hash"][0]

        data_check_string = "\\n".join(
            f"{key}={params[key][0]}" for key in sorted(k for k in params if k != "hash")
        )

        secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
        calculated = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(calculated, received_hash):
            raise ValueError("hash mismatch")

        # Защита от Replay Attack: проверка срока давности auth_date
        if max_age_sec is not None and "auth_date" in params:
            import time
            auth_date = int(params["auth_date"][0])
            if time.time() - auth_date > max_age_sec:
                raise ValueError("initData expired (auth_date too old)")

        user_payload = json.loads(params["user"][0])
        if not isinstance(user_payload, dict) or "id" not in user_payload:
            raise ValueError("user payload missing")
        return user_payload"""

assert target in text, "target not found"
text = text.replace(target, replacement, 1)

with open("api/dependencies.py", "w", encoding="utf-8") as f:
    f.write(text)

print("Updated api/dependencies.py safely")
