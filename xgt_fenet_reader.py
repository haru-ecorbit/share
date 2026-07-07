"""
LS ELECTRIC XGI FEnet (XGT 전용 프로토콜) 읽기 전용 클라이언트
대상 PLC : XGI-CPUU MAIN, TCP/IP 192.168.127.1 : 2004
용도     : 메모리맵 M 영역 태그 직접 읽기 (READ ONLY)
"""

import socket
import struct
from dataclasses import dataclass

COMPANY_ID_XGT   = b"LSIS-XGT\x00\x00"   # XGI/XGK/XGB 계열
COMPANY_ID_GLOFA = b"LGIS-GLOFA"          # 구형 Glofa 폴백용

SRC_CLIENT_TO_SERVER = 0x33
CMD_READ_REQUEST  = 0x0054
CMD_READ_RESPONSE = 0x0055

DT_BIT   = 0x0000
DT_BYTE  = 0x0001
DT_WORD  = 0x0002
DT_DWORD = 0x0003
DT_LWORD = 0x0004


class XGTError(IOError):
    pass


class XGTFEnetReader:
    def __init__(self, host, port=2004, timeout=3.0, company_id=COMPANY_ID_XGT):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.company_id = company_id
        self._sock = None
        self._invoke = 0

    def connect(self):
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self._sock.settimeout(self.timeout)
        return self

    def close(self):
        if self._sock:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()

    def _next_invoke(self):
        self._invoke = (self._invoke + 1) & 0xFFFF
        return self._invoke

    def _build_header(self, instruction_len, invoke_id):
        h = bytearray(20)
        h[0:10] = self.company_id
        h[13] = SRC_CLIENT_TO_SERVER
        struct.pack_into("<H", h, 14, invoke_id)
        struct.pack_into("<H", h, 16, instruction_len)
        return bytes(h)

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("PLC가 연결을 닫았습니다.")
            buf += chunk
        return buf

    def read_individual(self, variables, data_type):
        if not (1 <= len(variables) <= 16):
            raise ValueError("개별 읽기는 1~16개 변수만 가능합니다.")
        instr = bytearray()
        instr += struct.pack("<H", CMD_READ_REQUEST)
        instr += struct.pack("<H", data_type)
        instr += struct.pack("<H", 0x0000)
        instr += struct.pack("<H", len(variables))
        for v in variables:
            name = v.encode("ascii")
            instr += struct.pack("<H", len(name))
            instr += name
        invoke = self._next_invoke()
        frame = self._build_header(len(instr), invoke) + bytes(instr)
        self._sock.sendall(frame)
        return self._read_response()

    def _read_response(self):
        header = self._recv_exact(20)
        instr_len = struct.unpack_from("<H", header, 16)[0]
        instr = self._recv_exact(instr_len)
        cmd = struct.unpack_from("<H", instr, 0)[0]
        err = struct.unpack_from("<H", instr, 6)[0]
        if cmd != CMD_READ_RESPONSE:
            raise XGTError(f"예상치 못한 응답 명령: 0x{cmd:04X}")
        if err != 0:
            raise XGTError(f"PLC 에러 상태: 0x{err:04X}")
        count = struct.unpack_from("<H", instr, 8)[0]
        off = 10
        blocks = []
        for _ in range(count):
            size = struct.unpack_from("<H", instr, off)[0]
            off += 2
            blocks.append(instr[off:off + size])
            off += size
        return blocks


def decode_int16(raw, signed=True):
    return struct.unpack("<h" if signed else "<H", raw[:2])[0]


def decode_int32(raw, signed=False):
    return struct.unpack("<i" if signed else "<I", raw[:4])[0]


def linear_scale(raw_value, raw_min, raw_max, eng_min, eng_max):
    if raw_max == raw_min:
        return float(raw_value)
    return eng_min + (raw_value - raw_min) * (eng_max - eng_min) / (raw_max - raw_min)


def bit(word_value, n):
    return (word_value >> n) & 1


@dataclass
class AnalogTag:
    name: str
    device: str
    dtype: int
    signed: bool
    raw_min: float
    raw_max: float
    eng_min: float
    eng_max: float
    unit: str
    desc: str


TE201 = AnalogTag(
    name="TE201", device="%MW100", dtype=DT_WORD, signed=True,
    raw_min=0, raw_max=16000, eng_min=0, eng_max=1600, unit="°C", desc="소각로내 온도",
)


def read_analog(reader, tag):
    raw = reader.read_individual([tag.device], tag.dtype)[0]
    if tag.dtype == DT_WORD:
        raw_val = decode_int16(raw, tag.signed)
    elif tag.dtype == DT_DWORD:
        raw_val = decode_int32(raw, tag.signed)
    else:
        raise ValueError(f"미지원 dtype: {tag.dtype}")
    eng = linear_scale(raw_val, tag.raw_min, tag.raw_max, tag.eng_min, tag.eng_max)
    return raw_val, eng


def main():
    PLC_IP = "192.168.127.1"
    with XGTFEnetReader(PLC_IP, port=2004, timeout=3.0) as plc:
        raw, eng = read_analog(plc, TE201)
        print(f"[AI ] {TE201.name:12s} raw={raw:>8}  ->  {eng:8.1f} {TE201.unit}  ({TE201.desc})")


if __name__ == "__main__":
    main()