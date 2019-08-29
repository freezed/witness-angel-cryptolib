from typing import List

import itertools

from Crypto.Protocol.SecretSharing import Shamir
from Crypto.Util.Padding import unpad

from wacryptolib.utilities import split_as_chunks, split_as_chunks


def split_bytestring_as_shamir_shares(
    secret: bytes, shares_count: int, threshold_count: int
) -> dict:
    """Generate a shared secret of `shares_count` subkeys, with `threshold_count`
        of them required to recompute the initial `bytestring`.

        :param secret: bytestring to separate as shares, whatever its length
        :param shares_count: the number of shares to be created for the secret
        :param threshold_count: the minimal number of shares needed to recombine the key

        :return: a dict mapping indexes to full bytestring shares"""

    assert threshold_count < shares_count, (threshold_count, shares_count)

    all_chunk_shares = []  # List of lists of related 16-bytes shares

    # Split the secret into tuples of 16 bytes exactly (after padding)
    chunks = split_as_chunks(secret, chunk_size=16, must_pad=True)

    # Separate each chunk into share
    for chunk in chunks:
        shares = _split_128b_bytestring_into_shares(
            chunk, shares_count, threshold_count
        )
        all_chunk_shares.append(shares)
        del shares

    ##all_shares = list(map(list, zip(*all_shares)))
    ##shares_long_bytestring = {}

    full_shares = []

    for idx in range(shares_count):
        assert all(
            chunk_share[idx][0] == idx + 1 for chunk_share in all_chunk_shares
        )  # Share indexes start at 1
        idx_shares = (chunk_share[idx][1] for chunk_share in all_chunk_shares)
        complete_share = b"".join(idx_shares)
        full_shares.append((idx + 1, complete_share))

    return full_shares


def reconstruct_secret_from_samir_shares(shares: list) -> bytes:
    """Permits to reconstruct a key which has its secret shared
    into `shares_count` shares thanks to a list of `shares`

    :param shares: list of k full-length shares (k being exactly the threshold of this shared secret)

    :return: the key reconstructed as bytes"""

    shares_per_secret = []  # List of lists of same-index 16-bytes shares

    assert len(set(share[0] for share in shares)) == len(
        shares
    )  # All shares have unique idx

    for share in shares:
        idx, secret = share
        chunks = split_as_chunks(secret, chunk_size=16, must_pad=False)
        shares_per_secret.append([(idx, chunk) for chunk in chunks])

    assert (
        len(set(len(chunks) for chunks in shares_per_secret)) == 1
    )  # Same-length lists

    all_chunk_shares = list(zip(*shares_per_secret))

    chunks = []
    for chunk_shares in all_chunk_shares:
        print("CHUNK SHARES:", chunk_shares)
        chunk = _recombine_128b_shares_into_bytestring(chunk_shares)
        chunks.append(chunk)

    secret_padded = b"".join(chunks)
    secret = unpad(secret_padded, block_size=16)
    return secret

    """
    for index in range(0, shares_count):
        long_bytestring = shares_long_bytestring[index]
        split_long_bytestring = split_as_chunks(long_bytestring, 16)
        for slice in range(0, len(split_long_bytestring)):
            share = index + 1, split_long_bytestring[slice]
            shares.append(share)

    shares1 = []
    shares2 = []
    shares3 = []

    for share in range(len(shares)):
        if shares[share][0] == 1:
            shares1.append(shares[share])
        elif shares[share][0] == 2:
            shares2.append(shares[share])
        elif shares[share][0] == 3:
            shares3.append(shares[share])

    all_shares = list(zip(shares1, shares2, shares3))

    combined_shares = _recombine_shares_into_list(all_shares)
    if bytestring_length % 16 != 0:
        combined_shares[-1] = unpad(combined_shares[-1], 16)
    bytestring_reconstructed = b"".join(combined_shares)
    return bytestring_reconstructed
    """


def _split_128b_bytestring_into_shares(
    secret: bytes, shares_count: int, threshold_count: int
) -> List[tuple]:
    """Split a bytestring of exactly 128 bits into shares.

        :param bytestring: bytestring to split
        :param shares_count: number of shares to create
        :param threshold_count: number of shares needed to reconstitute the secret

        :return: list of tuples (index, share)"""

    assert len(secret) == 16
    shares = Shamir.split(k=threshold_count, n=shares_count, secret=secret)
    assert len(shares) == shares_count, shares
    return shares


def _recombine_128b_shares_into_bytestring(shares: List[tuple]) -> bytes:
    """Recombine shares of exactly 128 bits into a bytestring.

        :param bytestring: bytestring to split
        :param shares_count: number of shares to create
        :param threshold_count: number of shares needed to reconstitute the secret

        :return: list of tuples (index, share)"""

    secret = Shamir.combine(shares)
    return secret


def _recombine_shares_into_list(shares: List[bytes]) -> List[bytes]:
    """Recombine shares from a list of bytes corresponding
        to the `shares` of a bytestring. In the `shares` list, it is possible
        to have shares which doesn't come from the same initial message.

        :param shares: list of tuples composed of the share and its corresponding number

        :return: list of bytes with all the shares recombined."""

    combined_shares_list = []
    for slices in range(0, len(shares)):
        combined_share = Shamir.combine(shares[slices])
        combined_shares_list.append(combined_share)
    return combined_shares_list
