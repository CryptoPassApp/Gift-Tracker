with open("api/routes/user.py", "r", encoding="utf-8") as f:
    text = f.read()

target = """    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Telegram API unreachable: {exc}",
        ) from exc

    if not data.get("ok"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Telegram API error: {data.get('description', 'unknown')}",
        )"""

replacement = """    except httpx.HTTPError:
        logger.error("Telegram createInvoiceLink network error")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Telegram API unreachable: payment service temporarily unavailable",
        ) from None

    if not data.get("ok"):
        logger.warning("Telegram createInvoiceLink rejected: %s", data.get("description", "unknown"))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to create Telegram Stars invoice",
        )"""

assert target in text, "target not found"
text = text.replace(target, replacement, 1)

with open("api/routes/user.py", "w", encoding="utf-8") as f:
    f.write(text)

print("Updated api/routes/user.py safely")
