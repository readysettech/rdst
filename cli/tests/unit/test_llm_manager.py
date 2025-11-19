"""
Unit tests for LLM manager modules.

Tests llm_manager, security, and base provider infrastructure.
"""

import pytest
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Import module directly to avoid package __init__.py issues
def _import_module_directly(module_name, file_path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module

_lib_path = Path(__file__).parent.parent.parent / "lib"
security = _import_module_directly("security", _lib_path / "llm_manager" / "security.py")
base = _import_module_directly("base", _lib_path / "llm_manager" / "base.py")

# Import functions and classes
encrypt = security.encrypt
decrypt = security.decrypt
LLMKeyVault = security.LLMKeyVault
_kdf = security._kdf
_prng_stream = security._prng_stream

LLMError = base.LLMError
LLMDefaults = base.LLMDefaults
ProviderMessage = base.ProviderMessage
ProviderRequest = base.ProviderRequest
ProviderResponse = base.ProviderResponse
Conversation = base.Conversation


class TestKDF:
    """Tests for key derivation function."""

    def test_deterministic_output(self):
        """Test KDF produces deterministic output."""
        result1 = _kdf("test-secret")
        result2 = _kdf("test-secret")
        assert result1 == result2

    def test_different_secrets_different_keys(self):
        """Test different secrets produce different keys."""
        key1 = _kdf("secret1")
        key2 = _kdf("secret2")
        assert key1 != key2

    def test_key_length(self):
        """Test KDF produces 32-byte key (SHA-256)."""
        key = _kdf("any-secret")
        assert len(key) == 32


class TestPRNGStream:
    """Tests for pseudo-random number generator stream."""

    def test_deterministic_stream(self):
        """Test PRNG produces deterministic output."""
        key = _kdf("test-secret")
        stream1 = _prng_stream(key, 100)
        stream2 = _prng_stream(key, 100)
        assert stream1 == stream2

    def test_requested_length(self):
        """Test PRNG produces requested length."""
        key = _kdf("test-secret")
        stream = _prng_stream(key, 256)
        assert len(stream) == 256

    def test_zero_length(self):
        """Test PRNG with zero length."""
        key = _kdf("test-secret")
        stream = _prng_stream(key, 0)
        assert len(stream) == 0


class TestEncryptDecrypt:
    """Tests for encrypt/decrypt functions."""

    def test_roundtrip(self):
        """Test encrypting and decrypting returns original."""
        plaintext = b"Hello, World!"
        secret = "my-secret-key"

        ciphertext = encrypt(plaintext, secret)
        decrypted = decrypt(ciphertext, secret)

        assert decrypted == plaintext

    def test_different_plaintexts(self):
        """Test different plaintexts produce different ciphertexts."""
        secret = "shared-secret"
        ct1 = encrypt(b"message1", secret)
        ct2 = encrypt(b"message2", secret)
        assert ct1 != ct2

    def test_wrong_secret_fails(self):
        """Test decryption with wrong secret fails."""
        plaintext = b"sensitive data"
        ciphertext = encrypt(plaintext, "correct-secret")

        with pytest.raises(Exception):
            decrypt(ciphertext, "wrong-secret")

    def test_empty_plaintext(self):
        """Test encrypting empty data."""
        ciphertext = encrypt(b"", "secret")
        decrypted = decrypt(ciphertext, "secret")
        assert decrypted == b""

    def test_unicode_data(self):
        """Test encrypting unicode data."""
        plaintext = "Hello 世界 🌍".encode("utf-8")
        secret = "unicode-test"

        ciphertext = encrypt(plaintext, secret)
        decrypted = decrypt(ciphertext, secret)

        assert decrypted == plaintext

    def test_large_data(self):
        """Test encrypting larger data."""
        plaintext = b"x" * 10000
        secret = "large-data-test"

        ciphertext = encrypt(plaintext, secret)
        decrypted = decrypt(ciphertext, secret)

        assert decrypted == plaintext


class TestLLMKeyVault:
    """Tests for LLMKeyVault class."""

    @pytest.fixture
    def temp_vault_dir(self, temp_dir):
        """Create a temporary vault directory."""
        vault_dir = temp_dir / "keys"
        return vault_dir

    def test_path_for_provider(self, temp_vault_dir):
        """Test path generation for provider."""
        vault = LLMKeyVault(temp_vault_dir)

        path = vault.path_for("openai")
        assert path == temp_vault_dir / "openai.key.enc"

    def test_path_for_provider_lowercase(self, temp_vault_dir):
        """Test path is lowercase."""
        vault = LLMKeyVault(temp_vault_dir)

        path = vault.path_for("OpenAI")
        assert path == temp_vault_dir / "openai.key.enc"

    def test_write_and_read(self, temp_vault_dir):
        """Test writing and reading encrypted key."""
        vault = LLMKeyVault(temp_vault_dir)
        ciphertext = b"encrypted-key-data"

        path = vault.write_encrypted_key("claude", ciphertext)
        assert path.exists()

        read_back = vault.read_encrypted_key("claude")
        assert read_back == ciphertext

    def test_read_nonexistent(self, temp_vault_dir):
        """Test reading nonexistent key returns None."""
        vault = LLMKeyVault(temp_vault_dir)

        result = vault.read_encrypted_key("nonexistent")
        assert result is None

    def test_creates_parent_directories(self, temp_dir):
        """Test vault creates parent directories."""
        nested_path = temp_dir / "nested" / "path" / "keys"
        vault = LLMKeyVault(nested_path)

        vault.write_encrypted_key("test", b"data")

        assert nested_path.exists()
        assert (nested_path / "test.key.enc").exists()


class TestLLMError:
    """Tests for LLMError exception class."""

    def test_basic_error(self):
        """Test creating basic error."""
        error = LLMError("Something went wrong")
        assert str(error) == "Something went wrong"
        assert error.code == "LLM_ERROR"
        assert error.status is None
        assert error.cause is None

    def test_error_with_code(self):
        """Test error with custom code."""
        error = LLMError("No key", code="NO_API_KEY")
        assert error.code == "NO_API_KEY"

    def test_error_with_status(self):
        """Test error with HTTP status."""
        error = LLMError("Unauthorized", code="AUTH_ERROR", status=401)
        assert error.status == 401

    def test_error_with_cause(self):
        """Test error with underlying cause."""
        original = ValueError("original error")
        error = LLMError("Wrapped", cause=original)
        assert error.cause is original


class TestLLMDefaults:
    """Tests for LLMDefaults dataclass."""

    def test_default_values(self):
        """Test default values are set."""
        defaults = LLMDefaults()

        assert defaults.provider == "openai"
        assert defaults.model is None
        assert defaults.max_tokens == 800
        assert defaults.temperature == 0.2
        assert defaults.top_p is None
        assert defaults.stop_sequences is None
        assert defaults.debug is False

    def test_custom_values(self):
        """Test custom values are applied."""
        defaults = LLMDefaults(
            provider="claude",
            model="claude-3-opus",
            max_tokens=2000,
            temperature=0.7
        )

        assert defaults.provider == "claude"
        assert defaults.model == "claude-3-opus"
        assert defaults.max_tokens == 2000
        assert defaults.temperature == 0.7


class TestProviderMessage:
    """Tests for ProviderMessage dataclass."""

    def test_user_message(self):
        """Test creating user message."""
        msg = ProviderMessage(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_system_message(self):
        """Test creating system message."""
        msg = ProviderMessage(role="system", content="You are helpful")
        assert msg.role == "system"
        assert msg.content == "You are helpful"


class TestProviderRequest:
    """Tests for ProviderRequest dataclass."""

    def test_basic_request(self):
        """Test creating basic request."""
        messages = [ProviderMessage(role="user", content="Hi")]
        request = ProviderRequest(model="gpt-4", messages=messages)

        assert request.model == "gpt-4"
        assert len(request.messages) == 1
        assert request.temperature == 0.2  # Default

    def test_as_chat_dicts(self):
        """Test converting to chat dictionaries."""
        messages = [
            ProviderMessage(role="system", content="Be helpful"),
            ProviderMessage(role="user", content="Hi")
        ]
        request = ProviderRequest(model="gpt-4", messages=messages)

        dicts = request.as_chat_dicts()

        assert len(dicts) == 2
        assert dicts[0] == {"role": "system", "content": "Be helpful"}
        assert dicts[1] == {"role": "user", "content": "Hi"}


class TestProviderResponse:
    """Tests for ProviderResponse dataclass."""

    def test_basic_response(self):
        """Test creating basic response."""
        response = ProviderResponse(text="Hello there!")
        assert response.text == "Hello there!"
        assert response.usage is None
        assert response.raw is None

    def test_response_with_usage(self):
        """Test response with token usage."""
        usage = {
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30
        }
        response = ProviderResponse(text="Response", usage=usage)

        assert response.usage["total_tokens"] == 30


class TestConversation:
    """Tests for Conversation class."""

    @pytest.fixture
    def mock_provider(self):
        """Create a mock provider."""
        provider = MagicMock()
        provider.default_model.return_value = "default-model"
        provider.complete.return_value = ProviderResponse(
            text="AI response",
            usage={"total_tokens": 50}
        )
        return provider

    def test_conversation_creation(self, mock_provider):
        """Test creating conversation."""
        conv = Conversation(provider=mock_provider, api_key="test-key")

        assert conv.provider == mock_provider
        assert conv.api_key == "test-key"
        assert conv.messages == []

    def test_add_messages(self, mock_provider):
        """Test adding messages."""
        conv = Conversation(provider=mock_provider, api_key="key")

        conv.system("You are helpful")
        conv.user("Hi")
        conv.assistant("Hello!")

        assert len(conv.messages) == 3
        assert conv.messages[0].role == "system"
        assert conv.messages[1].role == "user"
        assert conv.messages[2].role == "assistant"

    def test_reset(self, mock_provider):
        """Test resetting conversation."""
        conv = Conversation(provider=mock_provider, api_key="key")
        conv.user("Message 1")
        conv.user("Message 2")

        conv.reset()

        assert conv.messages == []

    def test_with_model(self, mock_provider):
        """Test creating conversation with different model."""
        conv = Conversation(provider=mock_provider, api_key="key", model="model-a")
        new_conv = conv.with_model("model-b")

        assert new_conv.model == "model-b"
        assert conv.model == "model-a"  # Original unchanged

    def test_complete_calls_provider(self, mock_provider):
        """Test complete calls provider."""
        conv = Conversation(provider=mock_provider, api_key="test-key")
        conv.system("Be helpful")
        conv.user("Hello")

        response = conv.complete()

        assert response.text == "AI response"
        assert mock_provider.complete.called

    def test_complete_adds_assistant_message(self, mock_provider):
        """Test complete adds assistant response to history."""
        conv = Conversation(provider=mock_provider, api_key="test-key")
        conv.user("Hello")

        conv.complete()

        assert len(conv.messages) == 2
        assert conv.messages[1].role == "assistant"
        assert conv.messages[1].content == "AI response"


class TestLLMManagerIntegration:
    """Integration tests for LLM key management workflow."""

    def test_full_key_management_workflow(self, temp_dir):
        """Test full workflow of encrypting, storing, and retrieving API key."""
        # Create vault
        keys_dir = temp_dir / "keys"
        vault = LLMKeyVault(keys_dir)

        # Encrypt and save key
        api_key = "sk-test-api-key-12345"
        shared_secret = "my-shared-secret"

        encrypted = encrypt(api_key.encode("utf-8"), shared_secret)
        path = vault.write_encrypted_key("openai", encrypted)

        assert path.exists()

        # Read and decrypt key
        read_encrypted = vault.read_encrypted_key("openai")
        decrypted = decrypt(read_encrypted, shared_secret)

        assert decrypted.decode("utf-8") == api_key

    def test_multiple_providers(self, temp_dir):
        """Test storing keys for multiple providers."""
        keys_dir = temp_dir / "keys"
        vault = LLMKeyVault(keys_dir)
        shared_secret = "shared-secret"

        # Store keys for multiple providers
        providers = {
            "openai": "sk-openai-key",
            "claude": "sk-claude-key",
            "custom": "sk-custom-key"
        }

        for provider, key in providers.items():
            encrypted = encrypt(key.encode("utf-8"), shared_secret)
            vault.write_encrypted_key(provider, encrypted)

        # Verify all can be retrieved
        for provider, expected_key in providers.items():
            encrypted = vault.read_encrypted_key(provider)
            decrypted = decrypt(encrypted, shared_secret).decode("utf-8")
            assert decrypted == expected_key

    def test_key_overwrite(self, temp_dir):
        """Test overwriting an existing key."""
        keys_dir = temp_dir / "keys"
        vault = LLMKeyVault(keys_dir)
        shared_secret = "secret"

        # Write initial key
        initial_key = "initial-key"
        encrypted = encrypt(initial_key.encode("utf-8"), shared_secret)
        vault.write_encrypted_key("provider", encrypted)

        # Overwrite with new key
        new_key = "new-key"
        encrypted = encrypt(new_key.encode("utf-8"), shared_secret)
        vault.write_encrypted_key("provider", encrypted)

        # Verify new key is stored
        read_encrypted = vault.read_encrypted_key("provider")
        decrypted = decrypt(read_encrypted, shared_secret).decode("utf-8")

        assert decrypted == new_key
