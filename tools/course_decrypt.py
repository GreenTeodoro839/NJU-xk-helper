import base64
import json
import sys
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

# ================= 配置 =================
# 和 xk.py 保持一致的密钥
AES_KEY = 'wHm1xj3afURghi0c'


# =======================================

def decrypt_payload(encrypted_b64):
    """
    解密逻辑：Base64解码 -> AES-ECB解密 -> 去除PKCS7填充 -> utf-8解码
    """
    try:
        # 1. Base64 解码
        encrypted_bytes = base64.b64decode(encrypted_b64)

        # 2. AES 解密
        key_bytes = AES_KEY.encode('utf-8')
        cipher = AES.new(key_bytes, AES.MODE_ECB)
        decrypted_padded = cipher.decrypt(encrypted_bytes)

        # 3. 去填充 (Unpad)
        decrypted_bytes = unpad(decrypted_padded, AES.block_size)

        return decrypted_bytes.decode('utf-8')
    except Exception as e:
        return None


def main():
    print("=" * 60)
    print("   南大选课 Payload 解密工具 (AES-ECB)")
    print("=" * 60)

    while True:
        try:
            # 1. 获取输入
            print("\n请粘贴抓包到的 addParam 内容 (输入 'q' 退出):")
            raw_input = input(">>> ").strip()

            if raw_input.lower() == 'q':
                print("拜拜 👋")
                break

            if not raw_input:
                continue

            # 2. 执行解密
            decrypted_text = decrypt_payload(raw_input)

            if decrypted_text:
                print("\n✅ 解密成功！原始字符串如下:")
                print("-" * 50)
                print(decrypted_text)
                print("-" * 50)

                # 3. 尝试智能解析 (分离 JSON 和 timestrap)
                if "?timestrap=" in decrypted_text:
                    json_part, time_part = decrypted_text.split("?timestrap=")
                    print(f"🕒 时间戳: {time_part}")

                    try:
                        json_obj = json.loads(json_part)
                        print("📦 JSON 数据 (格式化后):")
                        print(json.dumps(json_obj, indent=4, ensure_ascii=False))
                    except:
                        print("⚠️ JSON 解析失败，可能格式不标准")
                else:
                    # 如果没有时间戳，直接尝试解析整个字符串
                    try:
                        json_obj = json.loads(decrypted_text)
                        print("📦 JSON 数据 (格式化后):")
                        print(json.dumps(json_obj, indent=4, ensure_ascii=False))
                    except:
                        pass
            else:
                print("\n❌ 解密失败！")
                print("可能原因：")
                print("1. 粘贴的字符串不完整")
                print("2. 并不是 Base64 格式")
                print("3. 密钥不匹配")

        except KeyboardInterrupt:
            print("\n\n强制退出。")
            sys.exit(0)


if __name__ == "__main__":
    main()