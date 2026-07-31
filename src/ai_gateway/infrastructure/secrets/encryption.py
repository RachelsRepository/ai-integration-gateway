"""Symmetric encryption for stored provider credentials."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from ai_gateway.domain.errors import ValidationError


class CredentialEncryptor:
    """Encrypts and decrypts credential material with a Fernet key."""

    def __init__(self, key: str | bytes) -> None:
        """Initialise the encryptor.

        Args:
            key: A url-safe base64-encoded 32-byte Fernet key.

        Raises:
            ValidationError: If the key is malformed.
        """
        try:
            self._fernet = Fernet(key if isinstance(key, bytes) else key.encode("utf-8"))
        except (ValueError, TypeError) as exc:
            raise ValidationError("Invalid credential encryption key") from exc

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a plaintext secret.

        Args:
            plaintext: Secret to protect.

        Returns:
            The url-safe ciphertext token.
        """
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, token: str) -> str:
        """Decrypt a ciphertext token.

        Args:
            token: Ciphertext produced by :meth:`encrypt`.

        Returns:
            The plaintext secret.

        Raises:
            ValidationError: If the token is invalid or tampered with.
        """
        try:
            return self._fernet.decrypt(token.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise ValidationError("Credential token is invalid or corrupted") from exc

    @staticmethod
    def generate_key() -> str:
        """Generate a new Fernet key.

        Returns:
            A url-safe base64-encoded key suitable for configuration.
        """
        return Fernet.generate_key().decode("utf-8")


__all__ = ["CredentialEncryptor"]
