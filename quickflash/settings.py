import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import trust

INSTALL_FIELDS = {"packages", "user", "timezone"}
FIELDS = {"name", "ssh_keys", "wifi"} | INSTALL_FIELDS
USER_FIELDS = {"name", "sudo", "hashed_password"}
PACKAGE = r"[A-Za-z_][A-Za-z0-9_'+-]*"


def hostname(value):
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[a-z][a-z0-9-]{0,52}", value)
        or value.endswith("-")
        or value in ("test", "bootstrap", "quickflash")
    ):
        raise ValueError("name must be a lowercase DNS label of at most 53 characters")
    return value


def public_keys(values, field="ssh_keys"):
    if not isinstance(values, list) or not values:
        raise ValueError(f"{field} must be a nonempty array of public keys")
    if not all(isinstance(value, str) for value in values):
        raise ValueError(f"{field} must contain public key strings")
    return trust.validate_public_keys(values)


def password_hash(value, field):
    if not isinstance(value, str) or not re.fullmatch(
        r"\$[A-Za-z0-9]+\$[^\s:]+\$[^\s:]+", value
    ):
        raise ValueError(f"{field} must be a crypt password hash (mkpasswd)")
    return value


def timezone(value):
    if not isinstance(value, str):
        raise ValueError("timezone must be an IANA name, e.g. America/New_York")
    try:
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise ValueError(
            "timezone must be an IANA name, e.g. America/New_York"
        ) from error
    return value


def user(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - USER_FIELDS or "name" not in value:
        raise ValueError(
            "user must be null or an object with name and optional "
            "sudo, hashed_password"
        )
    name = value["name"]
    if (
        not isinstance(name, str)
        or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", name)
        or name == "root"
    ):
        raise ValueError("user.name must be a lowercase Unix user name other than root")
    result = {
        "name": name,
        "sudo": value.get("sudo", True),
    }
    if not isinstance(result["sudo"], bool):
        raise ValueError("user.sudo must be true or false")
    if "hashed_password" in value:
        result["hashed_password"] = password_hash(
            value["hashed_password"], "user.hashed_password"
        )
    return result


def validate(value, *, partial=False):
    """Normalize host settings; an absent user means ask before installation."""
    if not isinstance(value, dict) or not value or set(value) - FIELDS:
        raise ValueError(f"Settings may only contain: {', '.join(sorted(FIELDS))}")
    if not partial and ("name" not in value or "ssh_keys" not in value):
        raise ValueError("Settings require name and ssh_keys")
    packages = value.get("packages", [])
    if not isinstance(packages, list) or not all(
        isinstance(p, str) and re.fullmatch(rf"{PACKAGE}(\.{PACKAGE})*", p)
        for p in packages
    ):
        raise ValueError(
            'packages must be nixpkgs attribute names, e.g. ["git", "vim"]'
        )
    result = {}
    if "name" in value:
        result["name"] = hostname(value["name"])
    if "ssh_keys" in value:
        result["ssh_keys"] = public_keys(value["ssh_keys"])
    if "packages" in value or not partial:
        result["packages"] = packages
    if "user" in value:
        result["user"] = user(value["user"])
    if "timezone" in value:
        result["timezone"] = timezone(value["timezone"])
    return result
