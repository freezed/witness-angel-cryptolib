import Crypto.Hash
from Crypto.Cipher import AES, ChaCha20_Poly1305, PKCS1_OAEP
from Crypto.PublicKey import RSA
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad, unpad

from wacryptolib.utilities import split_as_chunks

RSA_OAEP_CHUNKS_SIZE = 60
RSA_OAEP_HASH_ALGO = Crypto.Hash.SHA256


def _get_encryption_type_conf(encryption_type):
    encryption_type = encryption_type.upper()
    if encryption_type not in ENCRYPTION_TYPES_REGISTRY:
        raise ValueError("Unknown cipher type '%s'" % encryption_type)

    encryption_type_conf = ENCRYPTION_TYPES_REGISTRY[encryption_type]
    return encryption_type_conf


def encrypt_bytestring(plaintext: bytes, encryption_type: str, key: bytes) -> dict:
    """Encrypt a bytestring with the selected algorithm for the given payload,
    using the provided key (which must be of a compatible type and length).

    :return: dictionary with encryption data, and
        a "type" field echoing `encryption_type`."""
    encryption_type_conf = _get_encryption_type_conf(encryption_type=encryption_type)
    encryption_function = encryption_type_conf["encryption_function"]
    cipherdict = encryption_function(key=key, plaintext=plaintext)
    cipherdict["type"] = encryption_type
    return cipherdict


def decrypt_bytestring(cipherdict: dict, key: bytes) -> bytes:
    """Decrypt a bytestring with the selected algorithm for the given encrypted data dict,
    using the provided key (which must be of a compatible type and length).

    :return: dictionary with encryption data."""
    encryption_type = cipherdict["type"]
    encryption_type_conf = _get_encryption_type_conf(encryption_type)
    decryption_function = encryption_type_conf["decryption_function"]
    plaintext = decryption_function(key=key, cipherdict=cipherdict)
    return plaintext


def _encrypt_via_aes_cbc(plaintext: bytes, key: bytes) -> dict:
    """Encrypt a bytestring using AES (CBC mode).

    :param plaintext: the bytes to cipher
    :param key: AES cryptographic key. It must be 16, 24 or 32 bytes long
        (respectively for *AES-128*, *AES-192* or *AES-256*).

    :return: dict with fields "iv" and "ciphertext" as bytestrings"""

    iv = get_random_bytes(AES.block_size)
    cipher = AES.new(key, AES.MODE_CBC, iv)
    ciphertext = cipher.encrypt(pad(plaintext, block_size=AES.block_size))
    cipherdict = {"iv": iv, "ciphertext": ciphertext}
    return cipherdict


def _decrypt_via_aes_cbc(cipherdict: dict, key: bytes) -> bytes:
    """Decrypt a bytestring using AES (CBC mode).

    :param cipherdict: dict with fields "iv" and "ciphertext" as bytestrings
    :param key: the cryptographic key used to decipher

    :return: the decrypted bytestring"""

    iv = cipherdict["iv"]
    ciphertext = cipherdict["ciphertext"]
    decipher = AES.new(key, AES.MODE_CBC, iv)
    plaintext = unpad(decipher.decrypt(ciphertext), block_size=AES.block_size)
    return plaintext


def _encrypt_via_aes_eax(plaintext: bytes, key: bytes) -> dict:
    """Encrypt a bytestring using AES (EAX mode).

    :param plaintext: the bytes to cipher
    :param key: AES cryptographic key. It must be 16, 24 or 32 bytes long
        (respectively for *AES-128*, *AES-192* or *AES-256*).

    :return: dict with fields "ciphertext", "tag" and "nonce" as bytestrings"""

    cipher = AES.new(key, AES.MODE_EAX)
    nonce = cipher.nonce
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)
    cipherdict = {"ciphertext": ciphertext, "tag": tag, "nonce": nonce}
    return cipherdict


def _decrypt_via_aes_eax(cipherdict: dict, key: bytes) -> bytes:
    """Decrypt a bytestring using AES (EAX mode).

    :param cipherdict: dict with fields "ciphertext", "tag" and "nonce" as bytestrings
    :param key: the cryptographic key used to decipher

    :return: the decrypted bytestring"""

    decipher = AES.new(key, AES.MODE_EAX, nonce=cipherdict["nonce"])
    plaintext = decipher.decrypt(cipherdict["ciphertext"])
    decipher.verify(cipherdict["tag"])
    return plaintext


def _encrypt_via_chacha20_poly1305(
    plaintext: bytes, key: bytes, aad: bytes = b"header"
) -> dict:
    """Encrypt a bytestring with the stream cipher ChaCha20.

    Additional cleartext data can be provided so that the
    generated mac tag also verifies its integrity.

    :param plaintext: the bytes to cipher
    :param key: 32 bytes long cryptographic key
    :param aad: optional "additional authenticated data"

    :return: dict with fields "ciphertext", "tag", "nonce" and "header" as bytestrings"""

    cipher = ChaCha20_Poly1305.new(key=key)
    cipher.update(aad)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)
    nonce = cipher.nonce
    encryption = {"ciphertext": ciphertext, "tag": tag, "nonce": nonce, "aad": aad}
    return encryption


def _decrypt_via_chacha20_poly1305(cipherdict: dict, key: bytes) -> bytes:
    """Decrypt a bytestring with the stream cipher ChaCha20.

    :param cipherdict: dict with fields "ciphertext", "tag", "nonce" and "header" as bytestrings
    :param key: the cryptographic key used to decipher

    :return: the decrypted bytestring"""

    decipher = ChaCha20_Poly1305.new(key=key, nonce=cipherdict["nonce"])
    decipher.update(cipherdict["aad"])
    plaintext = decipher.decrypt_and_verify(
        ciphertext=cipherdict["ciphertext"], received_mac_tag=cipherdict["tag"]
    )
    return plaintext


def _encrypt_via_rsa_oaep(plaintext: bytes, key: RSA.RsaKey) -> dict:
    """Encrypt a bytestring with PKCS#1 RSA OAEP (asymmetric algo).

    :param key: public RSA key
    :param plaintext: the bytes to cipher

    :return: a dict with field `digest_list`, containing bytestring chunks of variable width."""

    cipher = PKCS1_OAEP.new(key=key, hashAlgo=RSA_OAEP_HASH_ALGO)
    chunks = split_as_chunks(
        plaintext,
        chunk_size=RSA_OAEP_CHUNKS_SIZE,
        must_pad=False,
        accept_incomplete_chunk=True,
    )

    encrypted_chunks = []
    for chunk in chunks:
        encrypted_chunk = cipher.encrypt(chunk)
        encrypted_chunks.append(encrypted_chunk)
    return dict(digest_list=encrypted_chunks)


def _decrypt_via_rsa_oaep(cipherdict: dict, key: RSA.RsaKey) -> bytes:
    """Decrypt a bytestring with PKCS#1 RSA OAEP (asymmetric algo).

    :param cipherdict: list of ciphertext chunks
    :param key: private RSA key

    :return: the decrypted bytestring"""

    decipher = PKCS1_OAEP.new(key, hashAlgo=RSA_OAEP_HASH_ALGO)

    encrypted_chunks = cipherdict["digest_list"]

    decrypted_chunks = []
    for encrypted_chunk in encrypted_chunks:
        decrypted_chunk = decipher.decrypt(encrypted_chunk)
        decrypted_chunks.append(decrypted_chunk)
    return b"".join(decrypted_chunks)


ENCRYPTION_TYPES_REGISTRY = dict(
    AES_CBC={
        "encryption_function": _encrypt_via_aes_cbc,
        "decryption_function": _decrypt_via_aes_cbc,
    },
    AES_EAX={
        "encryption_function": _encrypt_via_aes_eax,
        "decryption_function": _decrypt_via_aes_eax,
    },
    CHACHA20_POLY1305={
        "encryption_function": _encrypt_via_chacha20_poly1305,
        "decryption_function": _decrypt_via_chacha20_poly1305,
    },
    RSA_OAEP={
        "encryption_function": _encrypt_via_rsa_oaep,
        "decryption_function": _decrypt_via_rsa_oaep,
    },
)

#: These values can be used as 'encryption_type'.
SUPPORTED_ENCRYPTION_TYPES = sorted(ENCRYPTION_TYPES_REGISTRY.keys())
