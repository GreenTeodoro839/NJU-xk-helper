"""
金智教务选课系统前端 DES 加密 Python 移植
对应 JS: strEnc(data, key1, key2, key3) + $.base64.encode()
密钥固定为 ("this", "password", "is")
"""
import base64 as _b64


# ==================== 低层工具 ====================

def _str_to_bt(s):
    """将最多 4 个字符转为 64-bit 数组（与 JS strToBt 完全一致）"""
    leng = len(s)
    bt = [0] * 64
    for n in range(4):
        if n < leng:
            code = ord(s[n])
        else:
            code = 0
        for t in range(16):
            pwr = 1
            for m in range(15, t, -1):
                pwr *= 2
            bt[16 * n + t] = int(code / pwr) % 2
    return bt


def _bt4_to_hex(bits4: str) -> str:
    _map = {
        "0000": "0", "0001": "1", "0010": "2", "0011": "3",
        "0100": "4", "0101": "5", "0110": "6", "0111": "7",
        "1000": "8", "1001": "9", "1010": "A", "1011": "B",
        "1100": "C", "1101": "D", "1110": "E", "1111": "F",
    }
    return _map[bits4]


def _bt64_to_hex(bt):
    s = ""
    for i in range(16):
        chunk = ""
        for j in range(4):
            chunk += str(bt[4 * i + j])
        s += _bt4_to_hex(chunk)
    return s


def _get_box_binary(val):
    _map = {
        0: "0000", 1: "0001", 2: "0010", 3: "0011",
        4: "0100", 5: "0101", 6: "0110", 7: "0111",
        8: "1000", 9: "1001", 10: "1010", 11: "1011",
        12: "1100", 13: "1101", 14: "1110", 15: "1111",
    }
    return _map[val]


def _get_key_bytes(key):
    arr = []
    leng = len(key)
    n = leng // 4
    rem = leng % 4
    for i in range(n):
        arr.append(_str_to_bt(key[4 * i: 4 * i + 4]))
    if rem > 0:
        arr.append(_str_to_bt(key[4 * n: leng]))
    return arr


# ==================== DES 核心（与 JS 一一对应） ====================

def _init_permute(bt):
    """JS: initPermute — 自定义初始置换"""
    e = [0] * 64
    m = 1
    n = 0
    for i in range(4):
        k = 0
        for j in range(7, -1, -1):
            e[8 * i + k] = bt[8 * j + m]
            e[8 * i + k + 32] = bt[8 * j + n]
            k += 1
        m += 2
        n += 2
    return e


def _expand_permute(bt):
    """JS: expandPermute"""
    e = [0] * 48
    for i in range(8):
        if i == 0:
            e[6 * i + 0] = bt[31]
        else:
            e[6 * i + 0] = bt[4 * i - 1]
        e[6 * i + 1] = bt[4 * i + 0]
        e[6 * i + 2] = bt[4 * i + 1]
        e[6 * i + 3] = bt[4 * i + 2]
        e[6 * i + 4] = bt[4 * i + 3]
        if i == 7:
            e[6 * i + 5] = bt[0]
        else:
            e[6 * i + 5] = bt[4 * i + 4]
    return e


def _xor(a, b):
    return [a[i] ^ b[i] for i in range(len(a))]


def _s_box_permute(bt):
    """JS: sBoxPermute（包含 8 个 S-Box）"""
    S = [
        [[14,4,13,1,2,15,11,8,3,10,6,12,5,9,0,7],
         [0,15,7,4,14,2,13,1,10,6,12,11,9,5,3,8],
         [4,1,14,8,13,6,2,11,15,12,9,7,3,10,5,0],
         [15,12,8,2,4,9,1,7,5,11,3,14,10,0,6,13]],
        [[15,1,8,14,6,11,3,4,9,7,2,13,12,0,5,10],
         [3,13,4,7,15,2,8,14,12,0,1,10,6,9,11,5],
         [0,14,7,11,10,4,13,1,5,8,12,6,9,3,2,15],
         [13,8,10,1,3,15,4,2,11,6,7,12,0,5,14,9]],
        [[10,0,9,14,6,3,15,5,1,13,12,7,11,4,2,8],
         [13,7,0,9,3,4,6,10,2,8,5,14,12,11,15,1],
         [13,6,4,9,8,15,3,0,11,1,2,12,5,10,14,7],
         [1,10,13,0,6,9,8,7,4,15,14,3,11,5,2,12]],
        [[7,13,14,3,0,6,9,10,1,2,8,5,11,12,4,15],
         [13,8,11,5,6,15,0,3,4,7,2,12,1,10,14,9],
         [10,6,9,0,12,11,7,13,15,1,3,14,5,2,8,4],
         [3,15,0,6,10,1,13,8,9,4,5,11,12,7,2,14]],
        [[2,12,4,1,7,10,11,6,8,5,3,15,13,0,14,9],
         [14,11,2,12,4,7,13,1,5,0,15,10,3,9,8,6],
         [4,2,1,11,10,13,7,8,15,9,12,5,6,3,0,14],
         [11,8,12,7,1,14,2,13,6,15,0,9,10,4,5,3]],
        [[12,1,10,15,9,2,6,8,0,13,3,4,14,7,5,11],
         [10,15,4,2,7,12,9,5,6,1,13,14,0,11,3,8],
         [9,14,15,5,2,8,12,3,7,0,4,10,1,13,11,6],
         [4,3,2,12,9,5,15,10,11,14,1,7,6,0,8,13]],
        [[4,11,2,14,15,0,8,13,3,12,9,7,5,10,6,1],
         [13,0,11,7,4,9,1,10,14,3,5,12,2,15,8,6],
         [1,4,11,13,12,3,7,14,10,15,6,8,0,5,9,2],
         [6,11,13,8,1,4,10,7,9,5,0,15,14,2,3,12]],
        [[13,2,8,4,6,15,11,1,10,9,3,14,5,0,12,7],
         [1,15,13,8,10,3,7,4,12,5,6,11,0,14,9,2],  # 注意: JS 原始第 11 位是 11 不是 2
         [7,11,4,1,9,12,14,2,0,6,10,13,15,3,5,8],
         [2,1,14,7,4,10,8,13,15,12,9,0,3,5,6,11]],
    ]
    e = [0] * 32
    for m in range(8):
        row = 2 * bt[6 * m + 0] + bt[6 * m + 5]
        col = (2 * bt[6 * m + 1] * 2 * 2
               + 2 * bt[6 * m + 2] * 2
               + 2 * bt[6 * m + 3]
               + bt[6 * m + 4])
        binary = _get_box_binary(S[m][row][col])
        e[4 * m + 0] = int(binary[0])
        e[4 * m + 1] = int(binary[1])
        e[4 * m + 2] = int(binary[2])
        e[4 * m + 3] = int(binary[3])
    return e


def _p_permute(bt):
    """JS: pPermute"""
    e = [0] * 32
    e[0]=bt[15]; e[1]=bt[6]; e[2]=bt[19]; e[3]=bt[20]
    e[4]=bt[28]; e[5]=bt[11]; e[6]=bt[27]; e[7]=bt[16]
    e[8]=bt[0]; e[9]=bt[14]; e[10]=bt[22]; e[11]=bt[25]
    e[12]=bt[4]; e[13]=bt[17]; e[14]=bt[30]; e[15]=bt[9]
    e[16]=bt[1]; e[17]=bt[7]; e[18]=bt[23]; e[19]=bt[13]
    e[20]=bt[31]; e[21]=bt[26]; e[22]=bt[2]; e[23]=bt[8]
    e[24]=bt[18]; e[25]=bt[12]; e[26]=bt[29]; e[27]=bt[5]
    e[28]=bt[21]; e[29]=bt[10]; e[30]=bt[3]; e[31]=bt[24]
    return e


def _finally_permute(bt):
    """JS: finallyPermute"""
    e = [0] * 64
    e[0]=bt[39]; e[1]=bt[7]; e[2]=bt[47]; e[3]=bt[15]
    e[4]=bt[55]; e[5]=bt[23]; e[6]=bt[63]; e[7]=bt[31]
    e[8]=bt[38]; e[9]=bt[6]; e[10]=bt[46]; e[11]=bt[14]
    e[12]=bt[54]; e[13]=bt[22]; e[14]=bt[62]; e[15]=bt[30]
    e[16]=bt[37]; e[17]=bt[5]; e[18]=bt[45]; e[19]=bt[13]
    e[20]=bt[53]; e[21]=bt[21]; e[22]=bt[61]; e[23]=bt[29]
    e[24]=bt[36]; e[25]=bt[4]; e[26]=bt[44]; e[27]=bt[12]
    e[28]=bt[52]; e[29]=bt[20]; e[30]=bt[60]; e[31]=bt[28]
    e[32]=bt[35]; e[33]=bt[3]; e[34]=bt[43]; e[35]=bt[11]
    e[36]=bt[51]; e[37]=bt[19]; e[38]=bt[59]; e[39]=bt[27]
    e[40]=bt[34]; e[41]=bt[2]; e[42]=bt[42]; e[43]=bt[10]
    e[44]=bt[50]; e[45]=bt[18]; e[46]=bt[58]; e[47]=bt[26]
    e[48]=bt[33]; e[49]=bt[1]; e[50]=bt[41]; e[51]=bt[9]
    e[52]=bt[49]; e[53]=bt[17]; e[54]=bt[57]; e[55]=bt[25]
    e[56]=bt[32]; e[57]=bt[0]; e[58]=bt[40]; e[59]=bt[8]
    e[60]=bt[48]; e[61]=bt[16]; e[62]=bt[56]; e[63]=bt[24]
    return e


def _generate_keys(key_bt):
    """JS: generateKeys"""
    LOOP = [1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1]
    e = [0] * 56
    for t in range(7):
        k = 7
        for j in range(8):
            e[8 * t + j] = key_bt[8 * k + t]
            k -= 1

    keys = []
    for rnd in range(16):
        for _ in range(LOOP[rnd]):
            s0, s28 = e[0], e[28]
            for k in range(27):
                e[k] = e[k + 1]
                e[28 + k] = e[29 + k]
            e[27] = s0
            e[55] = s28

        c = [0] * 48
        c[0]=e[13]; c[1]=e[16]; c[2]=e[10]; c[3]=e[23]
        c[4]=e[0]; c[5]=e[4]; c[6]=e[2]; c[7]=e[27]
        c[8]=e[14]; c[9]=e[5]; c[10]=e[20]; c[11]=e[9]
        c[12]=e[22]; c[13]=e[18]; c[14]=e[11]; c[15]=e[3]
        c[16]=e[25]; c[17]=e[7]; c[18]=e[15]; c[19]=e[6]
        c[20]=e[26]; c[21]=e[19]; c[22]=e[12]; c[23]=e[1]
        c[24]=e[40]; c[25]=e[51]; c[26]=e[30]; c[27]=e[36]
        c[28]=e[46]; c[29]=e[54]; c[30]=e[29]; c[31]=e[39]
        c[32]=e[50]; c[33]=e[44]; c[34]=e[32]; c[35]=e[47]
        c[36]=e[43]; c[37]=e[48]; c[38]=e[38]; c[39]=e[55]
        c[40]=e[33]; c[41]=e[52]; c[42]=e[45]; c[43]=e[41]
        c[44]=e[49]; c[45]=e[35]; c[46]=e[28]; c[47]=e[31]
        keys.append(c)
    return keys


def _enc(data_bt, key_bt):
    """JS: enc — 单块 DES 加密"""
    keys = _generate_keys(key_bt)
    n = _init_permute(data_bt)

    t = n[:32]      # left
    s = n[32:]      # right
    o = [0] * 32

    for c in range(16):
        for f in range(32):
            o[f] = t[f]
            t[f] = s[f]
        lk = keys[c]
        b = _xor(_p_permute(_s_box_permute(_xor(_expand_permute(s), lk))), o)
        for u in range(32):
            s[u] = b[u]

    k = s + t  # swap L/R
    return _finally_permute(k)


# ==================== 公开 API ====================

def str_enc(data: str, key1: str, key2: str, key3: str) -> str:
    """
    与 JS strEnc(data, key1, key2, key3) 完全等价。
    对 data 做三轮 DES 加密，返回大写 HEX 字符串。
    """
    k1 = _get_key_bytes(key1) if key1 else []
    k2 = _get_key_bytes(key2) if key2 else []
    k3 = _get_key_bytes(key3) if key3 else []

    leng = len(data)
    if leng == 0:
        return ""

    result = ""
    iterator = leng // 4
    remainder = leng % 4

    for i in range(iterator):
        block = _str_to_bt(data[4 * i: 4 * i + 4])
        for kb in k1:
            block = _enc(block, kb)
        for kb in k2:
            block = _enc(block, kb)
        for kb in k3:
            block = _enc(block, kb)
        result += _bt64_to_hex(block)

    if remainder > 0:
        block = _str_to_bt(data[4 * iterator: leng])
        for kb in k1:
            block = _enc(block, kb)
        for kb in k2:
            block = _enc(block, kb)
        for kb in k3:
            block = _enc(block, kb)
        result += _bt64_to_hex(block)

    return result


def encrypt_password(plain_pwd: str) -> str:
    """
    将明文密码加密为选课系统登录所需格式：
      Base64( strEnc(pwd, "this", "password", "is") )
    """
    hex_str = str_enc(plain_pwd, "this", "password", "is")
    return _b64.b64encode(hex_str.encode("utf-8")).decode("utf-8")


if __name__ == "__main__":
    import sys
    pwd = sys.argv[1] if len(sys.argv) > 1 else input("请输入明文密码: ")
    print("加密结果:", encrypt_password(pwd))
