"""命令行入库 / CDK 激活（无需打开 GUI）。"""

from __future__ import annotations

import argparse
import asyncio
import sys

from box_service import BoxService, ImportOptions


def _pick_source(service: BoxService, source_key: str | None):
    sources = service.get_manifest_sources()
    if not sources:
        return None
    if not source_key:
        return sources[0]
    key = source_key.lower()
    for s in sources:
        if s.key.lower() == key or key in s.name.lower():
            return s
    print(f"未找到清单源: {source_key}", file=sys.stderr)
    print("可用源:", ", ".join(s.key for s in sources[:8]), file=sys.stderr)
    return None


async def main_async(args: argparse.Namespace) -> int:
    service = BoxService()
    await service.initialize()

    if args.prepare:
        ok, msg = await service.prepare_activation_environment()
        print(msg)
        if ok and args.open_steam:
            service.open_steam_activate_window()
        return 0 if ok else 1

    app_ids: list[str] = []
    if args.cdk:
        validation = service.cdk.validate(args.cdk)
        if not validation.valid:
            print(validation.message, file=sys.stderr)
            return 1
        app_ids = [validation.appid]
        print(f"CDK 有效 -> AppID {validation.appid} ({validation.name})")
    elif args.appid:
        app_ids = [str(a).strip() for a in args.appid if str(a).strip().isdigit()]
    else:
        print("请指定 --cdk、--appid 或 --prepare", file=sys.stderr)
        return 1

    if not app_ids:
        print("未指定有效的 AppID", file=sys.stderr)
        return 1

    source = _pick_source(service, args.source)
    if not source:
        print("无可用清单源", file=sys.stderr)
        return 1

    options = ImportOptions(
        auto_update_manifest=args.auto_update,
        add_all_dlc=args.dlc,
        patch_workshop_key=False,
    )
    github_repo = source.repo if source.kind in ("builtin_github", "custom_github") else None
    ok_count = 0

    for app_id in app_ids:
        if args.cdk and len(app_ids) == 1:
            result = await service.activate_cdk(
                args.cdk,
                source,
                options,
                auto_fallback=not args.no_fallback,
                github_repo=github_repo,
                auto_finalize=not args.no_finalize,
                open_steam_ui=args.open_steam,
            )
            print(result.message)
            return 0 if result.success else 1

        print(f"正在入库 AppID {app_id} …")
        if not args.no_fallback:
            result = await service.import_game_with_fallback(
                app_id, source, options, github_repo=github_repo
            )
        else:
            result = await service.import_game(app_id, source, options, github_repo=github_repo)

        if not result.success:
            print(f"失败: {result.message}", file=sys.stderr)
            continue

        print(result.message)
        if not args.no_finalize:
            ok, msg = await service.finalize_one_click_import(app_id)
            print(msg)
            if not ok:
                return 1
        if args.open_steam:
            service.open_steam_install_page(app_id)
        ok_count += 1

    if ok_count == 0:
        return 1
    print(f"完成: 成功 {ok_count}/{len(app_ids)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="命令行入库 / CDK 激活",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python cdk_cli.py --prepare
  python cdk_cli.py --cdk DEMO-7300-CSGO-0001
  python cdk_cli.py --appid 730
  python cdk_cli.py --appid 730 570 105600
  python cdk_cli.py --appid 730 --source manifesthub2
        """,
    )
    parser.add_argument("--cdk", help="CDK 激活码（校验后入库并绑定）")
    parser.add_argument("--appid", nargs="+", help="直接按 AppID 入库")
    parser.add_argument("--prepare", action="store_true", help="仅部署激活环境")
    parser.add_argument("--source", help="清单源 key，如 manifesthub2 / sudama")
    parser.add_argument("--no-fallback", action="store_true", help="关闭失败自动换源")
    parser.add_argument("--no-finalize", action="store_true", help="跳过注入与重启 Steam")
    parser.add_argument("--no-open-steam", dest="open_steam", action="store_false", help="不打开 Steam 页面")
    parser.add_argument("--dlc", action="store_true", help="入库 DLC")
    parser.add_argument("--auto-update", action="store_true", help="自动更新清单")
    parser.set_defaults(open_steam=True)
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
