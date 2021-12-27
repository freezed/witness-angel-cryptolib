import random
import time
import copy

import pytest
from Crypto.Random import get_random_bytes

from wacryptolib.cipher import _encrypt_via_rsa_oaep
from wacryptolib.escrow import (
    EscrowApi,
    generate_free_keypair_for_least_provisioned_key_algo,
    get_free_keys_generator_worker,
    ReadonlyEscrowApi,
    generate_keypair_for_storage,
)
from wacryptolib.keygen import (
    load_asymmetric_key_from_pem_bytestring,
    SUPPORTED_ASYMMETRIC_KEY_ALGOS,
    generate_keypair,
)
from wacryptolib.exceptions import KeyDoesNotExist, SignatureVerificationError, DecryptionError
from wacryptolib.keystore import DummyKeystore
from wacryptolib.signature import verify_message_signature
from wacryptolib.utilities import generate_uuid0


def test_escrow_api_workflow():

    keystore = DummyKeystore()
    escrow_api = EscrowApi(keystore=keystore)

    keychain_uid = generate_uuid0()
    keychain_uid_other = generate_uuid0()
    keychain_uid_unexisting = generate_uuid0()
    secret = get_random_bytes(127)
    secret_too_big = get_random_bytes(140)

    for _ in range(2):
        generate_free_keypair_for_least_provisioned_key_algo(
            keystore=keystore, max_free_keys_per_algo=10, key_algos=["RSA_OAEP", "DSA_DSS"]
        )
    assert keystore.get_free_keypairs_count("DSA_DSS") == 1
    assert keystore.get_free_keypairs_count("ECC_DSS") == 0
    assert keystore.get_free_keypairs_count("RSA_OAEP") == 1
    assert keystore.get_free_keypairs_count("RSA_PSS") == 0  # Different from other RSA keys

    # Keypair is well auto-created by get_public_key(), by default
    public_key_rsa_oaep_pem = escrow_api.fetch_public_key(keychain_uid=keychain_uid, key_algo="RSA_OAEP")

    with pytest.raises(KeyDoesNotExist, match="not found"):  # Key NOT autogenerated
        escrow_api.fetch_public_key(keychain_uid=generate_uuid0(), key_algo="RSA_OAEP", must_exist=True)

    _public_key_rsa_oaep_pem2 = escrow_api.fetch_public_key(keychain_uid=keychain_uid, key_algo="RSA_OAEP")
    assert _public_key_rsa_oaep_pem2 == public_key_rsa_oaep_pem  # Same KEYS!

    _public_key_rsa_pss_pem = escrow_api.fetch_public_key(keychain_uid=keychain_uid, key_algo="RSA_PSS")
    assert _public_key_rsa_pss_pem != public_key_rsa_oaep_pem  # Different KEYS!

    public_key_rsa_oaep = load_asymmetric_key_from_pem_bytestring(key_pem=public_key_rsa_oaep_pem, key_algo="RSA_OAEP")

    assert keystore.get_free_keypairs_count("DSA_DSS") == 1
    assert keystore.get_free_keypairs_count("ECC_DSS") == 0
    assert keystore.get_free_keypairs_count("RSA_OAEP") == 0  # Taken
    assert keystore.get_free_keypairs_count("RSA_PSS") == 0

    signature = escrow_api.get_message_signature(keychain_uid=keychain_uid, message=secret, signature_algo="DSA_DSS")

    with pytest.raises(ValueError, match="too big"):
        escrow_api.get_message_signature(keychain_uid=keychain_uid, message=secret_too_big, signature_algo="DSA_DSS")

    assert keystore.get_free_keypairs_count("DSA_DSS") == 0  # Taken
    assert keystore.get_free_keypairs_count("ECC_DSS") == 0
    assert keystore.get_free_keypairs_count("RSA_OAEP") == 0
    assert keystore.get_free_keypairs_count("RSA_PSS") == 0

    public_key_dsa_pem = escrow_api.fetch_public_key(keychain_uid=keychain_uid, key_algo="DSA_DSS")
    public_key_dsa = load_asymmetric_key_from_pem_bytestring(key_pem=public_key_dsa_pem, key_algo="DSA_DSS")

    verify_message_signature(message=secret, signature=signature, key=public_key_dsa, signature_algo="DSA_DSS")
    signature["digest"] += b"xyz"
    with pytest.raises(SignatureVerificationError, match="Failed.*verification"):
        verify_message_signature(message=secret, signature=signature, key=public_key_dsa, signature_algo="DSA_DSS")

    # Keypair is well auto-created by get_message_signature(), even when no more free keys
    signature = escrow_api.get_message_signature(
        keychain_uid=keychain_uid_other, message=secret, signature_algo="RSA_PSS"
    )
    assert signature

    # Keypair well autocreated by get_public_key(), even when no more free keys
    public_key_pem = escrow_api.fetch_public_key(keychain_uid=keychain_uid_other, key_algo="DSA_DSS")
    assert public_key_pem

    cipherdict = _encrypt_via_rsa_oaep(plaintext=secret, key_dict=dict(key=public_key_rsa_oaep))

    # Works even without decryption authorization request, by default:
    decrypted = escrow_api.decrypt_with_private_key(
        keychain_uid=keychain_uid, encryption_algo="RSA_OAEP", cipherdict=cipherdict
    )
    assert decrypted == secret

    # NO auto-creation of keypair in decrypt_with_private_key()
    with pytest.raises(KeyDoesNotExist, match="not found"):
        escrow_api.decrypt_with_private_key(
            keychain_uid=keychain_uid_unexisting, encryption_algo="RSA_OAEP", cipherdict=cipherdict
        )

    wrong_cipherdict = copy.deepcopy(cipherdict)
    wrong_cipherdict["digest_list"].append(b"aaabbbccc")
    with pytest.raises(ValueError, match="Ciphertext with incorrect length"):
        escrow_api.decrypt_with_private_key(
            keychain_uid=keychain_uid, encryption_algo="RSA_OAEP", cipherdict=wrong_cipherdict
        )

    with pytest.raises(ValueError, match="empty"):
        escrow_api.request_decryption_authorization(keypair_identifiers=[], request_message="I need this decryption!")
    # Authorization always granted for now, in dummy implementation
    result = escrow_api.request_decryption_authorization(
        keypair_identifiers=[dict(keychain_uid=keychain_uid, key_algo="RSA_OAEP")],
        request_message="I need this decryption!",
    )
    assert "accepted" in result["response_message"]
    assert not result["has_errors"]
    assert result["keypair_statuses"]["accepted"]

    # TEST PASSPHRASE PROTECTIONS

    keychain_uid_passphrased = generate_uuid0()
    good_passphrase = "good_passphrase"

    keypair_cipher_passphrased = generate_keypair_for_storage(
        key_algo="RSA_OAEP", keystore=keystore, keychain_uid=keychain_uid_passphrased, passphrase=good_passphrase
    )

    result = escrow_api.request_decryption_authorization(
        keypair_identifiers=[dict(keychain_uid=keychain_uid_passphrased, key_algo="RSA_OAEP")],
        request_message="I need this decryption too!",
    )
    assert "denied" in result["response_message"]
    assert result["has_errors"]
    assert result["keypair_statuses"]["missing_passphrase"]

    result = escrow_api.request_decryption_authorization(
        keypair_identifiers=[dict(keychain_uid=keychain_uid_passphrased, key_algo="RSA_OAEP")],
        request_message="I need this decryption too!",
        passphrases=["aaa"],
    )
    assert "denied" in result["response_message"]
    assert result["has_errors"]
    assert result["keypair_statuses"]["missing_passphrase"]

    result = escrow_api.request_decryption_authorization(
        keypair_identifiers=[dict(keychain_uid=keychain_uid_passphrased, key_algo="RSA_OAEP")],
        request_message="I need this decryption too!",
        passphrases=["dsd", good_passphrase],
    )
    assert "accepted" in result["response_message"]
    assert not result["has_errors"]
    assert result["keypair_statuses"]["accepted"]

    public_key_rsa_oaep2 = load_asymmetric_key_from_pem_bytestring(
        key_pem=keypair_cipher_passphrased["public_key"], key_algo="RSA_OAEP"
    )
    cipherdict = _encrypt_via_rsa_oaep(plaintext=secret, key_dict=dict(key=public_key_rsa_oaep2))

    with pytest.raises(DecryptionError, match="not decrypt"):
        escrow_api.decrypt_with_private_key(
            keychain_uid=keychain_uid_passphrased, encryption_algo="RSA_OAEP", cipherdict=cipherdict
        )

    with pytest.raises(DecryptionError, match="not decrypt"):
        escrow_api.decrypt_with_private_key(
            keychain_uid=keychain_uid_passphrased,
            encryption_algo="RSA_OAEP",
            cipherdict=cipherdict,
            passphrases=["something"],
        )

    decrypted = escrow_api.decrypt_with_private_key(
        keychain_uid=keychain_uid_passphrased,
        encryption_algo="RSA_OAEP",
        cipherdict=cipherdict,
        passphrases=[good_passphrase],
    )
    assert decrypted == secret

    assert keystore.get_free_keypairs_count("DSA_DSS") == 0
    assert keystore.get_free_keypairs_count("ECC_DSS") == 0
    assert keystore.get_free_keypairs_count("RSA_OAEP") == 0
    assert keystore.get_free_keypairs_count("RSA_PSS") == 0


def test_readonly_escrow_api_behaviour():

    keystore = DummyKeystore()
    escrow_api = ReadonlyEscrowApi(keystore=keystore)

    keychain_uid = generate_uuid0()
    key_algo_cipher = "RSA_OAEP"
    key_algo_signature = "RSA_PSS"
    secret = get_random_bytes(127)

    for must_exist in (True, False):
        with pytest.raises(KeyDoesNotExist, match="not found"):
            escrow_api.fetch_public_key(keychain_uid=keychain_uid, key_algo=key_algo_signature, must_exist=must_exist)

    with pytest.raises(KeyDoesNotExist, match="not found"):
        escrow_api.get_message_signature(keychain_uid=keychain_uid, message=secret, signature_algo="RSA_PSS")

    # Always accepted for now, dummy implementation
    result = escrow_api.request_decryption_authorization(
        keypair_identifiers=[dict(keychain_uid=keychain_uid, key_algo=key_algo_cipher)],
        request_message="I need this decryption!",
    )
    assert "denied" in result["response_message"]
    assert result["has_errors"]
    assert result["keypair_statuses"]["missing_private_key"]

    # Still no auto-creation of keypair in decrypt_with_private_key()
    with pytest.raises(KeyDoesNotExist, match="not found"):
        escrow_api.decrypt_with_private_key(keychain_uid=keychain_uid, encryption_algo=key_algo_cipher, cipherdict={})

    # Now we generate wanted keys #

    keypair_cipher = generate_keypair_for_storage(
        key_algo=key_algo_cipher, keystore=keystore, keychain_uid=keychain_uid
    )

    keypair_signature = generate_keypair_for_storage(
        key_algo=key_algo_signature, keystore=keystore, keychain_uid=keychain_uid
    )

    public_key2 = escrow_api.fetch_public_key(
        keychain_uid=keychain_uid, key_algo=key_algo_signature, must_exist=must_exist
    )
    assert public_key2 == keypair_signature["public_key"]

    signature = escrow_api.get_message_signature(keychain_uid=keychain_uid, message=secret, signature_algo="RSA_PSS")
    assert signature and isinstance(signature, dict)

    private_key_cipher = load_asymmetric_key_from_pem_bytestring(
        key_pem=keypair_cipher["private_key"], key_algo=key_algo_cipher
    )
    cipherdict = _encrypt_via_rsa_oaep(plaintext=secret, key_dict=dict(key=private_key_cipher))
    decrypted = escrow_api.decrypt_with_private_key(
        keychain_uid=keychain_uid, encryption_algo=key_algo_cipher, cipherdict=cipherdict
    )
    assert decrypted == secret


def test_generate_free_keypair_for_least_provisioned_key_algo():

    generated_keys_count = 0

    def keygen_func(key_algo, serialize):
        nonlocal generated_keys_count
        generated_keys_count += 1
        return dict(private_key=b"someprivatekey", public_key=b"somepublickey")

    # Check the fallback on "all types of keys" for key_algos parameter

    keystore = DummyKeystore()

    for _ in range(4):
        res = generate_free_keypair_for_least_provisioned_key_algo(
            keystore=keystore,
            max_free_keys_per_algo=10,
            keygen_func=keygen_func,
            # no key_algos parameter provided
        )
        assert res

    assert keystore.get_free_keypairs_count("DSA_DSS") == 1
    assert keystore.get_free_keypairs_count("ECC_DSS") == 1
    assert keystore.get_free_keypairs_count("RSA_OAEP") == 1
    assert keystore.get_free_keypairs_count("RSA_PSS") == 1
    assert generated_keys_count == 4

    # Now test with a restricted set of key types

    keystore = DummyKeystore()
    restricted_key_algos = ["DSA_DSS", "ECC_DSS", "RSA_OAEP"]
    generated_keys_count = 0

    for _ in range(7):
        res = generate_free_keypair_for_least_provisioned_key_algo(
            keystore=keystore,
            max_free_keys_per_algo=10,
            keygen_func=keygen_func,
            key_algos=restricted_key_algos,
        )
        assert res

    assert keystore.get_free_keypairs_count("DSA_DSS") == 3
    assert keystore.get_free_keypairs_count("ECC_DSS") == 2
    assert keystore.get_free_keypairs_count("RSA_OAEP") == 2
    assert generated_keys_count == 7

    for _ in range(23):
        res = generate_free_keypair_for_least_provisioned_key_algo(
            keystore=keystore,
            max_free_keys_per_algo=10,
            keygen_func=keygen_func,
            key_algos=restricted_key_algos,
        )
        assert res

    assert keystore.get_free_keypairs_count("DSA_DSS") == 10
    assert keystore.get_free_keypairs_count("ECC_DSS") == 10
    assert keystore.get_free_keypairs_count("RSA_OAEP") == 10
    assert generated_keys_count == 30

    res = generate_free_keypair_for_least_provisioned_key_algo(
        keystore=keystore,
        max_free_keys_per_algo=10,
        keygen_func=keygen_func,
        key_algos=restricted_key_algos,
    )
    assert not res
    assert generated_keys_count == 30  # Unchanged

    for _ in range(7):
        generate_free_keypair_for_least_provisioned_key_algo(
            keystore=keystore,
            max_free_keys_per_algo=15,
            keygen_func=keygen_func,
            key_algos=["RSA_OAEP", "DSA_DSS"],
        )

    assert keystore.get_free_keypairs_count("DSA_DSS") == 14  # First in sorting order
    assert keystore.get_free_keypairs_count("ECC_DSS") == 10
    assert keystore.get_free_keypairs_count("RSA_OAEP") == 13
    assert generated_keys_count == 37

    res = generate_free_keypair_for_least_provisioned_key_algo(
        keystore=keystore,
        max_free_keys_per_algo=20,
        keygen_func=keygen_func,
        key_algos=restricted_key_algos,
    )
    assert res
    assert keystore.get_free_keypairs_count("DSA_DSS") == 14
    assert keystore.get_free_keypairs_count("ECC_DSS") == 11
    assert keystore.get_free_keypairs_count("RSA_OAEP") == 13
    assert generated_keys_count == 38

    res = generate_free_keypair_for_least_provisioned_key_algo(
        keystore=keystore,
        max_free_keys_per_algo=5,
        keygen_func=keygen_func,
        key_algos=restricted_key_algos,
    )
    assert not res
    assert generated_keys_count == 38


def test_get_free_keys_generator_worker():

    generate_keys_count = 0

    keystore = DummyKeystore()

    def keygen_func(key_algo, serialize):
        nonlocal generate_keys_count
        generate_keys_count += 1
        time.sleep(0.01)
        return dict(private_key=b"someprivatekey2", public_key=b"somepublickey2")

    worker = get_free_keys_generator_worker(
        keystore=keystore,
        max_free_keys_per_algo=30,
        sleep_on_overflow_s=0.5,
        keygen_func=keygen_func,
    )

    try:
        worker.start()
        time.sleep(0.5)
        worker.stop()
        worker.join()

        assert 10 < generate_keys_count < 50, generate_keys_count  # Not enough time to generate all

        worker.start()
        time.sleep(6)
        worker.stop()
        worker.join()

        assert (
            generate_keys_count == 120  # 4 key types for now
        ), generate_keys_count  # All keys had the time to be generated

        start = time.time()
        worker.start()
        worker.stop()
        worker.join()
        end = time.time()
        assert (end - start) > 0.4  # sleep-on-overflow occurred

    finally:
        if worker.is_running:
            worker.stop()
