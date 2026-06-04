"""生成 CDK 激活码。用法: python gen_cdk.py 730 [--count 5] [--name 游戏名]"""

from __future__ import annotations

import argparse
import sys

from cdk_service import CdkService


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 Steam 游戏盒子 CDK 激活码")
    parser.add_argument("appid", help="Steam AppID")
    parser.add_argument("--count", type=int, default=1, help="生成数量")
    parser.add_argument("--name", default="", help="游戏名称（可选）")
    parser.add_argument("--note", default="", help="备注")
    parser.add_argument("--signed", action="store_true", help="输出签名 CDK（无需写入数据库）")
    args = parser.parse_args()

    service = CdkService()

    if args.signed:
        secret = str(service.settings().get("secret", ""))
        cdk = service.make_signed_cdk(args.appid, secret)
        print(f"AppID: {args.appid}")
        print(f"签名 CDK: {cdk}")
        print(f"Secret: {secret}")
        return 0

    try:
        codes = service.generate_batch(args.appid, args.count, name=args.name, note=args.note)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1

    print(f"已为 AppID {args.appid} 生成 {len(codes)} 个 CDK：")
    for code in codes:
        print(code)
    print(f"\n已写入: {service.db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
