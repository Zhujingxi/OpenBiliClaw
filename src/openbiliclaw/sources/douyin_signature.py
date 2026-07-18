"""Minimal Douyin Web URL signing helpers.

The X-Bogus implementation is adapted from Evil0ctal's
Douyin_TikTok_Download_API project, licensed under Apache-2.0:
https://github.com/Evil0ctal/Douyin_TikTok_Download_API
"""

from __future__ import annotations

import base64
import hashlib
import time


class XBogusSigner:
    """Generate the legacy X-Bogus query parameter for Douyin Web URLs."""

    def __init__(self, user_agent: str) -> None:
        self._character = "Dkdpgh4ZKsQB80/Mfvw36XI1R25-WUAlEi7NLboqYTOPuzmFjJnryx9HVGcaStCe="
        self._ua_key = b"\x00\x01\x0c"
        self._user_agent = user_agent

    @property
    def user_agent(self) -> str:
        return self._user_agent

    def sign(self, url: str) -> str:
        """Return *url* with an appended ``X-Bogus`` parameter."""
        ua_md5_array = self._md5_str_to_array(
            self._md5(
                base64.b64encode(
                    self._rc4_encrypt(
                        self._ua_key,
                        self._user_agent.encode("ISO-8859-1"),
                    )
                ).decode("ISO-8859-1")
            )
        )

        empty_md5_array = self._md5_str_to_array(
            self._md5(self._md5_str_to_array("d41d8cd98f00b204e9800998ecf8427e"))
        )
        url_md5_array = self._md5_encrypt(url)
        timer = int(time.time())
        ct = 536919696

        new_array: list[int | float] = [
            64,
            0.00390625,
            1,
            12,
            url_md5_array[14],
            url_md5_array[15],
            empty_md5_array[14],
            empty_md5_array[15],
            ua_md5_array[14],
            ua_md5_array[15],
            timer >> 24 & 255,
            timer >> 16 & 255,
            timer >> 8 & 255,
            timer & 255,
            ct >> 24 & 255,
            ct >> 16 & 255,
            ct >> 8 & 255,
            ct & 255,
        ]

        xor_result = int(new_array[0])
        for value in new_array[1:]:
            xor_result ^= int(value)
        new_array.append(xor_result)

        odd: list[int] = []
        even: list[int] = []
        idx = 0
        while idx < len(new_array):
            odd.append(int(new_array[idx]))
            if idx + 1 < len(new_array):
                even.append(int(new_array[idx + 1]))
            idx += 2

        merged = odd + even
        garbled = self._encoding_conversion2(
            2,
            255,
            self._rc4_encrypt(
                bytes([255]),
                self._encoding_conversion(*merged).encode("ISO-8859-1"),
            ).decode("ISO-8859-1"),
        )

        xb = ""
        idx = 0
        while idx < len(garbled):
            xb += self._calculation(
                ord(garbled[idx]),
                ord(garbled[idx + 1]),
                ord(garbled[idx + 2]),
            )
            idx += 3
        return f"{url}&X-Bogus={xb}"

    def _md5_str_to_array(self, md5_str: str) -> list[int]:
        if isinstance(md5_str, str) and len(md5_str) > 32:
            return [ord(char) for char in md5_str]

        array: list[int] = []
        idx = 0
        while idx < len(md5_str):
            array.append(int(md5_str[idx : idx + 2], 16))
            idx += 2
        return array

    def _md5(self, input_data: str | list[int]) -> str:
        data = self._md5_str_to_array(input_data) if isinstance(input_data, str) else input_data
        md5_hash = hashlib.md5()
        md5_hash.update(bytes(data))
        return md5_hash.hexdigest()

    def _md5_encrypt(self, url_path: str) -> list[int]:
        hashed = self._md5(self._md5_str_to_array(self._md5(url_path)))
        return self._md5_str_to_array(hashed)

    def _encoding_conversion(self, *values: int) -> str:
        a, b, c, e, d, t, f, r, n, o, i, _, x, u, s, ell, v, h, p = values
        payload = [a, int(i), b, _, c, x, e, u, d, s, t, ell, f, v, r, h, n, p, o]
        return bytes(payload).decode("ISO-8859-1")

    @staticmethod
    def _encoding_conversion2(a: int, b: int, c: str) -> str:
        return chr(a) + chr(b) + c

    @staticmethod
    def _rc4_encrypt(key: bytes, data: bytes) -> bytearray:
        state = list(range(256))
        j = 0
        encrypted = bytearray()

        for i in range(256):
            j = (j + state[i] + key[i % len(key)]) % 256
            state[i], state[j] = state[j], state[i]

        i = j = 0
        for byte in data:
            i = (i + 1) % 256
            j = (j + state[i]) % 256
            state[i], state[j] = state[j], state[i]
            encrypted.append(byte ^ state[(state[i] + state[j]) % 256])

        return encrypted

    def _calculation(self, a1: int, a2: int, a3: int) -> str:
        x3 = ((a1 & 255) << 16) | ((a2 & 255) << 8) | (a3 & 255)
        return (
            self._character[(x3 & 16515072) >> 18]
            + self._character[(x3 & 258048) >> 12]
            + self._character[(x3 & 4032) >> 6]
            + self._character[x3 & 63]
        )
