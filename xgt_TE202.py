#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TE202 주소 확정 프로브
====================================================================
AutoBase 상세화면 확인 결과 (TE202, 소각로출구온도):
    현재값 1003 C
    Port 000, Addres 0101
    계기 raw 0~16000  ->  공학 0~1600   (즉 raw ÷ 10)
  => FEnet raw 로 읽으면 약 10030 이 나와야 함 (÷10 = 1003)

'Addres 0101'의 해석 후보를 모두 찍어서 10030 근처가 나오는 주소를 찾는다.
그 주소가 확정되면 0101 -> 실제 XGT 주소 변환 규칙이 일반화된다.

⚠️ 읽기 전용.
"""

import socket
import struct

COMPANY_ID_XGT = b"LSIS-XGT\x00\x00"
COMPANY_ID_GLOFA = b"LGIS-GLOFA"
PLC_IP = "192.168.127.1"
CID = COMPANY_ID_XGT   # 타임아웃 시 COMPANY_ID_GLOFA

TARGET_RAW = 10030     # 기대 raw (현재값 1003 x 10). ±수십 오차 허용
TARGET_ENG = 1003


class XGTError(IOError):
    pass


class Reader:
    def __init__(self, host, port=2004, timeout=3.0, cid=COMPANY_ID_XGT):
        self.host = host; self.port = port; self.timeout = timeout; self.cid = cid
        self.sock = None; self.inv = 0

    def __enter__(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout); return self

    def __exit__(self, *a):
        if self.sock: self.sock.close()

    def _recv(self, n):
        b = b""
        while len(b) < n:
            c = self.sock.recv(n - len(b))
            if not c: raise ConnectionError("closed")
            b += c
        return b

    def read1(self, dev, dtype=0x0002):
        nb = dev.encode()
        instr = struct.pack("<HHHH", 0x0054, dtype, 0, 1) + struct.pack("<H", len(nb)) + nb
        self.inv = (self.inv + 1) & 0xFFFF
        h = bytearray(20); h[0:10] = self.cid; h[13] = 0x33
        struct.pack_into("<H", h, 14, self.inv); struct.pack_into("<H", h, 16, len(instr))
        self.sock.sendall(bytes(h) + instr)
        hd = self._recv(20); ilen = struct.unpack_from("<H", hd, 16)[0]
        ins = self._recv(ilen)
        cmd = struct.unpack_from("<H", ins, 0)[0]; err = struct.unpack_from("<H", ins, 6)[0]
        if cmd != 0x0055: raise XGTError("cmd 0x%04X" % cmd)
        if err != 0: raise XGTError("PLC err 0x%04X" % err)
        cnt = struct.unpack_from("<H", ins, 8)[0]
        if cnt < 1: return None
        sz = struct.unpack_from("<H", ins, 10)[0]
        return ins[12:12 + sz]


# 'Addres 0101' 해석 후보 (여러 device / 여러 진법)
CANDIDATES = [
    # 0101을 16진 워드주소로
    "%MW257",       # 0x0101
    # 0101을 십진 워드주소로
    "%MW101",
    # D 영역(데이터 레지스터)도 가능성
    "%DW257", "%DW101",
    # 순서번호(446) 부근 - 혹시 순차 매핑일 때
    "%MW446", "%MW445", "%MW447",
    # 0101 근방 스캔 (16진 기준 주변)
    "%MW256", "%MW258", "%MW255", "%MW259", "%MW260",
    # 십진 근방
    "%MW100", "%MW102", "%MW103",
    # 바이트단위 해석
    "%MB514", "%MB202",
]


def near(v, target, tol=60):
    return v is not None and abs(v - target) <= tol


if __name__ == "__main__":
    print("TE202 확정 프로브 - 기대 raw≈%d (현재값 %d C)" % (TARGET_RAW, TARGET_ENG))
    print("=" * 60)
    with Reader(PLC_IP, cid=CID) as plc:
        found = []
        for dev in CANDIDATES:
            try:
                raw = plc.read1(dev)
                if raw is None or len(raw) < 2:
                    print("%-9s -> (데이터 없음)" % dev); continue
                u = struct.unpack("<H", raw[:2])[0]
                s = struct.unpack("<h", raw[:2])[0]
                mark = ""
                if near(u, TARGET_RAW) or near(u, TARGET_ENG):
                    mark = "  <<< 후보!"
                    found.append(dev)
                print("%-9s  u16=%6d  s16=%7d  (/10=%.1f)  hex=%s%s"
                      % (dev, u, s, u / 10.0, raw.hex(), mark))
            except XGTError as e:
                print("%-9s  ERR %s" % (dev, e))
            except Exception as e:
                print("%-9s  EXC %r" % (dev, e))
        print("=" * 60)
        if found:
            print("확정 후보 주소: %s" % ", ".join(found))
            print("→ 이 주소가 TE202(0101)의 실제 위치입니다.")
        else:
            print("근접값 없음. 화면의 TE202 현재값이 지금 몇인지 다시 확인 후,")
            print("TARGET_ENG 를 그 값으로 바꿔 재실행하거나 scan_wide.py 로 광역 스캔하세요.")
