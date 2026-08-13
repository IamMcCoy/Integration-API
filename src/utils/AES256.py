"""
Author : Wonjun Kim
e-mail : wonjun.kim@seculayer.com
Powered by Seculayer © 2026 AI Team, R&D Center.
"""
from __future__ import annotations

import base64
import hashlib

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes


class AES256:
    """
    conf/db.properties 의 자격증명을 감싸는 데 쓴다.

    KEY 가 소스에 하드코딩돼 있어 저장소를 가진 사람은 누구나 복호화한다.
    비밀을 지키는 장치가 아니라 설정 파일에 평문이 보이지 않게 하는 수준이다.
    실제 보호가 필요해지면 키를 환경변수로 빼거나 KMS 로 옮긴다.
    """

    BLOCK_SIZE = 16
    KEY = b'AES/CBC/PKCS5Padding'

    @staticmethod
    def _pad(data: bytes) -> bytes:
        """PKCS7 패딩"""
        pad_len = AES256.BLOCK_SIZE - len(data) % AES256.BLOCK_SIZE
        return data + bytes([pad_len] * pad_len)

    @staticmethod
    def _unpad(data: bytes) -> bytes:
        """PKCS7 언패딩"""
        return data[:-data[-1]]

    @classmethod
    def _make_key(cls) -> bytes:
        """고정 키를 32바이트 AES-256 키로 변환"""
        return hashlib.sha256(cls.KEY).digest()

    def encrypt(self, plaintext: str) -> str:
        """문자열 암호화 → Base64 출력"""
        key_bytes = self._make_key()
        data = self._pad(plaintext.encode())
        iv = get_random_bytes(self.BLOCK_SIZE)
        cipher = AES.new(key_bytes, AES.MODE_CBC, iv)
        encrypted = cipher.encrypt(data)
        return base64.b64encode(iv + encrypted).decode('utf-8')

    def decrypt(self, ciphertext: str) -> str:
        """Base64 문자열 복호화 → 원문 반환"""
        key_bytes = self._make_key()
        raw = base64.b64decode(ciphertext)
        iv = raw[:self.BLOCK_SIZE]
        encrypted = raw[self.BLOCK_SIZE:]
        cipher = AES.new(key_bytes, AES.MODE_CBC, iv)
        decrypted = self._unpad(cipher.decrypt(encrypted))
        return decrypted.decode('utf-8')


if __name__ == '__main__':
    import sys

    aes = AES256()

    for sample in ('integration', 'p@ssw0rd!#$', '가나다', 'x' * 16, ''):
        assert aes.decrypt(aes.encrypt(sample)) == sample, f"Round-trip failed: {sample!r}"

    # IV 가 매번 달라야 같은 평문이 같은 암호문으로 굳지 않는다.
    assert aes.encrypt('same') != aes.encrypt('same'), 'IV must be random per call'

    # conf/db.properties 에 넣을 암호문을 만드는 통로.
    #   python -m src.utils.AES256 <평문>
    if len(sys.argv) > 1:
        for plaintext in sys.argv[1:]:
            print(aes.encrypt(plaintext))
    else:
        print('OK: round-trip, random IV — pass a plaintext to encrypt it')
